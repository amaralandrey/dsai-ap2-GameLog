import hashlib
import re
from datetime import datetime

import pytest
from werkzeug.datastructures import MultiDict

from app import tempo
from app.models import AuditoriaToken, LimiteTaxaToken, TokenApi, User, db
from conftest import login, make_user


def token(app, username="ana", escopos=("ler", "escrever"), dias=30):
    from app.tokens import criar

    with app.app_context():
        user = User.query.filter_by(username_lower=username).first() or db.session.get(User, make_user(app, username))
        _, texto = criar(user, "teste", list(escopos), dias)
        db.session.commit()
        return texto


def auth(texto):
    return {"Authorization": f"Bearer {texto}"}


def relogio(monkeypatch, quando):
    monkeypatch.setattr(tempo, "agora", lambda: quando.replace(tzinfo=tempo.FUSO))


def linha(texto):
    from app.tokens import hash_token

    return TokenApi.query.filter_by(token_hash=hash_token(texto)).one()


def test_ap2_02_exclusao_da_conta_revoga_os_tokens(app):
    texto = token(app)
    with app.app_context():
        from app.privacidade.anonimizar import anonimizar_conta
        from app.tokens import autenticar

        anonimizar_conta(User.query.filter_by(username_lower="ana").one())
        db.session.commit()
        assert autenticar(texto) is None
        assert TokenApi.query.count() == 0


def test_ap2_01_formato_e_so_hash_no_banco(app):
    texto = token(app)
    assert re.fullmatch(r"glp_[A-Za-z0-9]{40}", texto)
    with app.app_context():
        t = linha(texto)
        assert t.token_hash == hashlib.sha256(texto.encode()).hexdigest()
        assert t.prefixo == texto[:10]
        assert t.escopos == "ler escrever"
        valores = [getattr(t, c.name) for c in TokenApi.__table__.columns]
        assert texto not in [str(v) for v in valores]
        assert AuditoriaToken.query.filter_by(token_id=t.id, acao="criar").count() == 1


def test_ap2_01_validacao(app):
    from app.tokens import criar, revogar, validar

    uid = make_user(app, "ana")
    with app.app_context():
        user = db.session.get(User, uid)
        form = lambda **kw: MultiDict({"nome": "cli", "escopos": ["ler"], "dias": "30", **kw})  # noqa: E731
        assert validar(user, form())[1] == {}
        assert validar(user, form())[0] == {"nome": "cli", "escopos": ["ler"], "dias": 30}
        assert "nome" in validar(user, form(nome=""))[1]
        assert "nome" in validar(user, form(nome="x" * 61))[1]
        assert "escopos" in validar(user, MultiDict({"nome": "cli", "dias": "30"}))[1]
        assert "escopos" in validar(user, form(escopos=["admin"]))[1]
        assert "dias" in validar(user, form(dias="7"))[1]
        tokens = [criar(user, f"t{i}", ["ler"], 30)[0] for i in range(10)]
        db.session.commit()
        assert "10" in validar(user, form())[1]["nome"]
        revogar(tokens[0])
        db.session.commit()
        assert validar(user, form())[1] == {}


def test_ap2_01_expirado_nao_conta_no_limite(app, monkeypatch):
    from app.tokens import criar, validar

    uid = make_user(app, "ana")
    relogio(monkeypatch, datetime(2026, 11, 1, 10, 0))
    with app.app_context():
        user = db.session.get(User, uid)
        for i in range(10):
            criar(user, f"t{i}", ["ler"], 30)
        db.session.commit()
        relogio(monkeypatch, datetime(2026, 12, 5, 10, 0))
        assert validar(user, MultiDict({"nome": "x", "escopos": "ler", "dias": "30"}))[1] == {}


def test_ap2_03_autenticar_recusa_expirado_revogado_desconhecido(app, monkeypatch):
    from app.tokens import autenticar, revogar

    relogio(monkeypatch, datetime(2026, 11, 20, 10, 0))
    texto = token(app, dias=30)
    with app.app_context():
        assert autenticar(texto).id == linha(texto).id
        relogio(monkeypatch, datetime(2026, 12, 20, 10, 1))
        assert autenticar(texto) is None
        relogio(monkeypatch, datetime(2026, 11, 21, 10, 0))
        assert autenticar(texto) is not None
        revogar(linha(texto))
        db.session.commit()
        assert autenticar(texto) is None
        assert autenticar("glp_x") is None
        assert autenticar(None) is None
        assert autenticar("") is None


def test_ap2_05_janela_fixa_por_minuto(app, monkeypatch):
    from app.tokens import consumir

    texto = token(app)
    relogio(monkeypatch, datetime(2026, 11, 20, 10, 0, 5))
    with app.app_context():
        t = linha(texto)
        resultados = [consumir(t) for _ in range(60)]
        assert all(r[0] for r in resultados)
        assert resultados[0][1] == 59 and resultados[-1][1] == 0
        assert consumir(t) == (False, 0, 55)
        relogio(monkeypatch, datetime(2026, 11, 20, 10, 1, 0))
        permitido, restantes, _ = consumir(t)
        assert (permitido, restantes) == (True, 59)
        assert LimiteTaxaToken.query.filter_by(token_id=t.id).count() == 1


@pytest.fixture
def com_token(app, client):
    texto = token(app)
    make_user(app, "bia")
    return texto


def test_ap2_01_tela_mostra_o_token_uma_vez(app, client):
    make_user(app, "ana")
    login(client)
    r = client.post("/u/ana/tokens", data={"nome": "meu cli", "escopos": ["ler", "escrever"], "dias": "90"})
    assert r.status_code == 201
    assert r.headers["Cache-Control"] == "no-store"
    achado = re.search(r"glp_[A-Za-z0-9]{40}", r.get_data(as_text=True))
    assert achado
    pagina = client.get("/u/ana/tokens").get_data(as_text=True)
    assert achado.group(0) not in pagina
    assert achado.group(0)[:10] in pagina and "meu cli" in pagina and "escrever" in pagina


def test_ap2_01_tela_erros_com_400(app, client):
    make_user(app, "ana")
    login(client)
    r = client.post("/u/ana/tokens", data={"nome": "", "dias": "7"})
    assert r.status_code == 400
    assert not re.search(r"glp_[A-Za-z0-9]{40}", r.get_data(as_text=True))
    with app.app_context():
        assert TokenApi.query.count() == 0


def test_ap2_01_acesso(app, client, com_token):
    make_user(app, "root", role="admin")
    r = client.get("/u/ana/tokens")
    assert r.status_code == 302 and "/entrar" in r.headers["Location"]
    login(client, "bia")
    assert client.get("/u/ana/tokens").status_code == 403
    assert client.post("/u/ana/tokens", data={"nome": "x", "escopos": "ler", "dias": "30"}).status_code == 403
    client.get("/sair")
    login(client, "root")
    assert client.get("/u/ana/tokens").status_code == 403


def test_ap2_02_revogar(app, client, com_token):
    from app.tokens import autenticar

    with app.app_context():
        tid = linha(com_token).id
    login(client, "bia")
    assert client.post(f"/u/ana/tokens/{tid}/revogar").status_code == 403
    client.get("/sair")
    login(client)
    assert client.post(f"/u/ana/tokens/{tid}/revogar").status_code == 302
    with app.app_context():
        assert autenticar(com_token) is None
        assert AuditoriaToken.query.filter_by(token_id=tid, acao="revogar").count() == 1
    assert "revogado" in client.get("/u/ana/tokens").get_data(as_text=True)


def test_ap2_02_token_de_outro_dono_404(app, client, com_token):
    texto_bia = token(app, "bia")
    with app.app_context():
        tid = linha(texto_bia).id
    login(client)
    assert client.get(f"/u/ana/tokens/{tid}").status_code == 404
    assert client.post(f"/u/ana/tokens/{tid}/revogar").status_code == 404


def test_ap2_01_limite_reconferido_apos_flush(app):
    from app.tokens import LimiteAtingido, criar

    uid = make_user(app, "ana")
    with app.app_context():
        ana = db.session.get(User, uid)
        for _ in range(10):
            criar(ana, "t", ["ler"], 30)
        db.session.commit()
        with pytest.raises(LimiteAtingido):
            criar(ana, "11º", ["ler"], 30)
        db.session.rollback()
        assert TokenApi.query.filter_by(user_id=uid).count() == 10


def test_ap2_01_tela_limite_apos_corrida_400(app, client, monkeypatch):
    from app import tokens

    for _ in range(10):
        token(app)
    monkeypatch.setattr(tokens, "validar", lambda user, form: ({"nome": "x", "escopos": ["ler"], "dias": 30}, {}))
    login(client)
    r = client.post("/u/ana/tokens", data={"nome": "x", "escopos": "ler", "dias": "30"})
    assert r.status_code == 400 and "Limite de 10 tokens ativos" in r.get_data(as_text=True)
    assert not re.search(r"glp_[A-Za-z0-9]{40}", r.get_data(as_text=True))
    with app.app_context():
        assert TokenApi.query.count() == 10
        assert AuditoriaToken.query.filter_by(acao="criar").count() == 10


def test_global_09_telas_de_token_em_belem(app, client, monkeypatch):
    relogio(monkeypatch, datetime(2026, 11, 20, 22, 30))
    texto = token(app)
    assert client.get("/api/v2/eu", headers={"Authorization": f"Bearer {texto}"}).status_code == 200
    with app.app_context():
        tid = linha(texto).id
    login(client)
    lista = client.get("/u/ana/tokens").get_data(as_text=True)
    assert "<td>20/11/2026</td><td>20/12/2026</td>" in lista and "<td>20/11/2026 22:30</td>" in lista
    assert "21/11/2026" not in lista and "21/12/2026" not in lista
    client.post(f"/u/ana/tokens/{tid}/revogar")
    detalhe = client.get(f"/u/ana/tokens/{tid}").get_data(as_text=True)
    assert "expira em 20/12/2026" in detalhe and "revogado em 20/11/2026" in detalhe
    assert "<td>20/11/2026 22:30</td>" in detalhe
    assert "21/11/2026" not in detalhe and "01:30" not in detalhe


def test_ap2_02_trocar_senha_e_encerrar_sessoes_nao_revogam(app, client, com_token):
    from app.tokens import autenticar

    login(client)
    client.post("/conta/seguranca/sessoes")
    client.post("/conta/seguranca/senha", data={"atual": "senha1234", "nova": "outrasenha99",
                                                "confirmacao": "outrasenha99"})
    with app.app_context():
        assert db.session.get(User, linha(com_token).user_id).session_version >= 2
        assert autenticar(com_token) is not None


def test_ap2_06_auditoria_paginada_sem_token(app, client, com_token):
    from app.tokens import auditar

    with app.app_context():
        t = linha(com_token)
        tid = t.id
        for i in range(21):
            auditar(t, "escrita", "PUT", f"/api/v2/eu/status/jogo-{i}", 200)
        db.session.commit()
    login(client)
    p1 = client.get(f"/u/ana/tokens/{tid}").get_data(as_text=True)
    p2 = client.get(f"/u/ana/tokens/{tid}?page=2").get_data(as_text=True)
    assert "/api/v2/eu/status/jogo-" in p1 and "200" in p1
    assert p1.count("/api/v2/eu/status/jogo-") == 20
    assert p2.count("/api/v2/eu/status/jogo-") + p2.count("criar") >= 2
    assert com_token not in p1 + p2


def test_ap2_02_cta_revoga_tokens_so_quando_marcado(app, client, com_token):
    from app.tokens import autenticar

    login(client)
    assert "revogar_tokens" in client.get("/conta/seguranca").get_data(as_text=True)
    client.post("/conta/seguranca/sessoes")
    with app.app_context():
        assert autenticar(com_token) is not None
    login(client)
    client.post("/conta/seguranca/sessoes", data={"revogar_tokens": "1"})
    with app.app_context():
        assert autenticar(com_token) is None
        assert AuditoriaToken.query.filter_by(acao="revogar").count() == 1
