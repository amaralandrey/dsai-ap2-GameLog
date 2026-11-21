from datetime import date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from app.fontes import FONTES, importar, normalizar_url_loja, vincular
from app.models import Game, JogoFonte, db
from app.seed import run
from app.sync import sincronizar
from conftest import make_game


def item(i="1", **kw):
    base = dict(external_id=i, title=f"Jogo Loja {i}", synopsis="s", release_date=date(2023, 3, 1),
                developer="Dev", genres=["Ação"], platforms=["Windows"], cover_url=None)
    return {**base, **kw}


def fonte(*itens, **attrs):
    return SimpleNamespace(listar=lambda limite, termo=None: list(itens)[:limite], **attrs)


def jogo(app, titulo="Jogo Teste"):
    slug = make_game(app, titulo)
    return Game.query.filter_by(slug=slug).one()


def test_fnt04_vinculos_de_fonte_e_de_loja_so_vinculada(app):
    with app.app_context():
        g = jogo(app)
        vincular(g, "steam", "42")
        vincular(g, "epic", "hades")
        vincular(g, "arquivo", "42")
        db.session.commit()
        assert {(v.fonte, v.external_id) for v in g.fontes} == {("steam", "42"), ("epic", "hades"), ("arquivo", "42")}
        assert [v.fonte for v in g.lojas] == ["steam", "epic"]  # arquivo só registra a origem
        with pytest.raises(ValueError):
            JogoFonte(game=g, fonte="loja-inventada", external_id="1")


def test_fnt04_unico_por_fonte_e_id_e_por_jogo_e_fonte(app):
    with app.app_context():
        a, b = jogo(app, "Jogo A"), jogo(app, "Jogo B")
        db.session.add(JogoFonte(game=a, fonte="steam", external_id="1"))
        db.session.commit()
        for g, eid in ((b, "1"), (a, "2")):
            db.session.add(JogoFonte(game=g, fonte="steam", external_id=eid))
            with pytest.raises(IntegrityError):
                db.session.commit()
            db.session.rollback()


def test_fnt05_seed_vincula_origem_e_loja_steam(app):
    with app.app_context():
        run(42, real=True)
        g = Game.query.first()
        # o snapshot enriquecido também traz gog, epic e rawg (SNP-06); arquivo e steam estão em todos
        assert all({"arquivo", "steam"} <= {v.fonte for v in x.fontes} for x in Game.query)
        steam = next(v for v in g.fontes if v.fonte == "steam")
        assert steam.external_id == g.external_id
        assert steam.url_loja == f"https://store.steampowered.com/app/{g.external_id}"
        assert next(v for v in g.fontes if v.fonte == "arquivo").url_loja is None
        assert (g.source, g.external_id) != (None, None)  # Game.source continua sendo a origem


def test_fnt05_seed_em_banco_existente_cria_vinculo_que_falta(app):
    with app.app_context():
        g = jogo(app)
        g.source, g.external_id = "steam", "620"
        db.session.commit()
        run(42, real=True)
        run(42, real=True)
        assert [(v.fonte, v.url_loja) for v in g.fontes] == [("steam", "https://store.steampowered.com/app/620")]
        assert Game.query.count() == 1


def test_fnt05_vincular_existentes_sem_consulta_por_jogo(app):
    from sqlalchemy import event

    from app.fontes import vincular_existentes

    def consultas(de, ate):
        for i in range(de, ate):
            make_game(app, f"Vinculado {i}")
        with app.app_context():
            for g in Game.query.filter(Game.source.is_(None)):
                g.source, g.external_id = "steam", str(g.id)
                vincular(g, "steam", str(g.id))
            db.session.commit()
            db.session.expire_all()
            contadas = []

            def conta(*a):
                contadas.append(1)
            event.listen(db.engine, "before_cursor_execute", conta)
            vincular_existentes()
            event.remove(db.engine, "before_cursor_execute", conta)
            return len(contadas)
    assert consultas(0, 2) == consultas(2, 8)


def test_fnt06_importar_procura_por_jogofonte_e_nao_duplica(app):
    with app.app_context():
        g = jogo(app)  # jogo manual, sem source, mas já vinculado à Steam
        vincular(g, "steam", "42")
        db.session.commit()
        assert importar("steam", modulo=fonte(item("42"), item("43"))) == (1, 1, 0)
        assert importar("steam", modulo=fonte(item("42"), item("43"))) == (0, 2, 0)
        assert Game.query.count() == 2 and JogoFonte.query.count() == 2
        assert importar("falsa", modulo=fonte(item("7"))) == (1, 0, 0)
        assert "falsa" not in FONTES  # a fonte injetada só vale enquanto importa


def test_fnt06_snapshot_e_steam_sao_o_mesmo_jogo(app):
    with app.app_context():
        importar("arquivo", limite=1)
        eid = Game.query.one().external_id
        assert importar("steam", modulo=fonte(item(eid))) == (0, 1, 0)
        assert Game.query.count() == 1


def test_fnt06_sync_nao_duplica_e_marca_ultima_sincronizacao(app):
    with app.app_context():
        assert sincronizar("steam", modulo=fonte(item("1"), item("2"))) == (2, 0, 0, 0)
        v1 = JogoFonte.query.filter_by(external_id="1").one()
        v1.ultima_sincronizacao = datetime(2000, 1, 1)
        db.session.commit()
        assert sincronizar("steam", limite=1, modulo=fonte(item("1"), item("2"))) == (0, 0, 1, 0)
        assert Game.query.count() == 2 and JogoFonte.query.count() == 2
        assert JogoFonte.query.filter_by(external_id="1").one().ultima_sincronizacao > datetime(2026, 1, 1)
        assert JogoFonte.query.filter_by(external_id="1").one().url_loja == "https://store.steampowered.com/app/1"


def test_fnt06_comandos_reexecutados_nao_duplicam(app):
    FONTES["falsa"] = fonte(item("1"), item("2"))
    try:
        cli = app.test_cli_runner()
        for _ in range(2):
            cli.invoke(args=["import-games", "--fonte", "falsa"])
            cli.invoke(args=["sync-games", "--fonte", "falsa"])
    finally:
        del FONTES["falsa"]
    with app.app_context():
        assert Game.query.count() == 2 and JogoFonte.query.count() == 2


@pytest.mark.parametrize("fonte_, url, esperado", [
    ("steam", "https://store.steampowered.com/app/620/Portal_2/", "https://store.steampowered.com/app/620/Portal_2/"),
    ("gog", "https://www.gog.com/en/game/hades", "https://www.gog.com/en/game/hades"),
    ("epic", "https://www.epicgames.com/store/pt-BR/product/hades/home", "https://store.epicgames.com/p/hades"),
    ("epic", "https://store.epicgames.com/en-US/p/alan-wake-2", "https://store.epicgames.com/p/alan-wake-2"),
    ("epic", "https://store.epicgames.com/p/control", "https://store.epicgames.com/p/control"),
    ("epic", "https://store.epicgames.com/en-US/browse", None),
    ("steam", "http://store.steampowered.com/app/620", None),
    ("steam", "https://store.steampowered.com.evil.com/app/620", None),
    ("steam", "https://user@store.steampowered.com/app/620", None),
    ("steam", "https://store.steampowered.com:8443/app/620", None),
    ("steam", "https://store.steampowered.com:porta/app/620", None),
    ("steam", "https://www.gog.com/en/game/hades", None),  # host de outra loja
    ("rawg", "https://rawg.io/games/hades", None),  # rawg não é loja
    ("epic", "javascript:alert(1)", None),
    ("steam", "", None),
    ("steam", None, None),
])
def test_fnt07_url_loja_so_https_de_host_da_loja(fonte_, url, esperado):
    assert normalizar_url_loja(fonte_, url) == esperado


def test_fnt07_url_invalida_fica_vazia_e_o_vinculo_existe(app):
    with app.app_context():
        g = jogo(app)
        v = vincular(g, "epic", "hades", "http://store.epicgames.com/p/hades")
        db.session.commit()
        assert JogoFonte.query.one() is v and v.url_loja is None
        vincular(g, "epic", "hades", "https://store.epicgames.com/pt-BR/p/hades")
        assert v.url_loja == "https://store.epicgames.com/p/hades"
