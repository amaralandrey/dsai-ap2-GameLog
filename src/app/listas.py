import json

from flask import Blueprint, abort, flash, redirect, render_template, request
from sqlalchemy import case

from .models import STATUSES, Activity, Game, GameList, ListaInteligente, ListItem, Status, db, now, visible_games
from .gamificacao import emitir_evento
from .util import current_user, login_required, paginate

bp = Blueprint("listas", __name__)
MAX_LISTS, MAX_ITEMS = 50, 200  # LIS-10


def back(default="/"):
    return redirect(request.referrer or default)


@bp.route("/jogos/<slug>/status", methods=["POST"])
@login_required
def set_status(slug):
    game = visible_games().filter_by(slug=slug).first_or_404()
    me, value = current_user(), request.form.get("status", "")
    cur = Status.query.filter_by(user_id=me.id, game_id=game.id).first()
    if value == "":  # LIS-02: remover status é permitido
        if cur:
            db.session.delete(cur)
    elif value in STATUSES:
        if cur:  # LIS-01/02: um status por jogo, trocar substitui
            changed, cur.value, cur.updated_at = cur.value != value, value, now()
        else:
            changed = True
            db.session.add(Status(user_id=me.id, game_id=game.id, value=value))
        if value == "jogado" and changed:
            ja = Activity.query.filter_by(user_id=me.id, kind="jogado", game_id=game.id).first()
            if ja is None:  # SOC-05/DIA-04: uma vez só
                db.session.add(Activity(user_id=me.id, kind="jogado", game_id=game.id))
    else:
        flash("Status inválido.", "erro")
        return back(f"/jogos/{slug}")
    db.session.commit()
    emitir_evento(me.id, "status", game_id=game.id)  # GAM-03
    return back(f"/jogos/{slug}")


@bp.route("/meus-jogos")
@login_required
def my_games():
    me, sel = current_user(), request.args.get("status")
    order = case(*[(Status.value == k, i) for i, k in enumerate(STATUSES)], else_=9)
    q = Status.query.filter_by(user_id=me.id).join(Game)
    if sel in STATUSES:
        q = q.filter(Status.value == sel)
    page = paginate(q.order_by(order, Status.updated_at.desc()), request.args.get("page"))
    counts = {k: Status.query.filter_by(user_id=me.id, value=k).count() for k in STATUSES}
    return render_template("listas/meus_jogos.html", page=page, sel=sel, statuses=STATUSES, counts=counts)


@bp.route("/listas", methods=["GET", "POST"])
@login_required
def my_lists():
    me, errors = current_user(), {}
    if request.method == "POST":
        name, desc = request.form.get("name", "").strip(), request.form.get("description", "").strip()
        if not 1 <= len(name) <= 80:
            errors["name"] = "O nome é obrigatório e deve ter até 80 caracteres."
        elif GameList.query.filter_by(user_id=me.id).count() >= MAX_LISTS:
            errors["name"] = f"Limite de {MAX_LISTS} listas atingido."
        if not errors:
            gl = GameList(user_id=me.id, name=name, description=desc[:500], public=request.form.get("visibility") != "privada")
            db.session.add(gl)
            db.session.flush()
            if gl.public:
                db.session.add(Activity(user_id=me.id, kind="lista", list_id=gl.id))
            db.session.commit()
            emitir_evento(me.id, "lista", list_id=gl.id)  # GAM-03
            return redirect(f"/listas/{gl.id}")
    from .listas_inteligentes import descricao

    lists = GameList.query.filter_by(user_id=me.id).order_by(GameList.created_at.desc()).all()
    inteligentes = [(li, descricao(json.loads(li.regra)))  # LSA-06
                    for li in ListaInteligente.query.filter_by(user_id=me.id).order_by(ListaInteligente.created_at.desc())]
    return render_template("listas/minhas.html", lists=lists, inteligentes=inteligentes, errors=errors,
                           form=request.form), (400 if errors else 200)


def get_list(lid, owner_only=False):
    """LIS-08: privada responde 404 a quem não é dono. Escrita de não-dono em pública: 403."""
    from .diario import pode_ver_perfil

    gl, me = db.get_or_404(GameList, lid), current_user()
    own = me is not None and me.id == gl.user_id
    if not own and (not gl.public or not pode_ver_perfil(me, gl.user)):  # DIA-12
        abort(404)
    if owner_only and not own:
        abort(403 if me else 404)
    return gl, own


@bp.route("/listas/<int:lid>")
def view_list(lid):
    from .listas_inteligentes import progresso_comum

    gl, own = get_list(lid)
    items = [i for i in gl.items if own or i.game.removed_at is None]  # LIS-09
    return render_template("listas/lista.html", gl=gl, own=own, items=items, prog=progresso_comum(gl, own),
                           statuses=STATUSES)


@bp.route("/listas/<int:lid>/editar", methods=["POST"])
@login_required
def edit_list(lid):
    gl, _ = get_list(lid, owner_only=True)
    name = request.form.get("name", "").strip()
    if not 1 <= len(name) <= 80:
        flash("O nome é obrigatório e deve ter até 80 caracteres.", "erro")
    else:
        gl.name, gl.description = name, request.form.get("description", "").strip()[:500]
        gl.public = request.form.get("visibility") != "privada"
        db.session.commit()
        flash("Lista atualizada.", "ok")
    return redirect(f"/listas/{lid}")


@bp.route("/listas/<int:lid>/apagar", methods=["POST"])
@login_required
def delete_list(lid):
    gl, _ = get_list(lid, owner_only=True)
    Activity.query.filter_by(list_id=lid).delete()  # LIS-11: status e avaliações ficam intactos
    db.session.delete(gl)
    db.session.commit()
    return redirect("/listas")


def renumber(gl):
    for pos, it in enumerate(sorted(gl.items, key=lambda i: i.position)):
        it.position = pos


@bp.route("/jogos/<slug>/adicionar-a-lista", methods=["POST"])
@login_required
def add_to_list(slug):
    game = visible_games().filter_by(slug=slug).first_or_404()
    try:
        lid = int(request.form.get("list_id", ""))
    except ValueError:
        abort(400)
    gl, _ = get_list(lid, owner_only=True)
    if any(i.game_id == game.id for i in gl.items):  # LIS-06
        flash("Este jogo já está na lista.", "erro")
    elif len(gl.items) >= MAX_ITEMS:
        flash(f"Limite de {MAX_ITEMS} jogos por lista atingido.", "erro")
    else:
        db.session.add(ListItem(list_id=gl.id, game_id=game.id, position=len(gl.items)))
        if gl.public:
            db.session.add(Activity(user_id=gl.user_id, kind="lista_jogo", game_id=game.id, list_id=gl.id))
        db.session.commit()
        emitir_evento(gl.user_id, "lista", list_id=gl.id)
        flash(f"Adicionado a “{gl.name}”.", "ok")
    return back(f"/jogos/{slug}")


@bp.route("/listas/<int:lid>/jogos/<int:gid>/remover", methods=["POST"])
@login_required
def remove_item(lid, gid):
    gl, _ = get_list(lid, owner_only=True)
    ListItem.query.filter_by(list_id=lid, game_id=gid).delete()
    Activity.query.filter_by(kind="lista_jogo", list_id=lid, game_id=gid).delete()  # SOC-05
    db.session.flush()
    db.session.refresh(gl)
    renumber(gl)
    db.session.commit()
    return redirect(f"/listas/{lid}")


@bp.route("/listas/<int:lid>/jogos/<int:gid>/mover", methods=["POST"])
@login_required
def move_item(lid, gid):
    gl, _ = get_list(lid, owner_only=True)  # LIS-07: ordem persistida
    items = sorted(gl.items, key=lambda i: i.position)
    idx = next((n for n, i in enumerate(items) if i.game_id == gid), None)
    if idx is None:
        abort(404)
    to = idx - 1 if request.form.get("dir") == "up" else idx + 1
    if 0 <= to < len(items):
        items[idx], items[to] = items[to], items[idx]
        for pos, it in enumerate(items):
            it.position = pos
        db.session.commit()
    return redirect(f"/listas/{lid}")
