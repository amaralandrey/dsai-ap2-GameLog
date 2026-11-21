"""Verificação de idade (IDA-01..12), sem rede e com o relógio de Belém fixo em 16/11/2026."""
from datetime import date, datetime

import pytest

from app import tempo
from app.menores import idade as ida
from app.models import User, VerificacaoIdade, db
from conftest import make_user

HOJE = date(2026, 11, 16)


@pytest.fixture(autouse=True)
def relogio(monkeypatch):
    monkeypatch.setattr(tempo, "agora", lambda: datetime(2026, 11, 16, 12, 0, tzinfo=tempo.FUSO))


def anos_atras(anos, dias=0):
    """Nascimento de quem faz `anos` hoje; `dias` > 0 faz o aniversário cair daqui a `dias` dias."""
    return date(HOJE.year - anos, HOJE.month, HOJE.day + dias)


def usuario(app, nome="ana"):
    make_user(app, nome)
    return User.query.filter_by(username=nome).one()


# ---------- IDA-03 ----------
@pytest.mark.parametrize("nascimento,faixa", [
    (anos_atras(18), "adulto"), (anos_atras(18, dias=1), "adolescente"),  # o dia do aniversário e a véspera
    (anos_atras(17), "adolescente"), (anos_atras(16), "adolescente"),
    (anos_atras(16, dias=1), "adolescente_vinculado"), (anos_atras(15), "adolescente_vinculado"),
    (anos_atras(12), "adolescente_vinculado"),
])
def test_ida_03_faixas_nos_limites(nascimento, faixa):
    assert ida.faixa_de(nascimento) == faixa


def test_ida_03_aniversario_muda_a_faixa_sem_job(app, monkeypatch):
    with app.app_context():
        u = usuario(app)
        u.nascimento = anos_atras(18, dias=1)
        assert ida.faixa_etaria(u) == "adolescente"
        monkeypatch.setattr(tempo, "agora", lambda: datetime(2026, 11, 17, 0, 1, tzinfo=tempo.FUSO))
        assert ida.faixa_etaria(u) == "adulto"


def test_ida_03_sem_data_e_a_faixa_mais_protetiva(app):
    with app.app_context():
        u = usuario(app)
        u.nascimento = None
        assert ida.faixa_etaria(u) == "adolescente_vinculado"


# ---------- IDA-01, IDA-02 (validação) ----------
@pytest.mark.parametrize("texto,trecho", [
    ("", "válida"), ("2010-02-30", "válida"), ("31/04/2000", "válida"), ("ontem", "válida"),
    ("2026-11-17", "futuro"), ("1906-11-15", "120 anos"),
])
def test_ida_01_data_invalida_com_mensagem(texto, trecho):
    with pytest.raises(ida.IdadeInvalida, match=trecho):
        ida.ler_nascimento(texto)


def test_ida_01_formatos_aceitos():
    assert ida.ler_nascimento("2000-03-01") == ida.ler_nascimento("01/03/2000") == date(2000, 3, 1)
    assert ida.ler_nascimento("1906-11-16") == date(1906, 11, 16)  # exatamente 120 anos


@pytest.mark.parametrize("nascimento,anos", [(anos_atras(11), 11), (anos_atras(12, dias=1), 11),
                                             (anos_atras(12), 12)])
def test_ida_02_idade_nos_limites_de_12_anos(nascimento, anos):
    assert ida.idade(nascimento) == anos


# ---------- IDA-06, IDA-08 ----------
def test_ida_06_toda_verificacao_grava_historico_so_de_insercao(app):
    with app.app_context():
        u = usuario(app)
        u.nascimento = None
        ida.registrar(u, ida.Resultado(anos_atras(30), "autodeclaracao", False))
        db.session.commit()
        v = VerificacaoIdade.query.one()
        assert (v.metodo, v.faixa_resultante, v.confiavel, v.divergente) == ("autodeclaracao", "adulto", False, False)
        assert {c.name for c in VerificacaoIdade.__table__.columns} == {
            "id", "user_id", "metodo", "faixa_resultante", "confiavel", "divergente", "created_at"}
        v.faixa_resultante = "adolescente"
        with pytest.raises(ValueError, match="só de inserção"):
            db.session.commit()


@pytest.mark.parametrize("sinais,faixa,confiavel", [
    ([(30, False), (14, False)], "adolescente_vinculado", False),  # dois autodeclarados: o mais protetivo
    ([(14, False), (30, False)], "adolescente_vinculado", False),
    ([(14, False), (30, True)], "adulto", True),  # o confiável corrige o autodeclarado
    ([(30, True), (14, False)], "adolescente_vinculado", False),  # sinal mais novo protege mesmo sem prova
    ([(30, True), (40, False)], "adulto", True),  # autodeclaração não deixa menos protetivo
    ([(17, True), (30, True)], "adolescente", True),  # dois confiáveis: o mais protetivo
])
def test_ida_08_divergencia_vale_a_faixa_mais_protetiva(app, sinais, faixa, confiavel):
    with app.app_context():
        u = usuario(app)
        u.nascimento = None
        for anos, conf in sinais:
            ida.registrar(u, ida.Resultado(anos_atras(anos), "responsavel" if conf else "autodeclaracao", conf))
        db.session.commit()
        assert (ida.faixa_etaria(u), u.nascimento_confiavel) == (faixa, confiavel)
        assert u.aguardando_responsavel is (faixa == "adolescente_vinculado")
        assert [v.divergente for v in VerificacaoIdade.query.order_by(VerificacaoIdade.id)][1] is (
            ida.faixa_de(anos_atras(sinais[0][0])) != ida.faixa_de(anos_atras(sinais[1][0])))


def test_ida_05_verificacao_de_menor_de_12_suspende(app):
    with app.app_context():
        u = usuario(app)
        ida.registrar(u, ida.Resultado(anos_atras(10), "provedor_simulado", True))
        db.session.commit()
        assert u.suspensa_em is not None


# ---------- IDA-01, IDA-02, IDA-04 (cadastro) ----------
def cadastrar(client, nascimento, nome="novo"):
    return client.post("/cadastro", data={"username": nome, "email": f"{nome}@ex.com", "password": "senha1234",
                                          "nascimento": nascimento.isoformat() if isinstance(nascimento, date) else nascimento})


@pytest.mark.parametrize("nascimento,destino,aguardando", [
    (anos_atras(18), "/", False), (anos_atras(18, dias=1), "/", False), (anos_atras(16), "/", False),
    (anos_atras(16, dias=1), "/conta/pendente", True), (anos_atras(12), "/conta/pendente", True),
])
def test_ida_04_cadastro_por_faixa(app, client, nascimento, destino, aguardando):
    r = cadastrar(client, nascimento)
    assert r.status_code == 302 and r.headers["Location"] == destino
    with app.app_context():
        u = User.query.one()
        assert u.aguardando_responsavel is aguardando
        v = VerificacaoIdade.query.one()
        assert (v.metodo, v.confiavel) == ("autodeclaracao", False)


@pytest.mark.parametrize("nascimento,mensagem", [
    (anos_atras(11), "O GameLog é para pessoas a partir de 12 anos"),
    (anos_atras(12, dias=1), "O GameLog é para pessoas a partir de 12 anos"),  # véspera dos 12
    ("2010-02-30", "data de nascimento válida"), ("", "data de nascimento válida"),
    ("2026-11-17", "futuro"), ("1900-01-01", "120 anos"),
])
def test_ida_02_recusa_sem_gravar_nada(app, client, nascimento, mensagem):
    from app.models import LoginAttempt

    r = cadastrar(client, nascimento)
    assert r.status_code == 400 and mensagem in r.get_data(as_text=True)
    with app.app_context():
        assert User.query.count() == VerificacaoIdade.query.count() == LoginAttempt.query.count() == 0


def test_ida_04_conta_pendente_so_acessa_rotas_livres_e_publicas(app, client):
    from conftest import make_game

    make_game(app)
    cadastrar(client, anos_atras(14))
    for rota in ("/listas", "/feed", "/u/novo/editar"):
        r = client.get(rota)
        assert r.status_code == 302 and r.headers["Location"] == "/conta/pendente", rota
    assert client.post("/jogos/jogo-teste/avaliar", data={"score": 8}).headers["Location"] == "/conta/pendente"
    assert "aguarda um responsável" in client.get("/conta/pendente").get_data(as_text=True)
    for publica in ("/", "/buscar?q=jogo", "/jogos/jogo-teste", "/u/novo"):
        assert client.get(publica).status_code == 200, publica
    assert client.post("/sair").status_code == 302


def test_ida_04_aniversario_de_16_anos_libera_a_conta_pendente(app, client, monkeypatch):
    from conftest import login

    make_user(app, "chefe", role="admin")
    cadastrar(client, anos_atras(16, dias=1))  # 15 anos hoje, 16 amanhã
    assert client.get("/listas").headers["Location"] == "/conta/pendente"
    monkeypatch.setattr(tempo, "agora", lambda: datetime(2026, 11, 17, 0, 1, tzinfo=tempo.FUSO))
    assert client.get("/listas").status_code == 200
    assert client.get("/conta/pendente").headers["Location"] == "/"
    chefe = app.test_client()
    login(chefe, "chefe")
    html = chefe.get("/admin/usuarios").get_data(as_text=True)
    assert "16 ou 17 anos" in html and "aguardando responsável" not in html


def test_ida_04_data_errada_no_cadastro_pede_revisao_e_o_admin_libera(app, client, monkeypatch):
    from conftest import login

    monkeypatch.delenv("VERIFICACAO_PROVEDOR", raising=False)
    make_user(app, "chefe", role="admin")
    cadastrar(client, date(2012, 1, 1))  # quis digitar 2002
    html = client.get("/conta/pendente").get_data(as_text=True)
    assert 'href="/conta/idade"' not in html and "Pedir revisão da data" in html  # só há autodeclaração
    client.post("/conta/pendente/revisao")
    client.post("/conta/pendente/revisao")  # repetir não muda nada
    assert "Você pediu a revisão da data" in client.get("/conta/pendente").get_data(as_text=True)
    chefe = app.test_client()
    login(chefe, "chefe")
    html = chefe.get("/admin/usuarios").get_data(as_text=True)
    tabela = html[html.index("<table"):]
    assert tabela.index("@novo") < tabela.index("@chefe") and "pediu revisão da data" in tabela
    with app.app_context():
        nid = User.query.filter_by(username="novo").one().id
    chefe.post(f"/admin/usuarios/{nid}/nova-data")
    assert client.get("/listas").headers["Location"].startswith("/conta/nascimento")
    client.post("/conta/nascimento", data={"nascimento": "2002-01-01"})
    assert client.get("/listas").status_code == 200
    with app.app_context():
        assert db.session.get(User, nid).revisao_idade_pedida_em is None


def test_ida_04_link_de_nova_verificacao_so_com_adaptador_confiavel(app, pendente_14, monkeypatch):
    monkeypatch.setenv("VERIFICACAO_PROVEDOR", "simulado")
    assert 'href="/conta/idade"' in pendente_14.get("/conta/pendente").get_data(as_text=True)


@pytest.mark.parametrize("caminho,livre", [("/termos", True), ("/conta/pendente/revisao", True),
                                           ("/termos-de-clube", False), ("/sair-do-clube", False),
                                           ("/conta/idade-historico", False)])
def test_ida_04_rota_livre_e_caminho_exato_ou_segmento(app, caminho, livre):
    from app.util import _pendencia_de_idade

    with app.app_context():
        u = usuario(app)
        u.aguardando_responsavel, u.nascimento = True, anos_atras(14)
        with app.test_request_context(caminho):
            assert (_pendencia_de_idade(u) is None) == livre


# ---------- IDA-05 ----------
def sem_data(app, nome="ana", role="jogador"):
    make_user(app, nome, role=role)
    with app.app_context():
        User.query.filter_by(username=nome).one().nascimento = None
        db.session.commit()


@pytest.mark.parametrize("role,rota", [("jogador", "/listas"), ("admin", "/admin/")])
def test_ida_05_conta_antiga_informa_a_data_antes_de_tudo(app, client, role, rota):
    from conftest import login

    sem_data(app, role=role)
    login(client)
    r = client.get(rota)
    assert r.status_code == 302 and r.headers["Location"] == f"/conta/nascimento?next={rota}"
    assert client.post(r.headers["Location"], data={"nascimento": "2030-01-01"}).status_code == 400
    r = client.post(r.headers["Location"], data={"nascimento": "1990-05-05"})
    assert r.status_code == 302 and r.headers["Location"] == rota  # volta para a página que tinha pedido
    assert client.get(rota).status_code == 200
    assert client.get("/conta/nascimento").headers["Location"] == "/"


def test_ida_05_conta_antiga_de_menor_de_12_e_suspensa(app, client):
    from conftest import login

    sem_data(app)
    login(client)
    r = client.post("/conta/nascimento", data={"nascimento": anos_atras(10).isoformat()}, follow_redirects=True)
    assert "Conta suspensa" in r.get_data(as_text=True)
    assert client.get("/listas").status_code == 302 and "/entrar" in client.get("/listas").headers["Location"]
    r = login(client)
    assert r.status_code == 403 and "Conta suspensa" in r.get_data(as_text=True)
    with app.app_context():
        assert User.query.one().suspensa_em is not None


def test_ida_05_suspensa_por_data_errada_volta_com_nova_data_pelo_admin(app, client):
    from app.models import AuditLog
    from conftest import login

    make_user(app, "chefe", role="admin")
    sem_data(app)
    login(client)
    client.post("/conta/nascimento", data={"nascimento": "2020-01-01"})  # quis digitar 1990
    assert login(client).status_code == 403
    chefe = app.test_client()
    login(chefe, "chefe")
    assert "Pedir nova data" in chefe.get("/admin/usuarios").get_data(as_text=True)
    with app.app_context():
        aid = User.query.filter_by(username="ana").one().id
    chefe.post(f"/admin/usuarios/{aid}/nova-data")
    assert login(client).status_code == 302 and client.get("/listas").headers["Location"].startswith("/conta/nascimento")
    client.post("/conta/nascimento", data={"nascimento": "1990-01-01"})
    assert client.get("/listas").status_code == 200
    with app.app_context():
        assert [a.action for a in AuditLog.query] == ["idade.nova_data"]


def test_ida_05_suspensao_por_verificacao_confiavel_nao_se_desfaz(app, client, monkeypatch):
    from conftest import login

    monkeypatch.setenv("VERIFICACAO_PROVEDOR", "simulado")
    make_user(app, "chefe", role="admin")
    make_user(app)
    login(client)
    client.post("/conta/idade/provedor_simulado", data={"codigo": "CRIANCA"})
    chefe = app.test_client()
    login(chefe, "chefe")
    assert "Pedir nova data" not in chefe.get("/admin/usuarios").get_data(as_text=True)
    with app.app_context():
        aid = User.query.filter_by(username="ana").one().id
    r = chefe.post(f"/admin/usuarios/{aid}/nova-data", follow_redirects=True)
    assert "verificação confiável" in r.get_data(as_text=True) and login(client).status_code == 403


def test_ida_05_conta_antiga_de_14_anos_fica_pendente(app, client):
    from conftest import login

    sem_data(app)
    login(client)
    r = client.post("/conta/nascimento", data={"nascimento": anos_atras(14).isoformat()})
    assert r.headers["Location"] == "/conta/pendente"


# ---------- IDA-09 ----------
def test_ida_09_editar_perfil_nao_muda_o_nascimento(app, client):
    from conftest import login

    make_user(app)
    login(client)
    r = client.post("/u/ana/editar", data={"display_name": "Ana", "bio": "", "nascimento": "2012-01-01"})
    assert r.status_code == 302
    with app.app_context():
        assert User.query.one().nascimento == date(1990, 1, 1)


# ---------- IDA-07, IDA-10 ----------
@pytest.fixture
def pendente_14(app, client):
    cadastrar(client, anos_atras(14))
    return client


def test_ida_07_provedor_simulado_desligado_responde_404(app, pendente_14, monkeypatch):
    monkeypatch.delenv("VERIFICACAO_PROVEDOR", raising=False)
    assert pendente_14.post("/conta/idade/provedor_simulado", data={"codigo": "ADULTO"}).status_code == 404
    assert pendente_14.post("/conta/idade/responsavel", data={"nascimento": "1990-01-01"}).status_code == 404
    assert pendente_14.post("/conta/idade/inventado").status_code == 404
    assert "Código de verificação" not in pendente_14.get("/conta/idade").get_data(as_text=True)


def test_ida_10_provedor_confiavel_libera_e_autodeclaracao_nao(app, pendente_14, monkeypatch):
    monkeypatch.setenv("VERIFICACAO_PROVEDOR", "simulado")
    assert "Código de verificação" in pendente_14.get("/conta/idade").get_data(as_text=True)
    r = pendente_14.post("/conta/idade/autodeclaracao", data={"nascimento": "1990-01-01"})
    assert r.headers["Location"] == "/conta/pendente"  # autodeclarar mais idade não libera
    assert pendente_14.post("/conta/idade/provedor_simulado", data={"codigo": "nada"}).status_code == 400
    r = pendente_14.post("/conta/idade/provedor_simulado", data={"codigo": "adulto"}, follow_redirects=True)
    assert "Faixa da conta: 18 anos ou mais" in r.get_data(as_text=True)
    assert pendente_14.get("/listas").status_code == 200
    with app.app_context():
        u = User.query.one()
        assert (u.aguardando_responsavel, u.nascimento_confiavel) == (False, True)
        assert [(v.metodo, v.confiavel, v.divergente) for v in VerificacaoIdade.query.order_by(VerificacaoIdade.id)] == [
            ("autodeclaracao", False, False), ("autodeclaracao", False, True), ("provedor_simulado", True, True)]


def test_ida_10_provedor_que_aponta_menos_de_12_suspende(app, client, monkeypatch):
    from conftest import login

    monkeypatch.setenv("VERIFICACAO_PROVEDOR", "simulado")
    make_user(app)
    login(client)
    r = client.post("/conta/idade/provedor_simulado", data={"codigo": "CRIANCA"}, follow_redirects=True)
    assert "Conta suspensa" in r.get_data(as_text=True) and login(client).status_code == 403


# ---------- IDA-11 ----------
def test_ida_11_admin_contesta_a_idade_com_auditoria(app, monkeypatch):
    from app.models import AuditLog
    from conftest import login

    make_user(app, "chefe", role="admin")
    make_user(app)
    chefe, ana = app.test_client(), app.test_client()
    login(chefe, "chefe")
    login(ana)
    with app.app_context():
        aid = User.query.filter_by(username="ana").one().id
    assert ana.post(f"/admin/usuarios/{aid}/contestar-idade").status_code == 403
    assert "@ana" in chefe.get("/admin/usuarios").get_data(as_text=True)
    chefe.post(f"/admin/usuarios/{aid}/contestar-idade")
    chefe.post(f"/admin/usuarios/{aid}/contestar-idade")  # repetir não duplica a auditoria
    with app.app_context():
        u = db.session.get(User, aid)
        assert ida.faixa_etaria(u) == "adolescente_vinculado" and u.aguardando_responsavel  # adulto contestado
        assert [a.action for a in AuditLog.query] == ["idade.contestar"]
    assert ana.get("/listas").headers["Location"] == "/conta/pendente"
    assert "contestada pela moderação" in ana.get("/conta/pendente").get_data(as_text=True)
    ana.post("/conta/idade/autodeclaracao", data={"nascimento": "1990-01-01"})
    assert ana.get("/listas").status_code == 302  # autodeclaração não encerra a contestação
    monkeypatch.setenv("VERIFICACAO_PROVEDOR", "simulado")
    ana.post("/conta/idade/provedor_simulado", data={"codigo": "ADULTO"})
    assert ana.get("/listas").status_code == 200
    with app.app_context():
        assert db.session.get(User, aid).idade_contestada_em is None


def test_ida_11_admin_nao_contesta_a_propria_idade_e_contestacao_de_admin_tem_volta(app):
    from app.models import AuditLog
    from conftest import login

    make_user(app, "chefe", role="admin")
    make_user(app, "vice", role="admin")
    chefe, vice = app.test_client(), app.test_client()
    login(chefe, "chefe")
    login(vice, "vice")
    with app.app_context():
        cid, vid = (User.query.filter_by(username=n).one().id for n in ("chefe", "vice"))
    r = chefe.post(f"/admin/usuarios/{cid}/contestar-idade", follow_redirects=True)
    assert "revisada por outro admin" in r.get_data(as_text=True) and chefe.get("/admin/").status_code == 200
    assert f"/admin/usuarios/{cid}/contestar-idade" not in chefe.get("/admin/usuarios").get_data(as_text=True)
    chefe.post(f"/admin/usuarios/{vid}/contestar-idade")
    assert vice.get("/admin/").headers["Location"] == "/conta/pendente"
    assert vice.post(f"/admin/usuarios/{vid}/retirar-contestacao").headers["Location"] == "/conta/pendente"
    assert "Retirar contestação" in chefe.get("/admin/usuarios").get_data(as_text=True)
    chefe.post(f"/admin/usuarios/{vid}/retirar-contestacao")
    assert vice.get("/admin/").status_code == 200
    with app.app_context():
        u = db.session.get(User, vid)
        assert (u.idade_contestada_em, u.aguardando_responsavel, ida.faixa_etaria(u)) == (None, False, "adulto")
        assert [a.action for a in AuditLog.query.order_by(AuditLog.id)] == ["idade.contestar", "idade.retirar_contestacao"]


def test_ida_11_retirar_contestacao_de_conta_de_14_anos_mantem_a_espera(app, client):
    from conftest import login

    make_user(app, "chefe", role="admin")
    cadastrar(client, anos_atras(14))
    chefe = app.test_client()
    login(chefe, "chefe")
    with app.app_context():
        nid = User.query.filter_by(username="novo").one().id
    chefe.post(f"/admin/usuarios/{nid}/contestar-idade")
    chefe.post(f"/admin/usuarios/{nid}/retirar-contestacao")
    assert client.get("/listas").headers["Location"] == "/conta/pendente"


# ---------- IDA-12 ----------
def test_ida_12_nascimento_nunca_aparece_nas_respostas(app):
    from conftest import login, make_game
    from app.models import Game, Review

    make_user(app, "chefe", role="admin")
    make_user(app)
    make_game(app)
    with app.app_context():
        u = User.query.filter_by(username="ana").one()
        u.nascimento = date(1987, 3, 14)
        db.session.add(Review(user_id=u.id, game_id=Game.query.one().id, score=8, text="bom"))
        db.session.commit()
    ana, chefe = app.test_client(), app.test_client()
    login(ana)
    login(chefe, "chefe")
    respostas = [app.test_client().get("/u/ana"), ana.get("/u/ana"), ana.get("/u/ana/editar"), ana.get("/feed"),
                 ana.get("/conta/idade"), app.test_client().get("/api/jogos/jogo-teste"),
                 app.test_client().get("/api/jogos/jogo-teste/avaliacoes"), chefe.get("/admin/usuarios")]
    for r in respostas:
        texto = r.get_data(as_text=True)
        assert r.status_code == 200 and "1987" not in texto and "14/03" not in texto, r.request.path
