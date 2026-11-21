from datetime import date
from difflib import SequenceMatcher
from types import SimpleNamespace

import pytest

from app import catalogo_merge
from app.catalogo_merge import Perfil, normalizar_titulo, perfil_item, perfil_jogo, registrar_pendencia, semelhanca
from app.fontes import FONTES, importar
from app.models import AuditLog, Game, JogoFonte, PendenciaMerge, db
from app.sync import sincronizar
from conftest import login, make_game, make_user

STEAM_W3 = "https://store.steampowered.com/app/292030/The_Witcher_3_Wild_Hunt/"


def item(eid, title="Hades", year=2020, dev="Supergiant Games", **kw):
    return {"external_id": eid, "title": title, "synopsis": "s", "release_date": date(year, 9, 17),
            "developer": dev, "genres": ["Ação"], "platforms": ["Windows"], "cover_url": None, **kw}


def fonte(*itens):
    return SimpleNamespace(listar=lambda limite, termo=None: list(itens)[:limite])


def jogos():
    return Game.query.order_by(Game.id).all()


@pytest.fixture
def hades(app):
    """Hades vindo da Steam, o jogo que já está no catálogo."""
    with app.app_context():
        assert importar("steam", modulo=fonte(item("1145360"))) == (1, 0, 0)
    return app


# ---------- MRG-01: semelhança ----------
@pytest.mark.parametrize("titulo, esperado", [
    ("The Witcher® 3: Wild Hunt – Game of the Year Edition", "the witcher 3 wild hunt"),
    ("DARK SOULS™: REMASTERED", "dark souls remastered"),
    ("Pokémon Édition Spéciale", "pokemon edition speciale"),
    ("Deus Ex: Human Revolution - Director's Cut", "deus ex human revolution"),
    ("Deus Ex: Human Revolution Directors Cut", "deus ex human revolution"),
    ("Fallout 3: GOTY", "fallout 3"),
    ("Fallout 3 GOTY Edition", "fallout 3"),
    ("Age of Empires II: Definitive Edition", "age of empires ii"),
    ("Goty Hunter", "hunter"),
    ("  Hades  ", "hades"),
    ("", ""),
    (None, ""),
])
def test_mrg01_titulo_normalizado_sem_acentos_simbolos_e_sufixos(titulo, esperado):
    assert normalizar_titulo(titulo) == esperado


def test_mrg01_sufixo_so_sai_como_palavra_inteira():
    assert normalizar_titulo("Gotyland") == "gotyland"


@pytest.mark.parametrize("ano_b, dev_b, extra", [
    (2020, "supergiant-games", 30),  # mesmo ano e mesma desenvolvedora
    (2021, "supergiant-games", 20),  # ano vizinho vale 10
    (2019, "supergiant-games", 20),
    (2022, "supergiant-games", 10),  # dois anos: nada pelo ano
    (2020, "outra", 20),
    (2020, "", 20),  # desenvolvedora vazia nunca conta como igual
])
def test_mrg01_ano_e_desenvolvedora_somam_ao_titulo(ano_b, dev_b, extra):
    a, b = Perfil("hades", 2020, "supergiant-games"), Perfil("hades ii", ano_b, dev_b)
    titulo = round(70 * SequenceMatcher(None, "hades", "hades ii").ratio())
    assert titulo == 54 and semelhanca(a, b) == titulo + extra


def test_mrg01_mesmo_titulo_ano_e_dev_vale_100_e_e_deterministico():
    a, b = Perfil("the witcher 3 wild hunt", 2015, "cd-projekt-red"), Perfil("the witcher 3 wild hunt", 2015, "cd-projekt-red")
    assert semelhanca(a, b) == 100 and {semelhanca(a, b) for _ in range(5)} == {100}
    assert semelhanca(a, Perfil("tetris", 1984, "outra")) < 30


def test_mrg01_desenvolvedora_desconhecida_nao_conta_como_mesma():
    a = perfil_item({"developer": "Desenvolvedora desconhecida"}, "Hades", date(2020, 1, 1))
    assert a.dev == "" and semelhanca(a, a) == 90


def test_mrg01_perfil_do_jogo_e_do_item_coincidem(hades):
    with hades.app_context():
        assert perfil_jogo(Game.query.one()) == perfil_item(item("x"), "Hades™", date(2020, 1, 2))


# ---------- MRG-02: o item é de um jogo existente ----------
def test_mrg02_link_de_loja_conhecido_vincula_sem_calcular_semelhanca(app, monkeypatch):
    with app.app_context():
        importar("steam", modulo=fonte(item("292030", "The Witcher 3: Wild Hunt", 2015, "CD PROJEKT RED")))
        monkeypatch.setattr(catalogo_merge, "semelhanca", lambda a, b: pytest.fail("calculou semelhança"))
        monkeypatch.setattr(catalogo_merge, "mais_parecido", lambda *a: pytest.fail("procurou parecido"))
        novo = item("3328", "Um nome qualquer", 1999, "Outra", lojas=[("steam", STEAM_W3)])
        assert importar("rawg", modulo=fonte(novo)) == (1, 0, 0)  # conta como importado
        (game,) = jogos()
        assert game.title == "The Witcher 3: Wild Hunt" and sorted(v.fonte for v in game.fontes) == ["rawg", "steam"]
        assert PendenciaMerge.query.count() == 0


def test_mrg02_link_da_propria_fonte_tambem_reconhece(app):
    """O link de loja do item da GOG (url_loja) bate com o vínculo GOG que a RAWG criou."""
    with app.app_context():
        importar("rawg", modulo=fonte(item("1", lojas=[("gog", "https://www.gog.com/en/game/hades")])))
        assert importar("gog", modulo=fonte(item("1207", "Outro título", url_loja="https://www.gog.com/game/hades"))) == (1, 0, 0)
        assert len(jogos()) == 1


@pytest.mark.parametrize("ano, dev", [(2021, "Supergiant Games"), (2019, "Supergiant Games"), (2020, "Outra")])
def test_mrg02_semelhanca_90_ou_mais_vincula_em_vez_de_criar(hades, ano, dev):
    with hades.app_context():
        assert importar("gog", modulo=fonte(item("1207", "HADES™", ano, dev))) == (1, 0, 0)
        (game,) = jogos()
        assert game.source == "steam" and sorted(v.fonte for v in game.fontes) == ["gog", "steam"]
        assert PendenciaMerge.query.count() == 0


def test_mrg02_na_sincronizacao_vinculo_conta_como_atualizado(hades):
    with hades.app_context():
        assert sincronizar("gog", modulo=fonte(item("1207", "Hades: Game of the Year Edition"))) == (0, 1, 0, 0)
        assert len(jogos()) == 1 and {v.fonte for v in jogos()[0].fontes} == {"steam", "gog"}


def test_mrg02_fonte_ligada_depois_nao_sobrescreve_metadados(hades):
    """Só a fonte de origem atualiza o jogo; senão Steam e GOG trocariam o título a cada execução."""
    with hades.app_context():
        sincronizar("gog", modulo=fonte(item("1207", "Hades GOTY", synopsis="da GOG")))
        assert sincronizar("gog", modulo=fonte(item("1207", "Hades GOTY", synopsis="da GOG"))) == (0, 0, 1, 0)
        game = Game.query.one()
        assert game.title == "Hades" and game.synopsis == "s"
        assert sincronizar("steam", modulo=fonte(item("1145360", synopsis="nova"))) == (0, 1, 0, 0)
        assert Game.query.one().synopsis == "nova"


def test_mrg02_arquivo_atualiza_pela_loja_que_copia(app):
    """O snapshot (arquivo ← Steam) continua sendo corrigido pela sincronização da Steam."""
    with app.app_context():
        importar("arquivo", modulo=SimpleNamespace(listar=fonte(item("1145360")).listar, LOJA_DE_ORIGEM="steam"))
        assert sincronizar("steam", modulo=fonte(item("1145360", synopsis="nova"))) == (0, 1, 0, 0)
        assert Game.query.one().synopsis == "nova"


def test_mrg02_id_da_steam_do_item_do_arquivo_reconhece_o_jogo(hades):
    """O item do snapshot traz o id da Steam: com outro título, ainda é o mesmo jogo."""
    with hades.app_context():
        assert importar("arquivo", modulo=fonte(item("1145360", "Totally Other Name", 1999, "Outra"))) == (1, 0, 0)
        (game,) = jogos()
        assert game.title == "Hades" and sorted(v.fonte for v in game.fontes) == ["arquivo", "steam"]
        assert PendenciaMerge.query.count() == 0


def test_fnt04_vincular_nao_devolve_o_vinculo_de_outro_jogo(hades):
    from app.fontes import vincular

    make_game(hades, "Outro Jogo")
    with hades.app_context():
        outro = Game.query.filter_by(slug="outro-jogo").one()
        assert vincular(outro, "steam", "1145360") is None
        assert [(v.fonte, v.game.title) for v in JogoFonte.query.filter_by(fonte="steam")] == [("steam", "Hades")]


def test_mrg02_jogo_com_outro_vinculo_da_mesma_fonte_vira_pendencia(hades):
    """Duas entradas da mesma loja não cabem num jogo (FNT-04): o admin decide."""
    with hades.app_context():
        assert importar("steam", modulo=fonte(item("999", "Hades"))) == (1, 0, 0)
        assert len(jogos()) == 2
        p = PendenciaMerge.query.one()
        assert (p.jogo_a.source, p.jogo_b.source, p.semelhanca) == ("steam", "steam", 100)


def test_mrg02_link_conhecido_com_vinculo_ocupado_e_ignorado(app):
    with app.app_context():
        importar("rawg", modulo=fonte(item("1", lojas=[("steam", STEAM_W3)])))
        r = importar("rawg", modulo=fonte(item("2", "Outro", 1990, lojas=[("steam", STEAM_W3)])))
        assert r == (0, 1, 0) and len(jogos()) == 1 and [v.external_id for v in jogos()[0].fontes if v.fonte == "rawg"] == ["1"]
        assert sincronizar("rawg", modulo=fonte(item("2", "Outro", 1990, lojas=[("steam", STEAM_W3)]))) == (0, 0, 1, 0)


def test_mrg02_painel_vincula_e_avisa(hades, monkeypatch):
    make_user(hades, "adm", role="admin")
    falsa = SimpleNamespace(buscar=lambda q, limite: [], detalhe=lambda eid: item(eid, "Hades"))
    monkeypatch.setitem(FONTES, "gog", falsa)
    c = hades.test_client()
    login(c, "adm")
    r = c.post("/admin/importar", data={"fonte": "gog", "external_id": "1207"}, follow_redirects=True)
    assert r.request.path == "/jogos/hades" and "já estava no catálogo e agora também aparece" in r.get_data(as_text=True)
    with hades.app_context():
        assert len(jogos()) == 1 and AuditLog.query.filter_by(action="jogo.importar").count() == 1
    r = c.post("/admin/importar/lote", data={"fonte": "gog", "ids": ["1208"]})
    with hades.app_context():
        assert len(jogos()) == 2  # 1208 não cabe no mesmo jogo (já tem gog 1207): jogo novo com pendência
        assert PendenciaMerge.query.count() == 1


# ---------- MRG-03: semelhança incerta cria jogo e pendência ----------
@pytest.mark.parametrize("titulo, ano, dev, nota", [
    ("Hades", 2022, "Supergiant Games", 80),  # mesmo título, ano distante
    ("Hades", 2021, "Outra", 80),
    ("Hades II", 2020, "Supergiant Games", 84),
    ("Hades", 2030, "Supergiant Games", 80),  # ≥ 90 é impossível com mais de 2 anos; vira pendência como 70-89
])
def test_mrg03_semelhanca_70_a_89_cria_jogo_e_pendencia(hades, titulo, ano, dev, nota):
    with hades.app_context():
        assert importar("gog", modulo=fonte(item("1207", titulo, ano, dev))) == (1, 0, 0)
        steam, gog = jogos()
        p = PendenciaMerge.query.one()
        assert (p.jogo_a_id, p.jogo_b_id, p.semelhanca, p.status) == (steam.id, gog.id, nota, "pendente")


def test_mrg03_semelhanca_menor_que_70_so_cria(hades):
    with hades.app_context():
        assert sincronizar("gog", modulo=fonte(item("1207", "Celeste", 2018, "Maddy Makes Games"))) == (1, 0, 0, 0)
        assert len(jogos()) == 2 and PendenciaMerge.query.count() == 0


def test_mrg03_pendencia_unica_para_o_par_em_qualquer_ordem(app):
    make_game(app, "Hades")
    make_game(app, "Hades II")
    with app.app_context():
        a, b = jogos()
        assert registrar_pendencia(b, a, 84) is not None
        assert registrar_pendencia(a, b, 84) is None and registrar_pendencia(b, a, 84) is None
        assert registrar_pendencia(a, a, 100) is None
        db.session.commit()
        p = PendenciaMerge.query.one()
        assert (p.jogo_a_id, p.jogo_b_id) == (a.id, b.id)


def test_mrg03_o_mais_parecido_ganha_e_empate_fica_com_o_menor_id(app):
    for t in ("Celeste", "Hades II", "Hades IX"):
        make_game(app, t, year=2020, dev="Supergiant Games")
    with app.app_context():
        _, hades_ii, _ = jogos()
        importar("gog", modulo=fonte(item("1", "Hades I", 2022)))  # 65 pelo título + 10 da dev, com os dois
        p = PendenciaMerge.query.one()
        assert (p.jogo_a_id, p.semelhanca) == (hades_ii.id, 75)


# ---------- MRG-04: sugerir-duplicatas ----------
def test_mrg04_sugerir_duplicatas_registra_sem_mesclar(app):
    make_game(app, "Fallout 3", year=2008, dev="Bethesda")
    make_game(app, "Fallout 3: GOTY", year=2009, dev="Bethesda")
    make_game(app, "Celeste", year=2018, dev="Maddy")
    runner = app.test_cli_runner()
    r = runner.invoke(args=["sugerir-duplicatas"])
    assert r.exit_code == 0 and r.output == "pendencias=1\n"
    with app.app_context():
        p = PendenciaMerge.query.one()
        assert (p.jogo_a.title, p.jogo_b.title, p.semelhanca, p.status) == ("Fallout 3", "Fallout 3: GOTY", 90, "pendente")
        assert all(g.removed_at is None and g.mesclado_em_id is None for g in jogos())
    assert runner.invoke(args=["sugerir-duplicatas"]).output == "pendencias=0\n"


def test_mrg04_jogos_removidos_ficam_de_fora(app):
    make_game(app, "Fallout 3")
    make_game(app, "Fallout 3 GOTY Edition")
    with app.app_context():
        jogos()[1].removed_at = date(2026, 1, 1)
        db.session.commit()
    assert app.test_cli_runner().invoke(args=["sugerir-duplicatas"]).output == "pendencias=0\n"


# ---------- MRG-14: o vínculo GOG da RAWG passa a usar o id da GOG ----------
@pytest.mark.parametrize("url_loja", ["https://www.gog.com/en/game/hades", None])
def test_mrg14_gog_troca_o_slug_da_rawg_pelo_id_numerico(app, url_loja):
    with app.app_context():
        importar("rawg", modulo=fonte(item("1", lojas=[("gog", "https://www.gog.com/game/hades")])))
        assert JogoFonte.query.filter_by(fonte="gog").one().external_id == "hades"
        assert sincronizar("gog", modulo=fonte(item("1207664663", url_loja=url_loja))) == (0, 1, 0, 0)
        (v,) = JogoFonte.query.filter_by(fonte="gog").all()
        assert v.external_id == "1207664663" and v.url_loja == (url_loja or "https://www.gog.com/game/hades")
        assert len(jogos()) == 1
        assert sincronizar("gog", modulo=fonte(item("1207664663", url_loja=url_loja))) == (0, 0, 1, 0)


def test_mrg14_na_importacao_tambem_sem_segundo_vinculo(app):
    with app.app_context():
        importar("rawg", modulo=fonte(item("1", lojas=[("gog", "https://www.gog.com/game/hades")])))
        assert importar("gog", modulo=fonte(item("42", url_loja="https://www.gog.com/game/hades"))) == (1, 0, 0)
        assert [(v.external_id, v.url_loja) for v in JogoFonte.query.filter_by(fonte="gog")] == [
            ("42", "https://www.gog.com/game/hades")]
