from datetime import date
from types import SimpleNamespace

import pytest

from app import admin
from app.fontes import FONTES, steam, vincular
from app.models import AuditLog, Game, JogoFonte, db
from conftest import login, make_game, make_user


def detalhe_de(eid, titulo):
    return {"external_id": eid, "title": titulo, "synopsis": "s", "release_date": date(2020, 1, 1),
            "developer": "Dev", "genres": ["Ação"], "platforms": ["Windows"], "cover_url": None}


JOGOS = {"1": "Hades", "2": "Celeste", "3": "Inscryption", "4": "Hollow Knight"}


def detalhe(eid):
    if eid == "caiu":
        raise OSError("sem rede")
    return detalhe_de(eid, JOGOS[eid]) if eid in JOGOS else None


@pytest.fixture
def gog():
    FONTES["gog"] = SimpleNamespace(
        NOME="GOG", listar=lambda limite, termo=None: [], detalhe=detalhe,
        buscar=lambda q, n=10: [{"external_id": i, "title": t, "cover_url": None} for i, t in JOGOS.items()][:n])
    yield FONTES["gog"]
    FONTES.pop("gog", None)


@pytest.fixture
def adm(app, client, gog):
    make_user(app, "root", role="admin")
    login(client, "root")
    return client


def lote(c, *ids, fonte="gog"):
    data = {"fonte": fonte, "q": "jogo", "ids": list(ids)}
    data.update({f"titulo-{i}": JOGOS.get(i, f"Item {i}") for i in ids})
    return c.post("/admin/importar/lote", data=data)


def test_fnt08_seletor_so_com_fontes_que_buscam_e_detalham(adm):
    html = adm.get("/admin/importar").data.decode()
    assert '<option value="steam" selected>Steam</option>' in html and '<option value="gog" >GOG</option>' in html
    assert 'value="arquivo"' not in html  # arquivo não tem buscar nem detalhe
    assert admin.fontes_admin() == ["steam", "gog"]


def test_fnt08_fonte_na_url_e_ja_no_catalogo_pela_fonte_escolhida(app, adm, monkeypatch):
    monkeypatch.setattr(steam, "buscar", lambda q, n=10: [{"external_id": "1", "title": "Hades", "cover_url": None}])
    with app.app_context():
        g = Game.query.filter_by(slug=make_game(app, "Hades")).one()
        vincular(g, "steam", "1")
        db.session.commit()
    html = adm.get("/admin/importar?fonte=gog&q=jogo").data.decode()
    assert "Importar jogos da GOG" in html and html.count("Importar</button>") == 4  # steam:1 não marca gog:1
    assert "No catálogo" in adm.get("/admin/importar?q=hades").data.decode()  # padrão: steam
    for fonte in ("inventada", "arquivo"):
        assert adm.get(f"/admin/importar?fonte={fonte}&q=jogo").status_code == 400


def test_fnt08_importar_pela_fonte_escolhida_audita_a_fonte(app, adm):
    r = adm.post("/admin/importar", data={"fonte": "gog", "q": "jogo", "external_id": "2"})
    assert r.headers["Location"] == "/jogos/celeste"
    with app.app_context():
        assert [(v.fonte, v.external_id) for v in Game.query.one().fontes] == [("gog", "2")]
        assert AuditLog.query.filter_by(action="jogo.importar").one().detail == "Celeste (gog:2)"
    assert "No catálogo" in adm.get("/admin/importar?fonte=gog&q=jogo").data.decode()
    r = adm.post("/admin/importar", data={"fonte": "gog", "q": "jogo", "external_id": "caiu"})
    assert r.headers["Location"] == "/admin/importar?fonte=gog&q=jogo"


def test_fnt09_lote_conta_importados_existentes_e_falhas(app, adm):
    lote(adm, "1")
    html = lote(adm, "1", "2", "3", "caiu", "99", "2").data.decode()
    assert "Importados: 2</strong> — Celeste, Inscryption" in html
    assert "Já existiam: 1</strong> — Hades" in html
    assert "Falharam: 2</strong> — Item caiu, Item 99" in html and "Sem processar: 0" in html
    with app.app_context():
        assert Game.query.count() == 3 and JogoFonte.query.filter_by(fonte="gog").count() == 3
        assert sorted(a.detail for a in AuditLog.query.filter_by(action="jogo.importar")) == [
            "Celeste (gog:2)", "Hades (gog:1)", "Inscryption (gog:3)"]


def test_fnt09_lote_vazio_ou_acima_de_10_nao_processa(app, adm):
    for ids in ([], [str(i) for i in range(11)]):
        r = lote(adm, *ids)
        assert r.status_code == 302 and r.headers["Location"] == "/admin/importar?fonte=gog&q=jogo"
        assert "de 1 a 10 jogos" in adm.get(r.headers["Location"]).data.decode()
    with app.app_context():
        assert Game.query.count() == 0


def test_fnt09_tela_tem_lote_com_csrf(app, adm):
    html = adm.get("/admin/importar?fonte=gog&q=jogo").data.decode()
    assert 'action="/admin/importar/lote"' in html and 'name="csrf_token"' in html
    assert html.count('name="ids"') == 4 and "Importar marcados</button>" in html
    app.config["CSRF"] = True
    assert lote(adm, "1").status_code == 400


def test_fnt10_lote_para_de_iniciar_itens_depois_de_20s(app, adm, monkeypatch):
    tempos = iter([0, 0, 19.9, 20, 20])
    monkeypatch.setattr(admin, "relogio", lambda: next(tempos))
    html = lote(adm, "1", "2", "3", "4").data.decode()
    assert "Importados: 2</strong> — Hades, Celeste" in html
    assert "Sem processar: 2</strong> — Inscryption, Hollow Knight" in html
    with app.app_context():
        assert Game.query.count() == 2


def test_fnt10_gunicorn_com_timeout_de_60s():
    from pathlib import Path

    render = (Path(__file__).parent.parent / "render.yaml").read_text()
    assert "--timeout 60" in render


def test_fnt11_so_admin(app, gog):
    make_user(app, "ana")
    anon, jogador = app.test_client(), app.test_client()
    login(jogador, "ana")
    for url in ("/admin/importar?fonte=gog", "/admin/importar/lote"):
        metodo = "post" if url.endswith("lote") else "get"
        assert getattr(anon, metodo)(url).status_code == 302
        assert getattr(jogador, metodo)(url).status_code == 403
