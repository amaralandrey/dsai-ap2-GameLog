from app.models import User, db
from conftest import login, make_user

OK = {"username": "ana", "email": "ana@ex.com", "password": "senha1234", "nascimento": "1995-06-15", "aceite": "1"}


def signup(client, **kw):
    return client.post("/cadastro", data={**OK, **kw})


def test_cad01_validacoes(client, app):
    assert signup(client, username="ab").status_code == 400
    assert signup(client, username="x" * 21).status_code == 400
    assert signup(client, email="invalido").status_code == 400
    assert signup(client, password="curta").status_code == 400
    assert signup(client).status_code == 302
    assert signup(client, username="ANA", email="outro@ex.com").status_code == 400  # sem diferenciar maiúsculas
    assert signup(client, username="bia", email="ANA@ex.com").status_code == 400  # e-mail único


def test_cad01_cadastro_simultaneo_mostra_erro_e_nao_500(client, app, corrida):
    corrida("INSERT INTO user (username, username_lower, email, password_hash, display_name, bio, role, "
            "session_version, created_at, perfil_privado, nascimento_confiavel, aguardando_responsavel) "
            "VALUES ('Ana', 'ana', 'ana@ex.com', 'x', 'Ana', '', 'jogador', 0, '2026-01-01', 0, 0, 0)")
    r = signup(client)
    assert r.status_code == 400
    html = r.get_data(as_text=True)
    assert "Nome de usuário já em uso." in html and "E-mail já cadastrado." in html
    with app.app_context():
        assert User.query.count() == 1


def test_cad02_hash_com_salt(client, app):
    signup(client)
    with app.app_context():
        h = User.query.one().password_hash
        assert "senha1234" not in h and h.count("$") >= 2


def test_cad03_login_generico(client, app):
    make_user(app)
    assert login(client).status_code == 302
    c2 = app.test_client()
    a = c2.post("/entrar", data={"email": "ana@ex.com", "password": "errada123"})
    b = c2.post("/entrar", data={"email": "nao@existe.com", "password": "errada123"})
    assert a.status_code == b.status_code == 401
    assert "E-mail ou senha incorretos" in a.get_data(as_text=True)
    assert "E-mail ou senha incorretos" in b.get_data(as_text=True)


def test_cad03_email_inexistente_tambem_confere_hash(client, app, monkeypatch):
    from app import auth

    conferidos = []
    original = auth.check_password_hash
    monkeypatch.setattr(auth, "check_password_hash", lambda h, s: conferidos.append(h) or original(h, s))
    assert client.post("/entrar", data={"email": "nao@existe.com", "password": "senha1234"}).status_code == 401
    assert conferidos == [auth.hash_ficticio()]


def test_cad04_bloqueio_apos_5_falhas(client, app):
    make_user(app)
    for _ in range(5):
        client.post("/entrar", data={"email": "ana@ex.com", "password": "x"})
    r = login(client)  # senha certa, mas bloqueado
    assert r.status_code == 429


def test_cad05_logout_invalida_sessao_no_servidor(client, app):
    make_user(app)
    login(client)
    with client.session_transaction() as s:
        old = dict(s)
    client.post("/sair")
    with client.session_transaction() as s:
        s.update(old)  # reaproveita o cookie antigo
    assert client.get("/u/ana/editar").status_code == 302  # continua deslogado


def test_cad06_volta_a_acao_apos_login(client, app):
    make_user(app)
    r = client.get("/u/ana/editar")
    assert r.status_code == 302 and "next=" in r.headers["Location"]
    r = client.post("/entrar?next=/u/ana/editar", data={"email": "ana@ex.com", "password": "senha1234"})
    assert r.headers["Location"] == "/u/ana/editar"
    # open redirect bloqueado
    c2 = app.test_client()
    r = c2.post("/entrar?next=//evil.com", data={"email": "ana@ex.com", "password": "senha1234"})
    assert r.headers["Location"] == "/"


def test_cad06_safe_next_recusa_barra_invertida_e_controle():
    from app.util import safe_next

    for ruim in ["/\\evil.com", "/\tevil.com", "/\t/evil.com", "/\n/evil.com", "//evil.com",
                 "https://evil.com", "/x\\y", "\\evil.com"]:
        assert safe_next(ruim) == "/", ruim
    for bom in ["/u/ana/editar", "/jogos/x?aba=2#c1", "/"]:
        assert safe_next(bom) == bom


def test_cad06_post_sem_login_volta_a_pagina_de_origem(client, app):
    make_user(app)
    make_user(app, "bia")
    r = client.post("/u/bia/seguir", headers={"Referer": "http://localhost/u/bia?aba=listas"})
    assert r.status_code == 302
    r = client.post(r.headers["Location"], data={"email": "ana@ex.com", "password": "senha1234"})
    assert r.headers["Location"] == "/u/bia?aba=listas"
    # referrer de outro host não vira destino
    c2 = app.test_client()
    r = c2.post("/u/bia/seguir", headers={"Referer": "http://evil.com/u/bia"})
    r = c2.post(r.headers["Location"], data={"email": "ana@ex.com", "password": "senha1234"})
    assert r.headers["Location"] == "/"


def test_cad07_perfil_publico(client, app):
    make_user(app)
    html = client.get("/u/ana").get_data(as_text=True)
    assert "avaliações" in html and "seguidores" in html and "no GameLog desde" in html
    assert client.get("/u/fantasma").status_code == 404


def test_cad08_edicao_e_avatar(client, app):
    make_user(app)
    login(client)
    assert client.post("/u/ana/editar", data={"display_name": "Ana", "bio": "x" * 281}).status_code == 400
    assert client.post("/u/ana/editar", data={"display_name": "Ana Lima", "bio": "oi"}).status_code == 302
    html = client.get("/u/ana").get_data(as_text=True)
    assert "Ana Lima" in html and 'class="avatar big" style="background:hsl(' in html


def test_cad09_nao_edita_outro(client, app):
    make_user(app)
    make_user(app, "bia")
    login(client)
    assert client.get("/u/bia/editar").status_code == 403
    assert client.post("/u/bia/editar", data={"display_name": "hack"}).status_code == 403


def test_cad10_novo_cadastro_e_jogador(client, app):
    signup(client)
    with app.app_context():
        assert User.query.one().role == "jogador"


def test_csrf_bloqueia_post_sem_token(tmp_path):
    from app import create_app

    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path}/c.db"})
    assert app.test_client().post("/cadastro", data=OK).status_code == 400
    c = app.test_client()
    with c.session_transaction() as s:
        s["csrf"] = "abc"
    assert c.post("/cadastro", data={**OK, "csrf_token": "abç"}).status_code == 400
    assert c.post("/cadastro", data={**OK, "csrf_token": "abc"}).status_code == 302


def test_cookie_de_banco_anterior_nao_autentica_quem_herdou_o_id(tmp_path):
    from app import create_app

    cfg = {"TESTING": True, "CSRF": False, "SECRET_KEY": "fixa"}
    antigo = create_app({**cfg, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path}/a.db"})
    make_user(antigo, "ana")
    c = antigo.test_client()
    login(c, "ana")
    with c.session_transaction() as s:
        cookie = dict(s)
    novo = create_app({**cfg, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path}/b.db"})  # banco recriado no boot
    make_user(novo, "bia")  # herda o id 1 e o session_version 0 da ana
    c2 = novo.test_client()
    with c2.session_transaction() as s:
        s.update(cookie)
    assert c2.get("/u/bia/editar").status_code == 302


def test_cookie_de_sessao_seguro_em_producao(tmp_path):
    from app import create_app

    app = create_app({"TESTING": True, "PRODUCAO": True, "SECRET_KEY": "fixa",
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path}/p.db"})
    cookie = app.test_client().get("/entrar").headers["Set-Cookie"]
    assert "Secure" in cookie and "SameSite=Lax" in cookie


def test_cookie_de_sessao_lax_fora_de_producao(client):
    cookie = client.get("/entrar").headers["Set-Cookie"]
    assert "SameSite=Lax" in cookie and "Secure" not in cookie


def test_producao_sem_secret_key_nao_sobe(tmp_path, monkeypatch):
    import pytest

    from app import create_app

    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app({"TESTING": True, "PRODUCAO": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path}/p.db"})
