"""Listas inteligentes, progresso das listas, exportação e ordem completa (LSA-01..12).

Cada regra do catálogo `REGRAS` valida os próprios parâmetros e monta uma consulta sobre os dados do dono;
os jogos são calculados a cada acesso e nunca copiados para `ListItem` (LSA-03).
"""
import csv
import io
import json
from datetime import date, timedelta

from flask import Blueprint, Response, abort, flash, redirect, render_template, request
from sqlalchemy import func
from sqlalchemy.orm import joinedload, selectinload

from . import serializadores
from .diario import pode_ver_perfil
from .diario_import import sem_formula
from .listas import MAX_ITEMS, MAX_LISTS, get_list
from .menores.politica import visibilidade_padrao
from .models import (
    LOJAS, NOMES_DAS_LOJAS, STATUSES, Game, GameList, Genre, JogoFonte, ListaInteligente, ListItem, Review,
    SessaoJogo, Status, db, game_genres,
)
from .tempo import agora, hoje
from .util import current_user, login_required, paginate

bp = Blueprint("listas_inteligentes", __name__)
MAX_INTELIGENTES = 20


class ParametroInvalido(ValueError):
    pass


def _inteiro(params, nome, minimo, maximo, padrao):
    bruto = params.get(nome, "")
    if bruto in ("", None):
        return padrao
    try:
        valor = int(bruto)
    except (TypeError, ValueError):
        raise ParametroInvalido(f"{nome} deve ser um número inteiro.") from None
    if not minimo <= valor <= maximo:
        raise ParametroInvalido(f"{nome} deve ficar entre {minimo} e {maximo}.")
    return valor


def _jogos_visiveis():
    """Sem removidos nem mesclados em outro (LSA-04, MRG-08)."""
    return Game.query.filter(Game.removed_at.is_(None), Game.mesclado_em_id.is_(None))


def _status_do_dono(user_id):
    return Status.query.filter_by(user_id=user_id).subquery()


# ---------- as regras ----------
def largados_params(p):
    return {"dias": _inteiro(p, "dias", 7, 365, 30)}


def largados(user_id, p):
    """Jogando sem sessão há N dias (ou nunca) e abandonados; a sessão mais antiga primeiro."""
    ultima = (db.session.query(SessaoJogo.game_id, func.max(SessaoJogo.jogada_em).label("dia"))
              .filter(SessaoJogo.user_id == user_id).group_by(SessaoJogo.game_id).subquery())
    limite = hoje() - timedelta(days=p["dias"])
    st = _status_do_dono(user_id)
    horas = int(agora().utcoffset().total_seconds() // 3600)
    parado = func.coalesce(ultima.c.dia, func.date(st.c.updated_at, f"{horas} hours"))  # dia em Belém
    return (
        _jogos_visiveis().join(st, st.c.game_id == Game.id).outerjoin(ultima, ultima.c.game_id == Game.id)
        .filter((st.c.value == "abandonado") | ((st.c.value == "jogando") & (parado <= limite)))
        .order_by(parado, Game.title)
    )


def loja_params(p):
    if p.get("loja") not in LOJAS:
        raise ParametroInvalido("Escolha a loja: Steam, GOG ou Epic.")
    return {"loja": p["loja"]}


def wishlist_na_loja(user_id, p):
    st = _status_do_dono(user_id)
    return (
        _jogos_visiveis().join(st, st.c.game_id == Game.id)
        .join(JogoFonte, (JogoFonte.game_id == Game.id) & (JogoFonte.fonte == p["loja"]))
        .filter(st.c.value == "planejado").order_by(Game.title)
    )


def ano_params(p):
    return {"ano": _inteiro(p, "ano", 2000, hoje().year, hoje().year)}


def zerados_no_ano(user_id, p):
    st = _status_do_dono(user_id)
    return (
        _jogos_visiveis().join(st, st.c.game_id == Game.id)
        .filter(st.c.zerado_em.between(date(p["ano"], 1, 1), date(p["ano"], 12, 31)))
        .order_by(st.c.zerado_em, Game.title)
    )


def genero_params(p):
    genero = Genre.query.filter_by(slug=p.get("genero", "")).first()
    if genero is None:
        raise ParametroInvalido("Escolha um gênero existente.")
    status = p.get("status") or ""
    if status and status not in STATUSES:
        raise ParametroInvalido("Status desconhecido.")
    return {"genero": genero.slug, "status": status}


def por_genero(user_id, p):
    st = _status_do_dono(user_id)
    q = (_jogos_visiveis().join(st, st.c.game_id == Game.id)
         .join(game_genres, game_genres.c.game_id == Game.id).join(Genre, Genre.id == game_genres.c.genre_id)
         .filter(Genre.slug == p["genero"]))
    if p["status"]:
        q = q.filter(st.c.value == p["status"])
    return q.order_by(Game.title)


def top_params(p):
    return {"n": _inteiro(p, "n", 5, 50, 10)}


def mais_jogados(user_id, p):
    total = func.sum(SessaoJogo.duracao_min)
    ids = (db.session.query(SessaoJogo.game_id).join(Game, Game.id == SessaoJogo.game_id)
           .filter(SessaoJogo.user_id == user_id, Game.removed_at.is_(None), Game.mesclado_em_id.is_(None))
           .group_by(SessaoJogo.game_id).order_by(total.desc(), SessaoJogo.game_id).limit(p["n"]).subquery())
    soma = (db.session.query(SessaoJogo.game_id, func.sum(SessaoJogo.duracao_min).label("m"))
            .filter(SessaoJogo.user_id == user_id).group_by(SessaoJogo.game_id).subquery())
    return (_jogos_visiveis().join(ids, ids.c.game_id == Game.id).join(soma, soma.c.game_id == Game.id)
            .order_by(soma.c.m.desc(), Game.title))


def sem_params(p):
    return {}


def jogados_sem_avaliacao(user_id, p):
    st = _status_do_dono(user_id)
    avaliados = db.session.query(Review.game_id).filter(Review.user_id == user_id)
    return (_jogos_visiveis().join(st, st.c.game_id == Game.id)
            .filter(st.c.value == "jogado", Game.id.not_in(avaliados)).order_by(Game.title))


REGRAS = {
    "largados": ("Comecei e larguei", largados_params, largados,
                 lambda p: f"Jogando sem sessão há {p['dias']} dias ou abandonados"),
    "wishlist_na_loja": ("Wishlist disponível numa loja", loja_params, wishlist_na_loja,
                         lambda p: f"Planejados disponíveis na {NOMES_DAS_LOJAS[p['loja']]}"),
    "zerados_no_ano": ("Zerados no ano", ano_params, zerados_no_ano, lambda p: f"Zerados em {p['ano']}"),
    "por_genero": ("Por gênero", genero_params, por_genero,
                   lambda p: f"Jogos de {p['genero']}" + (f" com status {STATUSES[p['status']].lower()}" if p["status"] else "")),
    "mais_jogados": ("Mais jogados", top_params, mais_jogados, lambda p: f"Os {p['n']} jogos com mais horas no diário"),
    "jogados_sem_avaliacao": ("Jogados sem avaliação", sem_params, jogados_sem_avaliacao,
                              lambda p: "Jogados que você ainda não avaliou"),
}


def validar_regra(tipo, params):
    """Regra gravável como JSON, ou ParametroInvalido (LSA-02)."""
    if tipo not in REGRAS:
        raise ParametroInvalido("Regra desconhecida.")
    return {"tipo": tipo, "parametros": REGRAS[tipo][1](params)}


def descricao(regra):
    return REGRAS[regra["tipo"]][3](regra["parametros"])


def jogos_da_regra(lista):
    regra = json.loads(lista.regra)
    return REGRAS[regra["tipo"]][2](lista.user_id, regra["parametros"])


# ---------- progresso (LSA-08) ----------
def progresso(user_id, game_ids_q):
    """Status do dono e horas registradas nesses jogos; `game_ids_q` é uma subconsulta de ids. Duas consultas."""
    por_status = dict(
        db.session.query(Status.value, func.count())
        .filter(Status.user_id == user_id, Status.game_id.in_(game_ids_q)).group_by(Status.value).all()
    )
    minutos = (db.session.query(func.coalesce(func.sum(SessaoJogo.duracao_min), 0))
               .filter(SessaoJogo.user_id == user_id, SessaoJogo.game_id.in_(game_ids_q)).scalar())
    return {"status": {k: por_status.get(k, 0) for k in STATUSES}, "minutos": minutos}


def progresso_comum(gl, own):
    ids = (db.session.query(ListItem.game_id).join(Game, Game.id == ListItem.game_id)
           .filter(ListItem.list_id == gl.id))
    if not own:
        ids = ids.filter(Game.removed_at.is_(None))
    total = ids.count()
    return {"total": total, **progresso(gl.user_id, ids.scalar_subquery())}


def progresso_inteligente(lista):
    q = jogos_da_regra(lista)
    ids = q.with_entities(Game.id).order_by(None)
    return {"total": ids.count(), **progresso(lista.user_id, ids.scalar_subquery())}


# ---------- exportação (LSA-09) ----------
def _status_e_horas(user_id, games):
    ids = [g.id for g in games]
    if not ids:
        return {}, {}
    status = dict(db.session.query(Status.game_id, Status.value).filter(Status.user_id == user_id, Status.game_id.in_(ids)))
    minutos = dict(db.session.query(SessaoJogo.game_id, func.sum(SessaoJogo.duracao_min))
                   .filter(SessaoJogo.user_id == user_id, SessaoJogo.game_id.in_(ids)).group_by(SessaoJogo.game_id))
    return status, minutos


def _carregar_relacoes(games):
    """Desenvolvedora, gêneros e plataformas de todos os jogos em 3 consultas, para o serializador."""
    ids = [g.id for g in games]
    if ids:
        Game.query.filter(Game.id.in_(ids)).options(
            joinedload(Game.developer), selectinload(Game.genres), selectinload(Game.platforms)).all()


def dados_da_lista(nome, url, publica, user_id, games, automatica=False, regra=None):
    _carregar_relacoes(games)
    status, minutos = _status_e_horas(user_id, games)
    itens = [(i, g, status.get(g.id), minutos.get(g.id, 0)) for i, g in enumerate(games, start=1)]
    return serializadores.lista(nome, url, publica, itens, automatica, regra)


def exportar(nome, url, publica, user_id, games, formato, automatica=False, regra=None):
    dados = dados_da_lista(nome, url, publica, user_id, games, automatica, regra)
    if formato == "json":
        return Response(json.dumps(dados, ensure_ascii=False, indent=1), mimetype="application/json",
                        headers={"Content-Disposition": 'attachment; filename="lista.json"'})
    saida = io.StringIO()
    escritor = csv.writer(saida)
    escritor.writerow(["posicao", "slug", "titulo", "lancamento", "status", "horas"])
    for item in dados["itens"]:
        j = item["jogo"]
        escritor.writerow([item["posicao"], sem_formula(j["slug"]), sem_formula(j["titulo"]), j["lancamento"],
                           item["status"] or "", item["horas"]])
    return Response(saida.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="lista.csv"'})


# ---------- rotas ----------
def inteligente_ou_404(lid, dono=False):
    """LSA-05: privada ou de perfil que o visitante não vê responde 404; escrita de outra pessoa, 403."""
    lista, me = db.get_or_404(ListaInteligente, lid), current_user()
    own = me is not None and me.id == lista.user_id
    if not own and (not lista.publica or not pode_ver_perfil(me, lista.user)):
        abort(404)
    if dono and not own:
        abort(403 if me else 404)
    return lista, own


def _form_regra(form):
    params = {k: form.get(k, "") for k in ("dias", "loja", "ano", "genero", "status", "n")}
    return validar_regra(form.get("tipo", ""), params)


def _tela_nova(erros, form, status=200):
    return render_template("listas/inteligente_nova.html", erros=erros, form=form, regras=REGRAS, lojas=LOJAS,
                           nomes_lojas=NOMES_DAS_LOJAS, generos=Genre.query.order_by(Genre.name).all(),
                           statuses=STATUSES), status


@bp.route("/listas/inteligentes/nova", methods=["GET", "POST"])
@login_required
def nova():
    me = current_user()
    if request.method == "GET":
        return _tela_nova({}, {})
    erros, nome = {}, request.form.get("nome", "").strip()
    if not 1 <= len(nome) <= 80:
        erros["nome"] = "O nome é obrigatório e deve ter até 80 caracteres."
    elif ListaInteligente.query.filter_by(user_id=me.id).count() >= MAX_INTELIGENTES:
        erros["nome"] = f"Limite de {MAX_INTELIGENTES} listas inteligentes atingido."
    try:
        regra = _form_regra(request.form)
    except ParametroInvalido as exc:
        erros["regra"] = str(exc)
    if erros:
        return _tela_nova(erros, request.form, 400)
    publica = request.form.get("visibilidade", visibilidade_padrao(me)) != "privada"
    lista = ListaInteligente(user_id=me.id, nome=nome, publica=publica, regra=json.dumps(regra))
    db.session.add(lista)
    db.session.commit()
    return redirect(f"/listas/inteligentes/{lista.id}")


@bp.route("/listas/inteligentes/<int:lid>")
def ver(lid):
    lista, own = inteligente_ou_404(lid)
    page = paginate(jogos_da_regra(lista), request.args.get("page"))
    return render_template("listas/inteligente.html", lista=lista, own=own, page=page,
                           descricao=descricao(json.loads(lista.regra)), regra=json.loads(lista.regra),
                           prog=progresso_inteligente(lista), statuses=STATUSES, regras=REGRAS, lojas=LOJAS,
                           nomes_lojas=NOMES_DAS_LOJAS, generos=Genre.query.order_by(Genre.name).all())


@bp.route("/listas/inteligentes/<int:lid>/editar", methods=["POST"])
@login_required
def editar(lid):
    lista, _ = inteligente_ou_404(lid, dono=True)
    nome = request.form.get("nome", "").strip()
    try:
        regra = _form_regra({**request.form, "tipo": json.loads(lista.regra)["tipo"]})
    except ParametroInvalido as exc:
        flash(str(exc), "erro")
        return ver(lid), 400  # LSA-02
    if not 1 <= len(nome) <= 80:
        flash("O nome é obrigatório e deve ter até 80 caracteres.", "erro")
        return ver(lid), 400
    lista.nome, lista.regra = nome, json.dumps(regra)
    lista.publica = request.form.get("visibilidade") != "privada"
    db.session.commit()
    flash("Lista atualizada.", "ok")
    return redirect(f"/listas/inteligentes/{lid}")


@bp.route("/listas/inteligentes/<int:lid>/apagar", methods=["POST"])
@login_required
def apagar(lid):
    lista, _ = inteligente_ou_404(lid, dono=True)
    db.session.delete(lista)
    db.session.commit()
    return redirect("/listas")


@bp.route("/listas/inteligentes/<int:lid>/congelar", methods=["POST"])
@login_required
def congelar(lid):
    """Copia os jogos atuais numa lista comum, cortando em 200 itens (LSA-07)."""
    lista, _ = inteligente_ou_404(lid, dono=True)
    if GameList.query.filter_by(user_id=lista.user_id).count() >= MAX_LISTS:  # LIS-10
        flash(f"Limite de {MAX_LISTS} listas atingido.", "erro")
        return redirect(f"/listas/inteligentes/{lid}")
    jogos = jogos_da_regra(lista).limit(MAX_ITEMS + 1).all()
    gl = GameList(user_id=lista.user_id, name=lista.nome[:80], description=descricao(json.loads(lista.regra))[:500],
                  public=lista.publica)
    db.session.add(gl)
    db.session.flush()
    for pos, game in enumerate(jogos[:MAX_ITEMS]):
        db.session.add(ListItem(list_id=gl.id, game_id=game.id, position=pos))
    db.session.commit()
    if len(jogos) > MAX_ITEMS:
        flash(f"A lista foi cortada nos primeiros {MAX_ITEMS} jogos.", "erro")
    else:
        flash("Lista congelada como lista comum.", "ok")
    return redirect(f"/listas/{gl.id}")


@bp.route("/listas/inteligentes/<int:lid>/exportar.<formato>")
def exportar_inteligente(lid, formato):
    if formato not in ("json", "csv"):
        abort(404)
    lista, _ = inteligente_ou_404(lid)
    regra = json.loads(lista.regra)
    return exportar(lista.nome, f"/listas/inteligentes/{lid}", lista.publica, lista.user_id,
                    jogos_da_regra(lista).all(), formato, automatica=True, regra=regra)


@bp.route("/listas/<int:lid>/exportar.<formato>")
def exportar_comum(lid, formato):
    if formato not in ("json", "csv"):
        abort(404)
    gl, own = get_list(lid)
    itens = ListItem.query.filter_by(list_id=gl.id).options(joinedload(ListItem.game)).order_by(ListItem.position)
    games = [i.game for i in itens if own or i.game.removed_at is None]
    return exportar(gl.name, f"/listas/{lid}", gl.public, gl.user_id, games, formato)


@bp.route("/listas/<int:lid>/ordem", methods=["POST"])
@login_required
def ordem(lid):
    """A ordem inteira de uma vez; faltando, repetido ou de fora responde 400 sem mudar nada (LSA-10)."""
    gl, _ = get_list(lid, owner_only=True)
    try:
        ids = [int(x) for x in request.form.get("ordem", "").split(",") if x.strip()]
    except ValueError:
        abort(400)
    itens = {i.game_id: i for i in gl.items}
    if len(ids) != len(set(ids)) or set(ids) != set(itens):
        abort(400)
    for pos, gid in enumerate(ids):
        itens[gid].position = pos
    db.session.commit()
    if request.headers.get("X-Requested-With") == "fetch":
        return "", 204
    return redirect(f"/listas/{lid}")
