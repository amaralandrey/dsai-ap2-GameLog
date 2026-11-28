from flask import Blueprint, abort, flash, redirect, render_template, request
from sqlalchemy import func, null
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from .bloqueios import NAO_SEGUE, bloqueado_entre, com_bloqueio, sql_com_bloqueio, sql_silenciados
from .menores.idade import sql_menor
from .menores.politica import e_menor, pode
from .models import Activity, Comment, Follow, Game, GameList, Review, User, db, now
from .diario import ocultos_para, perfil_ou_404
from .gamificacao import emitir_evento
from .notificacoes import avisar_seguidor, notificar
from .util import current_user, login_required, paginate

bp = Blueprint("social", __name__)


def get_user(username):
    return User.query.filter_by(username_lower=username.lower()).first_or_404()


@bp.route("/u/<username>/seguir", methods=["POST"])
@login_required
def follow(username):
    target, me = get_user(username), current_user()
    if target.id == me.id:  # SOC-01
        flash("Você não pode seguir a si mesmo.", "erro")
    elif bloqueado_entre(me, target) or not pode(me, "seguir", target):  # BLQ-02: a mesma mensagem nos dois sentidos
        flash(NAO_SEGUE, "erro")
    elif not Follow.query.filter_by(follower_id=me.id, followed_id=target.id).first():  # SOC-02
        pedido = e_menor(target)  # PMN-03: seguir um menor é um pedido, que nasce pendente
        db.session.add(Follow(follower_id=me.id, followed_id=target.id, aprovado_em=null() if pedido else now()))
        try:
            # NTF-05; o pedido a quem tem de 12 a 15 anos vai ao responsável (extensão de RSP-06)
            if not pedido or pode(target, "aprovar_seguidor"):
                avisar_seguidor(target.id, me, pedido)
            else:
                avisar_responsaveis(target, me)
            db.session.commit()
        except IntegrityError:  # duplo envio: a outra requisição já gravou o mesmo par
            db.session.rollback()
        else:
            if pedido:
                flash("Pedido enviado. Você passa a seguir quando ele for aprovado.", "ok")
            else:
                eventos_de_seguir(me.id, target.id)
    return redirect(f"/u/{target.username}")


def avisar_responsaveis(menor, seguidor):
    from .menores.responsavel import responsaveis_ativos

    for rid in responsaveis_ativos(menor):
        notificar(rid, "responsavel", ator_id=seguidor.id, alvo_url=f"/responsavel/{menor.id}",
                  texto=f"@{seguidor.username} pediu para seguir {menor.display_name}.")


def eventos_de_seguir(seguidor_id, seguido_id):
    emitir_evento(seguidor_id, "seguir")  # GAM-03
    emitir_evento(seguido_id, "seguidor")


@bp.route("/u/<username>/deixar-de-seguir", methods=["POST"])
@login_required
def unfollow(username):
    target = get_user(username)
    Follow.query.filter_by(follower_id=current_user().id, followed_id=target.id).delete()
    db.session.commit()
    return redirect(f"/u/{target.username}")


def people(username, kind):
    u = perfil_ou_404(username)  # DIA-12
    col, other = (Follow.followed_id, Follow.follower_id) if kind == "seguidores" else (Follow.follower_id, Follow.followed_id)
    q = User.query.join(Follow, other == User.id).filter(col == u.id, Follow.aprovado).order_by(User.username_lower)
    return render_template("social/pessoas.html", u=u, kind=kind, page=paginate(q, request.args.get("page")))


@bp.route("/u/<username>/seguidores")
def followers(username):
    return people(username, "seguidores")


@bp.route("/u/<username>/seguindo")
def following(username):
    return people(username, "seguindo")


def feed_query(user_id, visitante=None):
    """SOC-04..08: só de quem sigo; esconde lista privada, avaliação oculta, jogo removido, perfil privado (DIA-13),
    bloqueados e silenciados (BLQ-04, BLQ-07), tudo na mesma consulta (BLQ-09)."""
    return (
        Activity.query.join(Follow, (Follow.followed_id == Activity.user_id) & (Follow.follower_id == user_id)
                            & Follow.aprovado)  # PMN-03: pedido pendente não leva ao feed
        .outerjoin(Review, Review.id == Activity.review_id)
        .outerjoin(Game, Game.id == Activity.game_id)
        .outerjoin(GameList, GameList.id == Activity.list_id)
        .filter(
            (Activity.review_id.is_(None)) | (Review.hidden.is_(False)),
            (Activity.game_id.is_(None)) | (Game.removed_at.is_(None)),
            (Activity.list_id.is_(None)) | (GameList.public.is_(True)),
            Activity.user_id.not_in(ocultos_para(visitante)),
            Activity.user_id.not_in(sql_silenciados(user_id)),  # BLQ-07
        )
        .order_by(Activity.created_at.desc(), Activity.id.desc())
    )


def suggestions(me, limit=5):
    """SOC-09: usuários mais ativos que ainda não sigo."""
    followed = db.session.query(Follow.followed_id).filter(Follow.follower_id == me.id)
    return (
        User.query.outerjoin(Activity, Activity.user_id == User.id)
        .filter(User.id != me.id, User.id.not_in(followed), User.id.not_in(sql_com_bloqueio(me.id)),
                ~sql_menor())
        .group_by(User.id)
        .order_by(func.count(Activity.id).desc(), User.username_lower)
        .limit(limit)
        .all()
    )


@bp.route("/feed")
@login_required
def feed():
    me = current_user()
    q = feed_query(me.id, me).options(
        selectinload(Activity.review).selectinload(Review.comments).joinedload(Comment.user),
        selectinload(Activity.user), selectinload(Activity.game), selectinload(Activity.list))
    page = paginate(q, request.args.get("page"))
    return render_template("social/feed.html", page=page, suggestions=suggestions(me) if not page.items else [],
                           com_bloqueio=com_bloqueio(me.id))


# ---------- pedidos para seguir um menor (PMN-03) ----------
@bp.route("/conta/pedidos")
@login_required
def pedidos():
    """Quem pediu para seguir; de 12 a 15 anos a lista só informa, porque quem aprova é o responsável (RSP-06)."""
    me = current_user()
    q = (Follow.query.filter(Follow.followed_id == me.id, ~Follow.aprovado).join(User, User.id == Follow.follower_id)
         .order_by(Follow.id.desc()))
    return render_template("social/pedidos.html", page=paginate(q, request.args.get("page")),
                           aprova=pode(me, "aprovar_seguidor"))


def pedido_ou_404(fid):
    pedido = db.session.get(Follow, fid)
    if pedido is None or pedido.followed_id != current_user().id or pedido.aprovado:
        abort(404)
    return pedido


@bp.route("/conta/pedidos/<int:fid>/aprovar", methods=["POST"])
@login_required
def aprovar(fid):
    pedido = pedido_ou_404(fid)
    if not pode(current_user(), "aprovar_seguidor"):
        abort(403)  # de 12 a 15 anos, só o responsável aprova (extensão de RSP-06)
    pedido.aprovado_em = now()
    db.session.commit()
    eventos_de_seguir(pedido.follower_id, pedido.followed_id)
    flash(f"@{pedido.follower.username} agora segue você.", "ok")
    return redirect("/conta/pedidos")


@bp.route("/conta/pedidos/<int:fid>/recusar", methods=["POST"])
@login_required
def recusar(fid):
    """Recusar protege, então vale em qualquer faixa."""
    pedido = pedido_ou_404(fid)
    db.session.delete(pedido)
    db.session.commit()
    flash("Pedido recusado.", "ok")
    return redirect("/conta/pedidos")
