"""Recuperação de senha (RSN-01..10): link assinado por e-mail, sem tabela nova.

O token leva o id e uma impressão da conta (hash da senha com `session_version`): trocar a senha ou encerrar as
sessões o invalida, então ele serve uma vez só. A resposta do pedido é a mesma exista a conta ou não (CAD-03).
"""
import hashlib
import hmac
from datetime import timedelta

from flask import Blueprint, current_app, flash, redirect, render_template, request
from werkzeug.security import generate_password_hash

from . import tempo
from .auth import EMAIL_RE
from .conta import LINK_INVALIDO, erro_senha_nova, ler_link, quando, registrar, sem_cache, serializador
from .emails import backend, enviar_email, url_publica
from .menores.idade import VINCULADO, faixa_etaria
from .models import EmailSaida, LoginAttempt, User, db
from .notificacoes import notificar
from .util import current_user

bp = Blueprint("recuperacao", __name__)

VALIDADE = timedelta(hours=1)  # RSN-02
MAX_POR_HORA = 3  # por conta (RSN-04)
RESPOSTA = "Se houver uma conta com esse e-mail, enviamos um link para redefinir a senha. Ele vale por 1 hora."


# ---------- ganchos das specs que ainda não estão na main ----------
def conta_anonimizada(user):
    from .privacidade.anonimizar import anonimizada

    return anonimizada(user)


def responsaveis_ativos(user):
    """Ids dos responsáveis com vínculo ativo (RSP-05)."""
    from .menores.responsavel import responsaveis_ativos as ativos

    return ativos(user)


# ---------- token (RSN-02, RSN-05) ----------
def impressao(user):
    """Muda quando a senha muda ou as sessões são encerradas, o que invalida o link (RSN-02, RSN-06)."""
    return hashlib.sha256(f"{user.password_hash}|{user.session_version}".encode()).hexdigest()


def token_recuperacao(user):
    return serializador("recuperar-senha").dumps({"u": user.id, "i": impressao(user)})


def ler_token_recuperacao(token):
    dados = ler_link(token, "recuperar-senha", VALIDADE)
    user = db.session.get(User, dados.get("u")) if isinstance(dados, dict) and isinstance(dados.get("u"), int) else None
    if user is None or not hmac.compare_digest(str(dados.get("i")), impressao(user)):
        return None
    return user


# ---------- pedido (RSN-01..04, RSN-09) ----------
def pode_recuperar(user):
    """Conta inexistente, anonimizada ou suspensa não recebe nada; a pendente de responsável recebe (RSN-03)."""
    return user is not None and user.suspensa_em is None and not conta_anonimizada(user)


def abaixo_do_limite(user):
    desde = tempo.agora_utc() - timedelta(hours=1)
    return EmailSaida.query.filter(EmailSaida.user_id == user.id, EmailSaida.modelo == "recuperar_senha",
                                   EmailSaida.criado_em >= desde).count() < MAX_POR_HORA


def recuperacao_indisponivel():
    """Em produção sem SMTP o link iria para o log do servidor (RSN-09, INC-09)."""
    return current_app.config.get("PRODUCAO") and backend() == "console"


@bp.route("/senha/recuperar", methods=["GET", "POST"])
def recuperar():
    if current_user():
        return redirect("/conta/seguranca")
    if recuperacao_indisponivel():
        return render_template("conta/recuperar.html", indisponivel=True, erros={}), 503
    if request.method == "GET":
        return render_template("conta/recuperar.html", erros={})
    email = request.form.get("email", "").strip().lower()
    if not EMAIL_RE.match(email):
        return render_template("conta/recuperar.html", erros={"email": "Informe um e-mail válido."}), 400
    user = User.query.filter_by(email=email).first()
    if pode_recuperar(user) and abaixo_do_limite(user):
        enviar_email(user, "recuperar_senha", link=url_publica(f"/senha/redefinir/{token_recuperacao(user)}"))
        db.session.commit()
    return render_template("conta/recuperar.html", enviado=RESPOSTA, erros={})


# ---------- redefinição (RSN-05..08) ----------
@bp.route("/senha/redefinir/<token>", methods=["GET", "POST"])
def redefinir(token):
    user = ler_token_recuperacao(token)
    if user is None:
        return sem_cache(render_template("conta/redefinir.html", invalido=LINK_INVALIDO, erros={}), 400)
    if request.method == "GET":
        return sem_cache(render_template("conta/redefinir.html", erros={}))
    problema = erro_senha_nova(request.form.get("nova", ""), request.form.get("confirmacao", ""))
    if problema:  # o token continua valendo: a impressão da conta não mudou
        return sem_cache(render_template("conta/redefinir.html", erros={"nova": problema}), 400)
    user.password_hash = generate_password_hash(request.form["nova"])
    user.session_version += 1
    LoginAttempt.query.filter_by(email=user.email).delete()
    enviar_email(user, "senha_alterada", quando=quando())
    registrar(user, "senha_redefinida")
    if faixa_etaria(user) == VINCULADO:  # RSN-08, PMN
        for rid in responsaveis_ativos(user):
            notificar(rid, "responsavel", texto=f"A senha de {user.display_name} foi redefinida por e-mail.")
    db.session.commit()
    flash("Senha redefinida. Entre com a nova senha.", "ok")
    return sem_cache(redirect("/entrar"), 302)
