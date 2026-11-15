from datetime import date
from urllib.parse import parse_qs, urlsplit

import pytest

from app.fontes import FONTES, base, gog, importar
from app.models import Game, JogoFonte
from app.sync import sincronizar
from conftest import http

CATALOGO = "catalog.gog.com/v1/catalog"


def produto(i, titulo):
    return {"id": str(i), "slug": f"jogo_{i}", "title": titulo, "productType": "game",
            "coverVertical": f"https://images.gog-statics.com/v{i}.jpg", "coverHorizontal": f"https://images.gog-statics.com/h{i}.png"}


def catalogo(*produtos, pages=1):
    return {"pages": pages, "productCount": len(produtos), "products": list(produtos)}


def jogo(i, titulo, **kw):
    """Formato de https://api.gog.com/v2/games/<id> (HAL: _links e _embedded)."""
    d = {
        "description": '<div><img src="x.png"></div><p>Um <b>RPG</b> de fantasia &amp; escolhas.</p><p>Sem DRM.</p>',
        "overview": "<p>Resumo</p>",
        "_links": {"store": {"href": f"https://www.gog.com/en/game/jogo_{i}"},
                   "boxArtImage": {"href": f"https://images.gog-statics.com/box{i}.jpg"}},
        "_embedded": {
            "product": {"id": int(i), "title": titulo, "globalReleaseDate": "2015-05-19T00:00:00+02:00"},
            "developers": [{"name": "CD PROJEKT RED"}, {"name": "Outra"}],
            "tags": [{"name": "Role-playing"}, {"name": "Action"}, {"name": "Fantasy"}],
            "supportedOperatingSystems": [{"operatingSystem": {"name": "windows"}}, {"operatingSystem": {"name": "osx"}}],
        },
    }
    d["_embedded"]["product"].update(kw)
    return d


@pytest.fixture
def loja(api):
    api.rotas[CATALOGO] = catalogo(produto(1, "The Witcher"), produto(2, "Gwent"))
    api.rotas["api.gog.com/v2/games/1"] = jogo(1, "The Witcher")
    api.rotas["api.gog.com/v2/games/2"] = jogo(2, "Gwent")
    return api


def test_gog01_registrada_e_com_o_contrato_das_fontes():
    assert FONTES["gog"] is gog
    assert all(callable(getattr(gog, f)) for f in ("listar", "buscar", "detalhe"))
    assert getattr(gog, "indisponivel", None) is None  # sem chave: sempre ligada


def test_gog01_lista_pelo_catalogo_sem_country_code_e_detalha_pela_api(app, loja):
    with app.app_context():
        assert importar("gog", limite=5, termo="witcher") == (2, 0, 0)
    cat, *detalhes = loja.pedidos
    partes = urlsplit(cat.full_url)
    assert f"{partes.netloc}{partes.path}" == CATALOGO
    params = parse_qs(partes.query)
    assert params["query"] == ["like:witcher"] and params["productType"] == ["in:game"] and "countryCode" not in params
    assert [d.full_url for d in detalhes] == ["https://api.gog.com/v2/games/1", "https://api.gog.com/v2/games/2"]
    assert all(p.get_header("User-agent") == base.USER_AGENT for p in loja.pedidos)  # FNT-02


def test_gog01_sem_termo_lista_os_destaques(app, loja):
    with app.app_context():
        importar("gog", limite=1)
    params = parse_qs(urlsplit(loja.pedidos[0].full_url).query)
    assert "query" not in params and params["productType"] == ["in:game"] and params["limit"] == ["1"]
    assert len(loja.pedidos) == 2  # um catálogo e um detalhe


def test_gog01_pagina_o_catalogo_ate_o_limite(api):
    api.rotas[CATALOGO] = (catalogo(*(produto(i, f"J{i}") for i in range(48)), pages=3),
                           catalogo(*(produto(i, f"J{i}") for i in range(48, 96)), pages=3))
    assert gog._ids(60, None) == [str(i) for i in range(60)]
    assert [parse_qs(urlsplit(u).query)["page"] for u in api.urls()] == [["1"], ["2"]]


def test_gog01_ultima_pagina_para_a_paginacao(api):
    api.rotas[CATALOGO] = catalogo(produto(1, "Único"), pages=1)
    assert gog._ids(40, "x") == ["1"] and len(api.pedidos) == 1


def test_gog02_detalhe_mapeia_os_campos(api):
    api.rotas["api.gog.com/v2/games/1"] = jogo(1, "The Witcher")
    assert gog.detalhe("1") == {
        "external_id": "1", "title": "The Witcher",
        "synopsis": "Um RPG de fantasia & escolhas.\nSem DRM.",
        "release_date": date(2015, 5, 19), "data_aproximada": False,
        "developer": "CD PROJEKT RED",
        "genres": ["Role-playing", "Action", "Fantasy"], "platforms": ["windows", "osx"],
        "cover_url": "https://images.gog-statics.com/box1.jpg",
        "url_loja": "https://www.gog.com/en/game/jogo_1",
    }


def test_gog02_importado_com_genero_plataforma_capa_e_link_da_loja(app, loja):
    with app.app_context():
        importar("gog", limite=1)
        g = Game.query.one()
        assert (g.source, g.external_id, g.developer.name) == ("gog", "1", "CD PROJEKT RED")
        assert [x.name for x in g.genres] == ["RPG", "Ação", "Fantasy"]  # GOG-08
        assert [x.name for x in g.platforms] == ["Windows", "macOS"]
        assert g.cover_url == "https://images.gog-statics.com/box1.jpg" and "<" not in g.synopsis
        assert [(v.fonte, v.url_loja) for v in g.fontes] == [("gog", "https://www.gog.com/en/game/jogo_1")]


def test_gog02_detalhe_sem_titulo_ou_dados_opcionais(api):
    api.rotas["api.gog.com/v2/games/3"] = {"_embedded": {"product": {}}}
    api.rotas["api.gog.com/v2/games/4"] = {"_embedded": {"product": {"title": "Mínimo"}}}
    assert gog.detalhe("3") is None
    j = gog.detalhe("4")
    assert (j["release_date"], j["developer"], j["genres"], j["cover_url"], j["url_loja"]) == (None, "", [], None, None)


@pytest.mark.parametrize("eid", ["abc", "", "1/../2", "-1"])
def test_gog02_id_que_nao_e_numero_nao_vai_a_rede(api, eid):
    assert gog.detalhe(eid) is None and api.pedidos == []


def test_gog12_busca_do_painel_usa_o_catalogo(loja):
    assert gog.buscar("witcher", 1) == [
        {"external_id": "1", "title": "The Witcher", "cover_url": "https://images.gog-statics.com/v1.jpg"}]
    assert parse_qs(urlsplit(loja.pedidos[0].full_url).query)["limit"] == ["1"]


@pytest.mark.parametrize("corpo", [{"erro": "x"}, {"products": None}, [], b"<html>"])
def test_gog15_catalogo_malformado_falha_sem_gravar(app, api, corpo):
    api.rotas[CATALOGO] = corpo
    with app.app_context():
        with pytest.raises(ValueError):
            importar("gog")
        assert Game.query.count() == 0
    with pytest.raises(ValueError):
        gog.buscar("x")


def test_gog15_detalhe_malformado_conta_como_falha(app, loja):
    loja.rotas["api.gog.com/v2/games/1"] = b"{quebrado"
    with app.app_context():
        assert importar("gog") == (1, 0, 1)
        assert [g.title for g in Game.query] == ["Gwent"]


def test_gog15_catalogo_vazio(app, api):
    api.rotas[CATALOGO] = catalogo()
    with app.app_context():
        assert importar("gog") == (0, 0, 0)
        assert sincronizar("gog") == (0, 0, 0, 0)


def test_gog15_timeout_no_detalhe_e_no_catalogo(app, loja):
    loja.rotas["api.gog.com/v2/games/2"] = TimeoutError("timed out")
    with app.app_context():
        assert importar("gog") == (1, 0, 1)
    loja.rotas[CATALOGO] = TimeoutError("timed out")
    r = app.test_cli_runner().invoke(args=["sync-games", "--fonte", "gog"])
    assert r.exit_code == 1 and r.output == "erro=fonte indisponivel: timed out\n"


def test_gog15_429_seguido_de_sucesso_nao_interrompe(app, loja):
    loja.rotas["api.gog.com/v2/games/1"] = (http(429), jogo(1, "The Witcher"))
    with app.app_context():
        assert importar("gog") == (2, 0, 0)


def test_gog11_429_persistente_interrompe_o_lote_e_mantem_o_gravado(app, loja):
    loja.rotas["api.gog.com/v2/games/2"] = http(429)
    cli = app.test_cli_runner()
    r = cli.invoke(args=["import-games", "--fonte", "gog"])
    assert r.exit_code == 0 and r.output == "importados=1 ignorados=0 falhas=0 interrompido=rate-limit\n"
    r = cli.invoke(args=["sync-games", "--fonte", "gog"])
    assert r.output == "novos=0 atualizados=0 ignorados=1 falhas=0 interrompido=rate-limit\n"
    with app.app_context():
        assert [v.external_id for v in JogoFonte.query] == ["1"]


def test_gog11_429_no_catalogo_interrompe_sem_nada_gravado(app, api):
    api.rotas[CATALOGO] = http(429)
    cli = app.test_cli_runner()
    assert cli.invoke(args=["import-games", "--fonte", "gog"]).output == \
        "importados=0 ignorados=0 falhas=0 interrompido=rate-limit\n"
    assert cli.invoke(args=["sync-games", "--fonte", "gog"]).output == \
        "novos=0 atualizados=0 ignorados=0 falhas=0 interrompido=rate-limit\n"
    assert len(api.pedidos) == 2 * base.TENTATIVAS  # as novas tentativas de FNT-01 vêm antes de desistir
