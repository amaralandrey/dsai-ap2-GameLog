"""Ofertas e alertas de preço (OFR-01..12): o mais barato, as promoções da wishlist e os avisos depois da coleta.

Os avisos saem por `notificar()` só com o consentimento `ofertas` (CNS) e nunca para menores (PMN-05, PMN-07).
"""
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request
from sqlalchemy import func

from .menores.politica import pode
from .models import (LOJAS_COM_PRECO, MAX_ALERTAS, NOMES_DAS_LOJAS, AlertaPreco, Consentimento, Game, JogoFonte,
                     PrecoLoja, Status, User, db, visible_games)
from .notificacoes import notificar
from .precos import ORDEM_DAS_LOJAS, VALIDADE, atual, atuais, reais
from .privacidade import consentimentos
from .tempo import agora_utc
from .util import current_user, login_required, paginate

bp = Blueprint("ofertas", __name__)
SO_ADULTOS = "Ofertas não estão disponíveis para menores de 18 anos"
DESCONTO_MIN, DESCONTO_MAX = 10, 90
MAX_CENTAVOS = 100_000_000
ITENS_NO_RESUMO = 5


def centavos(texto):
    """Reais digitados (`49,90` ou `49.90`) em centavos; inválido, zero ou negativo vira None (BUS-08)."""
    try:
        valor = Decimal((texto or "").strip().replace(",", "."))
    except InvalidOperation:
        return None
    if not valor.is_finite() or valor <= 0 or valor * 100 >= MAX_CENTAVOS:
        return None
    return int((valor * 100).to_integral_value())


def url_segura(url):
    """Só `http`/`https`, como gravado no vínculo, sem parâmetro nenhum acrescentado (OFR-10)."""
    return url if url and url.startswith(("http://", "https://")) else ""


def mais_barato(precos, momento=None):
    """A loja de menor preço entre os preços atuais; no empate, Steam antes de GOG (OFR-01)."""
    vigentes = [p for p in precos if atual(p, momento)]
    return min(vigentes, key=lambda p: (p.preco_centavos, LOJAS_COM_PRECO.index(p.fonte)), default=None)


def ultimas_linhas(*filtros):
    """Ids da linha mais recente de cada jogo e loja."""
    ordem = func.row_number().over(partition_by=(PrecoLoja.game_id, PrecoLoja.fonte),
                                   order_by=(PrecoLoja.coletado_em.desc(), PrecoLoja.id.desc()))
    sub = db.session.query(PrecoLoja.id, ordem.label("n")).filter(*filtros).subquery()
    return db.session.query(sub.c.id).filter(sub.c.n == 1)


def ler_filtros(args):
    """`loja`, `desconto_min` e `preco_max` válidos; o resto é ignorado sem erro (OFR-03)."""
    filtros = {}
    if args.get("loja") in LOJAS_COM_PRECO:
        filtros["loja"] = args["loja"]
    try:
        desconto = int(args.get("desconto_min", ""))
        if DESCONTO_MIN <= desconto <= DESCONTO_MAX:
            filtros["desconto_min"] = desconto
    except ValueError:
        pass
    if (maximo := centavos(args.get("preco_max"))) is not None:
        filtros["preco_max"] = maximo
    return filtros


def consulta(user, loja=None, desconto_min=None, preco_max=None):
    """(PrecoLoja, Game, url_loja) dos planejados em promoção, um por jogo: o mais barato das lojas que passam nos
    filtros; do maior desconto para o menor, depois o menor preço e o título (OFR-02)."""
    filtros = [PrecoLoja.id.in_(ultimas_linhas()), PrecoLoja.desconto_pct > 0,
               PrecoLoja.verificado_em >= agora_utc() - VALIDADE]
    if loja:
        filtros.append(PrecoLoja.fonte == loja)
    if desconto_min:
        filtros.append(PrecoLoja.desconto_pct >= desconto_min)
    if preco_max:
        filtros.append(PrecoLoja.preco_centavos <= preco_max)
    por_jogo = func.row_number().over(partition_by=PrecoLoja.game_id,
                                      order_by=(PrecoLoja.preco_centavos, ORDEM_DAS_LOJAS))
    sub = (db.session.query(PrecoLoja.id, por_jogo.label("n"))
           .join(Status, (Status.game_id == PrecoLoja.game_id) & (Status.user_id == user.id)
                 & (Status.value == "planejado"))
           .filter(*filtros).subquery())
    return (db.session.query(PrecoLoja, Game, JogoFonte.url_loja)
            .join(sub, (sub.c.id == PrecoLoja.id) & (sub.c.n == 1))
            .join(Game, Game.id == PrecoLoja.game_id).filter(Game.removed_at.is_(None))
            .outerjoin(JogoFonte, (JogoFonte.game_id == PrecoLoja.game_id) & (JogoFonte.fonte == PrecoLoja.fonte))
            .order_by(PrecoLoja.desconto_pct.desc(), PrecoLoja.preco_centavos, Game.title))


def menores_precos(game_ids):
    """Menor preço já visto de cada jogo numa consulta só, com o desempate de `precos.menor_preco` (PRC-09)."""
    ordem = func.row_number().over(partition_by=PrecoLoja.game_id,
                                   order_by=(PrecoLoja.preco_centavos, PrecoLoja.coletado_em, ORDEM_DAS_LOJAS))
    sub = db.session.query(PrecoLoja.id, ordem.label("n")).filter(PrecoLoja.game_id.in_(game_ids)).subquery()
    return {p.game_id: p for p in PrecoLoja.query.join(sub, (sub.c.id == PrecoLoja.id) & (sub.c.n == 1))}


def mais_baratos(game_ids):
    linhas = PrecoLoja.query.filter(PrecoLoja.id.in_(ultimas_linhas(PrecoLoja.game_id.in_(game_ids)))).all()
    return {gid: mais_barato([p for p in linhas if p.game_id == gid]) for gid in game_ids}


def so_adultos(acao):
    me = current_user()
    if not pode(me, acao):
        return render_template("erro.html", code=403, msg=SO_ADULTOS), 403
    return None


@bp.route("/ofertas")
@login_required
def lista():
    if negado := so_adultos("ver_ofertas"):
        return negado
    filtros = ler_filtros(request.args)
    page = paginate(consulta(current_user(), **filtros), request.args.get("page"))
    menores = menores_precos([g.id for _, g, _ in page.items])
    return render_template("ofertas/lista.html", page=page, filtros=filtros, menores=menores, lojas=LOJAS_COM_PRECO,
                           nomes_lojas=NOMES_DAS_LOJAS, url_segura=url_segura)


@bp.route("/ofertas/alertas")
@login_required
def alertas():
    if negado := so_adultos("ver_ofertas"):
        return negado
    me = current_user()
    itens = (AlertaPreco.query.filter_by(user_id=me.id).join(Game, Game.id == AlertaPreco.game_id)
             .order_by(Game.title).all())
    return render_template("ofertas/alertas.html", itens=itens, baratos=mais_baratos([a.game_id for a in itens]),
                           nomes_lojas=NOMES_DAS_LOJAS, maximo=MAX_ALERTAS,
                           consentiu=consentimentos.tem_consentimento(me, "ofertas"))


@bp.route("/jogos/<slug>/alerta", methods=["POST"])
@login_required
def criar_alerta(slug):
    """Cria ou edita o alerta do jogo; editar zera o disparo (OFR-04). Sem consentimento, o alerta fica salvo e só
    avisa depois da permissão (OFR-07)."""
    if negado := so_adultos("alerta_preco"):
        return negado
    me = current_user()
    game = visible_games().filter_by(slug=slug).first_or_404()
    alvo = centavos(request.form.get("preco_alvo"))
    teto = max((p.preco_centavos for p in atuais(game.id).values() if atual(p)), default=None)
    alerta = AlertaPreco.query.filter_by(user_id=me.id, game_id=game.id).first()
    if alvo is None or (teto is not None and alvo >= teto):
        flash("Informe um preço-alvo maior que zero" + (f" e menor que {reais(teto)}." if teto else "."), "erro")
        return redirect(f"/jogos/{game.slug}")
    if alerta is None and AlertaPreco.query.filter_by(user_id=me.id).count() >= MAX_ALERTAS:
        flash(f"Você já tem {MAX_ALERTAS} alertas de preço. Apague um em Meus alertas para criar outro.", "erro")
        return redirect(f"/jogos/{game.slug}")
    alerta = alerta or AlertaPreco(user_id=me.id, game_id=game.id)
    alerta.preco_alvo_centavos, alerta.disparado_em = alvo, None
    db.session.add(alerta)
    if consentimentos.marcado_no_form(request.form, "ofertas") and not consentimentos.tem_consentimento(me, "ofertas"):
        consentimentos.conceder(me, "ofertas", "contexto")
    db.session.commit()
    flash(f"Alerta salvo: avisamos quando {game.title} chegar a {reais(alvo)}.", "ok")
    return redirect(f"/jogos/{game.slug}")


@bp.route("/ofertas/alertas/<int:aid>/apagar", methods=["POST"])
@login_required
def apagar_alerta(aid):
    alerta = db.session.get(AlertaPreco, aid)
    if alerta is None or alerta.user_id != current_user().id:
        abort(404)
    db.session.delete(alerta)
    db.session.commit()
    flash("Alerta apagado.", "ok")
    return redirect("/ofertas/alertas")


# ---------- avisos depois da coleta (OFR-05, OFR-06, OFR-11) ----------
def _com_consentimento(q, coluna_user):
    """Junta a linha mais recente do consentimento `ofertas` de cada pessoa, sem consulta por pessoa."""
    recentes = (db.session.query(func.max(Consentimento.id)).filter(Consentimento.finalidade == "ofertas")
                .group_by(Consentimento.user_id))
    return q.outerjoin(Consentimento, (Consentimento.user_id == coluna_user) & Consentimento.id.in_(recentes))


def promocao_nova(linhas, desde):
    """A última linha da loja, se foi gravada nesta coleta e tem desconto maior que o maior da promoção corrente."""
    ultima = linhas[-1]
    if ultima.coletado_em < desde or ultima.desconto_pct == 0:
        return None
    avisado = 0
    for anterior in reversed(linhas[:-1]):
        if anterior.desconto_pct == 0:
            break
        avisado = max(avisado, anterior.desconto_pct)
    return ultima if ultima.desconto_pct > avisado else None


def dados_do_aviso(game, preco, urls):
    loja = NOMES_DAS_LOJAS[preco.fonte]
    return {"jogo": game.title, "loja": loja, "preco": reais(preco.preco_centavos), "desconto": preco.desconto_pct,
            "url_loja": url_segura(urls.get(preco.fonte))}


def avaliar_jogo(game_id, desde):
    game = db.session.get(Game, game_id)
    linhas = PrecoLoja.query.filter_by(game_id=game_id).order_by(PrecoLoja.coletado_em, PrecoLoja.id).all()
    if game is None or game.removed_at is not None or not linhas:
        return
    urls = dict(db.session.query(JogoFonte.fonte, JogoFonte.url_loja).filter(JogoFonte.game_id == game_id))
    por_loja = {}
    for p in linhas:
        por_loja.setdefault(p.fonte, []).append(p)
    novas = [p for p in (promocao_nova(ls, desde) for ls in por_loja.values()) if p]
    alvo_url = f"/jogos/{game.slug}"
    if novas:
        q = (db.session.query(User, Consentimento).join(Status, Status.user_id == User.id)
             .filter(Status.game_id == game_id, Status.value == "planejado"))
        for user, linha in _com_consentimento(q, User.id).order_by(User.id):
            if not consentimentos.vale(linha, user):
                continue
            for p in novas:
                aviso = dados_do_aviso(game, p, urls)
                notificar(user.id, "oferta", game_id=game_id, alvo_url=alvo_url, email=aviso,
                          texto=f"{game.title} com {p.desconto_pct}% de desconto na {aviso['loja']}: {aviso['preco']}")
    barato = mais_barato([ls[-1] for ls in por_loja.values()])
    if barato is None:
        return
    q = (db.session.query(AlertaPreco, User, Consentimento).join(User, User.id == AlertaPreco.user_id)
         .filter(AlertaPreco.game_id == game_id, AlertaPreco.disparado_em.is_(None),
                 AlertaPreco.preco_alvo_centavos >= barato.preco_centavos))
    for alerta, user, linha in _com_consentimento(q, User.id).order_by(AlertaPreco.id):
        if not consentimentos.vale(linha, user):
            continue
        aviso = dados_do_aviso(game, barato, urls)
        notificar(user.id, "preco_alvo", game_id=game_id, alvo_url=alvo_url, email=aviso,
                  texto=f"{game.title} chegou a {aviso['preco']} na {aviso['loja']} "
                        f"(seu alvo: {reais(alerta.preco_alvo_centavos)})")
        alerta.disparado_em = agora_utc()


def avaliar(game_ids, desde):
    """Avisos dos jogos com preço novo desde `desde`; quem chama faz o commit (OFR-05, OFR-06)."""
    for game_id in sorted(game_ids):
        avaliar_jogo(game_id, desde)


def do_resumo(user):
    """Até 5 ofertas da wishlist para o resumo semanal, só com consentimento e para adultos (OFR-07)."""
    if not consentimentos.tem_consentimento(user, "ofertas") or not pode(user, "ver_ofertas"):
        return []
    return [(f"{g.title}: {p.desconto_pct}% de desconto na {NOMES_DAS_LOJAS[p.fonte]}, {reais(p.preco_centavos)}",
             f"/jogos/{g.slug}") for p, g, _ in consulta(user).limit(ITENS_NO_RESUMO)]
