from flask import Blueprint, abort, redirect, render_template, request
from sqlalchemy import func
from sqlalchemy.orm import joinedload, selectinload

from .bloqueios import com_bloqueio
from .catalogo_merge import principal_do_mesclado
from .diario import minutos_por_usuario, ocultos_para
from .models import (
    NOMES_DAS_LOJAS, REPORT_REASONS, STATUSES, AlertaPreco, Comment, Game, GameList, Like, Review, Status, avg_subquery, db, game_genres, game_stats,
    visible_games,
)
from .ofertas import mais_barato, url_segura
from .precos import atual, atuais, grafico, historico, menor_preco
from .privacidade import consentimentos
from .util import current_user, paginate

bp = Blueprint("jogo", __name__)


def related_games(game, limit=6):
    """PAG-08: compartilham gênero; ordena por gêneros em comum e depois nota média."""
    sub = avg_subquery()
    shared = func.count(game_genres.c.genre_id)
    return (
        visible_games()
        .join(game_genres, game_genres.c.game_id == Game.id)
        .outerjoin(sub, sub.c.gid == Game.id)
        .filter(game_genres.c.genre_id.in_([g.id for g in game.genres]), Game.id != game.id)
        .group_by(Game.id)
        .order_by(shared.desc(), sub.c.avg.is_(None), sub.c.avg.desc(), Game.title)
        .limit(limit)
        .add_columns(sub.c.avg)
        .all()
    )


def redirecionar_mesclado(slug, para):
    """301 para o principal, mantendo a query string, ou 404 (PAG-09, MRG-08); `para` monta a URL nova."""
    principal = principal_do_mesclado(slug)
    if principal is None:
        abort(404)
    qs = request.query_string.decode()
    return redirect(para(principal.slug) + (f"?{qs}" if qs else ""), 301)


@bp.route("/jogos/<slug>")
def game_page(slug):
    game = visible_games().filter_by(slug=slug).first()
    if game is None:
        return redirecionar_mesclado(slug, lambda s: f"/jogos/{s}")
    me = current_user()
    likes = func.count(Like.id)
    q = (
        db.session.query(Review, likes.label("likes"))
        .outerjoin(Like, Like.review_id == Review.id)
        .filter(Review.game_id == game.id, Review.hidden.is_(False), Review.text != "",
                Review.user_id.not_in(ocultos_para(me)))  # DIA-13: a nota segue na média
        .group_by(Review.id)
        .options(selectinload(Review.comments).joinedload(Comment.user), joinedload(Review.user))  # COM-02 sem N+1
    )
    sort = "recentes" if request.args.get("ordem") == "recentes" else "curtidas"
    q = q.order_by(likes.desc(), Review.created_at.desc()) if sort == "curtidas" else q.order_by(Review.created_at.desc())
    page = paginate(q, request.args.get("page"))
    mine = status = alerta = None
    liked, my_lists = set(), []
    if me:
        mine = Review.query.filter_by(user_id=me.id, game_id=game.id).first()
        s = Status.query.filter_by(user_id=me.id, game_id=game.id).first()
        status = s.value if s else None
        da_pagina = [r.id for r, _ in page.items]
        liked = {i for (i,) in db.session.query(Like.review_id).filter(Like.user_id == me.id,
                                                                        Like.review_id.in_(da_pagina))}
        my_lists = GameList.query.filter_by(user_id=me.id).order_by(GameList.name).all()
        alerta = AlertaPreco.query.filter_by(user_id=me.id, game_id=game.id).first()
    stats = game_stats(game.id)
    horas = minutos_por_usuario(game.id, [r.user_id for r, _ in page.items] + ([mine.user_id] if mine else []))
    precos = atuais(game.id)  # PRC-13: duas consultas, com qualquer número de lojas e de linhas
    return render_template(
        "jogo/pagina.html", game=game, stats=stats, page=page, sort=sort, mine=mine, status=status, liked=liked,
        my_lists=my_lists, statuses=STATUSES, reasons=REPORT_REASONS, related=related_games(game),
        hist_max=max(stats["hist"].values()) or 1, nomes_lojas=NOMES_DAS_LOJAS, horas=horas,
        com_bloqueio=com_bloqueio(me.id) if me else set(),
        precos=precos, vigentes={f for f, p in precos.items() if atual(p)}, menor=menor_preco(game.id),
        barato=mais_barato(precos.values()), url_segura=url_segura, alerta=alerta,
        pedido_ofertas=consentimentos.pedido(me, "ofertas") if me else None,
    )


@bp.route("/jogos/<slug>/precos")
def historico_de_precos(slug):
    """Histórico por loja em gráfico e tabela paginada (PRC-10)."""
    game = visible_games().filter_by(slug=slug).first()
    if game is None:
        return redirecionar_mesclado(slug, lambda s: f"/jogos/{s}/precos")
    q = historico(game.id)
    page = paginate(q, request.args.get("page"))
    return render_template("jogo/precos.html", game=game, page=page, nomes_lojas=NOMES_DAS_LOJAS,
                           grafico=grafico(q.all()) if page.total else None)
