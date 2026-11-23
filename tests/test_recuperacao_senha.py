"""Recuperação de senha (RSN-01..10), sem rede, com o relógio fixo e a caixa de saída de EML."""
import re
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app import conta, jobs, recuperacao, tempo
from app.models import EmailSaida, EventoSeguranca, LoginAttempt, Notificacao, User, db

from conftest import login, make_user

RESPOSTA = "Se houver uma conta com esse e-mail, enviamos um link para redefinir a senha. Ele vale por 1 hora."


@pytest.fixture(autouse=True)
def relogio(monkeypatch):
    r = SimpleNamespace(momento=datetime(2026, 11, 16, 12, 0, 5, tzinfo=tempo.FUSO))
    monkeypatch.setattr(tempo, "agora", lambda: r.momento)
    r.avancar = lambda **kw: setattr(r, "momento", r.momento + timedelta(**kw))
    return r


@pytest.fixture(autouse=True)
def sem_smtp(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("URL_PUBLICA", raising=False)


def pedir(client, email="ana@ex.com"):
    return client.post("/senha/recuperar", data={"email": email})


def recuperacoes(app, uid=None):
    with app.app_context():
        q = EmailSaida.query.filter_by(modelo="recuperar_senha")
        return q.filter_by(user_id=uid).count() if uid else q.count()


def link(app):
    with app.app_context():
        e = EmailSaida.query.filter_by(modelo="recuperar_senha").order_by(EmailSaida.id.desc()).first()
        return re.search(r"/senha/redefinir/\S+", e.corpo_texto).group()


def mudar(app, uid, **campos):
    with app.app_context():
        u = db.session.get(User, uid)
        for k, v in campos.items():
            setattr(u, k, v)
        db.session.commit()


def ler(app, uid):
    with app.app_context():
        return db.session.get(User, uid)


def redefinir(client, caminho, nova="novasenha99", confirmacao=None):
    return client.post(caminho, data={"nova": nova, "confirmacao": confirmacao or nova})


# ---------- RSN-01 ----------
def test_rsn01_login_leva_a_pagina_que_pede_o_email(client):
    assert 'href="/senha/recuperar">Esqueci minha senha' in client.get("/entrar").get_data(as_text=True)
    r = client.get("/senha/recuperar")
    assert r.status_code == 200 and 'name="email"' in r.get_data(as_text=True)


def test_rsn01_email_invalido_responde_400(app, client):
    r = pedir(client, "sem-arroba")
    assert r.status_code == 400 and "Informe um e-mail válido" in r.get_data(as_text=True)
    assert recuperacoes(app) == 0


def test_rsn01_logado_vai_para_a_seguranca(app, client):
    make_user(app)
    login(client)
    assert client.get("/senha/recuperar").location.endswith("/conta/seguranca")


def test_rsn01_rsn03_resposta_igual_exista_a_conta_ou_nao(app, client, monkeypatch):
    uid = make_user(app)
    suspensa = make_user(app, "sus")
    mudar(app, suspensa, suspensa_em=tempo.agora_utc())
    anonimizada = make_user(app, "anon")
    monkeypatch.setattr(recuperacao, "conta_anonimizada", lambda u: u.id == anonimizada)
    respostas = [pedir(client, e) for e in ("ana@ex.com", "ninguem@ex.com", "anon@ex.com", "sus@ex.com")]
    assert {r.status_code for r in respostas} == {200}
    assert len({r.get_data() for r in respostas}) == 1 and RESPOSTA in respostas[0].get_data(as_text=True)
    assert recuperacoes(app) == 1 and recuperacoes(app, uid) == 1


# ---------- RSN-02 ----------
def test_rsn02_email_transacional_com_o_link(app, client):
    uid = make_user(app)
    pedir(client)
    with app.app_context():
        e = EmailSaida.query.filter_by(modelo="recuperar_senha").one()
        assert (e.user_id, e.para, e.tipo) == (uid, "ana@ex.com", None)  # transacional: ignora preferências
        assert "descadastro" not in e.corpo_texto.lower()
    assert link(app).startswith("/senha/redefinir/")


def test_rsn02_token_leva_o_id_e_a_impressao_da_conta(app):
    uid = make_user(app)
    with app.test_request_context(), app.app_context():
        u = db.session.get(User, uid)
        dados = conta.serializador("recuperar-senha").loads(recuperacao.token_recuperacao(u))
        assert dados == {"u": uid, "i": recuperacao.impressao(u)}
        assert u.password_hash not in recuperacao.token_recuperacao(u)


# ---------- RSN-03 ----------
def test_rsn03_conta_pendente_de_responsavel_recupera(app, client):
    uid = make_user(app)
    mudar(app, uid, nascimento=date(2013, 1, 1), aguardando_responsavel=True)
    pedir(client)
    assert recuperacoes(app, uid) == 1


# ---------- RSN-04 ----------
def test_rsn04_no_maximo_3_por_hora(app, client, relogio):
    uid = make_user(app)
    for _ in range(4):
        assert RESPOSTA in pedir(client).get_data(as_text=True)
    assert recuperacoes(app, uid) == 3
    with app.app_context():
        assert jobs.Job.query.count() == 3
    relogio.avancar(minutes=59)
    pedir(client)
    assert recuperacoes(app, uid) == 3
    relogio.avancar(minutes=1, seconds=1)
    pedir(client)
    assert recuperacoes(app, uid) == 4


# ---------- RSN-05 ----------
def test_rsn05_token_valido_mostra_o_formulario_sem_cache(app, client):
    make_user(app)
    pedir(client)
    r = client.get(link(app))
    assert r.status_code == 200 and 'name="confirmacao"' in r.get_data(as_text=True)
    assert (r.headers["Cache-Control"], r.headers["Referrer-Policy"]) == ("no-store", "no-referrer")


def test_rsn05_token_adulterado_com_outro_sal_ou_vencido(app, client, relogio):
    uid = make_user(app)
    pedir(client)
    caminho = link(app)
    with app.test_request_context():
        u = ler(app, uid)
        outro_sal = conta.serializador("trocar-email").dumps({"u": uid, "i": recuperacao.impressao(u)})
    invalidos = [caminho[:-3] + ("xyz" if not caminho.endswith("xyz") else "abc"), f"/senha/redefinir/{outro_sal}"]
    relogio.avancar(hours=1, seconds=1)
    invalidos.append(caminho)
    for c in invalidos:
        r = client.get(c)
        html = r.get_data(as_text=True)
        assert r.status_code == 400 and "Link inválido ou vencido" in html and 'href="/senha/recuperar"' in html
        assert "ana@ex.com" not in html
        assert (r.headers["Cache-Control"], r.headers["Referrer-Policy"]) == ("no-store", "no-referrer")
    relogio.avancar(seconds=-2)
    assert client.get(caminho).status_code == 200


def test_rsn05_sessoes_encerradas_invalidam_o_link(app, client):
    uid = make_user(app)
    pedir(client)
    mudar(app, uid, session_version=1)
    assert client.get(link(app)).status_code == 400


# ---------- RSN-06 ----------
@pytest.mark.parametrize("nova, confirmacao", [("curta12", "curta12"), ("novasenha99", "outrasenha99")])
def test_rsn06_senha_invalida_nao_gasta_o_token(app, client, nova, confirmacao):
    uid = make_user(app)
    pedir(client)
    caminho = link(app)
    hash_antes = ler(app, uid).password_hash
    assert redefinir(client, caminho, nova, confirmacao).status_code == 400
    assert ler(app, uid).password_hash == hash_antes
    assert redefinir(client, caminho).status_code == 302


def test_rsn06_redefine_derruba_sessoes_e_zera_tentativas(app, client):
    uid = make_user(app)
    logado = app.test_client()
    login(logado)
    for _ in range(3):
        client.post("/entrar", data={"email": "ana@ex.com", "password": "errada123"})
    pedir(client)
    caminho = link(app)
    r = redefinir(client, caminho)
    assert r.status_code == 302 and r.location.endswith("/entrar")
    assert "Senha redefinida. Entre com a nova senha." in client.get("/entrar").get_data(as_text=True)
    assert client.get("/conta/seguranca").status_code == 302  # sem login automático
    assert logado.get("/conta/seguranca").status_code == 302  # a outra sessão caiu
    assert ler(app, uid).session_version == 1
    with app.app_context():
        assert LoginAttempt.query.filter_by(email="ana@ex.com").count() == 0
    assert login(app.test_client(), password="novasenha99").status_code == 302
    assert client.get(caminho).status_code == 400  # o mesmo link não serve de novo
    assert redefinir(client, caminho, "outrasenha99").status_code == 400


# ---------- RSN-07 ----------
def test_rsn07_redefinir_nao_desliga_o_2fa(app, client):
    uid = make_user(app)
    segredo = conta.gerar_segredo()
    mudar(app, uid, totp_segredo=segredo, totp_ativado_em=tempo.agora_utc())
    pedir(client)
    redefinir(client, link(app))
    r = client.post("/entrar", data={"email": "ana@ex.com", "password": "novasenha99"})
    assert r.location.endswith("/entrar/2fa")
    assert ler(app, uid).totp_segredo == segredo
    r = client.post("/entrar/2fa", data={"codigo": conta.codigo_totp(segredo, conta.passo_atual())})
    assert r.status_code == 302 and client.get("/conta/seguranca").status_code == 200


# ---------- RSN-08 ----------
def test_rsn08_email_notificacao_e_historico(app, client):
    uid = make_user(app)
    pedir(client)
    redefinir(client, link(app))
    with app.app_context():
        aviso = EmailSaida.query.filter_by(modelo="senha_alterada").one()
        assert aviso.tipo is None and "16/11/2026 às 12:00" in aviso.corpo_texto and "/privacidade" in aviso.corpo_texto
        assert Notificacao.query.filter_by(user_id=uid, tipo="sistema").count() == 1
        assert [e.tipo for e in EventoSeguranca.query.filter_by(user_id=uid)] == ["senha_redefinida"]
    login(client, password="novasenha99")
    assert "Senha redefinida por e-mail" in client.get("/conta/seguranca").get_data(as_text=True)


def test_rsn08_responsavel_da_conta_vinculada_e_avisado(app, client, monkeypatch):
    menor = make_user(app, "teo")
    make_user(app)  # adulta: o responsável não entra
    resp = make_user(app, "mae")
    mudar(app, menor, nascimento=date(2013, 1, 1))
    monkeypatch.setattr(recuperacao, "responsaveis_ativos", lambda u: [resp])
    for nome in ("teo", "ana"):
        pedir(client, f"{nome}@ex.com")
        redefinir(client, link(app))
    with app.app_context():
        avisos = Notificacao.query.filter_by(tipo="responsavel").all()
        assert [(n.user_id, "teo" in n.texto) for n in avisos] == [(resp, True)]


# ---------- RSN-09 ----------
def test_rsn09_producao_sem_smtp_fica_indisponivel(app, client, monkeypatch):
    make_user(app)
    app.config["PRODUCAO"] = True
    for r in (client.get("/senha/recuperar"), pedir(client)):
        html = r.get_data(as_text=True)
        assert "A recuperação de senha está indisponível no momento" in html and 'href="/privacidade"' in html
    assert recuperacoes(app) == 0
    monkeypatch.setenv("SMTP_HOST", "smtp.exemplo.com")
    assert RESPOSTA in pedir(client).get_data(as_text=True)


def test_rsn09_corpo_removido_depois_do_envio_e_oculto_no_admin(app, client):
    make_user(app)
    make_user(app, "admin", role="admin")
    pedir(client)
    token = link(app).rsplit("/", 1)[1]
    admin = app.test_client()
    login(admin, "admin")
    with app.app_context():
        eid = EmailSaida.query.filter_by(modelo="recuperar_senha").one().id
    assert token not in admin.get(f"/admin/emails/{eid}").get_data(as_text=True)
    with app.app_context():
        jobs.ciclo()
    with app.app_context():
        e = db.session.get(EmailSaida, eid)
        assert e.status == "enviado" and e.corpo_texto == e.corpo_html == "[link removido após o envio]"
    html = admin.get(f"/admin/emails/{eid}").get_data(as_text=True)
    assert "[link removido" not in html and "não aparece aqui" in html
