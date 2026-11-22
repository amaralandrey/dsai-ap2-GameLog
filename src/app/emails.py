"""E-mail (EML-01..12): caixa de saída no banco, enviada pelo job `enviar_email`.

`enviar_email()` é o único jeito de mandar e-mail (GLOBAL-14): renderiza o modelo na hora, grava a linha
`pendente` e enfileira o job; a rota nunca espera o SMTP. Sem `SMTP_HOST`, o backend de console escreve no log.
"""
import os
import smtplib
from datetime import timedelta, timezone
from email.message import EmailMessage

from flask import Blueprint, current_app, has_request_context, request
from itsdangerous import BadSignature, URLSafeSerializer

from . import tempo
from .jobs import ErroPermanente, enfileirar, tarefa
from .menores.idade import ADULTO, VINCULADO, faixa_etaria
from .menores.politica import pode
from .models import EmailSaida, db
from .notificacoes import TIPOS, preferencia

bp = Blueprint("emails", __name__, cli_group="email")

RESUMO = "resumo"  # tipo do resumo semanal no descadastro e na caixa de saída
# modelos não transacionais e o tipo de preferência de cada um; None: o tipo vem de ctx["tipo"] (EML-05)
NAO_TRANSACIONAIS = {"notificacao": None, "oferta": None, "resumo_semanal": RESUMO}
CONSENTIMENTO_DO_TIPO = {RESUMO: "email_resumo", "oferta": "ofertas", "preco_alvo": "ofertas"}  # CNS, OFR-07
SEM_DESTINO = "conta_excluida"  # vai para `para` mesmo com a conta excluída, que é mascarado depois (DIR-08)
TIMEOUT_SMTP = 20
MAX_ERRO = 500
NOITE_INICIO, NOITE_FIM = 22, 7  # PMN-07


# ---------- ganchos das specs que ainda não estão na main ----------
def tem_consentimento(user, finalidade):
    """CNS-06: troque por `consentimentos.tem_consentimento` quando CNS entrar.

    Enquanto isso, ninguém consentiu: o resumo semanal e as ofertas por e-mail ficam desligados (EML-08).
    """
    return False


def revogar_consentimento(user, finalidade):
    """CNS-01: o descadastro grava a revogação com `origem="descadastro"` (EML-07); sem CNS, não há o que gravar."""


def conta_excluida(user):
    """DIR: troque pela marca de conta excluída ou anonimizada quando DIR entrar; hoje só a linha apagada conta."""
    return user is None


def email_engajamento_liberado(user):
    """PMN-08: de 16 a 17 anos o próprio usuário liga; de 12 a 15, só o responsável.

    Sem RSP não há responsável que ligue (RSP-07 é o ponto de extensão), então de 12 a 15 fica desligado.
    """
    return faixa_etaria(user) != VINCULADO and pode(user, "email_engajamento")


# ---------- renderização e enfileiramento (EML-01, EML-02) ----------
def url_publica(caminho):
    base = os.environ.get("URL_PUBLICA") or (request.host_url if has_request_context() else "http://localhost:5000")
    return base.rstrip("/") + caminho


def mascarar(endereco):
    """`j***@exemplo.com` (EML-10)."""
    nome, arroba, dominio = (endereco or "").partition("@")
    return f"{nome[:1]}***{arroba}{dominio}"


def renderizar(modelo, **ctx):
    """(assunto, texto, html); o assunto é o bloco `assunto` do .txt, e o .html escapa o conteúdo (autoescape)."""
    env = current_app.jinja_env
    texto = env.get_template(f"email/{modelo}.txt")  # modelo inexistente levanta TemplateNotFound aqui
    html = env.get_template(f"email/{modelo}.html")
    assunto = " ".join("".join(texto.blocks["assunto"](texto.new_context(ctx))).split())
    return assunto, texto.render(ctx), html.render(ctx, assunto=assunto)


def tipo_do_email(modelo, ctx):
    if modelo not in NAO_TRANSACIONAIS:
        return None
    return NAO_TRANSACIONAIS[modelo] or ctx["tipo"]


def adiar_noite(user, quando):
    """E-mail não transacional de menor gerado entre 22h e 7h sai às 7h de Belém (PMN-07)."""
    if user is None or faixa_etaria(user) == ADULTO:
        return quando
    local = tempo.para_belem(quando)
    if NOITE_FIM <= local.hour < NOITE_INICIO:
        return quando
    sete = local.replace(hour=NOITE_FIM, minute=0, second=0, microsecond=0)
    if local.hour >= NOITE_INICIO:
        sete += timedelta(days=1)
    return sete.astimezone(timezone.utc).replace(tzinfo=None)


def enviar_email(user, modelo, para=None, executar_em=None, **ctx):
    """Grava o e-mail `pendente` e enfileira o job; devolve a linha. Quem chama faz o commit (EML-01)."""
    if user is None and not para:
        raise ValueError("e-mail sem destinatário")
    tipo = tipo_do_email(modelo, ctx)
    descadastro = link_descadastro(user.id, tipo) if tipo else None
    assunto, texto, html = renderizar(modelo, user=user, link_descadastro=descadastro, url_publica=url_publica, **ctx)
    agora = tempo.agora_utc()
    email = EmailSaida(user_id=user.id if user else None, para=para or user.email, modelo=modelo, tipo=tipo,
                       assunto=assunto[:200], corpo_texto=texto, corpo_html=html, criado_em=agora)
    db.session.add(email)
    db.session.flush()
    quando = executar_em or agora
    if tipo:
        quando = adiar_noite(user, quando)
    enfileirar("enviar_email", executar_em=quando, chave_unica=f"email:{email.id}", email_id=email.id)
    return email


def token_descadastro(user_id, tipo):
    """Assinado com SECRET_KEY e sal próprio, sem expirar (EML-07)."""
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt="descadastro").dumps({"u": user_id, "t": tipo})


def link_descadastro(user_id, tipo):
    return url_publica(f"/email/descadastro/{token_descadastro(user_id, tipo)}")


def ler_token(token):
    """(user_id, tipo), ou None quando a assinatura não confere."""
    try:
        dados = URLSafeSerializer(current_app.config["SECRET_KEY"], salt="descadastro").loads(token)
        return int(dados["u"]), str(dados["t"])
    except (BadSignature, KeyError, TypeError, ValueError):
        return None


# ---------- envio (EML-03..05) ----------
def backend():
    return "smtp" if os.environ.get("SMTP_HOST") else "console"


def mensagem(email):
    """`multipart/alternative` com texto e HTML; o não transacional leva `List-Unsubscribe` (EML-06)."""
    msg = EmailMessage()
    msg["Subject"], msg["To"] = email.assunto, email.para
    msg["From"] = os.environ.get("EMAIL_REMETENTE") or "GameLog <nao-responda@gamelog.local>"
    if email.tipo:
        msg["List-Unsubscribe"] = f"<{link_descadastro(email.user_id, email.tipo)}>"
    msg.set_content(email.corpo_texto)
    msg.add_alternative(email.corpo_html, subtype="html")
    return msg


def entregar_smtp(msg):
    """SMTP_SSL na porta 465; nas outras, SMTP com starttls (EML-03)."""
    host, porta = os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT") or 587)
    classe = smtplib.SMTP_SSL if porta == 465 else smtplib.SMTP
    with classe(host, porta, timeout=TIMEOUT_SMTP) as smtp:
        if porta != 465:
            smtp.starttls()
        if os.environ.get("SMTP_USER"):
            smtp.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        smtp.send_message(msg)


def entregar_console(msg):
    current_app.logger.warning("e-mail (console) para=%s assunto=%s\n%s", msg["To"], msg["Subject"],
                               msg.get_body(("plain",)).get_content())


def codigo_smtp(exc):
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return max((codigo for codigo, _ in exc.recipients.values()), default=None)
    return getattr(exc, "smtp_code", None)


def descrever(exc):
    """Tipo, código e mensagem, sem a senha do SMTP (GLOBAL-16)."""
    texto = f"{type(exc).__name__}: {exc}"
    senha = os.environ.get("SMTP_PASSWORD")
    return (texto.replace(senha, "***") if senha else texto)[:MAX_ERRO]


def motivo_supressao(email):
    """Por que o e-mail não pode mais sair, conferido na hora do envio (EML-05); None se pode."""
    if email.modelo == SEM_DESTINO:
        return None
    if email.user_id is not None and conta_excluida(email.user):
        return "conta excluída"
    if email.tipo is None:  # transacional: ignora as preferências
        return None
    if email.tipo in TIPOS and not preferencia(email.user_id, email.tipo)[1]:
        return "preferência por e-mail desligada"
    finalidade = CONSENTIMENTO_DO_TIPO.get(email.tipo)
    if finalidade and not tem_consentimento(email.user, finalidade):
        return f"sem consentimento {finalidade}"
    if not email_engajamento_liberado(email.user):
        return "política de menores"
    return None


@tarefa("enviar_email")
def processar(email_id):
    """Envia um e-mail `pendente`; um já enviado (ou suprimido) não sai de novo num job repetido (EML-04)."""
    email = db.session.get(EmailSaida, email_id)
    if email is None or email.status != "pendente":
        return
    if motivo := motivo_supressao(email):
        email.status, email.erro = "suprimido", motivo
        return
    try:
        (entregar_smtp if backend() == "smtp" else entregar_console)(mensagem(email))
    except (smtplib.SMTPException, OSError) as exc:
        codigo, email.erro = codigo_smtp(exc), descrever(exc)
        permanente = codigo is not None and 500 <= codigo < 600
        if permanente:
            email.status = "falhou"
        db.session.commit()  # o erro fica gravado mesmo com o rollback do job (JOB-06)
        if permanente:
            raise ErroPermanente(email.erro) from None
        raise RuntimeError(email.erro) from None  # 4xx, conexão ou timeout: nova tentativa com espera (JOB-06)
    email.status, email.enviado_em, email.erro = "enviado", tempo.agora_utc(), None
    if email.modelo == SEM_DESTINO:
        email.para = mascarar(email.para)  # o GameLog não guarda o endereço depois do envio (DIR-08)
    db.session.commit()  # o enviado fica gravado antes do fim do job, que então nunca o reenvia
