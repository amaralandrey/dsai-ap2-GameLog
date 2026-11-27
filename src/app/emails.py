"""E-mail (EML-01..12): caixa de saída no banco, enviada pelo job `enviar_email`.

`enviar_email()` é o único jeito de mandar e-mail (GLOBAL-14): renderiza o modelo na hora, grava a linha
`pendente` e enfileira o job; a rota nunca espera o SMTP. Sem `SMTP_HOST`, o backend de console escreve no log.
"""
import os
import re
import smtplib
from datetime import timedelta, timezone
from email.message import EmailMessage

import click
from flask import Blueprint, current_app, flash, redirect, render_template, request
from itsdangerous import BadSignature, URLSafeSerializer

from . import tempo
from .admin import audit
from .diario import formatar_minutos
from .jobs import ErroPermanente, agenda_padrao, enfileirar, executar, tarefa
from .menores.idade import ADULTO, VINCULADO, faixa_etaria
from .menores.politica import pode
from .models import EMAIL_STATUS, Activity, EmailSaida, Job, PreferenciaNotificacao, User, db
from .notificacoes import SEM_ENGAJAMENTO, TIPOS, preferencia
from .privacidade.anonimizar import anonimizada
from .privacidade.consentimentos import revogar, tem_consentimento
from .social import feed_query
from .util import admin_required, paginate

bp = Blueprint("emails", __name__, cli_group="email")

RESUMO = "resumo"  # tipo do resumo semanal no descadastro e na caixa de saída
# modelos não transacionais e o tipo de preferência de cada um; None: o tipo vem de ctx["tipo"] (EML-05)
NAO_TRANSACIONAIS = {"notificacao": None, "oferta": None, "resumo_semanal": RESUMO}
CONSENTIMENTO_DO_TIPO = {RESUMO: "email_resumo", "oferta": "ofertas", "preco_alvo": "ofertas"}  # CNS, OFR-07
SEM_DESTINO = "conta_excluida"  # vai para `para` mesmo com a conta excluída, que é mascarado depois (DIR-08)
TIMEOUT_SMTP = 20
MAX_ERRO = 500
ENDERECO = re.compile(r"[^\s<>'\",;:()\[\]{}]+@[^\s<>'\",;:()\[\]{}]+")
NOITE_INICIO, NOITE_FIM = 22, 7  # PMN-07
MAX_ITENS_RESUMO = 10
CORPO_OCULTO = ("recuperar_senha",)  # o corpo leva link de acesso à conta: some depois do envio (RSN-09)
CORPO_REMOVIDO = "[link removido após o envio]"


def conta_excluida(user):
    """Linha apagada ou conta anonimizada (DIR, RTN-06): nenhum e-mail sai para ela (EML-05)."""
    return user is None or anonimizada(user)


def pode_ligar_email(user, tipo=RESUMO):
    """PMN-08: e-mail de engajamento começa desligado; de 16 a 17 anos o próprio usuário liga, de 12 a 15 não: lá o
    resumo depende do consentimento `email_resumo` dado pelo responsável (CNS-09, RSP-07).
    """
    return tipo in SEM_ENGAJAMENTO or faixa_etaria(user) != VINCULADO


def email_engajamento_liberado(user, tipo=RESUMO):
    """Na hora do envio: a mesma regra de PMN-08 e a política de menores (PMN-01); o resumo de 12 a 15 anos vale
    pelo consentimento dado pelo responsável, que quem chama confere."""
    liberado = pode_ligar_email(user, tipo) or tipo == RESUMO
    return tipo in SEM_ENGAJAMENTO or (liberado and pode(user, "email_engajamento"))


# ---------- renderização e enfileiramento (EML-01, EML-02) ----------
def url_publica(caminho):
    base = os.environ.get("URL_PUBLICA") or "http://localhost:5000"
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
    enfileirar_envio(email, adiar_noite(user, executar_em or agora) if tipo else executar_em)
    return email


def enfileirar_envio(email, executar_em=None):
    db.session.add(email)
    db.session.flush()
    enfileirar("enviar_email", executar_em=executar_em, chave_unica=f"email:{email.id}", email_id=email.id)


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
    if senha:
        texto = texto.replace(senha, "***")
    return ENDERECO.sub(lambda m: mascarar(m.group()), texto)[:MAX_ERRO]


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
    if not email_engajamento_liberado(email.user, email.tipo):
        return "política de menores"
    return None


def remover_corpo(email):
    if email.modelo in CORPO_OCULTO:
        email.corpo_texto = email.corpo_html = CORPO_REMOVIDO


@tarefa("enviar_email")
def processar(email_id):
    """Envia um e-mail `pendente`; um já enviado (ou suprimido) não sai de novo num job repetido (EML-04)."""
    email = db.session.get(EmailSaida, email_id)
    if email is None or email.status != "pendente":
        return
    if motivo := motivo_supressao(email):
        email.status, email.erro = "suprimido", motivo
        remover_corpo(email)
        return
    try:
        (entregar_smtp if backend() == "smtp" else entregar_console)(mensagem(email))
    except (smtplib.SMTPException, OSError) as exc:
        codigo, email.erro = codigo_smtp(exc), descrever(exc)
        permanente = codigo is not None and 500 <= codigo < 600
        if permanente:
            email.status = "falhou"
            remover_corpo(email)
            if email.modelo == SEM_DESTINO:
                email.para = mascarar(email.para)
        db.session.commit()  # o erro fica gravado mesmo com o rollback do job (JOB-06)
        if permanente:
            raise ErroPermanente(email.erro) from None
        raise RuntimeError(email.erro) from None  # 4xx, conexão ou timeout: nova tentativa com espera (JOB-06)
    email.status, email.enviado_em, email.erro = "enviado", tempo.agora_utc(), None
    remover_corpo(email)
    if email.modelo == SEM_DESTINO:
        email.para = mascarar(email.para)  # o GameLog não guarda o endereço depois do envio (DIR-08)
    db.session.commit()  # o enviado fica gravado antes do fim do job, que então nunca o reenvia


# ---------- notificações e descadastro (EML-06, EML-07) ----------
def avisar_por_email(notificacao):
    """O modelo `notificacao` para quem quer aquele tipo por e-mail; chamada por `notificar` (NTF-11)."""
    return enviar_email(db.session.get(User, notificacao.user_id), "notificacao", tipo=notificacao.tipo,
                        rotulo=TIPOS[notificacao.tipo], notificacao=notificacao)


@bp.route("/email/descadastro/<token>", methods=["GET", "POST"])
def descadastro(token):
    """Sem login: o GET confirma, e o POST desliga aquele tipo (EML-07)."""
    lido = ler_token(token)
    user = db.session.get(User, lido[0]) if lido else None
    if user is None or lido[1] not in (RESUMO, *TIPOS):  # sem dizer qual usuário seria
        return render_template("conta/descadastro.html", invalido=True), 400
    tipo = lido[1]
    rotulo = "Resumo semanal" if tipo == RESUMO else TIPOS[tipo]
    if request.method == "GET":
        return render_template("conta/descadastro.html", rotulo=rotulo)
    if tipo == RESUMO:
        revogar(user, "email_resumo", "descadastro")
    else:
        p = PreferenciaNotificacao.query.filter_by(user_id=user.id, tipo=tipo).first()
        p = p or PreferenciaNotificacao(user_id=user.id, tipo=tipo, site=True)
        p.email = False
        db.session.add(p)
    db.session.commit()
    return render_template("conta/descadastro.html", rotulo=rotulo, feito=True)


# ---------- resumo semanal (EML-08, EML-09) ----------
def frase(a):
    """(texto, caminho) de um item do feed, para os corpos em texto e HTML do resumo."""
    jogo, lista = a.game.title if a.game else "", a.list.name if a.list else ""
    textos = {
        "avaliou": f"avaliou {jogo}" + (f" com nota {a.review.score}" if a.review else ""),
        "jogado": f"marcou {jogo} como jogado",
        "lista": f"criou a lista {lista}",
        "lista_jogo": f"adicionou {jogo} à lista {lista}",
        "sessao": f"jogou {jogo} por {formatar_minutos(a.valor or 0)}",
        "importacao": "importou sessões para o diário",
        "conquista": f"desbloqueou a conquista {a.texto}",
    }
    caminho = f"/jogos/{a.game.slug}" if a.game else f"/listas/{a.list_id}" if a.list else f"/u/{a.user.username}"
    return f"{a.user.display_name} {textos.get(a.kind, a.texto)}", caminho


def semana_anterior(semana):
    """Instantes UTC do começo e do fim da semana ISO anterior à `semana` (GLOBAL-09)."""
    segunda = tempo.segunda_da_semana(semana)
    return tempo.inicio_do_dia_utc(segunda - timedelta(days=7)), tempo.inicio_do_dia_utc(segunda)


def itens_do_resumo(user, semana):
    """Até 10 itens da semana anterior pelas mesmas regras do feed: privado, bloqueio, silêncio (SOC-06/07)."""
    de, ate = semana_anterior(semana)
    return (feed_query(user.id, user).filter(Activity.created_at >= de, Activity.created_at < ate)
            .limit(MAX_ITENS_RESUMO).all())


def elegivel_ao_resumo(user):
    """Consentimento `email_resumo` (CNS-06) e PMN-08 (EML-08)."""
    return not conta_excluida(user) and tem_consentimento(user, "email_resumo") and email_engajamento_liberado(user)


@tarefa("email_resumo_semanal")
def resumo_semanal():
    """Um job por usuário elegível; a chave impede dois resumos na mesma semana (EML-08, JOB-02)."""
    semana = tempo.semana_iso(tempo.hoje())
    for user in User.query.filter(User.suspensa_em.is_(None)).order_by(User.id):
        if elegivel_ao_resumo(user):
            enfileirar("email_resumo", chave_unica=f"resumo:{user.id}:{semana}", user_id=user.id, semana=semana)


@tarefa("email_resumo")
def resumo_do_usuario(user_id, semana):
    """Monta e enfileira o resumo; sem item nenhum, não grava nada na caixa de saída (EML-09).

    A seção de ofertas da wishlist entra com OFR: ela depende do consentimento `ofertas` (OFR-07) e de
    `pode(user, "ver_ofertas")` (PMN).
    """
    user = db.session.get(User, user_id)
    if user is None or not elegivel_ao_resumo(user):
        return
    ja_enviado = EmailSaida.query.filter(  # o job já concluído não segura a chave: confere a caixa da semana
        EmailSaida.user_id == user_id, EmailSaida.modelo == "resumo_semanal",
        EmailSaida.criado_em >= tempo.inicio_do_dia_utc(tempo.segunda_da_semana(semana))).first()
    itens = [] if ja_enviado else itens_do_resumo(user, semana)
    if itens:
        enviar_email(user, "resumo_semanal", semana=semana, itens=[frase(a) for a in itens])


agenda_padrao("email_resumo_semanal", "0 8 * * 1", "email_resumo_semanal")  # segunda às 08:00 de Belém


# ---------- caixa de saída do admin (EML-10) ----------
@bp.route("/admin/emails")
@admin_required
def caixa():
    status, modelo = request.args.get("status", ""), request.args.get("modelo", "")
    q = EmailSaida.query
    if status in EMAIL_STATUS:
        q = q.filter(EmailSaida.status == status)
    if modelo:
        q = q.filter(EmailSaida.modelo == modelo)
    page = paginate(q.order_by(EmailSaida.criado_em.desc(), EmailSaida.id.desc()), request.args.get("page"))
    modelos = sorted(m for (m,) in db.session.query(EmailSaida.modelo).distinct())
    return render_template("admin/emails.html", page=page, status=status, modelo=modelo, todos_status=EMAIL_STATUS,
                           modelos=modelos, mascarar=mascarar, belem=tempo.para_belem)


@bp.route("/admin/emails/<int:eid>")
@admin_required
def ver(eid):
    return render_template("admin/email.html", e=db.get_or_404(EmailSaida, eid), mascarar=mascarar,
                           belem=tempo.para_belem, oculto=CORPO_OCULTO)


@bp.route("/admin/emails/<int:eid>/reenviar", methods=["POST"])
@admin_required
def reenviar(eid):
    """Um e-mail que falhou volta como linha nova, com o mesmo corpo (EML-10, GLOBAL-17)."""
    e = db.get_or_404(EmailSaida, eid)
    if e.status != "falhou" or e.modelo in CORPO_OCULTO:  # o link removido não serve: a pessoa pede outro (RSN-09)
        flash("Só um e-mail que falhou pode ser reenviado.", "erro")
        return redirect(f"/admin/emails/{e.id}")
    novo = EmailSaida(user_id=e.user_id, para=e.para, modelo=e.modelo, tipo=e.tipo, assunto=e.assunto,
                      corpo_texto=e.corpo_texto, corpo_html=e.corpo_html, criado_em=tempo.agora_utc())
    enfileirar_envio(novo, adiar_noite(e.user, novo.criado_em) if novo.tipo else None)
    audit("email.reenviar", f"e-mail {e.id} reenviado como {novo.id} ({e.modelo})")
    db.session.commit()
    flash(f"E-mail reenviado como #{novo.id}.", "ok")
    return redirect("/admin/emails")


# ---------- comando (EML-11) ----------
@bp.cli.command("teste")
@click.option("--para", required=True, help="endereço que recebe o e-mail de teste")
def teste_cmd(para):
    """Envia o modelo `teste` pelo backend configurado, sem mostrar a senha."""
    email = enviar_email(None, "teste", para=para)
    db.session.commit()
    executar(Job.query.filter_by(chave_unica=f"email:{email.id}").one().id)
    email = db.session.get(EmailSaida, email.id)
    click.echo(f"backend={backend()} status={email.status}")
    if email.erro:
        click.echo(f"erro={email.erro}")
