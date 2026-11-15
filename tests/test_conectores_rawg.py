from datetime import date
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from app.fontes import FONTES, importar, rawg
from app.models import Game, JogoFonte, db
from app.sync import sincronizar
from conftest import http, login, make_user

CHAVE = "chave-de-teste-0123456789abcdef"
RAIZ = Path(__file__).resolve().parent.parent
LISTA = "api.rawg.io/api/games?"


def resultado(i, nome):
    return {"id": i, "slug": f"jogo-{i}", "name": nome, "released": "2020-09-17",
            "background_image": f"https://media.rawg.io/media/games/{i}.jpg"}


def jogo(i, nome, **kw):
    """Formato de https://api.rawg.io/api/games/<id>."""
    return {"id": i, "slug": f"jogo-{i}", "name": nome, "released": "2020-09-17", "tba": False,
            "description": "<p>Fuja do <em>inferno</em>.</p>", "description_raw": "Fuja do inferno.",
            "background_image": f"https://media.rawg.io/media/games/{i}.jpg",
            "developers": [{"name": "Supergiant Games"}], "genres": [{"name": "Action"}, {"name": "RPG"}],
            "platforms": [{"platform": {"name": "PC"}}, {"platform": {"name": "macOS"}}, {"platform": {"name": "Nintendo Switch"}}],
            **kw}


def lojas(i, *links):
    return {"count": len(links), "next": None, "previous": None,
            "results": [{"id": n, "game_id": i, "store_id": s, "url": u} for n, (s, u) in enumerate(links)]}


STEAM = (1, "https://store.steampowered.com/app/1145360/Hades/")
GOG = (5, "https://www.gog.com/game/hades")
EPIC = (11, "https://www.epicgames.com/store/en-US/product/hades/home")
PLAYSTATION = (3, "https://store.playstation.com/en-us/product/UP0000-CUSA00000_00-HADES")


@pytest.fixture
def chave(monkeypatch):
    monkeypatch.setenv("RAWG_API_KEY", CHAVE)
    return CHAVE


@pytest.fixture
def loja(api, chave):
    api.rotas[LISTA] = {"count": 2, "next": None, "results": [resultado(1, "Hades"), resultado(2, "Celeste")]}
    api.rotas["api.rawg.io/api/games/1?"] = jogo(1, "Hades")
    api.rotas["api.rawg.io/api/games/1/stores"] = lojas(1, STEAM, GOG, EPIC, PLAYSTATION)
    api.rotas["api.rawg.io/api/games/2?"] = jogo(2, "Celeste")
    api.rotas["api.rawg.io/api/games/2/stores"] = lojas(2)
    return api


def vinculos(game):
    return sorted((v.fonte, v.external_id, v.url_loja) for v in game.fontes)


def test_gog03_registrada_e_le_a_chave_do_ambiente(app, loja):
    assert FONTES["rawg"] is rawg
    with app.app_context():
        assert importar("rawg", termo="hades") == (2, 0, 0)
    for url in loja.urls():
        assert parse_qs(urlsplit(url).query)["key"] == [CHAVE]
    assert parse_qs(urlsplit(loja.urls()[0]).query)["search"] == ["hades"]


def test_gog03_render_declara_a_chave_sem_valor():
    render = (RAIZ / "render.yaml").read_text()
    assert "- key: RAWG_API_KEY" in render
    trecho = render[render.index("- key: RAWG_API_KEY"):].splitlines()
    assert trecho[1].strip() == "sync: false" and "value" not in trecho[1]


@pytest.mark.parametrize("comando", ["import-games", "sync-games"])
def test_gog04_sem_chave_termina_com_mensagem_e_codigo_de_erro(app, api, comando):
    r = app.test_cli_runner().invoke(args=[comando, "--fonte", "rawg"])
    assert r.exit_code != 0 and "RAWG_API_KEY não configurada" in r.output
    assert "Traceback" not in r.output and isinstance(r.exception, SystemExit)
    assert api.pedidos == []


def test_gog04_sem_chave_a_rawg_some_do_painel(app, client):
    make_user(app, "root", role="admin")
    login(client, "root")
    assert 'value="rawg"' not in client.get("/admin/importar").data.decode()
    assert client.get("/admin/importar?fonte=rawg&q=hades").status_code == 400
    assert rawg.indisponivel() == "RAWG_API_KEY não configurada"


def test_gog04_chave_so_com_espacos_conta_como_ausente(monkeypatch):
    monkeypatch.setenv("RAWG_API_KEY", "   ")
    assert rawg.indisponivel() == "RAWG_API_KEY não configurada"
    with pytest.raises(RuntimeError, match="RAWG_API_KEY não configurada"):
        rawg.buscar("hades")


def test_gog05_chave_nunca_vai_para_o_banco(app, loja, tmp_path):
    cli = app.test_cli_runner()
    assert cli.invoke(args=["import-games", "--fonte", "rawg"]).exit_code == 0
    assert cli.invoke(args=["sync-games", "--fonte", "rawg"]).exit_code == 0
    with app.app_context():
        db.session.remove()
        db.engine.dispose()
    assert CHAVE.encode() not in (tmp_path / "t.db").read_bytes()


def test_gog05_chave_nao_aparece_em_erro_nem_na_saida(app, api, chave):
    api.rotas[LISTA] = HTTPError(f"https://api.rawg.io/api/games?key={chave}", 401, f"chave {chave} inválida", {}, None)
    r = app.test_cli_runner().invoke(args=["import-games", "--fonte", "rawg"])
    assert r.exit_code == 1 and "chave *** inválida" in r.output and chave not in r.output
    api.rotas[LISTA] = http(404)
    api.rotas[LISTA].filename = f"https://api.rawg.io/api/games?key={chave}"
    with pytest.raises(HTTPError) as exc:
        rawg.buscar("x")
    assert chave not in str(exc.value) and chave not in exc.value.filename and exc.value.code == 404


def test_gog05_chave_nao_aparece_nas_telas(app, client, loja):
    make_user(app, "root", role="admin")
    login(client, "root")
    paginas = [client.get("/admin/importar?fonte=rawg&q=hades"),
               client.post("/admin/importar", data={"fonte": "rawg", "q": "hades", "external_id": "1"},
                           follow_redirects=True)]
    assert all(p.status_code == 200 and CHAVE not in p.data.decode() for p in paginas)


def test_gog06_lojas_steam_gog_e_epic_viram_vinculos(app, loja):
    with app.app_context():
        importar("rawg", limite=1)
        g = Game.query.one()
        assert vinculos(g) == [
            ("epic", "hades", "https://store.epicgames.com/p/hades"),
            ("gog", "hades", "https://www.gog.com/game/hades"),
            ("rawg", "1", None),  # a origem dos dados; nunca guarda a URL da API
            ("steam", "1145360", "https://store.steampowered.com/app/1145360/Hades/"),
        ]
        assert [v.fonte for v in g.lojas] == ["steam", "gog", "epic"]  # a PlayStation é ignorada
    assert "api.rawg.io/api/games/1/stores" in loja.urls()[2]


def test_gog06_url_sem_id_reconhecivel_nao_gera_vinculo(app, loja):
    loja.rotas["api.rawg.io/api/games/1/stores"] = lojas(
        1, (1, "https://store.steampowered.com/search/?term=hades"), (5, "https://www.gog.com/en/games"),
        (11, "https://store.epicgames.com/en-US/browse"), (1, "https://outra-loja.com/app/1145360"))
    with app.app_context():
        importar("rawg", limite=1)
        assert [v.fonte for v in Game.query.one().fontes] == ["rawg"]


def test_gog06_vinculo_existente_so_ganha_o_link_que_falta(app, loja):
    with app.app_context():
        sincronizar("rawg", limite=1, modulo=None)
        g = Game.query.one()
        steam = next(v for v in g.fontes if v.fonte == "steam")
        steam.url_loja = None
        epic = next(v for v in g.fontes if v.fonte == "epic")
        epic.url_loja = "https://store.epicgames.com/p/hades-antigo"
        db.session.commit()
        loja.rotas["api.rawg.io/api/games/1/stores"] = lojas(1, STEAM, (11, "https://store.epicgames.com/p/hades"))
        assert sincronizar("rawg", limite=1) == (0, 0, 1, 0)
        assert steam.url_loja == "https://store.steampowered.com/app/1145360/Hades/"
        assert epic.url_loja == "https://store.epicgames.com/p/hades-antigo"  # link preenchido não muda
        assert JogoFonte.query.count() == 4 and steam.ultima_sincronizacao is not None


def test_gog06_link_de_outro_jogo_nao_gera_vinculo_nem_erro(app, loja):
    with app.app_context():
        importar("steam", modulo=type("F", (), {"listar": staticmethod(lambda limite, termo=None: [{
            "external_id": "1145360", "title": "Hades", "synopsis": "s", "release_date": date(2020, 9, 17),
            "developer": "Supergiant", "genres": [], "platforms": [], "cover_url": None}])}))
        assert importar("rawg", limite=1) == (1, 0, 0)
        steam_hades, rawg_hades = Game.query.filter_by(source="steam").one(), Game.query.filter_by(source="rawg").one()
        assert [(v.fonte, v.url_loja) for v in steam_hades.fontes] == [  # o link do outro jogo fica intacto
            ("steam", "https://store.steampowered.com/app/1145360")]
        assert sorted(v.fonte for v in rawg_hades.fontes) == ["epic", "gog", "rawg"]


def test_gog06_detalhe_mapeia_metadados_e_lojas(loja):
    assert rawg.detalhe("1") == {
        "external_id": "1", "title": "Hades", "synopsis": "Fuja do inferno.",
        "release_date": date(2020, 9, 17), "data_aproximada": False, "developer": "Supergiant Games",
        "genres": ["Action", "RPG"], "platforms": ["PC", "macOS", "Nintendo Switch"],
        "cover_url": "https://media.rawg.io/media/games/1.jpg",
        "lojas": [("steam", STEAM[1]), ("gog", GOG[1]), ("epic", EPIC[1])],
    }


def test_gog06_descricao_sem_versao_texto_usa_o_html_limpo(loja):
    loja.rotas["api.rawg.io/api/games/1?"] = jogo(1, "Hades", description_raw="", released="2020")
    j = rawg.detalhe("1")
    assert j["synopsis"] == "Fuja do inferno." and (j["release_date"], j["data_aproximada"]) == (date(2020, 1, 1), True)


@pytest.mark.parametrize("eid", ["hades", "", "1?x=2"])
def test_gog06_id_que_nao_e_numero_nao_vai_a_rede(loja, eid):
    assert rawg.detalhe(eid) is None and loja.pedidos == []


def test_gog07_sem_chave_todas_roda_as_outras_fontes_sem_erro(app, api, monkeypatch):
    monkeypatch.setitem(FONTES, "steam", type("F", (), {"listar": staticmethod(lambda limite, termo=None: [])}))
    api.rotas["catalog.gog.com"] = {"pages": 1, "products": []}
    r = app.test_cli_runner().invoke(args=["import-games", "--fonte", "todas"])
    assert r.exit_code == 0 and r.output.splitlines() == [
        "steam: importados=0 ignorados=0 falhas=0",
        "gog: importados=0 ignorados=0 falhas=0",
        "rawg: erro=RAWG_API_KEY não configurada",
    ]


def test_gog07_readme_explica_a_chave_no_env():
    readme, gitignore = (RAIZ / "README.md").read_text(), (RAIZ / ".gitignore").read_text()
    assert "RAWG_API_KEY" in readme and ".env" in readme and ".env" in gitignore.splitlines()
    assert "RAWG_API_KEY" in (RAIZ / ".env.example").read_text()


def test_gog11_429_nas_lojas_interrompe_o_lote(app, loja):
    loja.rotas["api.rawg.io/api/games/2/stores"] = http(429)
    r = app.test_cli_runner().invoke(args=["sync-games", "--fonte", "rawg"])
    assert r.output == "novos=1 atualizados=0 ignorados=0 falhas=0 interrompido=rate-limit\n"
    with app.app_context():
        assert [g.title for g in Game.query] == ["Hades"]


def test_gog12_painel_importa_um_e_em_lote_pela_rawg(app, client, loja):
    make_user(app, "root", role="admin")
    login(client, "root")
    html = client.get("/admin/importar?fonte=rawg&q=hades").data.decode()
    assert '<option value="rawg" selected>RAWG</option>' in html and "Celeste" in html
    r = client.post("/admin/importar", data={"fonte": "rawg", "q": "hades", "external_id": "1"})
    assert r.status_code == 302 and r.headers["Location"] == "/jogos/hades"
    r = client.post("/admin/importar/lote", data={"fonte": "rawg", "q": "hades", "ids": ["1", "2"],
                                                  "titulo-1": "Hades", "titulo-2": "Celeste"})
    assert "Importados: 1</strong> — Celeste" in r.data.decode() and "Já existiam: 1</strong> — Hades" in r.data.decode()
    with app.app_context():
        assert sorted(v.fonte for v in Game.query.filter_by(slug="hades").one().fontes) == ["epic", "gog", "rawg", "steam"]


@pytest.mark.parametrize("corpo", [{"detail": "x"}, {"results": None}, [], b"{"])
def test_gog15_lista_malformada_falha_sem_gravar(app, loja, corpo):
    loja.rotas[LISTA] = corpo
    with app.app_context():
        with pytest.raises(ValueError):
            importar("rawg")
        assert Game.query.count() == 0


def test_gog15_lojas_malformadas_contam_como_falha_do_item(app, loja):
    loja.rotas["api.rawg.io/api/games/1/stores"] = {"results": "x"}
    with app.app_context():
        assert importar("rawg") == (1, 0, 1)


def test_gog15_lista_vazia_timeout_e_429_seguido_de_sucesso(app, loja):
    loja.rotas[LISTA] = {"count": 0, "next": None, "results": []}
    with app.app_context():
        assert importar("rawg") == (0, 0, 0)
    loja.rotas[LISTA] = {"count": 2, "next": None, "results": [resultado(1, "Hades"), resultado(2, "Celeste")]}
    loja.rotas["api.rawg.io/api/games/1?"] = (http(429), jogo(1, "Hades"))
    loja.rotas["api.rawg.io/api/games/2?"] = TimeoutError("timed out")
    with app.app_context():
        assert importar("rawg") == (1, 0, 1)


def test_gog15_pagina_ate_o_limite_e_para_sem_proxima(loja):
    loja.rotas[LISTA] = ({"next": "p2", "results": [resultado(i, f"J{i}") for i in range(40)]},
                         {"next": None, "results": [resultado(i, f"J{i}") for i in range(40, 45)]})
    assert rawg._ids(100, None) == [str(i) for i in range(45)]
    assert [parse_qs(urlsplit(u).query).get("ordering") for u in loja.urls()] == [["-added"], ["-added"]]
