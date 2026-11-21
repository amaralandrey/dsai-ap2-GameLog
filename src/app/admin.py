import time
from datetime import date
from urllib.parse import urlencode

from flask import Blueprint, abort, flash, redirect, render_template, request

from sqlalchemy import func
from sqlalchemy.orm import joinedload, selectinload

from .catalogo_merge import MesclagemRecusada, ignorar, mesclar
from .fontes import FONTES, importar_item, indisponivel, jogo_por_fonte
from .models import AuditLog, Developer, Game, Genre, JogoFonte, PendenciaMerge, Platform, Report, Review, db, now
from .util import admin_required, current_user, paginate, slugify

bp = Blueprint("admin", __name__, url_prefix="/admin")
TAXONOMIES = {"generos": (Genre, "Gêneros"), "plataformas": (Platform, "Plataformas"), "desenvolvedoras": (Developer, "Desenvolvedoras")}


@bp.before_request
@admin_required  # ADM-01: toda rota /admin/* exige administrador (403) ou login (redirect)
def _guard():
    pass


def audit(action, detail=""):
    db.session.add(AuditLog(admin_id=current_user().id, action=action, detail=detail[:300]))  # ADM-09


@bp.route("/")
def index():
    return render_template(
        "admin/index.html", pending=Report.query.filter_by(status="pendente", alvo_tipo="avaliacao").count(),
        games=Game.query.count(),
        duplicatas=PendenciaMerge.query.filter_by(status="pendente").count(),
    )


# ---------- importar jogos reais (IAD-01..09, FNT-08..11) ----------
FONTE_PADRAO = "steam"
LOTE_MAX = 10
LOTE_LIMITE_S = 20  # depois disso o lote não inicia itens novos; com 5 s por chamada, fica abaixo dos 60 s do gunicorn
relogio = time.monotonic  # os testes trocam por um relógio falso (FNT-12)
SEM_RESPOSTA = "A fonte não respondeu. Tente de novo em instantes."


def fontes_admin():
    """Fontes que a tela consegue usar: as que oferecem buscar e detalhe e estão ligadas (FNT-08, GOG-04)."""
    return [n for n, m in FONTES.items()
            if callable(getattr(m, "buscar", None)) and callable(getattr(m, "detalhe", None)) and not indisponivel(m)]


def _fonte_escolhida():
    nome = request.values.get("fonte") or FONTE_PADRAO
    if nome not in fontes_admin():
        abort(400)
    return nome


def _url_busca(fonte, q):
    return "/admin/importar?" + urlencode({**({"fonte": fonte} if fonte != FONTE_PADRAO else {}), "q": q})


def _criar_importado(fonte, j):
    """Cria o jogo e audita com a fonte (IAD-07); devolve (status, jogo) como importar_item."""
    status, game = importar_item(fonte, j) if j else ("invalido", None)
    if status in ("ok", "vinculado"):
        audit("jogo.importar", f"{game.title} ({fonte}:{j['external_id']})")
        db.session.commit()
    else:
        db.session.rollback()
    return status, game


def _tela(fonte, q, **ctx):
    return render_template("admin/importar.html", q=q, fonte=fonte, fontes=fontes_admin(), modulos=FONTES, **ctx)


@bp.route("/importar", methods=["GET", "POST"])
def importar_jogos():
    nome = _fonte_escolhida()
    fonte = FONTES[nome]
    q = (request.values.get("q") or "").strip()
    if request.method == "POST":
        try:
            j = fonte.detalhe(request.form.get("external_id", ""))
        except Exception:  # rede, JSON, id inválido: nada é criado
            flash(SEM_RESPOSTA, "err")
            return redirect(_url_busca(nome, q))
        status, game = _criar_importado(nome, j)
        if status == "ok":
            flash(f"“{game.title}” importado.", "ok")
            return redirect(f"/jogos/{game.slug}")
        if status == "vinculado":  # o mesmo jogo já estava no catálogo: ganhou a loja nova (MRG-02)
            flash(f"“{game.title}” já estava no catálogo e agora também aparece nesta fonte.", "ok")
            return redirect(f"/jogos/{game.slug}")
        flash(f"“{game.title}” já estava no catálogo." if status == "duplicado" else
              "Esse item não é um jogo com título e data de lançamento válidos.", "err")
        return redirect(f"/jogos/{game.slug}" if game else _url_busca(nome, q))
    resultados, erro = [], None
    if len(q) >= 2:
        try:
            resultados = fonte.buscar(q, LOTE_MAX)
        except Exception:
            erro = SEM_RESPOSTA
    ids = [r["external_id"] for r in resultados]
    ja = {v.external_id for v in JogoFonte.query.filter(JogoFonte.fonte == nome, JogoFonte.external_id.in_(ids))}
    return _tela(nome, q, resultados=resultados, erro=erro, ja=ja)


def _importar_um(nome, eid):
    """Um item do lote; devolve a chave do resumo em que ele entra (FNT-09)."""
    if jogo_por_fonte(nome, eid):
        return "existentes"
    try:
        status, _ = _criar_importado(nome, FONTES[nome].detalhe(eid))
    except Exception:  # rede, JSON ou banco: o item falha e o lote continua
        db.session.rollback()
        return "falhas"
    return {"ok": "importados", "vinculado": "importados", "duplicado": "existentes"}.get(status, "falhas")


@bp.route("/importar/lote", methods=["POST"])
def importar_lote():
    nome = _fonte_escolhida()
    q = (request.form.get("q") or "").strip()
    ids = list(dict.fromkeys(i.strip() for i in request.form.getlist("ids") if i.strip()))
    if not 1 <= len(ids) <= LOTE_MAX:
        flash(f"Marque de 1 a {LOTE_MAX} jogos por envio.", "err")
        return redirect(_url_busca(nome, q))
    titulo = {i: (request.form.get(f"titulo-{i}") or i)[:150] for i in ids}
    lote = {"importados": [], "existentes": [], "falhas": [], "sem_processar": []}
    inicio = relogio()
    for n, eid in enumerate(ids):
        if relogio() - inicio >= LOTE_LIMITE_S:  # FNT-10: o resto fica listado, sem processar
            lote["sem_processar"] = [titulo[i] for i in ids[n:]]
            break
        lote[_importar_um(nome, eid)].append(titulo[eid])
    return _tela(nome, q, resultados=[], erro=None, ja=set(), lote=lote, voltar=_url_busca(nome, q))


# ---------- jogos ----------
def parse_game_form(f):
    errors, data = {}, {}
    title, synopsis = f.get("title", "").strip(), f.get("synopsis", "").strip()
    if not 1 <= len(title) <= 150:
        errors["title"] = "O título é obrigatório (até 150 caracteres)."
    if not synopsis:
        errors["synopsis"] = "A sinopse é obrigatória."
    try:
        data["release_date"] = date.fromisoformat(f.get("release_date", ""))
    except ValueError:
        errors["release_date"] = "Informe uma data de lançamento válida."
    dev = db.session.get(Developer, int(f["developer_id"])) if f.get("developer_id", "").isdigit() else None
    if not dev:
        errors["developer_id"] = "Escolha uma desenvolvedora."
    genres = Genre.query.filter(Genre.id.in_([int(x) for x in f.getlist("genre_ids") if x.isdigit()])).all()
    plats = Platform.query.filter(Platform.id.in_([int(x) for x in f.getlist("platform_ids") if x.isdigit()])).all()
    if not genres:
        errors["genre_ids"] = "Escolha ao menos um gênero."
    if not plats:
        errors["platform_ids"] = "Escolha ao menos uma plataforma."
    data.update(title=title, synopsis=synopsis, developer=dev, genres=genres, platforms=plats)
    return data, errors


def unique_slug(title, ignore_id=None):
    base = slug = slugify(title) or "jogo"
    n = 2
    while (other := Game.query.filter_by(slug=slug).first()) and other.id != ignore_id:
        slug, n = f"{base}-{n}", n + 1
    return slug


def game_form(game=None):
    errors, form = {}, request.form
    if request.method == "POST":
        data, errors = parse_game_form(request.form)
        if not errors:
            new = game is None
            game = game or Game()
            if new or game.title != data["title"]:
                game.slug = unique_slug(data["title"], game.id)  # antes de tocar nos atributos (autoflush)
            for k, v in data.items():
                setattr(game, k, v)
            db.session.add(game)
            db.session.flush()
            audit("jogo.criar" if new else "jogo.editar", f"{game.title} (#{game.id})")
            db.session.commit()
            flash("Jogo salvo.", "ok")
            return redirect("/admin/jogos")
    elif game:
        form = {"title": game.title, "synopsis": game.synopsis, "release_date": game.release_date.isoformat(),
                "developer_id": str(game.developer_id)}
    sel = {"genre_ids": {str(g.id) for g in game.genres} if game and request.method == "GET" else set(request.form.getlist("genre_ids")),
           "platform_ids": {str(p.id) for p in game.platforms} if game and request.method == "GET" else set(request.form.getlist("platform_ids"))}
    return render_template(
        "admin/jogo_form.html", game=game, errors=errors, form=form, sel=sel,
        genres=Genre.query.order_by(Genre.name).all(), platforms=Platform.query.order_by(Platform.name).all(),
        developers=Developer.query.order_by(Developer.name).all(),
    ), (400 if errors else 200)


@bp.route("/jogos")
def games():
    q = Game.query.order_by(Game.title)
    if request.args.get("q"):
        q = q.filter(Game.title.ilike(f"%{request.args['q']}%"))
    return render_template("admin/jogos.html", page=paginate(q, request.args.get("page")))


@bp.route("/jogos/novo", methods=["GET", "POST"])
def game_new():
    return game_form()


@bp.route("/jogos/<int:gid>/editar", methods=["GET", "POST"])
def game_edit(gid):
    return game_form(db.get_or_404(Game, gid))


@bp.route("/jogos/<int:gid>/remover", methods=["POST"])
def game_remove(gid):
    g = db.get_or_404(Game, gid)
    g.removed_at = now()  # soft delete (CAT-07)
    audit("jogo.remover", f"{g.title} (#{g.id})")
    db.session.commit()
    return redirect("/admin/jogos")


@bp.route("/jogos/<int:gid>/restaurar", methods=["POST"])
def game_restore(gid):
    g = db.get_or_404(Game, gid)
    if g.mesclado_em_id:  # ADM-02, MRG-08: desfazer mesclagem está fora do escopo
        flash(f"“{g.title}” foi mesclado em “{g.mesclado_em.title}” e não pode ser restaurado.", "erro")
        return redirect("/admin/jogos")
    g.removed_at = None
    audit("jogo.restaurar", f"{g.title} (#{g.id})")
    db.session.commit()
    return redirect("/admin/jogos")


# ---------- duplicatas do catálogo (MRG-05, MRG-06, MRG-10, MRG-11) ----------
DUPLICATAS = "/admin/catalogo/duplicatas"


def _avaliacoes_por_jogo(pendencias):
    ids = {i for p in pendencias for i in (p.jogo_a_id, p.jogo_b_id)}
    return dict(
        db.session.query(Review.game_id, func.count())
        .filter(Review.game_id.in_(ids), Review.hidden.is_(False)).group_by(Review.game_id).all()
    ) if ids else {}


@bp.route("/catalogo/duplicatas")
def duplicatas():
    lado = (selectinload(Game.fontes), joinedload(Game.developer))
    q = (PendenciaMerge.query.filter_by(status="pendente")
         .options(joinedload(PendenciaMerge.jogo_a).options(*lado), joinedload(PendenciaMerge.jogo_b).options(*lado))
         .order_by(PendenciaMerge.created_at, PendenciaMerge.id))
    page = paginate(q, request.args.get("page"))
    return render_template("admin/duplicatas.html", page=page, avaliacoes=_avaliacoes_por_jogo(page.items))


def _pendencia():
    pid = request.form.get("pendencia_id", "")
    return db.get_or_404(PendenciaMerge, int(pid)) if pid.isdigit() else abort(404)


def _par(a, b):
    return f"#{a.id} {a.slug} e #{b.id} {b.slug}"


@bp.route("/catalogo/mesclar", methods=["POST"])
def mesclar_duplicata():
    p = _pendencia()
    principal_id = request.form.get("principal_id", "")
    try:
        principal, secundario = mesclar(p, int(principal_id) if principal_id.isdigit() else None, current_user().id)
    except MesclagemRecusada as exc:  # nada muda (MRG-09)
        db.session.rollback()
        flash(str(exc), "erro")
        return redirect(DUPLICATAS)
    audit("catalogo.mesclar", f"principal {_par(principal, secundario)} (secundário)")
    db.session.commit()
    flash(f"“{secundario.title}” foi mesclado em “{principal.title}”.", "ok")
    return redirect(DUPLICATAS)


@bp.route("/catalogo/ignorar-merge", methods=["POST"])
def ignorar_duplicata():
    p = _pendencia()
    try:
        ignorar(p, current_user().id)
    except MesclagemRecusada as exc:
        flash(str(exc), "erro")
        return redirect(DUPLICATAS)
    audit("catalogo.ignorar", _par(p.jogo_a, p.jogo_b))
    db.session.commit()
    flash("Os dois jogos continuam separados e o par não será sugerido de novo.", "ok")
    return redirect(DUPLICATAS)


# ---------- gêneros, plataformas, desenvolvedoras ----------
def taxonomy(kind):
    if kind not in TAXONOMIES:
        abort(404)
    return TAXONOMIES[kind]


@bp.route("/<kind>", methods=["GET", "POST"])
def tax_list(kind):
    model, label = taxonomy(kind)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not 1 <= len(name) <= 80:
            flash("O nome é obrigatório (até 80 caracteres).", "erro")
        elif model.query.filter((model.name == name) | (model.slug == slugify(name))).first():
            flash("Já existe um item com esse nome.", "erro")
        else:
            db.session.add(model(name=name, slug=slugify(name)))
            audit(f"{kind}.criar", name)
            db.session.commit()
    items = model.query.order_by(model.name).all()
    return render_template("admin/taxonomia.html", kind=kind, label=label, items=items)


@bp.route("/<kind>/<int:iid>/editar", methods=["POST"])
def tax_edit(kind, iid):
    model, _ = taxonomy(kind)
    item, name = db.get_or_404(model, iid), request.form.get("name", "").strip()
    clash = model.query.filter(model.id != iid, (model.name == name) | (model.slug == slugify(name))).first()
    if not 1 <= len(name) <= 80 or clash:
        flash("Nome inválido ou já existente.", "erro")
    else:
        audit(f"{kind}.editar", f"{item.name} → {name}")
        item.name, item.slug = name, slugify(name)
        db.session.commit()
    return redirect(f"/admin/{kind}")


@bp.route("/<kind>/<int:iid>/apagar", methods=["POST"])
def tax_delete(kind, iid):
    model, _ = taxonomy(kind)
    item = db.get_or_404(model, iid)
    if item.games:  # ADM-04: item em uso não pode ser apagado
        flash(f"“{item.name}” está em uso por {len(item.games)} jogo(s) e não pode ser apagado.", "erro")
    else:
        audit(f"{kind}.apagar", item.name)
        db.session.delete(item)
        db.session.commit()
    return redirect(f"/admin/{kind}")


# ---------- moderação ----------
@bp.route("/denuncias")
def reports():
    status = request.args.get("status", "pendente")
    q = Report.query.filter_by(status=status, alvo_tipo="avaliacao")  # posts de clube vão para a fila do clube (CLB-11)
    q = q.order_by(Report.created_at, Report.id) if status == "pendente" else q.order_by(Report.decided_at.desc())
    return render_template("admin/denuncias.html", page=paginate(q, request.args.get("page")), status=status)


def decide(rid, new_status, hide):
    rep = db.get_or_404(Report, rid)
    if rep.alvo_tipo != "avaliacao":  # post de clube é decidido na fila do clube (CLB-11)
        abort(404)
    if rep.status != "pendente":
        flash("Esta denúncia já foi decidida.", "erro")
        return redirect("/admin/denuncias")
    targets = [rep]
    if hide:  # ocultar encerra todas as denúncias pendentes da mesma avaliação
        rep.review.hidden = True
        targets = Report.query.filter_by(review_id=rep.review_id, status="pendente").all()
    for r in targets:
        r.status, r.decided_by, r.decided_at = new_status, current_user().id, now()  # ADM-06
    audit(f"denuncia.{new_status}", f"avaliação #{rep.review_id}")
    db.session.commit()  # ADM-07: médias, histogramas e feed leem is hidden em tempo de consulta
    return redirect("/admin/denuncias")


@bp.route("/denuncias/<int:rid>/ocultar", methods=["POST"])
def report_hide(rid):
    return decide(rid, "ocultada", True)


@bp.route("/denuncias/<int:rid>/rejeitar", methods=["POST"])
def report_reject(rid):
    return decide(rid, "rejeitada", False)


@bp.route("/avaliacoes/<int:rid>/reverter", methods=["POST"])
def revert(rid):
    review = db.get_or_404(Review, rid)  # ADM-08
    review.hidden = False
    for r in Report.query.filter_by(review_id=rid, status="ocultada"):
        r.status = "revertida"
    audit("avaliacao.reverter", f"avaliação #{rid}")
    db.session.commit()
    return redirect("/admin/denuncias?status=ocultada")


@bp.route("/auditoria")
def audit_log():
    q = AuditLog.query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    return render_template("admin/auditoria.html", page=paginate(q, request.args.get("page")))
