import re
import secrets
from collections import namedtuple
from datetime import timedelta
from functools import cache

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from . import tempo
from .menores import idade
from .menores.politica import pode
from .privacidade import transparencia
from .models import Activity, Follow, GameList, LoginAttempt, Review, Silencio, User, db, now
from .afinidade import resumo_com
from .bloqueios import bloqueado_entre
from .diario import minutos_registrados, pode_ver_perfil, zerados
from .diario_import import ler_steam_id
from .conquistas import vitrine
from .progressao import resumo as resumo_progressao
from .retrospectiva import sparkline_do_perfil
from .perfil import dados as dados_perfil
from .privacidade.retencao import registrar_acesso
from .util import current_user, login_required, safe_next

bp = Blueprint("auth", __name__)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USER_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
MAX_FAILS, WINDOW = 5, timedelta(minutes=15)
SEGUNDO_PASSO, PRAZO_SEGUNDO_PASSO = "2fa", timedelta(minutes=5)  # CTA-08


@cache
def hash_ficticio():
    """Hash conferido quando o e-mail não existe, para o login não revelar contas pelo tempo de resposta."""
    return generate_password_hash(secrets.token_hex(16))


def validate_signup(username, email, password):
    errors = {}
    if not USER_RE.match(username):
        errors["username"] = "Use de 3 a 20 caracteres (letras, números ou _)."
    elif User.query.filter_by(username_lower=username.lower()).first():
        errors["username"] = "Nome de usuário já em uso."
    if not EMAIL_RE.match(email):
        errors["email"] = "Informe um e-mail válido."
    elif User.query.filter_by(email=email.lower()).first():
        errors["email"] = "E-mail já cadastrado."
    if len(password) < 8:
        errors["password"] = "A senha deve ter no mínimo 8 caracteres."
    return errors


def falhas_recentes(email):
    return LoginAttempt.query.filter(LoginAttempt.email == email, LoginAttempt.at >= now() - WINDOW).count()


def iniciar_segundo_passo(user, destino):
    """CTA-08: a senha confere, mas a sessão só nasce em `/entrar/2fa`; as tentativas falhas ainda não zeram."""
    session.clear()
    session[SEGUNDO_PASSO] = {"uid": user.id, "sv": user.session_version, "inst": current_app.config["INSTANCIA"],
                              "ate": tempo.agora().timestamp() + PRAZO_SEGUNDO_PASSO.total_seconds(),
                              "next": safe_next(destino), "erros": 0}
    return redirect("/entrar/2fa")


def start_session(user):
    session.clear()
    session["uid"], session["sv"] = user.id, user.session_version
    session["inst"] = current_app.config["INSTANCIA"]
    transparencia.marcar_em_dia(user)  # PRV-05, PRV-06: em dia, as páginas não reconferem os documentos


def validar_nascimento(form):
    """IDA-01, IDA-02: data válida e 12 anos ou mais; (erros, gravar(user))."""
    try:
        resultado = idade.ADAPTADORES["autodeclaracao"].verificar(None, {"nascimento": form.get("nascimento")})
    except idade.IdadeInvalida as exc:
        return {"nascimento": str(exc)}, None
    if idade.idade(resultado.nascimento) < idade.IDADE_MINIMA:
        return {"nascimento": idade.MENOR_DE_12}, None
    return {}, lambda user: idade.registrar(user, resultado)  # IDA-06: autodeclaração, não confiável


Parte = namedtuple("Parte", "validar template")
# PRV-04: cada parte valida o mesmo formulário e só grava se nenhuma falhar; CNS-04 entra entre a data e o aceite
PARTES_DO_CADASTRO = [
    Parte(validar_nascimento, "auth/_cadastro_nascimento.html"),
    Parte(transparencia.validar_aceite, "auth/_cadastro_aceite.html"),
]


def _form_cadastro(errors, form, status=200):
    recusa = errors.get("nascimento") if errors.get("nascimento") == idade.MENOR_DE_12 else None  # IDA-02 antes
    return render_template("auth/cadastro.html", errors=errors, form=form, recusa=recusa,
                           partes=[p.template for p in PARTES_DO_CADASTRO], resumo=transparencia.resumo()), status


@bp.route("/cadastro", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        f = request.form
        username, email, password = f.get("username", "").strip(), f.get("email", "").strip(), f.get("password", "")
        errors, gravacoes = validate_signup(username, email, password), []
        for parte in PARTES_DO_CADASTRO:  # todos os erros juntos, por campo; nada é gravado se um falhar
            erros_da_parte, gravar = parte.validar(f)
            errors.update(erros_da_parte)
            gravacoes.append(gravar)
        if errors:
            return _form_cadastro(errors, f, 400)
        user = User(
            username=username,
            username_lower=username.lower(),
            email=email.lower(),
            password_hash=generate_password_hash(password),
            display_name=username,
            role="jogador",  # CAD-10: todo cadastro novo é jogador
        )
        db.session.add(user)
        try:
            db.session.flush()
            for gravar in gravacoes:
                gravar(user)
            db.session.commit()
        except IntegrityError:  # cadastro simultâneo: a outra requisição gravou o mesmo usuário ou e-mail
            db.session.rollback()
            errors = validate_signup(username, email, password) or {"email": "E-mail já cadastrado."}
            return _form_cadastro(errors, f, 400)
        start_session(user)
        flash("Conta criada! Bem-vindo ao GameLog.", "ok")
        if idade.aguarda_responsavel(user):  # IDA-04
            return redirect("/conta/pendente")
        return redirect(safe_next(request.args.get("next")))
    return _form_cadastro({}, {})


@bp.route("/entrar", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email, password = request.form.get("email", "").strip().lower(), request.form.get("password", "")
        if falhas_recentes(email) >= MAX_FAILS:  # CAD-04
            flash("Muitas tentativas. Tente novamente em 15 minutos.", "erro")
            return render_template("auth/login.html", form=request.form), 429
        user = User.query.filter_by(email=email).first()
        confere = check_password_hash(user.password_hash if user else hash_ficticio(), password)  # CAD-03: mesmo tempo
        if user and confere:
            if user.suspensa_em is not None:  # IDA-05: a senha confere, mas a sessão não é criada
                flash("Conta suspensa.", "erro")
                return render_template("auth/login.html", form=request.form), 403
            if user.totp_ativado_em is not None:
                return iniciar_segundo_passo(user, request.args.get("next"))
            LoginAttempt.query.filter_by(email=email).delete()
            db.session.commit()
            start_session(user)
            registrar_acesso(user)  # RTN-05: zera também o aviso de conta inativa (RTN-06)
            db.session.commit()
            return redirect(safe_next(request.args.get("next")))
        db.session.add(LoginAttempt(email=email))
        db.session.commit()
        flash("E-mail ou senha incorretos.", "erro")  # CAD-03: mensagem genérica
        return render_template("auth/login.html", form=request.form), 401
    return render_template("auth/login.html", form={})


@bp.route("/sair", methods=["POST"])
def logout():
    u = current_user()
    if u:
        u.session_version += 1
        db.session.commit()
    session.clear()
    return redirect("/")


@bp.route("/u/<username>")
def profile(username):
    user = User.query.filter_by(username_lower=username.lower()).first_or_404()
    me = current_user()
    own = me is not None and me.id == user.id
    if bloqueado_entre(me, user):
        abort(404)  # BLQ-03: como um perfil inexistente, sem dizer quem bloqueou
    follow = Follow.query.filter_by(follower_id=me.id, followed_id=user.id).first() if me and not own else None
    following, pedido = follow is not None, follow is not None and not follow.aprovado  # PMN-03
    tem_pedidos = own and db.session.query(Follow.query.filter(Follow.followed_id == me.id, ~Follow.aprovado).exists()).scalar()
    silenciado = bool(me and not own and Silencio.query.filter_by(user_id=me.id, silenciado_id=user.id).first())
    if not pode_ver_perfil(me, user):  # DIA-12: só o cabeçalho
        return render_template("auth/perfil_privado.html", u=user, following=following, pedido=pedido,
                               silenciado=silenciado)
    lists = GameList.query.filter_by(user_id=user.id)
    if not own:
        lists = lists.filter_by(public=True)
    extra = dados_perfil(user.id, request.args.get("page"))
    stats = {
        "avaliacoes": extra["total"],  # PRF-06: mesmo critério das estatísticas (sem ocultas nem jogos removidos)
        "listas": lists.count(),
        "seguidores": Follow.query.filter_by(followed_id=user.id).filter(Follow.aprovado).count(),  # PMN-03
        "seguindo": Follow.query.filter_by(follower_id=user.id).filter(Follow.aprovado).count(),
        "minutos": minutos_registrados(user.id),  # DIA-09
        "zerados": zerados(user.id),
    }
    afin = resumo_com(me.id, user.id) if me and not own and pode(user, "historico_para_terceiros") else None  # AFI-08, PMN-06
    extra["sparkline"], _ = sparkline_do_perfil(user.id)  # RET-11
    extra["vitrine"] = vitrine(user.id, pode(user, "conquista_por_tempo"))  # GAM-09: lidas, sem recalcular (CON-06)
    extra["progressao"] = resumo_progressao(user.id)  # PRG-09
    return render_template(
        "auth/perfil.html", u=user, own=own, stats=stats, following=following, pedido=pedido, tem_pedidos=tem_pedidos, silenciado=silenciado, lists=lists.limit(10).all(), afin=afin,
        **extra,
    )


@bp.route("/u/<username>/editar", methods=["GET", "POST"])
@login_required
def edit_profile(username):
    user = User.query.filter_by(username_lower=username.lower()).first_or_404()
    if current_user().id != user.id:
        abort(403)  # CAD-09
    errors = {}
    if request.method == "POST":
        name, bio = request.form.get("display_name", "").strip(), request.form.get("bio", "").strip()
        if not 1 <= len(name) <= 60:
            errors["display_name"] = "Nome de exibição deve ter de 1 a 60 caracteres."
        if len(bio) > 280:
            errors["bio"] = "A bio deve ter até 280 caracteres."
        steam_id = ler_steam_id(request.form.get("steam_id"))  # HRS-01
        if steam_id is None:
            errors["steam_id"] = "Informe o SteamID64 (17 dígitos) ou a URL steamcommunity.com/profiles/<id>."
        elif steam_id and User.query.filter(User.steam_id == steam_id, User.id != user.id).first():
            errors["steam_id"] = "Esta conta da Steam já está vinculada a outro usuário"
        privado = bool(request.form.get("perfil_privado"))  # DIA-11
        if user.perfil_privado and not privado and not pode(user, "perfil_publico"):
            abort(403)  # PMN-02: de 12 a 15 anos, só o responsável abre o perfil (extensão de RSP-07)
        if not errors:
            user.display_name, user.bio = name, bio
            user.perfil_privado = privado
            user.steam_id = steam_id or None
            db.session.commit()
            flash("Perfil atualizado.", "ok")
            return redirect(f"/u/{user.username}")
        return render_template("auth/editar.html", u=user, errors=errors, form=request.form), 400
    return render_template("auth/editar.html", u=user, errors={}, form={"display_name": user.display_name, "bio": user.bio,
                                                                   "perfil_privado": user.perfil_privado,
                                                                   "steam_id": user.steam_id or ""})
