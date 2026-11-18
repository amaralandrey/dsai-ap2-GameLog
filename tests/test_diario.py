"""Diário de jogatina e visibilidade do perfil (DIA-01..14)."""
from datetime import date, datetime

import pytest
from sqlalchemy import event

from app import tempo
from app.models import Activity, Follow, Review, SessaoJogo, Status, User, db
from conftest import login, make_game, make_user

HOJE = date(2026, 11, 16)


@pytest.fixture(autouse=True)
def relogio(monkeypatch):
    """Segunda, 16/11/2026, 22:30 em Belém (já 17/11 em UTC)."""
    monkeypatch.setattr(tempo, "agora", lambda: datetime(2026, 11, 16, 22, 30, tzinfo=tempo.FUSO))


def sessao(client, slug="jogo-teste", **campos):
    dados = {"jogada_em": "2026-11-10", "duracao": "90", **campos}
    return client.post(f"/jogos/{slug}/sessoes", data=dados)


def uid(app, username):
    with app.app_context():
        return User.query.filter_by(username=username).one().id


def status_de(app, username, slug="jogo-teste"):
    from app.models import Game

    with app.app_context():
        g = Game.query.filter_by(slug=slug).one()
        return Status.query.filter_by(user_id=uid(app, username), game_id=g.id).first()


def sessoes(app):
    with app.app_context():
        return SessaoJogo.query.order_by(SessaoJogo.id).all()


@pytest.fixture
def ana(app, client):
    make_user(app, "ana")
    make_game(app)
    login(client, "ana")
    return client


def test_dia_01_exige_login(app, client):
    make_game(app)
    r = sessao(client)
    assert r.status_code == 302 and "/entrar" in r.headers["Location"]


@pytest.mark.parametrize("texto,minutos", [("90", 90), ("1h30", 90), ("1:30", 90), ("2h", 120), ("45min", 45),
                                           ("1h 5min", 65)])
def test_dia_01_formatos_de_duracao(app, ana, texto, minutos):
    assert sessao(ana, duracao=texto).status_code == 302
    assert sessoes(app)[0].duracao_min == minutos and sessoes(app)[0].origem == "manual"


@pytest.mark.parametrize("campos,campo_erro", [
    ({"duracao": "0"}, "duração"), ({"duracao": "1441"}, "duração"), ({"duracao": "abc"}, "duração"),
    ({"jogada_em": "31/02/2026"}, "data válida"), ({"plataforma_id": "999"}, "plataformas do jogo"),
    ({"inicio": "25:00"}, "HH:MM"), ({"anotacao": "x" * 1001}, "1000 caracteres"),
])
def test_dia_01_validacao_por_campo_sem_gravar(app, ana, campos, campo_erro):
    r = sessao(ana, **campos)
    assert r.status_code == 400 and campo_erro in r.get_data(as_text=True)
    assert sessoes(app) == []


def test_dia_01_grava_todos_os_campos(app, ana):
    from app.models import Platform

    with app.app_context():
        pid = Platform.query.one().id
    sessao(ana, inicio="20:15", plataforma_id=str(pid), anotacao="chefe final", spoiler="on", duracao="1440")
    s = sessoes(app)[0]
    assert (s.jogada_em, s.duracao_min, s.inicio.strftime("%H:%M"), s.plataforma_id, s.anotacao, s.spoiler) == (
        date(2026, 11, 10), 1440, "20:15", pid, "chefe final", True)


def test_dia_02_hoje_em_belem_vale_amanha_nao(app, ana):
    assert sessao(ana, jogada_em="2026-11-16").status_code == 302  # já é 17/11 em UTC
    r = sessao(ana, jogada_em="2026-11-17")
    assert r.status_code == 400 and "A data não pode estar no futuro" in r.get_data(as_text=True)
    assert len(sessoes(app)) == 1


@pytest.mark.parametrize("antes,depois", [(None, "jogando"), ("planejado", "jogando"), ("jogando", "jogando"),
                                          ("jogado", "jogado"), ("abandonado", "abandonado")])
def test_dia_03_status_a_partir_de_cada_ponto(app, ana, antes, depois):
    if antes:
        ana.post("/jogos/jogo-teste/status", data={"status": antes})
    sessao(ana)
    assert status_de(app, "ana").value == depois


def test_dia_03_jogo_removido_responde_404(app, ana):
    from app.models import Game, now

    with app.app_context():
        Game.query.one().removed_at = now()
        db.session.commit()
    assert sessao(ana).status_code == 404 and sessoes(app) == []


def test_dia_04_zerei_grava_data_mais_antiga_e_status_jogado(app, ana):
    sessao(ana, jogada_em="2026-11-10", zerou="on")
    sessao(ana, jogada_em="2026-11-12", zerou="on")
    st = status_de(app, "ana")
    assert st.value == "jogado" and st.zerado_em == date(2026, 11, 10)
    sessao(ana, jogada_em="2026-11-01", zerou="on")
    assert status_de(app, "ana").zerado_em == date(2026, 11, 1)
    with app.app_context():
        assert Activity.query.filter_by(kind="jogado").count() == 1


def test_dia_04_apagar_ultima_sessao_zerada_limpa_sem_mudar_status(app, ana):
    sessao(ana, jogada_em="2026-11-10", zerou="on")
    sessao(ana, jogada_em="2026-11-12", zerou="on")
    primeira, segunda = sessoes(app)
    ana.post(f"/sessoes/{primeira.id}/apagar")
    assert status_de(app, "ana").zerado_em == date(2026, 11, 12)
    ana.post(f"/sessoes/{segunda.id}/apagar")
    st = status_de(app, "ana")
    assert st.zerado_em is None and st.value == "jogado"


def test_dia_05_so_o_dono_edita_ou_apaga(app, ana):
    sessao(ana)
    sid = sessoes(app)[0].id
    make_user(app, "bia")
    outro = app.test_client()
    assert outro.post(f"/sessoes/{sid}/apagar").headers["Location"].startswith("/entrar")
    login(outro, "bia")
    assert outro.post(f"/sessoes/{sid}/apagar").status_code == 403
    assert outro.post(f"/sessoes/{sid}/editar", data={"jogada_em": "2026-11-10", "duracao": "10"}).status_code == 403
    assert len(sessoes(app)) == 1


def test_dia_05_editar_valida_e_grava(app, ana):
    sessao(ana)
    sid = sessoes(app)[0].id
    assert ana.get(f"/sessoes/{sid}/editar").status_code == 200
    r = ana.post(f"/sessoes/{sid}/editar", data={"jogada_em": "2026-11-20", "duracao": "30"})
    assert r.status_code == 400 and "futuro" in r.get_data(as_text=True)
    ana.post(f"/sessoes/{sid}/editar", data={"jogada_em": "2026-11-11", "duracao": "2h"})
    s = sessoes(app)[0]
    assert (s.jogada_em, s.duracao_min) == (date(2026, 11, 11), 120)


def test_dia_06_diario_ordenado_por_mes_com_filtro(app, ana):
    make_game(app, "Outro Jogo")
    sessao(ana, jogada_em="2026-10-30", anotacao="outubro")
    sessao(ana, jogada_em="2026-11-02", duracao="1h30", anotacao="segredo", spoiler="on")
    sessao(ana, slug="outro-jogo", jogada_em="2026-11-05", duracao="45", zerou="on")
    html = ana.get("/diario").get_data(as_text=True)
    assert html.index("novembro de 2026") < html.index("outubro de 2026")
    assert html.index("Outro Jogo") < html.index("1h 30min")
    assert "zerei" in html and "Anotação com spoiler" in html
    filtrado = ana.get("/diario?jogo=outro-jogo").get_data(as_text=True)
    assert "Outro Jogo" in filtrado and "1h 30min" not in filtrado


def test_dia_06_exige_login(client):
    assert client.get("/diario").status_code == 302


def test_dia_07_diario_de_outra_pessoa_e_vazio(app, ana):
    sessao(ana, anotacao="nota publica")
    make_user(app, "bia")
    assert "nota publica" in app.test_client().get("/u/ana/diario").get_data(as_text=True)
    assert "Nenhuma sessão" in app.test_client().get("/u/bia/diario").get_data(as_text=True)


def test_dia_08_soma_por_jogo_intervalo_e_origem(app, ana):
    from app.diario import minutos_registrados
    from app.models import Game

    make_game(app, "Outro Jogo")
    sessao(ana, jogada_em="2026-11-01", duracao="60")
    sessao(ana, jogada_em="2026-11-05", duracao="30")
    sessao(ana, slug="outro-jogo", jogada_em="2026-11-03", duracao="15")
    with app.app_context():
        a, g = uid(app, "ana"), Game.query.filter_by(slug="jogo-teste").one().id
        db.session.add(SessaoJogo(user_id=a, game_id=g, jogada_em=HOJE, duracao_min=500, origem="steam"))
        db.session.commit()
        assert minutos_registrados(a) == 605
        assert minutos_registrados(a, game_id=g) == 590
        assert minutos_registrados(a, de=date(2026, 11, 3), ate=date(2026, 11, 5)) == 45
        assert minutos_registrados(a, origens=("manual",)) == 105
        assert minutos_registrados(uid(app, "ana") + 99) == 0


def conta_consultas(app, client, url):
    consultas = []

    def conta(*a):
        consultas.append(1)
    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", conta)
        resp = client.get(url)
        event.remove(db.engine, "before_cursor_execute", conta)
    return resp, len(consultas)


def test_dia_06_consultas_do_diario_nao_crescem_com_as_sessoes(app, ana):
    sessao(ana, jogada_em="2026-11-01", duracao="30")
    _, com_uma = conta_consultas(app, ana, "/diario")
    for i in range(2, 8):
        make_game(app, f"Outro {i}")
        sessao(ana, slug=f"outro-{i}", jogada_em=f"2026-11-0{i}", duracao="30")
    resp, com_sete = conta_consultas(app, ana, "/diario")
    assert resp.status_code == 200 and com_sete == com_uma


def test_dia_09_horas_e_zerados_no_perfil(app, ana):
    sessao(ana, duracao="12h", zerou="on")
    sessao(ana, jogada_em="2026-11-11", duracao="30")
    html = app.test_client().get("/u/ana").get_data(as_text=True)
    assert "12h 30min" in html and "<strong>1</strong> jogo zerado" in html


def test_dia_09_selo_de_horas_na_pagina_do_jogo_com_uma_consulta(app, ana):
    from app.models import Game

    def autores(nomes, minutos):
        with app.app_context():
            gid = Game.query.one().id
            for n, m in zip(nomes, minutos):
                make_user(app, n)
                db.session.add(Review(user_id=uid(app, n), game_id=gid, score=8, text="bom"))
                db.session.add(SessaoJogo(user_id=uid(app, n), game_id=gid, jogada_em=HOJE, duracao_min=m))
            db.session.commit()
    autores(["u0"], [30])
    _, com_um = conta_consultas(app, app.test_client(), "/jogos/jogo-teste")
    autores([f"u{i}" for i in range(1, 6)], [60 * i + 30 for i in range(1, 6)])
    resp, com_seis = conta_consultas(app, app.test_client(), "/jogos/jogo-teste")
    html = resp.get_data(as_text=True)
    assert "&lt;1h registradas" in html and "5h registradas" in html and "1h registradas" in html
    assert com_seis == com_um


def test_dia_10_uma_atividade_por_jogo_e_dia(app, ana):
    make_user(app, "bia")
    with app.app_context():
        db.session.add(Follow(follower_id=uid(app, "bia"), followed_id=uid(app, "ana")))
        db.session.commit()
    sessao(ana, duracao="60")
    sessao(ana, duracao="60")
    sessao(ana, jogada_em="2026-11-11", duracao="30")
    with app.app_context():
        atividades = Activity.query.filter_by(kind="sessao").order_by(Activity.dia).all()
        assert [(a.dia.day, a.valor) for a in atividades] == [(10, 120), (11, 30)]
    bia = app.test_client()
    login(bia, "bia")
    assert "por 2h" in bia.get("/feed").get_data(as_text=True)
    primeira, segunda, terceira = sessoes(app)
    ana.post(f"/sessoes/{primeira.id}/apagar")
    with app.app_context():
        assert Activity.query.filter_by(kind="sessao", dia=date(2026, 11, 10)).one().valor == 60
    ana.post(f"/sessoes/{segunda.id}/apagar")
    with app.app_context():
        assert Activity.query.filter_by(kind="sessao").count() == 1


def test_dia_11_so_o_dono_torna_o_perfil_privado(app, ana):
    ana.post("/u/ana/editar", data={"display_name": "Ana", "bio": "", "perfil_privado": "on"})
    with app.app_context():
        assert User.query.filter_by(username="ana").one().perfil_privado is True
    make_user(app, "bia")
    bia = app.test_client()
    login(bia, "bia")
    assert bia.post("/u/ana/editar", data={"display_name": "x", "bio": ""}).status_code == 403


@pytest.fixture
def privada(app, ana):
    """ana tem perfil privado, uma lista pública e segue a carla; bia não é seguida por ela."""
    from app.models import GameList

    sessao(ana, anotacao="anotacao secreta")
    with app.app_context():
        u = User.query.filter_by(username="ana").one()
        u.perfil_privado = True
        db.session.add(GameList(user_id=u.id, name="Pública da Ana"))
        db.session.commit()
    for n in ("bia", "carla"):
        make_user(app, n)
    make_user(app, "chefe", role="admin")
    with app.app_context():
        db.session.add(Follow(follower_id=uid(app, "ana"), followed_id=uid(app, "carla")))
        db.session.add(Follow(follower_id=uid(app, "bia"), followed_id=uid(app, "ana")))
        db.session.commit()
    return app


def cliente(app, nome=None):
    c = app.test_client()
    if nome:
        login(c, nome)
    return c


@pytest.mark.parametrize("rota", ["/u/ana/diario", "/u/ana/seguidores", "/u/ana/seguindo", "/listas/1"])
def test_dia_12_paginas_derivadas_respondem_404(privada, rota):
    assert cliente(privada).get(rota).status_code == 404
    assert cliente(privada, "bia").get(rota).status_code == 404  # seguir a dona não basta
    assert cliente(privada, "carla").get(rota).status_code == 200  # a dona segue a carla
    assert cliente(privada, "chefe").get(rota).status_code == 200
    assert cliente(privada, "ana").get(rota).status_code == 200


def test_dia_12_comparar_responde_404(privada):
    assert cliente(privada, "bia").get("/u/ana/comparar").status_code == 404
    assert cliente(privada, "carla").get("/u/ana/comparar").status_code == 200


def test_dia_12_perfil_mostra_so_o_cabecalho(privada):
    html = cliente(privada).get("/u/ana").get_data(as_text=True)
    assert "perfil privado" in html and "Seguir" in html and "Horas registradas" not in html
    assert "Horas registradas" in cliente(privada, "carla").get("/u/ana").get_data(as_text=True)


def test_dia_12_pode_ver_perfil_e_a_regra_unica(privada):
    from app.diario import ocultos_para, pode_ver_perfil

    with privada.app_context():
        dona = User.query.filter_by(username="ana").one()
        for nome, esperado in (("bia", False), ("carla", True), ("chefe", True), ("ana", True)):
            v = User.query.filter_by(username=nome).one()
            assert pode_ver_perfil(v, dona) is esperado
            ocultos = set(db.session.execute(ocultos_para(v)).scalars())
            assert (dona.id not in ocultos) is esperado
        assert pode_ver_perfil(None, dona) is False


def test_dia_13_feed_e_pagina_do_jogo_escondem_e_a_media_nao_muda(privada):
    from app.models import Game

    with privada.app_context():
        g = Game.query.one()
        db.session.add(Review(user_id=uid(privada, "ana"), game_id=g.id, score=2, text="resenha da ana"))
        db.session.commit()
    pagina = cliente(privada, "bia").get("/jogos/jogo-teste").get_data(as_text=True)
    assert "resenha da ana" not in pagina and "2.0" in pagina  # a nota conta na média
    assert "resenha da ana" in cliente(privada, "carla").get("/jogos/jogo-teste").get_data(as_text=True)
    assert "Jogo Teste" not in cliente(privada, "bia").get("/feed").get_data(as_text=True)
    api = cliente(privada).get("/api/jogos/jogo-teste/avaliacoes").get_json()
    assert api["items"] == [] and api["total"] == 0
