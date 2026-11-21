"""Rankings e temporadas trimestrais (RNK-01..12).

Cada categoria é uma consulta agregada no banco, (usuário, valor, instante), com o desempate determinístico
de RNK-07. Temporada encerrada é lida do `SnapshotRanking`, que não muda depois de gravado (RNK-09).
"""
from datetime import date, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request
from sqlalchemy import case, func

from . import gamificacao
from .diario import ocultos_para
from .menores.politica import pode
from .models import (
    AuditLog, Desclassificacao, ExtratoXP, Follow, Game, Like, Review, SessaoJogo, SnapshotRanking, Status,
    Temporada, User, db, now,
)
from .tempo import hoje, inicio_do_dia_utc
from .util import admin_required, current_user, slugify

bp = Blueprint("rankings", __name__)
CATEGORIAS = {"xp": "XP", "zerados": "Jogos zerados", "horas": "Horas registradas", "curtidas": "Curtidas recebidas"}
CATEGORIA_PADRAO, JANELA_PADRAO = "xp", "atual"
POR_PAGINA, RETRATO = 20, 100
COM_DATA = ("manual", "csv")  # a sessão de saldo da Steam traria anos de horas de uma vez (DIA-08)
ESCOPOS = ("seguindo", "global", "clube")
_escopos_extras = {}


def registrar_escopo(nome, funcao):
    """Escopo vindo de outra spec (clube, CLB-09): `funcao(me, args) -> (ids permitidos, contexto)`."""
    _escopos_extras[nome] = funcao


def bloqueados_com(user):
    """Ids com bloqueio entre `user` e eles; a spec de bloqueios (BLQ) preenche (RNK-05)."""
    return set()


# ---------- temporadas (RNK-01) ----------
def trimestre(dia):
    t = (dia.month - 1) // 3
    inicio = date(dia.year, 3 * t + 1, 1)
    fim = date(dia.year + (t == 3), (3 * t + 3) % 12 + 1, 1) - timedelta(days=1)
    return f"{dia.year}-T{t + 1}", inicio, fim


def abrir_temporada(dia=None):
    """Cria a temporada do trimestre se não existir; devolve (temporada, criada)."""
    slug, inicio, fim = trimestre(dia or hoje())
    t = Temporada.query.filter_by(slug=slug).first()
    if t is not None:
        return t, False
    t = Temporada(nome=slug, slug=slug, inicio=inicio, fim=fim)
    db.session.add(t)
    db.session.commit()
    return t, True


def janela(valor, temporadas):
    """(temporada ou None, início, fim) da janela; valor desconhecido volta ao padrão (RNK-03)."""
    if valor == "sempre":
        return None, None, None
    por_slug = {t.slug: t for t in temporadas}
    if valor in por_slug:
        t = por_slug[valor]
        return t, t.inicio, t.fim
    atual = next((t for t in temporadas if t.inicio <= hoje() <= t.fim), None)
    if atual is not None:
        return atual, atual.inicio, atual.fim
    _, inicio, fim = trimestre(hoje())
    return None, inicio, fim


# ---------- agregação (RNK-02, RNK-07, RNK-11) ----------
def _entre(coluna, de, ate, data=True):
    if de is None:
        return True
    if data:
        return coluna.between(de, ate)
    return (coluna >= inicio_do_dia_utc(de)) & (coluna < inicio_do_dia_utc(ate + timedelta(days=1)))  # UTC


def consulta(categoria, de, ate):
    """Subconsulta (user_id, valor, instante) da categoria na janela."""
    if categoria == "zerados":
        return (db.session.query(Status.user_id.label("user_id"), func.count().label("valor"),
                                 func.max(Status.zerado_em).label("instante"))
                .join(Game, Game.id == Status.game_id)
                .filter(Status.zerado_em.isnot(None), Game.removed_at.is_(None), _entre(Status.zerado_em, de, ate))
                .group_by(Status.user_id))
    if categoria == "horas":
        return (db.session.query(SessaoJogo.user_id.label("user_id"), func.sum(SessaoJogo.duracao_min).label("valor"),
                                 func.max(SessaoJogo.created_at).label("instante"))
                .filter(SessaoJogo.origem.in_(COM_DATA), _entre(SessaoJogo.jogada_em, de, ate))
                .group_by(SessaoJogo.user_id))
    if categoria == "curtidas":  # curtida sem data (antes da AP2) só conta em "sempre"
        return (db.session.query(Review.user_id.label("user_id"), func.count(Like.id).label("valor"),
                                 func.max(Like.created_at).label("instante"))
                .join(Review, Review.id == Like.review_id).join(Game, Game.id == Review.game_id)
                .filter(Review.hidden.is_(False), Game.removed_at.is_(None), _entre(Like.created_at, de, ate, False))
                .group_by(Review.user_id))
    ganho = func.max(case((ExtratoXP.xp > 0, ExtratoXP.created_at)))  # estorno não atrasa o desempate (RNK-07)
    return (db.session.query(ExtratoXP.user_id.label("user_id"), func.sum(ExtratoXP.xp).label("valor"),
                             ganho.label("instante"))
            .filter(_entre(ExtratoXP.created_at, de, ate, False))
            .group_by(ExtratoXP.user_id))


def apurar(categoria, de, ate, temporada=None, ids=None, so_publicos=False, visitante=None):
    """Linhas (User, valor) ordenadas, numa consulta; quem tem 0 fica de fora e admins nunca entram."""
    sub = consulta(categoria, de, ate).subquery()
    q = (db.session.query(User, sub.c.valor).join(sub, sub.c.user_id == User.id)
         .filter(sub.c.valor > 0, User.role != "admin"))
    if temporada is not None:
        q = q.filter(User.id.not_in(db.session.query(Desclassificacao.user_id).filter(
            Desclassificacao.temporada_id == temporada.id, Desclassificacao.categoria == categoria,
            Desclassificacao.revertida_em.is_(None))))
    if ids is not None:
        q = q.filter(User.id.in_(ids))
    if so_publicos:
        q = q.filter(User.perfil_privado.is_(False))
    else:
        q = q.filter(User.id.not_in(ocultos_para(visitante)))
    q = q.order_by(sub.c.valor.desc(), func.coalesce(sub.c.instante, "9999"), User.id)
    linhas = [(u, v) for u, v in q if not so_publicos or pode(u, "ranking_global")]
    if visitante is not None:
        fora = bloqueados_com(visitante)
        linhas = [(u, v) for u, v in linhas if u.id not in fora]
    return linhas


def mostrar(categoria, valor):
    return f"{valor / 60:.1f}".replace(".", ",") + " h" if categoria == "horas" else str(valor)


# ---------- telas ----------
@bp.route("/rankings")
def pagina():
    me = current_user()
    temporadas = Temporada.query.order_by(Temporada.inicio.desc()).all()
    categoria = request.args.get("categoria") if request.args.get("categoria") in CATEGORIAS else CATEGORIA_PADRAO
    escopo = request.args.get("escopo")
    if escopo not in ESCOPOS:
        escopo = "seguindo" if me else "global"
    if escopo != "global" and me is None:  # RNK-04: o visitante vê só o global
        return redirect(f"/entrar?next={request.full_path.rstrip('?')}")  # GLOBAL-02
    temporada, de, ate = janela(request.args.get("janela", JANELA_PADRAO), temporadas)
    sel_janela = "sempre" if request.args.get("janela") == "sempre" else (temporada.slug if temporada else "atual")
    extra, ids = {}, None
    if escopo == "seguindo":
        ids = db.session.query(Follow.followed_id).filter(Follow.follower_id == me.id).union(
            db.session.query(User.id).filter(User.id == me.id))
    elif escopo in _escopos_extras:
        ids, extra = _escopos_extras[escopo](me, request.args)
    elif escopo != "global":
        escopo = "global"
    if temporada is not None and temporada.encerrada_em is not None:  # RNK-09: lida do retrato
        linhas = retrato(temporada, categoria, ids, me)
    else:
        linhas = apurar(categoria, de, ate, temporada, ids=ids, so_publicos=escopo == "global", visitante=me)
    try:
        pagina_n = max(1, int(request.args.get("page", 1)))
    except ValueError:
        pagina_n = 1
    paginas = max(1, -(-len(linhas) // POR_PAGINA))
    pagina_n = min(pagina_n, paginas)
    inicio = (pagina_n - 1) * POR_PAGINA
    cartao = None
    if me is not None:  # RNK-06
        cartao = next(((i + 1, v) for i, (u, v) in enumerate(linhas) if u.id == me.id), False)
    return render_template(
        "rankings/pagina.html", linhas=list(enumerate(linhas[inicio:inicio + POR_PAGINA], start=inicio + 1)),
        categoria=categoria, categorias=CATEGORIAS, escopo=escopo, temporadas=temporadas, sel_janela=sel_janela,
        temporada=temporada, cartao=cartao, mostrar=mostrar, pagina_n=pagina_n, paginas=paginas, extra=extra,
    )


def retrato(temporada, categoria, ids=None, visitante=None):
    """Linhas gravadas no encerramento, filtradas pelo escopo de quem consulta."""
    q = (db.session.query(User, SnapshotRanking.valor).join(User, User.id == SnapshotRanking.user_id)
         .filter(SnapshotRanking.temporada_id == temporada.id, SnapshotRanking.categoria == categoria))
    if ids is not None:
        q = q.filter(User.id.in_(ids), User.id.not_in(ocultos_para(visitante)))  # RNK-05
    fora = bloqueados_com(visitante) if visitante is not None else set()
    return [(u, v) for u, v in q.order_by(SnapshotRanking.posicao) if u.id not in fora]


@bp.route("/rankings/hall-da-fama")
def hall_da_fama():
    encerradas = Temporada.query.filter(Temporada.encerrada_em.isnot(None)).order_by(Temporada.fim.desc()).all()
    podios = {}
    for linha, user in (db.session.query(SnapshotRanking, User).join(User, User.id == SnapshotRanking.user_id)
                        .filter(SnapshotRanking.posicao <= 3).order_by(SnapshotRanking.posicao)):
        podios.setdefault((linha.temporada_id, linha.categoria), []).append((linha, user))
    return render_template("rankings/hall.html", encerradas=encerradas, podios=podios, categorias=CATEGORIAS,
                           mostrar=mostrar)


def nome_publico(user):
    """Conta anonimizada (DIR) aparece como "usuário removido"."""
    return "usuário removido" if user.username.startswith("removido-") else user.display_name


# ---------- encerramento (RNK-08) ----------
class TemporadaEmAndamento(Exception):
    pass


def encerrar(slug):
    """Grava o retrato dos 100 primeiros do global por categoria e premia o pódio; devolve False se já encerrada."""
    from .notificacoes import notificar

    t = Temporada.query.filter_by(slug=slug).first()
    if t is None:
        raise LookupError(slug)
    if t.encerrada_em is not None:
        return False
    if t.fim >= hoje():
        raise TemporadaEmAndamento(slug)
    premiados = []
    for categoria in CATEGORIAS:
        linhas = apurar(categoria, t.inicio, t.fim, t, so_publicos=True)[:RETRATO]
        for pos, (user, valor) in enumerate(linhas, start=1):
            db.session.add(SnapshotRanking(temporada_id=t.id, categoria=categoria, posicao=pos, user_id=user.id,
                                           valor=valor))
            if pos <= 3:
                premiados.append((user, categoria, pos))
    t.encerrada_em = now()
    for user, categoria, pos in premiados:
        notificar(user.id, "sistema", texto=f"Você ficou em {pos}º lugar em {CATEGORIAS[categoria]} na temporada {t.nome}",
                  alvo_url="/rankings/hall-da-fama")
    db.session.commit()
    for user, categoria, _ in premiados:
        gamificacao.conceder(user.id, f"podio-{categoria}")
    return True


# ---------- admin (RNK-10) ----------
def _auditar(acao, detalhe):
    db.session.add(AuditLog(admin_id=current_user().id, action=acao, detail=detalhe[:300]))


def _ler_data(texto):
    try:
        return date.fromisoformat(texto or "")
    except ValueError:
        return None


@bp.route("/admin/rankings", methods=["GET", "POST"])
@admin_required
def admin():
    erros, form = {}, request.form
    if request.method == "POST" and form.get("acao") == "temporada":
        nome, inicio, fim = form.get("nome", "").strip(), _ler_data(form.get("inicio")), _ler_data(form.get("fim"))
        slug = slugify(nome)
        if not 1 <= len(nome) <= 60 or not slug:
            erros["nome"] = "Informe um nome de até 60 caracteres."
        elif Temporada.query.filter_by(slug=slug).first():
            erros["nome"] = "Já existe uma temporada com esse nome."
        if inicio is None or fim is None or fim < inicio:
            erros["datas"] = "Informe início e fim válidos, com o fim depois do início."
        elif Temporada.query.filter(Temporada.inicio <= fim, Temporada.fim >= inicio).first():
            erros["datas"] = "As datas se sobrepõem a outra temporada."
        if not erros:
            db.session.add(Temporada(nome=nome, slug=slug, inicio=inicio, fim=fim))
            _auditar("ranking.temporada", f"{slug}: {inicio} a {fim}")
            db.session.commit()
            flash("Temporada criada.", "ok")
            return redirect("/admin/rankings")
    elif request.method == "POST" and form.get("acao") == "desclassificar":
        t = Temporada.query.filter_by(slug=form.get("temporada", "")).first()
        alvo = User.query.filter_by(username_lower=form.get("usuario", "").strip().lower()).first()
        motivo = form.get("motivo", "").strip()
        if t is None or t.encerrada_em is not None:
            erros["temporada"] = "Escolha uma temporada não encerrada."
        if alvo is None:
            erros["usuario"] = "Usuário não encontrado."
        if form.get("categoria") not in CATEGORIAS:
            erros["categoria"] = "Escolha a categoria."
        if not 1 <= len(motivo) <= 200:
            erros["motivo"] = "O motivo é obrigatório (até 200 caracteres)."
        if not erros:
            db.session.add(Desclassificacao(temporada_id=t.id, categoria=form["categoria"], user_id=alvo.id,
                                            motivo=motivo, admin_id=current_user().id))
            _auditar("ranking.desclassificar", f"@{alvo.username} em {t.slug}/{form['categoria']}: {motivo}")
            db.session.commit()
            flash("Usuário desclassificado.", "ok")
            return redirect("/admin/rankings")
    temporadas = Temporada.query.order_by(Temporada.inicio.desc()).all()
    desclassificacoes = (db.session.query(Desclassificacao, User, Temporada)
                         .join(User, User.id == Desclassificacao.user_id)
                         .join(Temporada, Temporada.id == Desclassificacao.temporada_id)
                         .order_by(Desclassificacao.id.desc()).all())
    return render_template("rankings/admin.html", temporadas=temporadas, desclassificacoes=desclassificacoes,
                           categorias=CATEGORIAS, erros=erros, form=form), (400 if erros else 200)


@bp.route("/admin/rankings/desclassificacoes/<int:did>/reverter", methods=["POST"])
@admin_required
def reverter(did):
    d = db.get_or_404(Desclassificacao, did)
    t = db.session.get(Temporada, d.temporada_id)
    if t.encerrada_em is not None:
        abort(400)  # só enquanto a temporada não foi encerrada
    d.revertida_em = now()
    _auditar("ranking.reverter", f"desclassificação #{d.id} em {t.slug}")
    db.session.commit()
    flash("Desclassificação revertida.", "ok")
    return redirect("/admin/rankings")
