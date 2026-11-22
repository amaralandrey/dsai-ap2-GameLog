"""E-mail (EML-01..12), sem rede: um SMTP falso no lugar de `smtplib.SMTP` e o relógio fixo."""
import smtplib
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import TemplateNotFound

from app import emails, jobs, tempo
from app.models import EmailSaida, Job, User, db

from conftest import make_user

SENHA = "segredo-do-smtp-123"


@pytest.fixture(autouse=True)
def relogio(monkeypatch):
    r = SimpleNamespace(momento=datetime(2026, 11, 16, 12, 0, tzinfo=tempo.FUSO))  # segunda-feira, meio-dia
    monkeypatch.setattr(tempo, "agora", lambda: r.momento)
    return r


@pytest.fixture(autouse=True)
def sem_smtp(monkeypatch):
    for nome in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_REMETENTE", "URL_PUBLICA"):
        monkeypatch.delenv(nome, raising=False)


class SMTPFalso:
    def __init__(self, registro, ssl, host, porta, timeout=None):
        self.registro, self.chamadas = registro, []
        registro.conexoes.append((host, porta, ssl, self.chamadas))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        self.chamadas.append("starttls")

    def login(self, usuario, senha):
        self.chamadas.append(("login", usuario, senha))

    def send_message(self, msg):
        if self.registro.erros:
            raise self.registro.erros.pop(0)
        self.registro.enviadas.append(msg)


@pytest.fixture
def smtp(monkeypatch):
    """Liga o backend SMTP com um servidor falso; `erros` são levantados em ordem no envio."""
    registro = SimpleNamespace(conexoes=[], enviadas=[], erros=[])
    monkeypatch.setenv("SMTP_HOST", "smtp.exemplo.com")
    monkeypatch.setenv("SMTP_USER", "gamelog")
    monkeypatch.setenv("SMTP_PASSWORD", SENHA)
    monkeypatch.setenv("EMAIL_REMETENTE", "GameLog <oi@exemplo.com>")
    monkeypatch.setattr(smtplib, "SMTP", lambda *a, **k: SMTPFalso(registro, False, *a, **k))
    monkeypatch.setattr(smtplib, "SMTP_SSL", lambda *a, **k: SMTPFalso(registro, True, *a, **k))
    return registro


def usuario(app, nome="ana", nascimento=None):
    uid = make_user(app, nome)
    if nascimento:
        with app.app_context():
            db.session.get(User, uid).nascimento = nascimento
            db.session.commit()
    return uid


def enviar(app, uid, modelo="teste", **ctx):
    """Enfileira pelo `enviar_email` e devolve o id da linha da caixa de saída."""
    with app.app_context():
        user = db.session.get(User, uid) if uid else None
        email = emails.enviar_email(user, modelo, **ctx)
        db.session.commit()
        return email.id


def rodar(app):
    with app.app_context():
        return jobs.ciclo()


def linha(app, eid):
    with app.app_context():
        return db.session.get(EmailSaida, eid)


def job_do(app, eid):
    with app.app_context():
        return Job.query.filter_by(chave_unica=f"email:{eid}").one()


# ---------- EML-01, EML-02 ----------
def test_eml_01_grava_pendente_e_enfileira_o_job(app):
    uid = usuario(app)
    eid = enviar(app, uid)
    e, j = linha(app, eid), job_do(app, eid)
    assert (e.user_id, e.para, e.modelo, e.status, e.tipo) == (uid, "ana@ex.com", "teste", "pendente", None)
    assert e.assunto == "Teste de envio do GameLog" and "configuração de envio" in e.corpo_texto
    assert "<html" in e.corpo_html and e.criado_em == tempo.agora_utc() and e.enviado_em is None
    assert (j.tipo, j.args, j.status) == ("enviar_email", f'{{"email_id": {eid}}}', "pendente")


def test_eml_01_para_substitui_o_endereco_e_dispensa_o_usuario(app):
    uid = usuario(app)
    assert linha(app, enviar(app, uid, para="novo@ex.com")).para == "novo@ex.com"
    sem_conta = linha(app, enviar(app, None, para="responsavel@ex.com"))
    assert (sem_conta.user_id, sem_conta.para) == (None, "responsavel@ex.com")
    with app.app_context(), pytest.raises(ValueError):
        emails.enviar_email(None, "teste")


def test_eml_01_executar_em_adia_o_job(app):
    uid = usuario(app)
    depois = tempo.agora_utc() + timedelta(hours=3)
    eid = enviar(app, uid, executar_em=depois)
    assert job_do(app, eid).executar_em == depois
    assert rodar(app)["executados"] == 0 and linha(app, eid).status == "pendente"


def test_eml_02_modelo_inexistente_falha_na_chamada(app):
    uid = usuario(app)
    with app.app_context():
        with pytest.raises(TemplateNotFound):
            emails.enviar_email(db.session.get(User, uid), "nao_existe")
        assert EmailSaida.query.count() == 0 and Job.query.count() == 0


def test_eml_02_html_sem_imagem_externa_nem_rastreamento(app):
    e = linha(app, enviar(app, usuario(app)))
    assert "<img" not in e.corpo_html and "style=" in e.corpo_html


# ---------- EML-03 ----------
def test_eml_03_console_sem_smtp_host(app, caplog):
    eid = enviar(app, usuario(app))
    assert rodar(app)["ok"] == 1
    e = linha(app, eid)
    assert e.status == "enviado" and e.enviado_em == tempo.agora_utc()
    assert "ana@ex.com" in caplog.text and "Teste de envio do GameLog" in caplog.text
    assert "configuração de envio" in caplog.text


def test_eml_03_smtp_com_starttls(app, smtp):
    eid = enviar(app, usuario(app))
    assert rodar(app)["ok"] == 1 and linha(app, eid).status == "enviado"
    (host, porta, ssl, chamadas), = smtp.conexoes
    assert (host, porta, ssl) == ("smtp.exemplo.com", 587, False)
    assert chamadas == ["starttls", ("login", "gamelog", SENHA)]
    msg, = smtp.enviadas
    assert msg.get_content_type() == "multipart/alternative"
    assert [p.get_content_type() for p in msg.iter_parts()] == ["text/plain", "text/html"]
    assert (msg["From"], msg["To"], msg["Subject"]) == ("GameLog <oi@exemplo.com>", "ana@ex.com",
                                                        "Teste de envio do GameLog")
    assert "List-Unsubscribe" not in msg  # transacional


def test_eml_03_smtp_ssl_na_465_e_sem_login(app, smtp, monkeypatch):
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.delenv("SMTP_USER")
    enviar(app, usuario(app))
    rodar(app)
    (_, porta, ssl, chamadas), = smtp.conexoes
    assert (porta, ssl, chamadas) == (465, True, [])
    assert len(smtp.enviadas) == 1


def test_eml_03_render_yaml_declara_as_variaveis_sem_valor():
    texto = (Path(__file__).resolve().parent.parent / "render.yaml").read_text()
    for nome in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_REMETENTE"):
        trecho = texto.split(f"- key: {nome}", 1)[1].split("- key:", 1)[0]
        assert "sync: false" in trecho, nome


# ---------- EML-04 ----------
def test_eml_04_falha_temporaria_tenta_de_novo(app, smtp, relogio):
    smtp.erros.append(smtplib.SMTPResponseException(451, b"tente mais tarde"))
    eid = enviar(app, usuario(app))
    assert rodar(app)["falhas"] == 1
    e, j = linha(app, eid), job_do(app, eid)
    assert e.status == "pendente" and "451" in e.erro
    assert j.status == "pendente" and j.tentativas == 1 and j.executar_em > tempo.agora_utc()
    relogio.momento += timedelta(minutes=1)
    assert rodar(app)["ok"] == 1 and linha(app, eid).status == "enviado" and len(smtp.enviadas) == 1


def test_eml_04_conexao_recusada_tambem_e_temporaria(app, smtp):
    smtp.erros.append(ConnectionRefusedError("sem conexão"))
    eid = enviar(app, usuario(app))
    rodar(app)
    assert linha(app, eid).status == "pendente" and job_do(app, eid).status == "pendente"


def test_eml_04_recusa_permanente_marca_falhou(app, smtp):
    smtp.erros.append(smtplib.SMTPRecipientsRefused({"ana@ex.com": (550, b"no such user")}))
    eid = enviar(app, usuario(app))
    assert rodar(app)["mortos"] == 1
    e = linha(app, eid)
    assert e.status == "falhou" and "550" in e.erro and job_do(app, eid).status == "morto"


def test_eml_04_senha_nunca_aparece_no_erro(app, smtp):
    smtp.erros.append(smtplib.SMTPAuthenticationError(535, f"bad credentials {SENHA}".encode()))
    eid = enviar(app, usuario(app))
    rodar(app)
    e, j = linha(app, eid), job_do(app, eid)
    assert e.status == "falhou" and SENHA not in e.erro and SENHA not in j.ultimo_erro and "***" in e.erro


def test_eml_04_enviado_nao_e_reenviado_por_job_repetido(app, smtp):
    eid = enviar(app, usuario(app))
    rodar(app)
    with app.app_context():
        Job.query.update({"status": "pendente", "executar_em": tempo.agora_utc()})
        db.session.commit()
    assert rodar(app)["ok"] == 1
    assert len(smtp.enviadas) == 1 and linha(app, eid).status == "enviado"
