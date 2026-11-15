from datetime import date
from types import SimpleNamespace

import pytest

from app.fontes import importar
from app.fontes.normalizar import generos, ler_data, plataformas, texto_puro
from app.models import Game, Genre, Platform
from app.sync import sincronizar
from conftest import make_game


def item(i="1", **kw):
    base = dict(external_id=i, title=f"Jogo {i}", synopsis="s", release_date=date(2020, 1, 1),
                developer="Dev", genres=["Ação"], platforms=["Windows"], cover_url=None)
    return {**base, **kw}


def fonte(*itens):
    return SimpleNamespace(listar=lambda limite, termo=None: list(itens)[:limite])


@pytest.mark.parametrize("nome, esperado", [
    ("Role-playing", "RPG"), ("Role-Playing (RPG)", "RPG"), ("RPG", "RPG"), ("Action", "Ação"),
    ("Adventure", "Aventura"), ("Strategy", "Estratégia"), ("Simulation", "Simulação"), ("Sports", "Esportes"),
    ("Racing", "Corrida"), ("Shooter", "Tiro"), ("Platformer", "Plataforma"), ("Massively Multiplayer", "Multijogador Massivo"),
    ("Free to Play", "Gratuitos para Jogar"), ("Ação", "Ação"), ("Fantasy", "Fantasy"),  # sem mapeamento: mantido
])
def test_gog08_sinonimos_de_genero(nome, esperado):
    assert generos([nome]) == [esperado]


@pytest.mark.parametrize("nome, esperado", [
    ("Win", "Windows"), ("PC", "Windows"), ("windows", "Windows"), ("Windows", "Windows"),
    ("osx", "macOS"), ("Mac OS X", "macOS"), ("mac", "macOS"), ("macOS", "macOS"),
    ("linux", "Linux"), ("PlayStation 5", "PlayStation 5"), ("Nintendo Switch", "Nintendo Switch"),
])
def test_gog08_sinonimos_de_plataforma(nome, esperado):
    assert plataformas([nome]) == [esperado]


def test_gog08_sinonimos_viram_um_nome_so_sem_vazios():
    assert plataformas(["Win", "PC", "windows", "", None, "osx", "Mac OS X"]) == ["Windows", "macOS"]
    assert generos(["Role-playing", "RPG", "Action", "  "]) == ["RPG", "Ação"]


def test_gog08_importacao_reaproveita_genero_e_plataforma_existentes(app):
    with app.app_context():
        make_game(app, "Existente", genre="RPG", platform="Windows")
        importar("gog", modulo=fonte(item("1", genres=["Role-playing", "RPG"], platforms=["Win", "PC", "osx"])))
        g = Game.query.filter_by(source="gog").one()
        assert [x.name for x in g.genres] == ["RPG"] and [x.name for x in g.platforms] == ["Windows", "macOS"]
        assert Genre.query.count() == 1 and Platform.query.count() == 2  # IMP-03: nada de "Role-playing" ou "Win"


def test_gog08_sincronizacao_tambem_normaliza(app):
    with app.app_context():
        sincronizar("gog", modulo=fonte(item("1", genres=["Action"], platforms=["windows"])))
        assert sincronizar("gog", modulo=fonte(item("1", genres=["Ação"], platforms=["PC"]))) == (0, 0, 1, 0)
        g = Game.query.one()
        assert [x.name for x in g.genres] == ["Ação"] and [x.name for x in g.platforms] == ["Windows"]


@pytest.mark.parametrize("valor, esperado", [
    ("2015-05-19", (date(2015, 5, 19), False)),
    ("2015-05-19T00:00:00+02:00", (date(2015, 5, 19), False)),
    ("2015-05-19T23:30:00", (date(2015, 5, 19), False)),
    ("2015-05-19 10:00:00Z", (date(2015, 5, 19), False)),
    ("2015.05.19", (date(2015, 5, 19), False)),
    (1431993600, (date(2015, 5, 19), False)),
    ("1431993600", (date(2015, 5, 19), False)),
    (1431993600.5, (date(2015, 5, 19), False)),
    ("2015", (date(2015, 1, 1), True)),
    (2015, (date(2015, 1, 1), True)),
    (date(2015, 5, 19), (date(2015, 5, 19), False)),
    ("19/05/2015", (None, False)),
    ("May 19, 2015", (None, False)),
    ("2015-02-30", (None, False)),
    ("2015.13.01", (None, False)),
    ("0000", (None, False)),
    ("", (None, False)),
    (None, (None, False)),
    (True, (None, False)),
    (10 ** 20, (None, False)),
])
def test_gog09_leitura_de_datas(valor, esperado):
    assert ler_data(valor) == esperado


def test_gog09_so_o_ano_grava_1o_de_janeiro_e_a_pagina_mostra_o_ano(app, client):
    with app.app_context():
        importar("gog", modulo=fonte(item("1", title="Antigo", release_date="1998"),
                                     item("2", title="Exato", release_date="2015.05.19")))
        antigo, exato = Game.query.filter_by(title="Antigo").one(), Game.query.filter_by(title="Exato").one()
        assert (antigo.release_date, antigo.data_aproximada) == (date(1998, 1, 1), True)
        assert (exato.release_date, exato.data_aproximada) == (date(2015, 5, 19), False)
    assert "Lançamento: 1998 ·" in client.get("/jogos/antigo").data.decode()
    assert "01/01/1998" not in client.get("/jogos/antigo").data.decode()
    assert "Lançamento: 19/05/2015 ·" in client.get("/jogos/exato").data.decode()


def test_gog09_data_desconhecida_nao_importa_e_sync_corrige_a_aproximada(app):
    with app.app_context():
        assert importar("gog", modulo=fonte(item("1", release_date="em breve"))) == (0, 1, 0)  # IMP-06
        assert sincronizar("gog", modulo=fonte(item("2", release_date="2019"))) == (1, 0, 0, 0)
        assert sincronizar("gog", modulo=fonte(item("2", release_date="2019-03-08"))) == (0, 1, 0, 0)
        g = Game.query.one()
        assert (g.release_date, g.data_aproximada) == (date(2019, 3, 8), False)


@pytest.mark.parametrize("entrada, esperado", [
    ("<p>Olá &amp; <b>bem-vindo</b></p>", "Olá & bem-vindo"),
    ("linha 1<br>linha&nbsp;2<br/>linha 3", "linha 1\nlinha 2\nlinha 3"),
    ('<div style="x"><img src="a.png"></div><p>\r\n<br>\r\n&nbsp;\r\n</p><hr><p>Texto   com   espaços</p>',
     "Texto com espaços"),
    ("<p>um</p><p>dois</p>", "um\ndois"),
    ("sem html", "sem html"),
    ("", ""),
    (None, ""),
])
def test_gog02_descricao_html_vira_texto_puro(entrada, esperado):
    assert texto_puro(entrada) == esperado
