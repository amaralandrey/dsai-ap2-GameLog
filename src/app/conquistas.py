"""Telas de conquistas: perfil, estante, catálogo com porcentagens e painel do admin (GAM-08..12)."""
from flask import Blueprint, abort, flash, redirect, render_template, request
from sqlalchemy import func

from .diario import perfil_ou_404
from .gamificacao import CATEGORIAS, METRICAS, RARIDADES
from .menores.politica import pode
from .models import AuditLog, Conquista, User, UsuarioConquista, db
from .util import admin_required, current_user, login_required, slugify

bp = Blueprint("conquistas", __name__)
MAX_ESTANTE = 3
RECENTES = 6


def vitrine(user_id, por_tempo=True):
    """Estante e as 6 mais recentes do perfil, numa consulta (GAM-09, PRF-09); sem `por_tempo`, sem as por tempo."""
    q = (db.session.query(UsuarioConquista, Conquista)
         .join(Conquista, Conquista.id == UsuarioConquista.conquista_id)
         .filter(UsuarioConquista.user_id == user_id, UsuarioConquista.desbloqueada_em.isnot(None)))
    if not por_tempo:
        q = q.filter(Conquista.por_tempo.is_(False))  # PMN-09: nem as ganhas antes aparecem
    desbloqueadas = q.order_by(UsuarioConquista.desbloqueada_em.desc(), UsuarioConquista.id.desc()).all()
    return {"estante": [x for x in desbloqueadas if x[0].fixada][:MAX_ESTANTE],
            "recentes": desbloqueadas[:RECENTES], "total": len(desbloqueadas)}


def _filtro(valor, opcoes):
    return valor if valor in opcoes else None  # valor inválido é ignorado


@bp.route("/u/<username>/conquistas")
def do_usuario(username):
    dono = perfil_ou_404(username)  # GAM-09: perfil privado responde 404
    me = current_user()
    own = me is not None and me.id == dono.id
    categoria = _filtro(request.args.get("categoria"), CATEGORIAS)
    raridade = _filtro(request.args.get("raridade"), RARIDADES)
    q = (db.session.query(UsuarioConquista, Conquista)
         .join(Conquista, Conquista.id == UsuarioConquista.conquista_id)
         .filter(UsuarioConquista.user_id == dono.id))
    if categoria:
        q = q.filter(Conquista.categoria == categoria)
    if raridade:
        q = q.filter(Conquista.raridade == raridade)
    por_tempo = pode(dono, "conquista_por_tempo")  # GAM-14, PMN-09: nem avaliadas nem mostradas
    if not por_tempo:
        q = q.filter(Conquista.por_tempo.is_(False))
    desbloqueadas = (q.filter(UsuarioConquista.desbloqueada_em.isnot(None))
                     .order_by(UsuarioConquista.desbloqueada_em.desc(), UsuarioConquista.id.desc()).all())
    bloqueadas = []
    if own:  # só o dono vê as bloqueadas, com a barra de progresso
        progresso = {uc.conquista_id: uc.progresso for uc, _ in q.filter(UsuarioConquista.desbloqueada_em.is_(None))}
        ganhas = {c.id for _, c in desbloqueadas}
        catalogo = Conquista.query.filter(Conquista.ativa.is_(True), Conquista.metrica != "concedida")
        if categoria:
            catalogo = catalogo.filter(Conquista.categoria == categoria)
        if raridade:
            catalogo = catalogo.filter(Conquista.raridade == raridade)
        if not por_tempo:
            catalogo = catalogo.filter(Conquista.por_tempo.is_(False))
        bloqueadas = [(c, progresso.get(c.id, 0)) for c in catalogo.order_by(Conquista.categoria, Conquista.meta)
                      if c.id not in ganhas]
    return render_template("conquistas/usuario.html", dono=dono, own=own, desbloqueadas=desbloqueadas,
                           bloqueadas=bloqueadas, categorias=CATEGORIAS, raridades=RARIDADES, categoria=categoria,
                           raridade=raridade, max_estante=MAX_ESTANTE)


def _minha_desbloqueada(username, codigo):
    me = current_user()
    if me.username_lower != username.lower():
        abort(403)
    uc = (UsuarioConquista.query.join(Conquista, Conquista.id == UsuarioConquista.conquista_id)
          .filter(UsuarioConquista.user_id == me.id, Conquista.codigo == codigo,
                  UsuarioConquista.desbloqueada_em.isnot(None)).first())
    if uc is None:
        abort(404)  # GAM-10: não desbloqueada
    return me, uc


@bp.route("/u/<username>/conquistas/<codigo>/fixar", methods=["POST"])
@login_required
def fixar(username, codigo):
    me, uc = _minha_desbloqueada(username, codigo)
    if not uc.fixada:
        if UsuarioConquista.query.filter_by(user_id=me.id, fixada=True).count() >= MAX_ESTANTE:
            flash(f"A estante tem no máximo {MAX_ESTANTE} conquistas", "erro")
            return redirect(f"/u/{me.username}/conquistas")
        uc.fixada = True
        db.session.commit()
    return redirect(f"/u/{me.username}/conquistas")


@bp.route("/u/<username>/conquistas/<codigo>/desafixar", methods=["POST"])
@login_required
def desafixar(username, codigo):
    me, uc = _minha_desbloqueada(username, codigo)
    uc.fixada = False
    db.session.commit()
    return redirect(f"/u/{me.username}/conquistas")


def porcentagens():
    """{conquista_id: % de usuários não admins que desbloquearam}, em duas consultas (GAM-11)."""
    jogadores = db.session.query(func.count(User.id)).filter(User.role != "admin").scalar() or 0
    contagens = dict(
        db.session.query(UsuarioConquista.conquista_id, func.count())
        .join(User, User.id == UsuarioConquista.user_id)
        .filter(User.role != "admin", UsuarioConquista.desbloqueada_em.isnot(None))
        .group_by(UsuarioConquista.conquista_id).all()
    )
    return {cid: round(100 * n / jogadores, 1) for cid, n in contagens.items()} if jogadores else {}


@bp.route("/conquistas")
def catalogo():
    conquistas = Conquista.query.filter_by(ativa=True).order_by(Conquista.categoria, Conquista.meta, Conquista.nome).all()
    me = current_user()
    minhas = set()
    if me:  # a secreta só se revela a quem a desbloqueou (GAM-08)
        minhas = {cid for (cid,) in db.session.query(UsuarioConquista.conquista_id).filter(
            UsuarioConquista.user_id == me.id, UsuarioConquista.desbloqueada_em.isnot(None))}
    por_categoria = {k: [c for c in conquistas if c.categoria == k] for k in CATEGORIAS}
    return render_template("conquistas/catalogo.html", por_categoria=por_categoria, categorias=CATEGORIAS,
                           raridades=RARIDADES, pct=porcentagens(), minhas=minhas)


# ---------- admin (GAM-12) ----------
def _validar(form, conquista=None):
    erros, dados = {}, {}
    dados["nome"] = form.get("nome", "").strip()
    if not 1 <= len(dados["nome"]) <= 60:
        erros["nome"] = "O nome é obrigatório e deve ter até 60 caracteres."
    dados["descricao"] = form.get("descricao", "").strip()
    if not 1 <= len(dados["descricao"]) <= 200:
        erros["descricao"] = "A descrição é obrigatória e deve ter até 200 caracteres."
    dados["metrica"] = form.get("metrica", "")
    if dados["metrica"] not in METRICAS:
        erros["metrica"] = "Escolha uma métrica do catálogo."
    try:
        dados["meta"] = int(form.get("meta", ""))
        if dados["meta"] < 1:
            raise ValueError
    except ValueError:
        erros["meta"] = "A meta deve ser um inteiro maior ou igual a 1."
    dados["categoria"] = form.get("categoria", "")
    if dados["categoria"] not in CATEGORIAS:
        erros["categoria"] = "Escolha uma categoria."
    dados["raridade"] = form.get("raridade", "")
    if dados["raridade"] not in RARIDADES:
        erros["raridade"] = "Escolha uma raridade."
    dados["icone"] = (form.get("icone", "").strip() or "🏆")[:8]
    dados["secreta"], dados["por_tempo"] = bool(form.get("secreta")), bool(form.get("por_tempo"))
    return dados, erros


def _auditar(acao, detalhe):
    db.session.add(AuditLog(admin_id=current_user().id, action=acao, detail=detalhe[:300]))  # GLOBAL-17


def _form_admin(conquista, erros, form, status=200):
    lista = Conquista.query.order_by(Conquista.ativa.desc(), Conquista.categoria, Conquista.nome).all()
    return render_template("conquistas/admin.html", conquistas=lista, conquista=conquista, erros=erros, form=form,
                           metricas=sorted(METRICAS), categorias=CATEGORIAS, raridades=RARIDADES), status


@bp.route("/admin/conquistas", methods=["GET", "POST"])
@admin_required
def admin_lista():
    if request.method == "GET":
        return _form_admin(None, {}, {})
    dados, erros = _validar(request.form)
    codigo = slugify(request.form.get("codigo", "") or dados["nome"])[:40]
    if not erros and (not codigo or Conquista.query.filter_by(codigo=codigo).first()):
        erros["codigo"] = "Código vazio ou já usado."
    if erros:
        return _form_admin(None, erros, request.form, 400)
    db.session.add(Conquista(codigo=codigo, do_admin=True, **dados))
    _auditar("conquista.criar", f"{codigo}: {dados['metrica']} >= {dados['meta']}")
    db.session.commit()
    flash("Conquista criada.", "ok")
    return redirect("/admin/conquistas")


@bp.route("/admin/conquistas/<int:cid>/editar", methods=["GET", "POST"])
@admin_required
def admin_editar(cid):
    conquista = db.get_or_404(Conquista, cid)
    if request.method == "GET":
        return _form_admin(conquista, {}, {c: getattr(conquista, c) for c in (
            "nome", "descricao", "metrica", "meta", "categoria", "raridade", "icone", "secreta", "por_tempo")})
    dados, erros = _validar(request.form, conquista)
    if erros:
        return _form_admin(conquista, erros, request.form, 400)
    for campo, valor in dados.items():
        setattr(conquista, campo, valor)
    conquista.do_admin = True  # editada no painel: o sincronizar não desfaz
    _auditar("conquista.editar", f"{conquista.codigo}: {dados['metrica']} >= {dados['meta']}")
    db.session.commit()
    flash("Conquista atualizada.", "ok")
    return redirect("/admin/conquistas")


@bp.route("/admin/conquistas/<int:cid>/desativar", methods=["POST"])
@admin_required
def admin_desativar(cid):
    conquista = db.get_or_404(Conquista, cid)
    conquista.ativa = False
    _auditar("conquista.desativar", conquista.codigo)
    db.session.commit()
    flash("Conquista desativada; quem já tem continua com ela, como aposentada.", "ok")
    return redirect("/admin/conquistas")
