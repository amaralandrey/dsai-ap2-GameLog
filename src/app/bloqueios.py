"""Bloquear e silenciar (BLQ-01..10).

`bloqueado_entre` é a regra única do bloqueio (GLOBAL-13); `sql_com_bloqueio` é a mesma regra em SQL, que
`diario.ocultos_para` junta à do perfil privado para as listagens filtrarem no banco.
"""
from flask import Blueprint, abort, flash, g, has_request_context, redirect, render_template, request
from sqlalchemy import case, or_, select

from .models import Bloqueio, Follow, Silencio, User, db
from .notificacoes import registrar_filtro
from .util import current_user, login_required, paginate

bp = Blueprint("bloqueios", __name__)
NAO_SEGUE = "Não foi possível seguir este usuário."  # BLQ-02: a mesma para quem bloqueou e quem foi bloqueado


def _id(user):
    return user if isinstance(user, int) else user.id


def sql_com_bloqueio(user_id):
    """Ids com bloqueio, em qualquer sentido, com `user_id`, como subconsulta.

    Um SELECT só, sem UNION: o SQLite não aceita UNION aninhado, e `ocultos_para` une este ao do perfil privado.
    """
    outro = case((Bloqueio.bloqueador_id == user_id, Bloqueio.bloqueado_id), else_=Bloqueio.bloqueador_id)
    return select(outro).where(or_(Bloqueio.bloqueador_id == user_id, Bloqueio.bloqueado_id == user_id))


def com_bloqueio(user_id):
    """Os mesmos ids de `sql_com_bloqueio` num conjunto; uma consulta por usuário e requisição."""
    cache = g.setdefault("_com_bloqueio", {}) if has_request_context() else {}
    if user_id not in cache:
        cache[user_id] = set(db.session.scalars(sql_com_bloqueio(user_id)))
    return cache[user_id]


def _esquecer():
    if has_request_context():
        g.pop("_com_bloqueio", None)


def bloqueado_entre(a, b):
    """Verdadeiro se qualquer um dos dois bloqueou o outro (BLQ-01)."""
    if a is None or b is None:
        return False
    return _id(b) in com_bloqueio(_id(a))


def sql_silenciados(user_id):
    return select(Silencio.silenciado_id).where(Silencio.user_id == user_id)


def bloquear(bloqueador, bloqueado, criado_por=None):
    """Grava o bloqueio e desfaz os follows nos dois sentidos (BLQ-01, BLQ-02); quem chama faz o commit."""
    if bloqueador.id == bloqueado.id:
        raise ValueError("ninguém bloqueia a si mesmo")
    if not Bloqueio.query.filter_by(bloqueador_id=bloqueador.id, bloqueado_id=bloqueado.id).first():
        db.session.add(Bloqueio(bloqueador_id=bloqueador.id, bloqueado_id=bloqueado.id,
                                criado_por=criado_por.id if criado_por else None))
    Follow.query.filter(or_((Follow.follower_id == bloqueador.id) & (Follow.followed_id == bloqueado.id),
                            (Follow.follower_id == bloqueado.id) & (Follow.followed_id == bloqueador.id))
                        ).delete(synchronize_session=False)
    _esquecer()


@registrar_filtro
def _sem_bloqueio_nem_silencio(user_id, tipo, ctx):
    """Descarta notificação entre quem tem bloqueio e a de quem o destinatário silenciou (BLQ-05, BLQ-07)."""
    ator_id = ctx.get("ator_id")
    if ator_id is None:
        return True
    if bloqueado_entre(user_id, ator_id):
        return False
    return Silencio.query.filter_by(user_id=user_id, silenciado_id=ator_id).first() is None


# ---------- rotas ----------
def _alvo(username):
    alvo, me = User.query.filter_by(username_lower=username.lower()).first_or_404(), current_user()
    if bloqueado_entre(me, alvo):
        abort(404)  # BLQ-03: quem tem bloqueio não enxerga o outro, nem para silenciar
    return alvo, me


@bp.route("/u/<username>/bloquear", methods=["POST"])
@login_required
def bloquear_rota(username):
    alvo, me = User.query.filter_by(username_lower=username.lower()).first_or_404(), current_user()
    if alvo.id == me.id:
        flash("Você não pode bloquear a si mesmo.", "erro")
        return redirect(f"/u/{me.username}")
    bloquear(me, alvo)
    db.session.commit()
    flash(f"@{alvo.username} foi bloqueado.", "ok")
    return redirect("/conta/bloqueios")


@bp.route("/u/<username>/desbloquear", methods=["POST"])
@login_required
def desbloquear(username):
    alvo, me = User.query.filter_by(username_lower=username.lower()).first_or_404(), current_user()
    b = Bloqueio.query.filter_by(bloqueador_id=me.id, bloqueado_id=alvo.id).first_or_404()
    if b.criado_por is not None and b.criado_por != me.id:
        abort(403)  # BLQ-08: só o responsável desfaz o que ele fez (ECA Digital art. 18, §2º)
    db.session.delete(b)
    db.session.commit()
    _esquecer()
    flash(f"@{alvo.username} foi desbloqueado.", "ok")
    return redirect("/conta/bloqueios")


@bp.route("/u/<username>/silenciar", methods=["POST"])
@login_required
def silenciar(username):
    alvo, me = _alvo(username)
    if alvo.id != me.id and not Silencio.query.filter_by(user_id=me.id, silenciado_id=alvo.id).first():
        db.session.add(Silencio(user_id=me.id, silenciado_id=alvo.id))
        db.session.commit()
        flash(f"@{alvo.username} foi silenciado.", "ok")
    return redirect(f"/u/{alvo.username}")


@bp.route("/u/<username>/dessilenciar", methods=["POST"])
@login_required
def dessilenciar(username):
    alvo, me = _alvo(username)
    Silencio.query.filter_by(user_id=me.id, silenciado_id=alvo.id).delete()
    db.session.commit()
    if request.form.get("next") == "conta":
        return redirect("/conta/bloqueios?aba=silenciados")
    return redirect(f"/u/{alvo.username}")


@bp.route("/conta/bloqueios")
@login_required
def lista():
    """Bloqueados e silenciados do dono, em abas paginadas (BLQ-08)."""
    me = current_user()
    aba = "silenciados" if request.args.get("aba") == "silenciados" else "bloqueados"
    if aba == "silenciados":
        q = Silencio.query.filter_by(user_id=me.id).join(User, User.id == Silencio.silenciado_id)
        q = q.order_by(Silencio.created_at.desc(), Silencio.id.desc())
    else:
        q = Bloqueio.query.filter_by(bloqueador_id=me.id).join(User, User.id == Bloqueio.bloqueado_id)
        q = q.order_by(Bloqueio.created_at.desc(), Bloqueio.id.desc())
    return render_template("bloqueios/lista.html", aba=aba, page=paginate(q, request.args.get("page")))
