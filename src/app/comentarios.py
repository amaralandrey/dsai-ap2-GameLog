from flask import Blueprint, abort, flash, redirect, request

from .bloqueios import bloqueado_entre
from .models import Comment, Review, db
from .gamificacao import emitir_evento
from .notificacoes import avisar_comentario
from .util import current_user, login_required, safe_next

bp = Blueprint("comentarios", __name__)


def back(review, comment=None):
    """Volta à página de origem (jogo ou feed), ancorada na avaliação/comentário."""
    anchor = f"#com-{comment.id}" if comment else f"#av-{review.id}"
    nxt = request.form.get("next")
    return redirect((safe_next(nxt) if nxt else f"/jogos/{review.game.slug}") + anchor)


@bp.route("/avaliacoes/<int:rid>/comentar", methods=["POST"])
@login_required
def add(rid):
    review = db.get_or_404(Review, rid)
    if review.hidden or review.game.removed_at or bloqueado_entre(current_user(), review.user_id):  # COM-05, BLQ-05
        abort(404)
    text = request.form.get("text", "").strip()
    if not text:
        flash("O comentário não pode ficar vazio.", "erro")  # COM-04
        return back(review)
    if len(text) > 500:
        flash("O comentário deve ter até 500 caracteres.", "erro")  # COM-04
        return back(review)
    comment = Comment(review_id=review.id, user_id=current_user().id, text=text)
    db.session.add(comment)
    db.session.flush()
    avisar_comentario(comment)  # NTF-05
    db.session.commit()
    emitir_evento(comment.user_id, "comentario", review_id=review.id)  # GAM-03
    return back(review, comment)


@bp.route("/comentarios/<int:cid>/apagar", methods=["POST"])
@login_required
def delete(cid):
    comment = db.get_or_404(Comment, cid)
    me = current_user()
    if comment.user_id != me.id and not me.is_admin:  # COM-03
        abort(403)
    if comment.user_id != me.id:
        from .admin import audit
        audit("comentario.apagar", f"#{comment.id} de @{comment.user.username}: {comment.text}")
    review = comment.review
    db.session.delete(comment)
    db.session.commit()
    flash("Comentário apagado.", "ok")
    return back(review)
