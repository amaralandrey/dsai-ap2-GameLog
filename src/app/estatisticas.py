from flask import Blueprint, render_template
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from .models import Game, Review, User, avg_subquery, db, visible_games

bp = Blueprint("estatisticas", __name__)

MIN_AVALIACOES_MOMENTO = 3  # EST-04: evita um 10 isolado virar "Jogo do Momento"


def contadores():
    """EST-02/06: os três totais numa única consulta (subconsultas escalares)."""
    reviews = (
        select(func.count(Review.id))
        .join(Game, Game.id == Review.game_id)
        .where(Review.hidden.is_(False), Game.removed_at.is_(None))
        .scalar_subquery()
    )
    jogos = select(func.count(Game.id)).where(Game.removed_at.is_(None)).scalar_subquery()
    usuarios = select(func.count(User.id)).scalar_subquery()
    row = db.session.execute(select(jogos, usuarios, reviews)).one()
    return {"jogos": row[0], "usuarios": row[1], "avaliacoes": row[2]}


def ranking():
    """Jogos visíveis com (avg, n) agregados uma vez só (EST-06)."""
    sub = avg_subquery()
    q = visible_games().join(sub, sub.c.gid == Game.id).options(joinedload(Game.developer))
    return q.add_columns(sub.c.avg, sub.c.n), sub


@bp.route("/estatisticas")
@bp.route("/stats")
def page():
    q, sub = ranking()
    mais_avaliados = q.order_by(sub.c.n.desc(), sub.c.avg.desc(), Game.title).limit(5).all()  # EST-03
    momento = (  # EST-04
        q.filter(sub.c.n >= MIN_AVALIACOES_MOMENTO).order_by(sub.c.avg.desc(), sub.c.n.desc(), Game.title).first()
    )
    return render_template(
        "estatisticas/pagina.html", totais=contadores(), mais_avaliados=mais_avaliados, momento=momento,
        minimo=MIN_AVALIACOES_MOMENTO,
    )
