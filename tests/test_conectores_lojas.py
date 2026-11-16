import json
from datetime import date
from types import SimpleNamespace

import pytest

from app.fontes import FONTES, external_id_da_url, vincular
from app.models import Game, db
from conftest import make_game


def item(i, **kw):
    return {"external_id": i, "title": f"Jogo {i}", "synopsis": "s", "release_date": date(2021, 1, 1),
            "developer": "Dev", "genres": ["Ação"], "platforms": ["Windows"], "cover_url": None, **kw}


def fonte(*itens, erro=None):
    def listar(limite, termo=None):
        if erro:
            raise erro
        return list(itens)[:limite]
    return SimpleNamespace(listar=listar)


@pytest.fixture
def fontes_falsas():
    """Troca as fontes reais por falsas; `todas` roda o que estiver em FONTES, menos `arquivo`."""
    original = dict(FONTES)

    def registrar(**fontes):
        FONTES.clear()
        FONTES.update(arquivo=original["arquivo"], **fontes)
    yield registrar
    FONTES.clear()
    FONTES.update(original)  # mesma ordem de antes: o seletor do painel depende dela


@pytest.mark.parametrize("loja, url, esperado", [
    ("steam", "https://store.steampowered.com/app/292030/The_Witcher_3_Wild_Hunt/", "292030"),
    ("steam", "https://store.steampowered.com/app/292030", "292030"),
    ("steam", "http://store.steampowered.com/app/292030/", "292030"),
    ("steam", "https://store.steampowered.com/sub/124923/", None),
    ("steam", "https://store.steampowered.com/app/abc/", None),
    ("gog", "https://www.gog.com/game/the_witcher_3_wild_hunt", "the_witcher_3_wild_hunt"),
    ("gog", "https://www.gog.com/en/game/the_witcher_3_wild_hunt", "the_witcher_3_wild_hunt"),
    ("gog", "https://www.gog.com/en/games?query=witcher", None),
    ("epic", "https://store.epicgames.com/p/hades", "hades"),
    ("epic", "https://store.epicgames.com/en-US/p/alan-wake-2", "alan-wake-2"),
    ("epic", "https://www.epicgames.com/store/en-US/product/hades/home", "hades"),
    ("epic", "https://www.epicgames.com/store/product/hades", "hades"),
    ("epic", "https://store.epicgames.com/en-US/browse", None),
    ("steam", "https://www.gog.com/game/hades", None),  # host de outra loja
    ("steam", "https://steampowered.com.evil.example/app/1", None),
    ("gog", "ftp://www.gog.com/game/hades", None),
    ("epic", "", None),
    ("epic", None, None),
    ("steam", "https://[::1/app/1", None),
])
def test_gog06_external_id_vem_da_url_da_loja(loja, url, esperado):
    assert external_id_da_url(loja, url) == esperado


@pytest.mark.parametrize("comando, resumo", [
    ("import-games", "importados=1 ignorados=0 falhas=0"),
    ("sync-games", "novos=1 atualizados=0 ignorados=0 falhas=0"),
])
def test_gog10_todas_roda_cada_fonte_menos_arquivo_com_prefixo(app, fontes_falsas, comando, resumo):
    fontes_falsas(steam=fonte(item("1")), gog=fonte(item("2")))
    r = app.test_cli_runner().invoke(args=[comando, "--fonte", "todas"])
    assert r.exit_code == 0 and r.output.splitlines() == [f"steam: {resumo}", f"gog: {resumo}"]
    with app.app_context():
        assert sorted(g.source for g in Game.query) == ["gog", "steam"]  # o snapshot `arquivo` não roda


@pytest.mark.parametrize("comando", ["import-games", "sync-games"])
def test_gog10_falha_de_uma_fonte_nao_impede_as_seguintes(app, fontes_falsas, comando):
    fontes_falsas(steam=fonte(erro=OSError("sem rede")), gog=fonte(item("2")))
    r = app.test_cli_runner().invoke(args=[comando, "--fonte", "todas"])
    linhas = r.output.splitlines()
    assert r.exit_code == 1 and linhas[0] == "steam: erro=fonte indisponivel: sem rede" and linhas[1].startswith("gog: ")
    with app.app_context():
        assert [g.source for g in Game.query] == ["gog"]


@pytest.mark.parametrize("fonte_", ["gog", "rawg", "todas"])
def test_gog10_comandos_aceitam_as_novas_fontes(app, fontes_falsas, fonte_):
    fontes_falsas(gog=fonte(), rawg=fonte())
    for comando in ("import-games", "sync-games"):
        r = app.test_cli_runner().invoke(args=[comando, "--fonte", fonte_])
        assert r.exit_code == 0, r.output


def test_gog10_fonte_desconhecida_continua_recusada(app):
    r = app.test_cli_runner().invoke(args=["import-games", "--fonte", "epic"])
    assert r.exit_code == 2 and "fonte desconhecida" in r.output


@pytest.fixture
def catalogo(app):
    """Três jogos: Witcher na Steam e na GOG, Hades só na Epic, Celeste sem loja (só a origem rawg)."""
    lojas = {"Witcher": ("steam", "gog"), "Hades": ("epic",), "Celeste": ("rawg",)}
    slugs = {make_game(app, titulo, genre="RPG"): titulo for titulo in lojas}
    with app.app_context():
        for slug, titulo in slugs.items():
            for loja in lojas[titulo]:
                vincular(Game.query.filter_by(slug=slug).one(), loja, titulo.lower())
        db.session.commit()


def titulos(client, query, rota="/buscar"):
    r = client.get(f"{rota}?{query}")
    assert r.status_code == 200
    if rota.startswith("/api"):
        return sorted(i["titulo"] for i in json.loads(r.data)["items"])
    return sorted(t for t in ("Witcher", "Hades", "Celeste") if f">{t}<" in r.data.decode())


@pytest.mark.parametrize("rota", ["/buscar", "/api/jogos"])
@pytest.mark.parametrize("query, esperado", [
    ("loja=gog", ["Witcher"]),
    ("loja=epic", ["Hades"]),
    ("loja=steam&loja=epic", ["Hades", "Witcher"]),
    ("loja=rawg", ["Celeste", "Hades", "Witcher"]),  # rawg não é loja: ignorado (FNT-04)
    ("loja=inventada", ["Celeste", "Hades", "Witcher"]),  # BUS-08
    ("loja=steam&q=hades", []),  # combina com os demais filtros (BUS-02)
    ("loja=epic&genero=rpg", ["Hades"]),
])
def test_gog13_filtro_por_loja(client, catalogo, rota, query, esperado):
    assert titulos(client, query, rota) == esperado


def test_gog13_filtro_fica_na_url_e_marcado_no_formulario(client, catalogo):
    html = client.get("/buscar?loja=gog&loja=epic").data.decode()
    assert 'name="loja" value="gog" checked' in html and 'name="loja" value="epic" checked' in html
    assert 'name="loja" value="steam" >' in html and 'value="rawg"' not in html
    assert "Nenhum jogo encontrado" not in html and 'href="/buscar">Limpar filtros' in html
    assert "loja" in json.loads(client.get("/api").data)["endpoints"]["/api/jogos"]


def test_gog13_paginacao_preserva_o_filtro(app, client):
    slugs = [make_game(app, f"Jogo {n:02d}") for n in range(25)]
    with app.app_context():
        for n, slug in enumerate(slugs):
            vincular(Game.query.filter_by(slug=slug).one(), "gog", str(n))
        db.session.commit()
    html = client.get("/buscar?loja=gog").data.decode()
    assert "loja=gog&amp;page=2" in html


def test_gog14_credito_da_rawg_so_com_vinculo_rawg(app, client, catalogo):
    credito = 'Dados fornecidos por <a href="https://rawg.io"'
    assert credito in client.get("/jogos/celeste").data.decode()
    assert credito not in client.get("/jogos/witcher").data.decode()
    with app.app_context():
        vincular(Game.query.filter_by(slug="witcher").one(), "rawg", "3328")  # links de loja vindos da RAWG
        db.session.commit()
    assert credito in client.get("/jogos/witcher").data.decode()
