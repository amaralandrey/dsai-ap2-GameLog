from flask import Blueprint, abort, flash, redirect, request
from sqlalchemy.exc import IntegrityError

from .bloqueios import bloqueado_entre
from .models import Activity, Like, Notificacao, Report, Review, Status, db, visible_games
from .gamificacao import emitir_evento
from .notificacoes import avisar_curtida
from .tempo import agora_utc
from .util import current_user, login_required

bp = Blueprint("avaliacoes", __name__)


def back(game, review=None):
    return redirect(f"/jogos/{game.slug}" + (f"#av-{review.id}" if review else ""))


def own_or_403(review):
    if review.user_id != current_user().id:
        abort(403)


def delete_review(review):
    """Remove a avaliação e tudo que depende dela (AVA-04); a preservada (DEN-09) só fica oculta, e as denúncias ficam
    com a trilha, sem a avaliação."""
    from .denuncias import preservado

    if preservado(review):
        review.hidden = True
        return
    Like.query.filter_by(review_id=review.id).delete()
    Report.query.filter_by(review_id=review.id).update({Report.review_id: None})
    Activity.query.filter_by(review_id=review.id).delete()
    Notificacao.query.filter_by(review_id=review.id).delete()
    db.session.delete(review)


def salvar_avaliacao(me, game, score, text, spoiler):
    review = Review.query.filter_by(user_id=me.id, game_id=game.id).first()
    nova = review is None
    if review:  # AVA-02: nova submissão edita a existente
        review.score, review.text, review.spoiler, review.edited = score, text, spoiler, True
    else:
        review = Review(user_id=me.id, game_id=game.id, score=score, text=text, spoiler=spoiler)
        db.session.add(review)
        db.session.flush()
        db.session.add(Activity(user_id=me.id, kind="avaliou", game_id=game.id, review_id=review.id))
    if not Status.query.filter_by(user_id=me.id, game_id=game.id).first():  # AVA-11
        db.session.add(Status(user_id=me.id, game_id=game.id, value="jogado"))
    db.session.commit()
    return review, nova


def ler_nota(valor):
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        nota = valor
    elif isinstance(valor, str) and valor.strip().isascii() and valor.strip().isdigit():
        nota = int(valor)
    else:
        return None
    return nota if 0 <= nota <= 10 else None


def avaliar(user, game, nota, texto, spoiler):
    erros = {}
    score = ler_nota(nota)
    if score is None:
        erros["nota"] = "A nota é obrigatória e deve ser um inteiro de 0 a 10."  # AVA-01
    if not isinstance(texto, str):
        erros["texto"] = "A resenha deve ser um texto."
    elif len(texto.strip()) > 5000:
        erros["texto"] = "A resenha deve ter até 5000 caracteres."  # AVA-03
    if erros:
        return None, False, erros
    texto = texto.strip()
    try:
        review, nova = salvar_avaliacao(user, game, score, texto, spoiler)
    except IntegrityError:  # duplo envio: a outra requisição criou a avaliação, então esta vira edição
        db.session.rollback()
        review, nova = salvar_avaliacao(user, game, score, texto, spoiler)
    emitir_evento(user.id, "avaliacao", review_id=review.id, game_id=game.id, nova=nova)  # GAM-03
    emitir_evento(user.id, "status", game_id=game.id)
    return review, nova, {}


@bp.route("/jogos/<slug>/avaliar", methods=["POST"])
@login_required
def rate(slug):
    game = visible_games().filter_by(slug=slug).first_or_404()
    f = request.form
    review, _, erros = avaliar(current_user(), game, f.get("score", ""), f.get("text", ""), bool(f.get("spoiler")))
    if erros:
        flash(next(iter(erros.values())), "erro")
        return back(game)
    flash("Avaliação salva.", "ok")
    return back(game, review)


@bp.route("/avaliacoes/<int:rid>/apagar", methods=["POST"])
@login_required
def delete(rid):
    review = db.get_or_404(Review, rid)
    own_or_403(review)
    game, autor = review.game, review.user_id
    delete_review(review)
    db.session.commit()
    emitir_evento(autor, "avaliacao_apagada", review_id=rid, game_id=game.id)
    flash("Avaliação apagada.", "ok")
    return back(game)


@bp.route("/avaliacoes/<int:rid>/curtir", methods=["POST"])
@login_required
def like(rid):
    review = db.get_or_404(Review, rid)
    if review.game.removed_at:  # CAT-07
        abort(404)
    me = current_user()
    if review.user_id == me.id:
        flash("Você não pode curtir a própria resenha.", "erro")  # AVA-06
    elif review.hidden or bloqueado_entre(me, review.user_id):  # BLQ-05
        abort(404)
    elif not Like.query.filter_by(user_id=me.id, review_id=rid).first():
        db.session.add(Like(user_id=me.id, review_id=rid, created_at=agora_utc()))  # RNK-02
        try:
            avisar_curtida(review, me.id)  # NTF-04
            db.session.commit()
        except IntegrityError:  # duplo envio: a outra requisição já curtiu
            db.session.rollback()
        else:
            emitir_evento(me.id, "curtir", review_id=rid)
            emitir_evento(review.user_id, "curtida", review_id=rid)
    return back(review.game, review)


@bp.route("/avaliacoes/<int:rid>/descurtir", methods=["POST"])
@login_required
def unlike(rid):
    review = db.get_or_404(Review, rid)
    Like.query.filter_by(user_id=current_user().id, review_id=rid).delete()
    db.session.commit()
    return back(review.game, review)


@bp.route("/avaliacoes/<int:rid>/denunciar", methods=["POST"])
@login_required
def report(rid):
    from .denuncias import denunciar

    review = db.get_or_404(Review, rid)
    erro = denunciar(current_user(), "avaliacao", review, request.form)
    flash(erro or "Denúncia enviada. Obrigado!", "erro" if erro else "ok")
    return back(review.game, review)
