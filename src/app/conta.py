"""Segurança da conta (CTA-01..12).

A página `/conta/seguranca` troca senha e e-mail, encerra as outras sessões e liga o 2FA por TOTP (RFC 6238),
feito só com a biblioteca padrão. O link de troca de e-mail é assinado com `itsdangerous` e lido no relógio de
`tempo`, sem tabela de tokens; a recuperação de senha (`recuperacao.py`) usa os mesmos links.
"""
import base64
import hashlib
import hmac
import re
import secrets
import struct
from datetime import timedelta

from flask import Blueprint, current_app, flash, make_response, redirect, render_template, request, session
from itsdangerous import BadData, TimestampSigner, URLSafeTimedSerializer
from markupsafe import Markup
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from . import qr, tempo
from .auth import EMAIL_RE, MAX_FAILS, SEGUNDO_PASSO, falhas_recentes, start_session
from .emails import enviar_email, mascarar, url_publica
from .models import CodigoRecuperacao, EventoSeguranca, LoginAttempt, User, db
from .notificacoes import notificar
from .util import current_user, login_required, safe_next

bp = Blueprint("conta", __name__)

PASSO, DIGITOS, VIZINHOS = 30, 6, 1  # CTA-07: 30 s, 6 dígitos, um passo de cada lado
BYTES_SEGREDO = 20  # 160 bits (CTA-06)
NUM_CODIGOS, BYTES_CODIGO = 10, 10  # CTA-09: 80 bits por código
MAX_ERROS_CODIGO = 5  # CTA-08
VALIDADE_EMAIL = timedelta(hours=24)  # CTA-03
MIN_SENHA = 8  # CAD-01
EVENTOS = {
    "senha": "Senha alterada",
    "email": "E-mail alterado",
    "sessoes": "Outras sessões encerradas",
    "2fa_ligado": "Dois fatores ligados",
    "2fa_desligado": "Dois fatores desligados",
    "codigos": "Novos códigos de recuperação",
    "senha_redefinida": "Senha redefinida por e-mail",
}
LINK_INVALIDO = "Link inválido ou vencido"


# ---------- ganchos das specs que ainda não estão na main ----------
def tem_tokens(user):
    """AP2: a opção "revogar também meus tokens" só aparece com tokens pessoais; sem AP2 na main, nunca."""
    return False


def revogar_tokens(user):
    """AP2: revoga os tokens pessoais da API v2 (CTA-11). Sem AP2 na main, não há token a revogar."""


# ---------- TOTP (CTA-06, CTA-07) ----------
def gerar_segredo():
    return base64.b32encode(secrets.token_bytes(BYTES_SEGREDO)).decode()


def codigo_totp(segredo, passo, digitos=DIGITOS, algoritmo=hashlib.sha1):
    """HOTP do passo (RFC 4226, truncamento dinâmico), que é o TOTP do instante `passo * 30` (RFC 6238)."""
    chave = base64.b32decode(segredo + "=" * (-len(segredo) % 8), casefold=True)
    resumo = hmac.new(chave, struct.pack(">Q", passo), algoritmo).digest()
    inicio = resumo[-1] & 0x0F
    numero = struct.unpack(">I", resumo[inicio:inicio + 4])[0] & 0x7FFFFFFF
    return str(numero % 10 ** digitos).zfill(digitos)


def passo_atual():
    return int(tempo.agora().timestamp()) // PASSO


def uri_otpauth(user, segredo):
    return f"otpauth://totp/GameLog:{user.username}?secret={segredo}&issuer=GameLog"


def verificar_totp(user, digitado):
    """Aceita o passo atual e um de cada lado; o passo aceito não vale de novo, nem os anteriores (CTA-07)."""
    digitado = re.sub(r"\s", "", digitado or "")
    if not user.totp_segredo or not re.fullmatch(rf"\d{{{DIGITOS}}}", digitado):
        return False
    atual = passo_atual()
    for passo in range(atual - VIZINHOS, atual + VIZINHOS + 1):
        if not hmac.compare_digest(codigo_totp(user.totp_segredo, passo), digitado):
            continue
        # condicional no banco: duas requisições com o mesmo código não passam juntas
        ok = User.query.filter(User.id == user.id, or_(User.totp_ultimo_passo.is_(None),
                                                       User.totp_ultimo_passo < passo)).update(
            {"totp_ultimo_passo": passo}, synchronize_session=False)
        db.session.refresh(user)
        return ok == 1
    return False


# ---------- códigos de recuperação (CTA-09) ----------
def normalizar_codigo(codigo):
    return re.sub(r"[\s-]", "", codigo or "").upper()


def hash_codigo(codigo):
    """SHA-256 basta: o código tem 80 bits aleatórios, ao contrário de uma senha escolhida por gente."""
    return hashlib.sha256(normalizar_codigo(codigo).encode()).hexdigest()


def gerar_codigos(user):
    """Apaga os anteriores e devolve 10 códigos novos, que só esta resposta mostra."""
    CodigoRecuperacao.query.filter_by(user_id=user.id).delete()
    codigos = []
    for _ in range(NUM_CODIGOS):
        bruto = base64.b32encode(secrets.token_bytes(BYTES_CODIGO)).decode()
        codigos.append("-".join(bruto[i:i + 4] for i in range(0, len(bruto), 4)))
        db.session.add(CodigoRecuperacao(user_id=user.id, codigo_hash=hash_codigo(bruto), created_at=tempo.agora_utc()))
    return codigos


def usar_codigo(user, digitado):
    """Gasta um código de recuperação ainda não usado; UPDATE condicional para o uso ser único."""
    if not normalizar_codigo(digitado):
        return False
    return CodigoRecuperacao.query.filter_by(user_id=user.id, codigo_hash=hash_codigo(digitado),
                                             usado_em=None).update(
        {"usado_em": tempo.agora_utc()}, synchronize_session=False) == 1


def segundo_fator(user, digitado):
    """TOTP ou, no lugar dele, um código de recuperação (CTA-08, CTA-09)."""
    return verificar_totp(user, digitado) or usar_codigo(user, digitado)


def dois_fatores(user):
    return user.totp_ativado_em is not None


# ---------- histórico e avisos (CTA-11) ----------
def registrar(user, tipo, revogar=False):
    """Linha no histórico, notificação `sistema` e, se pedido, revogação dos tokens; quem chama faz o commit."""
    db.session.add(EventoSeguranca(user_id=user.id, tipo=tipo, created_at=tempo.agora_utc()))
    notificar(user.id, "sistema", texto=f"{EVENTOS[tipo]}. Não foi você? Veja a segurança da conta.",
              alvo_url="/conta/seguranca")
    if revogar:
        revogar_tokens(user)


def quando():
    return tempo.agora().strftime("%d/%m/%Y às %H:%M (horário de Belém)")


def checar_senha(user, senha):
    """None se a senha confere; senão (mensagem, status). A errada conta como tentativa de login (CTA-02, CAD-04)."""
    if falhas_recentes(user.email) >= MAX_FAILS:
        return "Muitas tentativas. Tente novamente em 15 minutos.", 429
    if check_password_hash(user.password_hash, senha or ""):
        return None
    db.session.add(LoginAttempt(email=user.email))
    db.session.commit()
    return "Senha atual incorreta.", 400


def erro_senha_nova(nova, confirmacao):
    if len(nova) < MIN_SENHA:
        return f"A senha deve ter no mínimo {MIN_SENHA} caracteres."
    if nova != confirmacao:
        return "A confirmação não confere com a nova senha."
    return None


def revogar_pedido():
    return request.form.get("revogar_tokens") == "1"


def sem_cache(resposta, status=200):
    """Página com segredo, código ou token: fora do cache e sem `Referer` (CTA-06, CTA-09, RSN-05)."""
    resposta = make_response(resposta, status)
    resposta.headers["Cache-Control"] = "no-store"
    resposta.headers["Referrer-Policy"] = "no-referrer"
    return resposta


# ---------- links assinados (CTA-03; também RSN-02) ----------
class AssinadorDoRelogio(TimestampSigner):
    """O carimbo de tempo vem de `tempo.agora`, que os testes fixam (GLOBAL-09)."""

    def get_timestamp(self):
        return int(tempo.agora().timestamp())


def serializador(sal):
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=sal, signer=AssinadorDoRelogio)


def ler_link(token, sal, validade):
    try:
        return serializador(sal).loads(token, max_age=int(validade.total_seconds()))
    except BadData:
        return None


# ---------- página (CTA-01) ----------
def pagina(user, erros=None, status=200):
    eventos = (EventoSeguranca.query.filter_by(user_id=user.id)
               .order_by(EventoSeguranca.created_at.desc(), EventoSeguranca.id.desc()).limit(20).all())
    restantes = CodigoRecuperacao.query.filter_by(user_id=user.id, usado_em=None).count()
    return render_template("conta/seguranca.html", u=user, erros=erros or {}, eventos=eventos, nomes=EVENTOS,
                           dois_fatores=dois_fatores(user), restantes=restantes, tem_tokens=tem_tokens(user),
                           belem=tempo.para_belem), status


@bp.route("/conta/seguranca")
@login_required
def seguranca():
    return pagina(current_user())


def recriar_sessao(user, tipo):
    """CTA-02, CTA-05, CTA-10: as outras sessões caem e esta continua."""
    user.session_version += 1
    registrar(user, tipo, revogar_pedido())
    db.session.commit()
    start_session(user)


@bp.route("/conta/seguranca/senha", methods=["POST"])
@login_required
def trocar_senha():
    me, f = current_user(), request.form
    if erro := checar_senha(me, f.get("atual")):
        return pagina(me, {"senha": erro[0]}, erro[1])
    nova = f.get("nova", "")
    problema = erro_senha_nova(nova, f.get("confirmacao", ""))
    if not problema and check_password_hash(me.password_hash, nova):
        problema = "A nova senha precisa ser diferente da atual."
    if problema:
        return pagina(me, {"senha": problema}, 400)
    me.password_hash = generate_password_hash(nova)
    recriar_sessao(me, "senha")
    flash("Senha alterada. As sessões nos outros aparelhos foram encerradas.", "ok")
    return redirect("/conta/seguranca")


@bp.route("/conta/seguranca/sessoes", methods=["POST"])
@login_required
def encerrar_outras():
    recriar_sessao(current_user(), "sessoes")
    flash("Pronto: só este aparelho continua conectado.", "ok")
    return redirect("/conta/seguranca")


# ---------- troca de e-mail (CTA-03, CTA-04) ----------
@bp.route("/conta/seguranca/email", methods=["POST"])
@login_required
def trocar_email():
    me = current_user()
    if erro := checar_senha(me, request.form.get("senha")):
        return pagina(me, {"email": erro[0]}, erro[1])
    novo = request.form.get("email", "").strip().lower()
    if not EMAIL_RE.match(novo):
        return pagina(me, {"email": "Informe um e-mail válido."}, 400)
    if novo == me.email:
        return pagina(me, {"email": "Este já é o e-mail da conta."}, 400)
    if User.query.filter_by(email=novo).first():
        return pagina(me, {"email": "E-mail já cadastrado."}, 400)
    me.email_pendente = novo
    token = serializador("trocar-email").dumps({"u": me.id, "e": novo})
    enviar_email(me, "confirmar_email", para=novo, link=url_publica(f"/conta/email/confirmar/{token}"))
    db.session.commit()
    flash(f"Enviamos um link para {novo}. O e-mail só muda quando você abrir o link, que vale por 24 horas.", "ok")
    return redirect("/conta/seguranca")


@bp.route("/conta/email/confirmar/<token>")
def confirmar_email(token):
    """Vale uma vez: o link precisa bater com o e-mail pendente, que some na troca (CTA-03, CTA-04)."""
    dados = ler_link(token, "trocar-email", VALIDADE_EMAIL)
    user = db.session.get(User, dados.get("u")) if isinstance(dados, dict) else None
    if user is None or not user.email_pendente or user.email_pendente != dados.get("e"):
        return render_template("conta/email_confirmado.html", erro=LINK_INVALIDO), 400
    novo, antigo = user.email_pendente, user.email
    user.email_pendente = None
    if User.query.filter(User.email == novo, User.id != user.id).first():
        db.session.commit()
        return render_template("conta/email_confirmado.html", erro="Este e-mail passou a ser de outra conta."), 400
    user.email = novo
    try:
        db.session.flush()
    except IntegrityError:  # outra conta gravou o mesmo e-mail entre a checagem e o flush
        db.session.rollback()
        return render_template("conta/email_confirmado.html", erro="Este e-mail passou a ser de outra conta."), 400
    enviar_email(user, "email_alterado", para=antigo, quando=quando(), novo=mascarar(novo))
    registrar(user, "email")
    db.session.commit()
    return render_template("conta/email_confirmado.html", novo=novo)


# ---------- dois fatores (CTA-06, CTA-08..10) ----------
@bp.route("/conta/seguranca/2fa/ativar", methods=["POST"])
@login_required
def ativar_2fa():
    me = current_user()
    if dois_fatores(me):
        return redirect("/conta/seguranca")
    me.totp_segredo, me.totp_ultimo_passo = gerar_segredo(), None
    db.session.commit()
    return redirect("/conta/seguranca/2fa")


def tela_ativacao(me, erro=None, status=200):
    uri = uri_otpauth(me, me.totp_segredo)
    return sem_cache(render_template("conta/ativar_2fa.html", uri=uri, segredo=me.totp_segredo,
                                     qr=Markup(qr.svg(uri)), erro=erro, tem_tokens=tem_tokens(me)), status)


@bp.route("/conta/seguranca/2fa")
@login_required
def configurar_2fa():
    me = current_user()
    if dois_fatores(me) or not me.totp_segredo:  # ativado, o segredo não aparece de novo (CTA-06)
        return redirect("/conta/seguranca")
    return tela_ativacao(me)


@bp.route("/conta/seguranca/2fa/confirmar", methods=["POST"])
@login_required
def confirmar_2fa():
    me = current_user()
    if dois_fatores(me) or not me.totp_segredo:
        return redirect("/conta/seguranca")
    if not verificar_totp(me, request.form.get("codigo")):
        db.session.rollback()
        return tela_ativacao(me, "Código inválido. Confira o relógio do celular e tente de novo.", 400)
    me.totp_ativado_em = tempo.agora_utc()
    codigos = gerar_codigos(me)
    registrar(me, "2fa_ligado", revogar_pedido())
    db.session.commit()
    return sem_cache(render_template("conta/codigos.html", codigos=codigos, novo=True))


@bp.route("/conta/seguranca/2fa/codigos", methods=["POST"])
@login_required
def novos_codigos():
    me = current_user()
    if not dois_fatores(me):
        return redirect("/conta/seguranca")
    if erro := checar_senha(me, request.form.get("senha")):
        return pagina(me, {"2fa": erro[0]}, erro[1])
    codigos = gerar_codigos(me)
    registrar(me, "codigos", revogar_pedido())
    db.session.commit()
    return sem_cache(render_template("conta/codigos.html", codigos=codigos))


@bp.route("/conta/seguranca/2fa/desligar", methods=["POST"])
@login_required
def desligar_2fa():
    me = current_user()
    if not dois_fatores(me):
        return redirect("/conta/seguranca")
    if erro := checar_senha(me, request.form.get("senha")):
        return pagina(me, {"2fa": erro[0]}, erro[1])
    if not segundo_fator(me, request.form.get("codigo")):
        db.session.rollback()
        return pagina(me, {"2fa": "Código inválido."}, 400)
    me.totp_segredo = me.totp_ativado_em = me.totp_ultimo_passo = None
    CodigoRecuperacao.query.filter_by(user_id=me.id).delete()
    recriar_sessao(me, "2fa_desligado")
    flash("Dois fatores desligados.", "ok")
    return redirect("/conta/seguranca")


# ---------- login em dois passos (CTA-08) ----------
def passo_pendente():
    """O usuário do passo intermediário, ou None se ele não existe, venceu ou a conta mudou desde a senha."""
    p = session.get(SEGUNDO_PASSO)
    if not p or p.get("inst") != current_app.config["INSTANCIA"] or tempo.agora().timestamp() > p["ate"]:
        return None
    user = db.session.get(User, p["uid"])
    if user is None or user.session_version != p["sv"] or user.suspensa_em is not None or not dois_fatores(user):
        return None
    return user


def voltar_ao_login(mensagem):
    session.pop(SEGUNDO_PASSO, None)
    flash(mensagem, "erro")
    return redirect("/entrar")


@bp.route("/entrar/2fa", methods=["GET", "POST"])
def segundo_passo():
    user = passo_pendente()
    if user is None:
        return voltar_ao_login("Entre de novo com e-mail e senha.")
    if request.method == "GET":
        return render_template("conta/entrar_2fa.html")
    if falhas_recentes(user.email) >= MAX_FAILS:  # CAD-04 vale também no segundo passo
        session.pop(SEGUNDO_PASSO, None)
        flash("Muitas tentativas. Tente novamente em 15 minutos.", "erro")
        return render_template("auth/login.html", form={}), 429
    if segundo_fator(user, request.form.get("codigo")):
        destino = session[SEGUNDO_PASSO].get("next") or "/"
        LoginAttempt.query.filter_by(email=user.email).delete()  # só agora o contador zera (CAD-04)
        db.session.commit()
        start_session(user)
        return redirect(safe_next(destino))
    db.session.rollback()
    db.session.add(LoginAttempt(email=user.email))
    db.session.commit()
    pendente = dict(session[SEGUNDO_PASSO], erros=session[SEGUNDO_PASSO].get("erros", 0) + 1)
    if pendente["erros"] >= MAX_ERROS_CODIGO:
        return voltar_ao_login("E-mail, senha ou código incorretos.")
    session[SEGUNDO_PASSO] = pendente
    flash("Código inválido.", "erro")
    return render_template("conta/entrar_2fa.html"), 401
