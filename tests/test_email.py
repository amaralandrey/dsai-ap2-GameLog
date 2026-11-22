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


# ---------- EML-05, EML-06: notificações, supressão e transacionais ----------
def ligar_email(app, uid, tipo, ligado=True):
    from app.models import PreferenciaNotificacao

    with app.app_context():
        p = PreferenciaNotificacao.query.filter_by(user_id=uid, tipo=tipo).first()
        p = p or PreferenciaNotificacao(user_id=uid, tipo=tipo)
        p.email = ligado
        db.session.add(p)
        db.session.commit()


def notificar(app, uid, tipo="seguidor", **ctx):
    from app.notificacoes import notificar as criar

    with app.app_context():
        n = criar(uid, tipo, **ctx)
        db.session.commit()
        return n is not None


def caixa(app, **filtros):
    with app.app_context():
        return EmailSaida.query.filter_by(**filtros).order_by(EmailSaida.id).all()


def test_eml_06_notificar_enfileira_o_modelo_notificacao(app):
    ana, bia = usuario(app), usuario(app, "bia")
    assert notificar(app, ana, ator_id=bia) and caixa(app) == []  # "por e-mail" começa desligado (NTF-09)
    ligar_email(app, ana, "seguidor")
    assert notificar(app, ana, ator_id=bia)
    e, = caixa(app)
    assert (e.user_id, e.modelo, e.tipo) == (ana, "notificacao", "seguidor")
    assert e.assunto == "Nova notificação no GameLog: Novos seguidores" and "@bia" in e.corpo_texto


def test_eml_02_html_escapa_o_conteudo_do_usuario(app):
    ana, bia = usuario(app), usuario(app, "bia")
    with app.app_context():
        db.session.get(User, bia).display_name = "<script>alert(1)</script>"
        db.session.commit()
    ligar_email(app, ana, "seguidor")
    notificar(app, ana, ator_id=bia)
    e, = caixa(app)
    assert "<script>" not in e.corpo_html and "&lt;script&gt;alert(1)&lt;/script&gt;" in e.corpo_html
    assert "<script>alert(1)</script>" in e.corpo_texto  # texto puro não é HTML


def test_eml_06_rodape_e_list_unsubscribe_com_o_mesmo_link(app, smtp, monkeypatch):
    monkeypatch.setenv("URL_PUBLICA", "https://gamelog.exemplo")
    ana, bia = usuario(app), usuario(app, "bia")
    ligar_email(app, ana, "seguidor")
    notificar(app, ana, ator_id=bia)
    rodar(app)
    msg, = smtp.enviadas
    with app.app_context():
        link = emails.link_descadastro(ana, "seguidor")
    assert link.startswith("https://gamelog.exemplo/email/descadastro/")
    assert msg["List-Unsubscribe"] == f"<{link}>"
    e, = caixa(app)
    assert link in e.corpo_texto and link in e.corpo_html


def test_eml_05_supressao_por_preferencia_desligada_depois(app, smtp):
    ana, bia = usuario(app), usuario(app, "bia")
    ligar_email(app, ana, "seguidor")
    notificar(app, ana, ator_id=bia)
    ligar_email(app, ana, "seguidor", ligado=False)
    assert rodar(app)["ok"] == 1
    e, = caixa(app)
    assert (e.status, e.erro) == ("suprimido", "preferência por e-mail desligada") and smtp.enviadas == []


def oferta(app, uid):
    return enviar(app, uid, "notificacao", tipo="oferta", rotulo="Ofertas da wishlist",
                  notificacao=SimpleNamespace(ator=None, texto="", alvo_url=""))


def test_eml_05_supressao_por_consentimento(app, smtp, monkeypatch):
    ana = usuario(app)
    ligar_email(app, ana, "oferta")
    eid = oferta(app, ana)
    rodar(app)
    assert (linha(app, eid).status, linha(app, eid).erro) == ("suprimido", "sem consentimento ofertas")
    monkeypatch.setattr(emails, "tem_consentimento", lambda user, finalidade: finalidade == "ofertas")
    eid = oferta(app, ana)
    rodar(app)
    assert linha(app, eid).status == "enviado" and len(smtp.enviadas) == 1


def test_eml_05_supressao_por_conta_excluida(app, smtp, monkeypatch):
    eid = enviar(app, usuario(app))
    monkeypatch.setattr(emails, "conta_excluida", lambda user: True)
    rodar(app)
    assert (linha(app, eid).status, linha(app, eid).erro) == ("suprimido", "conta excluída") and smtp.enviadas == []


def test_eml_05_transacional_ignora_a_preferencia(app, smtp):
    from app.notificacoes import TIPOS

    ana = usuario(app)
    for tipo in TIPOS:
        ligar_email(app, ana, tipo, ligado=False)
    eid = enviar(app, ana)
    rodar(app)
    assert linha(app, eid).status == "enviado" and len(smtp.enviadas) == 1


def test_eml_05_confirmacao_de_exclusao_vai_ao_endereco_e_e_mascarada(app, smtp, monkeypatch):
    monkeypatch.setattr(emails, "conta_excluida", lambda user: True)
    eid = enviar(app, usuario(app), "conta_excluida", para="velho@ex.com")
    rodar(app)
    e = linha(app, eid)
    assert (e.status, e.para) == ("enviado", "v***@ex.com") and smtp.enviadas[0]["To"] == "velho@ex.com"


# ---------- EML-07 ----------
def token(app, uid, tipo, sal="descadastro"):
    from itsdangerous import URLSafeSerializer

    return URLSafeSerializer(app.config["SECRET_KEY"], salt=sal).dumps({"u": uid, "t": tipo})


def test_eml_07_descadastro_sem_login_desliga_o_tipo(app, client):
    from app.notificacoes import preferencia

    ana = usuario(app)
    ligar_email(app, ana, "seguidor")
    url = f"/email/descadastro/{token(app, ana, 'seguidor')}"
    r = client.get(url)
    assert r.status_code == 200 and "Novos seguidores" in r.get_data(as_text=True)
    assert "ana" not in r.get_data(as_text=True)
    assert client.post(url).status_code == 200
    with app.app_context():
        assert preferencia(ana, "seguidor") == (True, False)


def test_eml_07_descadastro_do_resumo_revoga_o_consentimento(app, client, monkeypatch):
    revogados = []
    monkeypatch.setattr(emails, "revogar_consentimento", lambda user, fin: revogados.append((user.id, fin)))
    ana = usuario(app)
    assert client.post(f"/email/descadastro/{token(app, ana, 'resumo')}").status_code == 200
    assert revogados == [(ana, "email_resumo")]


@pytest.mark.parametrize("estrago", ["adulterado", "outro_sal", "tipo_desconhecido", "usuario_inexistente"])
def test_eml_07_token_invalido_responde_400(app, client, estrago):
    ana = usuario(app)
    t = {"adulterado": token(app, ana, "seguidor")[:-2] + "xx", "outro_sal": token(app, ana, "seguidor", "senha"),
         "tipo_desconhecido": token(app, ana, "marketing"), "usuario_inexistente": token(app, 999, "seguidor")}[estrago]
    for metodo in (client.get, client.post):
        r = metodo(f"/email/descadastro/{t}")
        assert r.status_code == 400 and "Link inválido" in r.get_data(as_text=True)


# ---------- PMN-07, PMN-08 (a parte de e-mail da política de menores) ----------
ADOLESCENTE, VINCULADO = date(2009, 6, 1), date(2012, 6, 1)  # 17 e 14 anos em 2026-11-16


@pytest.mark.parametrize("hora,nascimento,esperado", [
    (23, ADOLESCENTE, datetime(2026, 11, 17, 7, 0)),
    (3, ADOLESCENTE, datetime(2026, 11, 16, 7, 0)),
    (22, ADOLESCENTE, datetime(2026, 11, 17, 7, 0)),
    (7, ADOLESCENTE, None),
    (23, None, None),  # adulto
])
def test_pmn_07_email_de_menor_a_noite_sai_as_7h(app, relogio, hora, nascimento, esperado):
    relogio.momento = relogio.momento.replace(hour=hora)
    ana, bia = usuario(app, nascimento=nascimento), usuario(app, "bia")
    ligar_email(app, ana, "seguidor")
    notificar(app, ana, ator_id=bia)
    e, = caixa(app)
    quando = job_do(app, e.id).executar_em
    if esperado is None:
        assert quando == tempo.agora_utc()
    else:
        assert tempo.para_belem(quando).replace(tzinfo=None) == esperado


def test_pmn_07_transacional_de_menor_sai_na_hora(app, relogio):
    relogio.momento = relogio.momento.replace(hour=23)
    eid = enviar(app, usuario(app, nascimento=ADOLESCENTE))
    assert job_do(app, eid).executar_em == tempo.agora_utc()


def salvar_preferencias(app, client, nome):
    from app.notificacoes import preferencia
    from conftest import login

    login(client, nome)
    client.post("/notificacoes/preferencias", data={"site_seguidor": "on", "email_seguidor": "on"})
    with app.app_context():
        return preferencia(User.query.filter_by(username=nome).one().id, "seguidor")[1]


def test_pmn_08_email_de_engajamento_comeca_desligado_para_menor(app):
    from app.notificacoes import preferencia

    teo = usuario(app, "teo", nascimento=ADOLESCENTE)
    with app.app_context():
        assert preferencia(teo, "seguidor")[1] is False


def test_pmn_08_adolescente_liga_o_email_sozinho(app, client):
    usuario(app, "teo", nascimento=ADOLESCENTE)
    assert salvar_preferencias(app, client, "teo") is True


def test_pmn_08_de_12_a_15_so_o_responsavel_liga(app, client):
    usuario(app, "lia", nascimento=VINCULADO)
    assert salvar_preferencias(app, client, "lia") is False
    assert "só o responsável" in client.get("/notificacoes/preferencias").get_data(as_text=True)


def test_pmn_08_de_12_a_15_email_de_engajamento_suprimido_no_envio(app, smtp):
    lia, bia = usuario(app, "lia", nascimento=VINCULADO), usuario(app, "bia")
    ligar_email(app, lia, "seguidor")  # gravado antes, por exemplo antes de a idade ser contestada
    notificar(app, lia, ator_id=bia)
    rodar(app)
    e, = caixa(app)
    assert (e.status, e.erro) == ("suprimido", "política de menores") and smtp.enviadas == []
