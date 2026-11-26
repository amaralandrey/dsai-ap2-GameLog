import json
from datetime import date, datetime

import pytest
from sqlalchemy import event

from app.models import Activity, AuditoriaToken, Follow, GameList, ListItem, Review, SessaoJogo, Status, User, db
from conftest import login, make_game, make_user
from test_api_v2_tokens import auth, relogio, token


@pytest.mark.parametrize("valor,esperado", [(8, 8), ("10", 10), (0, 0), (True, None), (8.5, None), ("8.5", None),
                                            (11, None), (-1, None), (None, None), ("", None), ("²", None),
                                            ("٣", None)])
def test_ap2_10_ler_nota(valor, esperado):
    from app.avaliacoes import ler_nota

    assert ler_nota(valor) == esperado


def sessao(app, username, slug, dia=date(2026, 11, 20), minutos=60, **kw):
    from app.models import Game

    with app.app_context():
        u = User.query.filter_by(username_lower=username).one()
        g = Game.query.filter_by(slug=slug).one()
        s = SessaoJogo(user_id=u.id, game_id=g.id, jogada_em=dia, duracao_min=minutos, origem="manual", **kw)
        db.session.add(s)
        db.session.commit()
        return s.id


def test_ap2_07_usuario_publico_sem_conta(app):
    from app.serializadores import usuario_publico

    uid = make_user(app, "ana")
    with app.app_context():
        u = db.session.get(User, uid)
        u.steam_id, u.bio = "76561198000000001", "oi"
        dados = usuario_publico(u)
        assert set(dados) == {"username", "nome", "bio", "perfil_privado", "membro_desde", "url"}
        texto = json.dumps(dados)
        assert "ana@ex.com" not in texto and "76561198000000001" not in texto and "1990" not in texto
        assert dados["url"].endswith("/u/ana")


def test_ap2_07_datas_iso_e_urls_absolutas(app, monkeypatch):
    from app.models import Game
    from app.serializadores import jogo, sessao as ser_sessao

    make_user(app, "ana")
    slug = make_game(app, "Alfa")
    sid = sessao(app, "ana", slug, zerou=True)
    monkeypatch.setenv("URL_PUBLICA", "https://x.test")
    with app.app_context():
        s = db.session.get(SessaoJogo, sid)
        dados = ser_sessao(s)
        assert dados["jogada_em"] == "2026-11-20" and dados["jogo"] == "alfa" and dados["zerou"] is True
        assert set(dados) == {"id", "jogo", "titulo", "jogada_em", "duracao_min", "inicio", "plataforma", "zerou",
                              "spoiler", "anotacao", "origem"}
        assert jogo(Game.query.one())["url"] == "https://x.test/jogos/alfa"


def test_ap2_07_avaliacao(app):
    from app.serializadores import avaliacao

    uid = make_user(app, "ana")
    slug = make_game(app, "Alfa")
    with app.app_context():
        from app.models import Game

        r = Review(user_id=uid, game_id=Game.query.filter_by(slug=slug).one().id, score=8, text="bom")
        db.session.add(r)
        db.session.commit()
        dados = avaliacao(r, 3)
        assert dados["usuario"] == "ana" and dados["jogo"] == "alfa" and dados["nota"] == 8 and dados["curtidas"] == 3
        assert "user_id" not in dados


def test_ap2_07_conquista_secreta_bloqueada(app):
    from app.models import Conquista, UsuarioConquista
    from app.serializadores import conquista

    uid = make_user(app, "ana")
    with app.app_context():
        c = Conquista(codigo="segredo-x", nome="Segredo", descricao="Faça X", categoria="critica", raridade="rara",
                      icone="?", metrica="avaliacoes", meta=3, secreta=True)
        db.session.add(c)
        db.session.flush()
        uc = UsuarioConquista(user_id=uid, conquista_id=c.id, progresso=1)
        db.session.add(uc)
        db.session.commit()
        dados = conquista(uc)
        assert dados["nome"] == "Conquista secreta" and dados["descricao"] is None
        assert dados["progresso"] == 1 and dados["meta"] == 3 and dados["desbloqueada_em"] is None
        uc.desbloqueada_em = datetime(2026, 11, 20, 12, 0)
        dados = conquista(uc)
        assert dados["nome"] == "Segredo" and dados["desbloqueada_em"] == "2026-11-20T12:00:00"


def test_ap2_03_401_uniforme(app, client, monkeypatch):
    from app.tokens import revogar
    from test_api_v2_tokens import linha

    relogio(monkeypatch, datetime(2026, 11, 20, 10, 0))
    valido, expirado, revogado = token(app), token(app, dias=30), token(app)
    with app.app_context():
        revogar(linha(revogado))
        linha(expirado).expira_em = datetime(2026, 11, 1)
        db.session.commit()
    assert client.get("/api/v2/eu", headers=auth(valido)).status_code == 200
    casos = [{}, auth("glp_" + "x" * 40), auth(expirado), auth(revogado), {"Authorization": "Basic eDp5"},
             {"Authorization": "Bearer"}]
    for headers in casos:
        r = client.get("/api/v2/eu", headers=headers)
        assert r.status_code == 401, headers
        assert r.get_json() == {"erro": "token inválido"}
        assert r.headers["WWW-Authenticate"] == "Bearer"
    assert client.get("/api/v2/jogos", headers=auth(revogado)).status_code == 401


def test_ap2_03_cookie_nao_autentica(app, client):
    make_user(app, "ana")
    texto_bia = token(app, "bia")
    login(client)
    assert client.get("/api/v2/eu").status_code == 401
    assert client.get("/api/v2/eu", headers=auth(texto_bia)).get_json()["username"] == "bia"


def test_ap2_03_sem_csrf(tmp_path):
    from app import create_app

    app = create_app({"TESTING": True, "CSRF": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path}/c.db"})
    texto = token(app)
    r = app.test_client().post("/api/v2/eu/diario", headers=auth(texto), json={})
    assert r.status_code != 400
    assert app.test_client().post("/listas", data={"name": "x"}).status_code == 400


def test_ap2_04_escopo_de_leitura(app, client):
    so_escrever = token(app, escopos=("escrever",))
    r = client.get("/api/v2/eu", headers=auth(so_escrever))
    assert r.status_code == 403
    assert r.get_json() == {"erro": "escopo insuficiente", "requer": "ler"}


def test_ap2_05_429_e_cabecalhos(app, client, monkeypatch):
    relogio(monkeypatch, datetime(2026, 11, 20, 10, 0, 30))
    texto = token(app)
    restantes = []
    for _ in range(60):
        r = client.get("/api/v2/eu", headers=auth(texto))
        assert r.status_code == 200
        assert r.headers["X-RateLimit-Limit"] == "60"
        restantes.append(int(r.headers["X-RateLimit-Remaining"]))
    assert restantes[0] == 59 and restantes[-1] == 0
    r = client.get("/api/v2/eu", headers=auth(texto))
    assert r.status_code == 429 and r.headers["Retry-After"] == "30"
    assert r.headers["X-RateLimit-Remaining"] == "0"
    relogio(monkeypatch, datetime(2026, 11, 20, 10, 1, 0))
    assert client.get("/api/v2/eu", headers=auth(texto)).status_code == 200


def test_ap2_06_uso_grava_ultimo_uso(app, client, monkeypatch):
    from test_api_v2_tokens import linha

    relogio(monkeypatch, datetime(2026, 11, 20, 10, 0))
    texto = token(app)
    client.get("/api/v2/jogos", headers=auth(texto))
    with app.app_context():
        assert linha(texto).ultimo_uso_em == datetime(2026, 11, 20, 13, 0)
        assert AuditoriaToken.query.filter_by(acao="escrita").count() == 0


def test_ap2_11_paginacao_com_proxima(app, client):
    for i in range(25):
        make_game(app, f"Jogo {i:02d}", genre="RPG" if i % 2 else "Ação")
    r = client.get("/api/v2/jogos?per_page=10&ordem=titulo").get_json()
    assert set(r) == {"items", "page", "pages", "total", "per_page", "proxima"}
    assert (r["page"], r["pages"], r["total"], r["per_page"]) == (1, 3, 25, 10)
    assert r["proxima"].startswith("http://localhost/api/v2/jogos?")
    assert "page=2" in r["proxima"] and "per_page=10" in r["proxima"] and "ordem=titulo" in r["proxima"]
    assert client.get("/api/v2/jogos?per_page=10&page=3").get_json()["proxima"] is None
    assert client.get("/api/v2/jogos?per_page=99").get_json()["per_page"] == 20
    filtrado = client.get("/api/v2/jogos?genero=rpg&per_page=5").get_json()
    assert filtrado["total"] == 12 and "genero=rpg" in filtrado["proxima"]


def test_ap2_13_indice_docs_e_cache(app, client):
    texto = token(app)
    r = client.get("/api/v2")
    corpo = r.get_json()
    assert corpo["versao"] == "2" and corpo["docs"] == "http://localhost/api/v2/docs"
    assert {"metodo": "PUT", "rota": "/api/v2/eu/status/<slug>", "escopo": "escrever"} in corpo["endpoints"]
    assert {"metodo": "GET", "rota": "/api/v2/eu", "escopo": "ler"} in corpo["endpoints"]
    assert r.headers["Access-Control-Allow-Origin"] == "*"
    docs = client.get("/api/v2/docs")
    assert docs.status_code == 200 and docs.mimetype == "text/html" and "curl" in docs.get_data(as_text=True)
    nada = client.get("/api/v2/nada")
    assert nada.status_code == 404 and nada.is_json
    metodo = client.delete("/api/v2")
    assert metodo.status_code == 405 and metodo.is_json
    assert client.get("/api/v2/jogos").headers["Cache-Control"] == "public, max-age=60"
    assert client.get("/api/v2/jogos", headers=auth(texto)).headers["Cache-Control"] == "no-store"
    assert client.get("/api/v2/eu", headers=auth(texto)).headers["Cache-Control"] == "no-store"
    assert "/api/v2" in client.get("/api").get_json()["endpoints"]


def test_ap2_13_dados_de_usuario_sem_token_no_store(app, client):
    make_user(app, "bia")
    slug = make_game(app, "Alfa")
    sessao(app, "bia", slug)
    for rota in ("/api/v2/usuarios/bia", "/api/v2/usuarios/bia/diario", "/api/v2/rankings/horas?janela=sempre"):
        r = client.get(rota)
        assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    assert client.get("/api/v2/jogos").headers["Cache-Control"] == "public, max-age=60"
    assert client.get(f"/api/v2/jogos/{slug}").headers["Cache-Control"] == "public, max-age=60"


def test_ap2_13_preflight(client):
    r = client.options("/api/v2/eu/status/alfa", headers={"Origin": "https://x.test",
                                                         "Access-Control-Request-Method": "PUT",
                                                         "Access-Control-Request-Headers": "authorization"})
    assert r.status_code == 200
    assert "Authorization" in r.headers["Access-Control-Allow-Headers"]
    assert "PUT" in r.headers["Access-Control-Allow-Methods"]
    assert r.headers["Access-Control-Allow-Origin"] == "*"


def jogo_id(app, slug):
    from app.models import Game

    with app.app_context():
        return Game.query.filter_by(slug=slug).one().id


def privado(app, username, valor=True):
    with app.app_context():
        User.query.filter_by(username_lower=username).one().perfil_privado = valor
        db.session.commit()


def test_ap2_08_jogos_publicos_loja_e_fontes(app, client):
    from app.models import Game, JogoFonte

    alfa, beta, gama = make_game(app, "Alfa"), make_game(app, "Beta"), make_game(app, "Gama")
    with app.app_context():
        a = Game.query.filter_by(slug=alfa).one()
        db.session.add(JogoFonte(game_id=a.id, fonte="gog", external_id="1", url_loja="https://gog.com/alfa"))
        b = Game.query.filter_by(slug=beta).one()
        b.mesclado_em_id, b.removed_at = a.id, datetime(2026, 11, 1)
        Game.query.filter_by(slug=gama).one().removed_at = datetime(2026, 11, 1)
        db.session.commit()
    lista = client.get("/api/v2/jogos?loja=gog").get_json()
    assert [j["slug"] for j in lista["items"]] == ["alfa"]
    assert {"nota_media", "avaliacoes", "url", "capa"} <= set(lista["items"][0])
    detalhe = client.get("/api/v2/jogos/alfa").get_json()
    assert detalhe["fontes"] == [{"fonte": "gog", "external_id": "1", "url": "https://gog.com/alfa"}]
    assert {"sinopse", "histograma", "precos", "menor_preco"} <= set(detalhe)
    removido = client.get("/api/v2/jogos/gama")
    assert removido.status_code == 404 and removido.is_json
    mesclado = client.get("/api/v2/jogos/beta/avaliacoes?page=2")
    assert mesclado.status_code == 301 and mesclado.headers["Location"].endswith("/api/v2/jogos/alfa/avaliacoes?page=2")
    assert client.get("/api/v2/jogos/beta").headers["Location"].endswith("/api/v2/jogos/alfa")


def test_ap2_08_avaliacao_de_perfil_privado(app, client):
    from test_perfil import avalia

    make_user(app, "ana")
    make_user(app, "bia")
    slug = make_game(app, "Alfa")
    gid = jogo_id(app, slug)
    avalia(app, "ana", gid, 10, "ótimo")
    avalia(app, "bia", gid, 4, "fraco")
    privado(app, "bia")
    sem_token = client.get("/api/v2/jogos/alfa/avaliacoes").get_json()
    assert [a["usuario"] for a in sem_token["items"]] == ["ana"]
    assert client.get("/api/v2/jogos/alfa").get_json()["nota_media"] == 7.0
    texto_ana = token(app, "ana")
    assert [a["usuario"] for a in client.get("/api/v2/jogos/alfa/avaliacoes", headers=auth(texto_ana))
            .get_json()["items"]] == ["ana"]
    with app.app_context():
        ana, bia = (User.query.filter_by(username_lower=n).one().id for n in ("ana", "bia"))
        db.session.add(Follow(follower_id=bia, followed_id=ana))
        db.session.commit()
    com_token = client.get("/api/v2/jogos/alfa/avaliacoes", headers=auth(texto_ana)).get_json()
    assert sorted(a["usuario"] for a in com_token["items"]) == ["ana", "bia"]


def test_ap2_09_eu(app, client):
    from app.models import Conquista, UsuarioConquista

    texto = token(app, "ana")
    texto_bia = token(app, "bia")
    alfa, beta = make_game(app, "Alfa"), make_game(app, "Beta")
    sessao(app, "ana", alfa, date(2026, 11, 5))
    sessao(app, "ana", alfa, date(2026, 11, 15))
    sessao(app, "ana", beta, date(2026, 11, 6))
    with app.app_context():
        ana, bia = (User.query.filter_by(username_lower=n).one().id for n in ("ana", "bia"))
        privada = GameList(user_id=ana, name="Secreta", public=False)
        da_bia = GameList(user_id=bia, name="Da Bia", public=True)
        db.session.add_all([privada, da_bia])
        db.session.flush()
        db.session.add(ListItem(list_id=privada.id, game_id=jogo_id(app, alfa), position=0))
        db.session.add(Status(user_id=ana, game_id=jogo_id(app, beta), value="planejado"))
        db.session.add(Status(user_id=ana, game_id=jogo_id(app, alfa), value="jogando"))
        c = Conquista(codigo="c1", nome="C1", descricao="d", categoria="critica", raridade="comum", icone="*",
                      metrica="avaliacoes", meta=5)
        db.session.add(c)
        db.session.flush()
        db.session.add(UsuarioConquista(user_id=ana, conquista_id=c.id, progresso=2))
        db.session.commit()
        pid, bid = privada.id, da_bia.id
    h = auth(texto)
    listas = client.get("/api/v2/eu/listas", headers=h).get_json()
    assert [(item["id"], item["nome"], item["publica"]) for item in listas["items"]] == [(pid, "Secreta", False)]
    uma = client.get(f"/api/v2/eu/listas/{pid}", headers=h).get_json()
    assert uma["itens"][0]["jogo"]["slug"] == "alfa"
    assert client.get(f"/api/v2/eu/listas/{bid}", headers=h).status_code == 404
    assert client.get(f"/api/v2/eu/listas/{pid}", headers=auth(texto_bia)).status_code == 404
    diario = client.get("/api/v2/eu/diario?de=2026-11-01&ate=2026-11-10&jogo=alfa", headers=h).get_json()
    assert [s["jogada_em"] for s in diario["items"]] == ["2026-11-05"]
    assert client.get("/api/v2/eu/diario", headers=h).get_json()["total"] == 3
    ruim = client.get("/api/v2/eu/diario?de=ontem", headers=h)
    assert ruim.status_code == 422 and "de" in ruim.get_json()["campos"]
    st = client.get("/api/v2/eu/status?status=jogando", headers=h).get_json()["items"]
    assert [(s["jogo"]["slug"], s["status"]) for s in st] == [("alfa", "jogando")]
    assert client.get("/api/v2/eu/status", headers=h).get_json()["total"] == 2
    conq = client.get("/api/v2/eu/conquistas", headers=h).get_json()["items"]
    assert [(c["codigo"], c["progresso"]) for c in conq] == [("c1", 2)]


def test_ap2_12_perfil_privado_404_igual_inexistente(app, client):
    from app.bloqueios import bloquear

    texto = token(app, "ana")
    make_user(app, "bia")
    make_user(app, "carla")
    slug = make_game(app, "Alfa")
    sessao(app, "carla", slug)
    privado(app, "bia")
    inexistente = client.get("/api/v2/usuarios/naoexiste")
    for url in ("/api/v2/usuarios/bia", "/api/v2/usuarios/bia/diario"):
        r = client.get(url, headers=auth(texto))
        assert r.status_code == 404 and r.get_json() == inexistente.get_json()
    assert client.get("/api/v2/usuarios/carla").get_json()["username"] == "carla"
    assert client.get("/api/v2/usuarios/carla/diario").get_json()["items"][0]["jogo"] == "alfa"
    with app.app_context():
        bloquear(User.query.filter_by(username_lower="carla").one(), User.query.filter_by(username_lower="ana").one())
        db.session.commit()
    assert client.get("/api/v2/usuarios/carla", headers=auth(texto)).status_code == 404
    assert client.get("/api/v2/usuarios/carla").status_code == 200


def test_ap2_12_ranking_segue_rnk(app, client):
    texto = token(app, "ana")
    for nome in ("bia", "carla", "davi"):
        make_user(app, nome)
    slug = make_game(app, "Alfa")
    for nome, minutos in (("ana", 120), ("bia", 300), ("carla", 200), ("davi", 60)):
        sessao(app, nome, slug, minutos=minutos)
    privado(app, "bia")
    with app.app_context():
        User.query.filter_by(username_lower="carla").one().nascimento = date(2009, 6, 1)
        ana, davi = (User.query.filter_by(username_lower=n).one().id for n in ("ana", "davi"))
        db.session.add(Follow(follower_id=ana, followed_id=davi))
        db.session.commit()
    glob = client.get("/api/v2/rankings/horas?janela=sempre").get_json()
    assert [(i["posicao"], i["usuario"]["username"], i["valor"]) for i in glob["items"]] == [(1, "ana", 120),
                                                                                             (2, "davi", 60)]
    seguindo = client.get("/api/v2/rankings/horas?janela=sempre&escopo=seguindo", headers=auth(texto)).get_json()
    assert [i["usuario"]["username"] for i in seguindo["items"]] == ["ana", "davi"]
    assert client.get("/api/v2/rankings/horas?escopo=seguindo").status_code == 401
    assert client.get("/api/v2/rankings/xyz").status_code == 404


@pytest.mark.parametrize("escopo", ["seguindo", "clube"])
def test_ap2_04_ranking_por_escopo_sem_ler_403(app, client, escopo):
    so_escrever = token(app, "ana", escopos=("escrever",))
    r = client.get(f"/api/v2/rankings/horas?escopo={escopo}", headers=auth(so_escrever))
    assert r.status_code == 403
    assert r.get_json() == {"erro": "escopo insuficiente", "requer": "ler"}
    assert "WWW-Authenticate" not in r.headers
    assert client.get(f"/api/v2/rankings/horas?escopo={escopo}").status_code == 401


def test_ap2_14_consultas_listagem_20(app, client):
    texto = token(app, "ana")
    slug = make_game(app, "Alfa")
    for d in range(1, 21):
        sessao(app, "ana", slug, date(2026, 11, d), minutos=30)
    consultas = []
    with app.app_context():
        def conta(*a):
            consultas.append(a[2])

        event.listen(db.engine, "before_cursor_execute", conta)
        try:
            r = client.get("/api/v2/eu/diario", headers=auth(texto))
        finally:
            event.remove(db.engine, "before_cursor_execute", conta)
    assert r.status_code == 200 and len(r.get_json()["items"]) == 20
    assert len(consultas) <= 8, consultas


def put(client, url, texto, corpo):
    return client.put(url, headers=auth(texto), json=corpo)


def test_ap2_04_escopo_de_escrita(app, client):
    so_ler = token(app, escopos=("ler",))
    make_game(app, "Alfa")
    r = put(client, "/api/v2/eu/status/alfa", so_ler, {"status": "jogado"})
    assert r.status_code == 403 and r.get_json() == {"erro": "escopo insuficiente", "requer": "escrever"}
    with app.app_context():
        assert Status.query.count() == 0


def test_ap2_10_status_mesma_regra_da_interface(app, client):
    texto = token(app)
    make_game(app, "Alfa")
    r = put(client, "/api/v2/eu/status/alfa", texto, {"status": "jogado"})
    assert r.status_code == 200 and r.get_json() == {"jogo": "alfa", "status": "jogado"}
    put(client, "/api/v2/eu/status/alfa", texto, {"status": "jogado"})
    with app.app_context():
        assert Status.query.one().value == "jogado"
        assert Activity.query.filter_by(kind="jogado").count() == 1
    assert put(client, "/api/v2/eu/status/alfa", texto, {"status": None}).get_json()["status"] is None
    with app.app_context():
        assert Status.query.count() == 0
    ruim = put(client, "/api/v2/eu/status/alfa", texto, {"status": "zerado"})
    assert ruim.status_code == 422 and "status" in ruim.get_json()["campos"]
    assert put(client, "/api/v2/eu/status/nada", texto, {"status": "jogado"}).status_code == 404


def test_ap2_10_avaliacao_ava_11_e_422(app, client):
    texto = token(app)
    make_game(app, "Alfa")
    r = put(client, "/api/v2/eu/avaliacoes/alfa", texto, {"nota": 8, "texto": "bom", "spoiler": False})
    assert r.status_code == 201 and r.get_json()["nota"] == 8
    with app.app_context():
        assert Status.query.one().value == "jogado"
    editada = put(client, "/api/v2/eu/avaliacoes/alfa", texto, {"nota": 9})
    assert editada.status_code == 200 and editada.get_json()["editada"] is True
    for corpo, campo in (({"nota": 11}, "nota"), ({"nota": True}, "nota"), ({"nota": 8.5}, "nota"),
                         ({"nota": 7, "texto": "x" * 5001}, "texto"), ({"nota": 7, "spoiler": "sim"}, "spoiler"),
                         ({"nota": 7, "texto": 3}, "texto")):
        r = put(client, "/api/v2/eu/avaliacoes/alfa", texto, corpo)
        assert r.status_code == 422 and campo in r.get_json()["campos"], corpo
    for corpo in ([1], "8", None):
        r = client.put("/api/v2/eu/avaliacoes/alfa", headers=auth(texto), data=json.dumps(corpo),
                       content_type="application/json")
        assert r.status_code == 422 and r.get_json()["erro"] == "corpo JSON inválido"
    r = client.put("/api/v2/eu/avaliacoes/alfa", headers=auth(texto), data="nota=8")
    assert r.status_code == 422
    with app.app_context():
        assert Review.query.one().score == 9


def test_ap2_10_diario_planejado_vira_jogando(app, client, monkeypatch):
    relogio(monkeypatch, datetime(2026, 11, 20, 10, 0))
    texto = token(app)
    make_game(app, "Alfa")
    with app.app_context():
        db.session.add(Status(user_id=User.query.one().id, game_id=jogo_id(app, "alfa"), value="planejado"))
        db.session.commit()
    r = client.post("/api/v2/eu/diario", headers=auth(texto),
                    json={"jogo": "alfa", "jogada_em": "2026-11-19", "duracao": "1h30", "zerou": False})
    assert r.status_code == 201 and r.get_json()["duracao_min"] == 90 and r.get_json()["jogada_em"] == "2026-11-19"
    with app.app_context():
        assert Status.query.one().value == "jogando"
    for corpo, campo in (({"duracao": 0}, "duracao"), ({"jogada_em": "2026-12-01"}, "jogada_em"),
                         ({"zerou": "sim"}, "zerou"), ({"jogo": "nada"}, "jogo"), ({"duracao": 1500}, "duracao")):
        base = {"jogo": "alfa", "jogada_em": "2026-11-19", "duracao": 30, **corpo}
        r = client.post("/api/v2/eu/diario", headers=auth(texto), json=base)
        assert r.status_code == 422 and campo in r.get_json()["campos"], corpo


def test_ap2_10_lista(app, client):
    texto = token(app, "ana")
    texto_bia = token(app, "bia")
    make_game(app, "Alfa")
    with app.app_context():
        gl = GameList(user_id=User.query.filter_by(username_lower="ana").one().id, name="Minha", public=True)
        db.session.add(gl)
        db.session.commit()
        lid = gl.id
    r = client.post(f"/api/v2/eu/listas/{lid}/jogos", headers=auth(texto), json={"jogo": "alfa"})
    assert r.status_code == 201 and r.get_json()["itens"][0]["jogo"]["slug"] == "alfa"
    with app.app_context():
        assert Activity.query.filter_by(kind="lista_jogo").count() == 1
    dup = client.post(f"/api/v2/eu/listas/{lid}/jogos", headers=auth(texto), json={"jogo": "alfa"})
    assert dup.status_code == 422 and "jogo" in dup.get_json()["campos"]
    assert client.post(f"/api/v2/eu/listas/{lid}/jogos", headers=auth(texto_bia), json={"jogo": "alfa"}).status_code == 404
    assert client.post(f"/api/v2/eu/listas/{lid}/jogos", headers=auth(texto), json={"jogo": "x"}).status_code == 422


def test_ap2_10_efeitos_iguais_a_interface(app, client, monkeypatch):
    from app import avaliacoes

    eventos = []
    monkeypatch.setattr(avaliacoes, "emitir_evento", lambda uid, tipo, **ctx: eventos.append(tipo))
    texto = token(app)
    make_game(app, "Alfa")
    put(client, "/api/v2/eu/avaliacoes/alfa", texto, {"nota": 8})
    assert eventos == ["avaliacao", "status"]
    with app.app_context():
        assert Activity.query.filter_by(kind="avaliou").count() == 1


def test_ap2_06_escrita_auditada_sem_corpo(app, client):
    texto = token(app)
    make_game(app, "Alfa")
    put(client, "/api/v2/eu/avaliacoes/alfa", texto, {"nota": 8, "texto": "segredo-do-corpo"})
    put(client, "/api/v2/eu/status/alfa", texto, {"status": "abandonado"})
    client.get("/api/v2/eu", headers=auth(texto))
    with app.app_context():
        linhas = AuditoriaToken.query.filter_by(acao="escrita").order_by(AuditoriaToken.id).all()
        assert [(a.metodo, a.rota, a.status_http) for a in linhas] == [
            ("PUT", "/api/v2/eu/avaliacoes/alfa", 201), ("PUT", "/api/v2/eu/status/alfa", 200)]
        tudo = json.dumps([[str(getattr(a, c.name)) for c in AuditoriaToken.__table__.columns]
                           for a in AuditoriaToken.query])
        assert texto not in tudo and "segredo-do-corpo" not in tudo



def test_ap2_03_conta_suspensa_ou_pendente_perde_o_token(app, client):
    texto = token(app)
    assert client.get("/api/v2/eu", headers=auth(texto)).status_code == 200
    with app.app_context():
        User.query.one().suspensa_em = datetime(2026, 11, 20)
        db.session.commit()
    assert client.get("/api/v2/eu", headers=auth(texto)).status_code == 401
    assert client.get("/api/v2/jogos", headers=auth(texto)).status_code == 401
    with app.app_context():
        u = User.query.one()
        u.suspensa_em, u.aguardando_responsavel, u.nascimento = None, True, date(2012, 1, 1)
        db.session.commit()
    assert client.get("/api/v2/eu", headers=auth(texto)).status_code == 401
    with app.app_context():
        u = User.query.one()
        u.aguardando_responsavel, u.nascimento = False, None
        db.session.commit()
    assert client.get("/api/v2/eu", headers=auth(texto)).status_code == 401


def test_ap2_09_conquistas_por_tempo_seguem_pmn(app, client):
    from app.models import Conquista, UsuarioConquista

    texto = token(app)
    with app.app_context():
        ana = User.query.one()
        ana.nascimento = date(2009, 6, 1)
        for codigo, por_tempo in (("c-tempo", True), ("c-normal", False)):
            c = Conquista(codigo=codigo, nome=codigo, descricao="d", categoria="diario", raridade="comum", icone="*",
                          metrica="horas", meta=5, por_tempo=por_tempo)
            db.session.add(c)
            db.session.flush()
            db.session.add(UsuarioConquista(user_id=ana.id, conquista_id=c.id))
        db.session.commit()
    itens = client.get("/api/v2/eu/conquistas", headers=auth(texto)).get_json()["items"]
    assert [c["codigo"] for c in itens] == ["c-normal"]


def test_ap2_04_token_so_escrita_nao_le_como_o_dono(app, client):
    so_escrever, ler = token(app, "ana", escopos=("escrever",)), token(app, "ana", escopos=("ler",))
    make_user(app, "bia")
    slug = make_game(app, "Alfa")
    sessao(app, "bia", slug)
    privado(app, "bia")
    with app.app_context():
        ana, bia = (User.query.filter_by(username_lower=n).one().id for n in ("ana", "bia"))
        db.session.add(Follow(follower_id=bia, followed_id=ana))
        db.session.commit()
    assert client.get("/api/v2/usuarios/bia/diario", headers=auth(ler)).status_code == 200
    assert client.get("/api/v2/usuarios/bia/diario", headers=auth(so_escrever)).status_code == 404
    assert client.get("/api/v2/usuarios/bia").status_code == 404
