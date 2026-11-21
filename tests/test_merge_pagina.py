import re

import pytest

from app.models import Game, JogoFonte, db
from conftest import make_game


def vincular(app, slug, *vinculos):
    with app.app_context():
        gid = Game.query.filter_by(slug=slug).one().id
        db.session.add_all(JogoFonte(game_id=gid, fonte=f, external_id=e, url_loja=u) for f, e, u in vinculos)
        db.session.commit()


def bloco(html):
    m = re.search(r'<p class="lojas">(.*?)</p>', html, re.S)
    return m.group(1) if m else None


# ---------- MRG-12: "Disponível em" ----------
def test_mrg12_lojas_vinculadas_com_link_em_nova_aba(app, client):
    slug = make_game(app, "Hades")
    vincular(app, slug, ("epic", "hades", "https://store.epicgames.com/p/hades"),
             ("steam", "1145360", "https://store.steampowered.com/app/1145360"),
             ("gog", "1207", "https://www.gog.com/game/hades"), ("rawg", "5", None))
    b = bloco(client.get(f"/jogos/{slug}").get_data(as_text=True))
    assert b.strip().startswith("Disponível em:")
    links = re.findall(r'<a class="tag" href="([^"]+)" target="_blank" rel="noopener">([^<]+)</a>', b)
    assert links == [("https://store.steampowered.com/app/1145360", "Steam"), ("https://www.gog.com/game/hades", "GOG"),
                     ("https://store.epicgames.com/p/hades", "Epic Games Store")]  # ordem de LOJAS; rawg não é loja


@pytest.mark.parametrize("url", [None, "", "javascript:alert(1)", "ftp://www.gog.com/game/hades", "//www.gog.com/game/x"])
def test_mrg12_loja_sem_url_http_aparece_sem_link(app, client, url):
    slug = make_game(app, "Hades")
    vincular(app, slug, ("gog", "1207", url))
    b = bloco(client.get(f"/jogos/{slug}").get_data(as_text=True))
    assert '<span class="tag">GOG</span>' in b and "<a" not in b and "javascript" not in b


def test_mrg12_http_simples_tambem_vira_link(app, client):
    slug = make_game(app, "Hades")
    vincular(app, slug, ("steam", "1", "http://store.steampowered.com/app/1"))
    assert 'href="http://store.steampowered.com/app/1" target="_blank" rel="noopener"' in client.get(f"/jogos/{slug}").get_data(as_text=True)


@pytest.mark.parametrize("vinculos", [(), (("rawg", "5", None),), (("arquivo", "7", None),)])
def test_mrg12_jogo_sem_loja_vinculada_nao_mostra_o_bloco(app, client, vinculos):
    slug = make_game(app, "Hades")
    vincular(app, slug, *vinculos)
    html = client.get(f"/jogos/{slug}").get_data(as_text=True)
    assert bloco(html) is None and "Disponível em" not in html


# ---------- MRG-13: fontes na API ----------
def test_mrg13_api_detalhe_traz_fontes_ordenadas_por_fonte(app, client):
    slug = make_game(app, "Hades")
    vincular(app, slug, ("steam", "1145360", "https://store.steampowered.com/app/1145360"), ("rawg", "5", None),
             ("gog", "1207", "https://www.gog.com/game/hades"), ("epic", "hades", "https://store.epicgames.com/p/hades"))
    d = client.get(f"/api/jogos/{slug}").get_json()
    assert d["fontes"] == [
        {"fonte": "epic", "external_id": "hades", "url": "https://store.epicgames.com/p/hades"},
        {"fonte": "gog", "external_id": "1207", "url": "https://www.gog.com/game/hades"},
        {"fonte": "rawg", "external_id": "5", "url": None},
        {"fonte": "steam", "external_id": "1145360", "url": "https://store.steampowered.com/app/1145360"},
    ]
    assert all(set(f) == {"fonte", "external_id", "url"} for f in d["fontes"])  # API-07: nada além disso


def test_mrg13_jogo_sem_vinculo_tem_fontes_vazia_e_a_lista_nao_muda(app, client):
    slug = make_game(app, "Hades")
    assert client.get(f"/api/jogos/{slug}").get_json()["fontes"] == []
    assert "fontes" not in client.get("/api/jogos").get_json()["items"][0]
