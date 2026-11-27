import json
from datetime import date

import pytest

from app import fontes, tempo
from app.fontes import arquivo, rawg, steam
from app.menores import classificacao as cls
from app.models import AuditLog, Game, GameList, ListItem, Review, db
from conftest import login, make_game, make_user

JOGOS = {"Livre": "L", "Teen": "14", "Adulto": "18", "Sem Faixa": None}


def anos_atras(anos):
    hoje = tempo.hoje()
    return date(hoje.year - anos, 1, 1) if (hoje.month, hoje.day) > (1, 1) else date(hoje.year - anos - 1, 1, 1)


def conta(app, nome, anos=None):
    from app.models import User

    uid = make_user(app, nome)
    if anos is not None:
        with app.app_context():
            db.session.get(User, uid).nascimento = anos_atras(anos)
            db.session.commit()
    return uid


def cliente(app, nome):
    c = app.test_client()
    login(c, nome)
    return c


@pytest.fixture
def catalogo(app):
    """Quatro jogos de Ação com faixas diferentes, todos avaliados por um adulto (para destaques e estatísticas)."""
    autor = conta(app, "autor")
    for titulo, faixa in JOGOS.items():
        slug = make_game(app, titulo)
        with app.app_context():
            g = Game.query.filter_by(slug=slug).one()
            g.classificacao, g.classificacao_origem = faixa, "steam" if faixa else None
            db.session.add_all(Review(user_id=make_user(app, f"r{titulo[:3]}{i}".replace(" ", "")) if i else autor,
                                      game_id=g.id, score=9) for i in range(3))
            db.session.commit()
    return autor


def vistos(html):
    return {t for t in JOGOS if t in html}


def formulario(g, faixa):
    return {"title": g.title, "synopsis": g.synopsis, "release_date": g.release_date.isoformat(),
            "developer_id": str(g.developer_id), "genre_ids": [str(x.id) for x in g.genres],
            "platform_ids": [str(x.id) for x in g.platforms], "classificacao": faixa}


# ---------- CLS-01 ----------
def test_cls_01_valores_aceitos(app):
    slug = make_game(app, "Hades")
    with app.app_context():
        g = Game.query.filter_by(slug=slug).one()
        for faixa in (*cls.FAIXAS, None):
            g.classificacao = faixa
        with pytest.raises(ValueError):
            g.classificacao = "13"
        with pytest.raises(ValueError):
            g.classificacao_origem = "gog"


def test_cls_01_formulario_do_admin_recusa_faixa_invalida(app):
    make_user(app, "root", role="admin")
    slug = make_game(app, "Hades")
    with app.app_context():
        g = Game.query.filter_by(slug=slug).one()
        dados = formulario(g, "13")
        gid = g.id
    r = cliente(app, "root").post(f"/admin/jogos/{gid}/editar", data=dados)
    assert r.status_code == 400 and "Escolha uma classificação da lista." in r.get_data(as_text=True)


# ---------- CLS-02, CLS-03 ----------
@pytest.mark.parametrize("idade,faixa", [(0, None), (None, None), ("abc", None), (10, "10"), (13, "14"), (16, "16"),
                                         (17, "18"), (18, "18"), (21, "18")])
def test_cls_02_required_age_da_steam(idade, faixa):
    assert cls.de_steam(idade) == faixa


def test_cls_02_conector_da_steam_traz_a_faixa(api, monkeypatch):
    monkeypatch.setattr(steam, "capa", lambda appid, header: header)
    api.rotas["appdetails?appids=10&"] = {"10": {"success": True, "data": {
        "type": "game", "name": "Hades", "required_age": "17", "release_date": {"date": "17 set. 2020"}}}}
    assert steam.detalhe("10")["classificacao"] == "18"


@pytest.mark.parametrize("slug,faixa", [("everyone", "L"), ("everyone-10-plus", "10"), ("teen", "14"),
                                        ("mature", "18"), ("adults-only", "18"), ("rating-pending", None), (None, None)])
def test_cls_03_esrb_da_rawg(slug, faixa):
    assert cls.de_rawg({"slug": slug} if slug else None) == faixa


def test_cls_03_conector_da_rawg_traz_a_faixa(api, monkeypatch):
    monkeypatch.setenv("RAWG_API_KEY", "chave-falsa")
    api.rotas["api.rawg.io/api/games?"] = {"results": [{"id": 1, "name": "Hades", "esrb_rating": {"slug": "teen"}}]}
    assert rawg.buscar("hades")[0]["classificacao"] == "14"


# ---------- CLS-04 ----------
class FonteFalsa:
    def __init__(self, faixa):
        self.faixa = faixa

    def listar(self, limite, termo=None):
        yield {"external_id": "10", "title": "Hades", "synopsis": "s", "release_date": date(2020, 9, 17),
               "developer": "Supergiant", "genres": ["Ação"], "platforms": ["PC"], "cover_url": None,
               "classificacao": self.faixa}


def test_cls_04_vale_a_mais_restritiva_e_o_admin_nunca_e_sobrescrito(app):
    from app.sync import sincronizar

    with app.app_context():
        assert sincronizar("steam", modulo=FonteFalsa("12")) == (1, 0, 0, 0)
        assert sincronizar("steam", modulo=FonteFalsa("14")) == (0, 1, 0, 0)
        assert Game.query.one().classificacao == "14"
        g = Game.query.one()
        assert cls.aplicar(g, "18", "rawg") and (g.classificacao, g.classificacao_origem) == ("18", "rawg")
        db.session.commit()
        sincronizar("steam", modulo=FonteFalsa("10"))
        assert Game.query.one().classificacao == "18"
        g = Game.query.one()
        g.classificacao, g.classificacao_origem = "12", "admin"
        db.session.commit()
        sincronizar("steam", modulo=FonteFalsa("18"))
        assert (Game.query.one().classificacao, Game.query.one().classificacao_origem) == ("12", "admin")


def test_cls_04_admin_muda_e_grava_auditoria(app):
    make_user(app, "root", role="admin")
    slug = make_game(app, "Hades")
    with app.app_context():
        g = Game.query.filter_by(slug=slug).one()
        g.classificacao, g.classificacao_origem = "14", "steam"
        db.session.commit()
        dados = formulario(g, "16")
        gid = g.id
    root = cliente(app, "root")
    root.post(f"/admin/jogos/{gid}/editar", data=dados)
    root.post(f"/admin/jogos/{gid}/editar", data=dados)
    with app.app_context():
        g = db.session.get(Game, gid)
        assert (g.classificacao, g.classificacao_origem) == ("16", "admin")
        assert [a.detail for a in AuditLog.query.filter_by(action="jogo.classificacao")] == ["Hades (#1): 14 → 16"]


# ---------- CLS-05 ----------
def test_cls_05_snapshot_com_e_sem_o_campo(app, tmp_path, monkeypatch):
    base = {"synopsis": "s", "release_date": "2020-09-17", "developer": "D", "genres": ["Ação"], "platforms": ["PC"],
            "cover_url": None}
    caminho = tmp_path / "jogos.json"
    caminho.write_text(json.dumps([{**base, "external_id": "1", "title": "Com", "classificacao": "16"},
                                   {**base, "external_id": "2", "title": "Rawg", "classificacao": "18",
                                    "classificacao_origem": "rawg"},
                                   {**base, "external_id": "3", "title": "Sem"}]), encoding="utf-8")
    monkeypatch.setattr(arquivo, "CAMINHO", caminho)
    with app.app_context():
        fontes.importar("arquivo", limite=10)
        assert {g.title: (g.classificacao, g.classificacao_origem) for g in Game.query} == {
            "Com": ("16", "steam"), "Rawg": ("18", "rawg"), "Sem": (None, None)}


def test_cls_05_enriquecer_snapshot_grava_a_faixa_da_rawg(tmp_path):
    from app.snapshot import enriquecer

    class RawgFalsa:
        def buscar(self, termo, limite):
            return [{"external_id": "9", "classificacao": "18"}]

        def lojas(self, eid):
            return [("steam", "https://store.steampowered.com/app/10/")]

    caminho = tmp_path / "jogos.json"
    caminho.write_text(json.dumps([{"external_id": "10", "title": "Hades", "classificacao": "14"},
                                   {"external_id": "20", "title": "Outro"}]), encoding="utf-8")
    enriquecer(str(caminho), RawgFalsa())
    hades, outro = json.loads(caminho.read_text(encoding="utf-8"))
    assert (hades["classificacao"], hades["classificacao_origem"]) == ("18", "rawg")
    assert outro["classificacao"] is None


# ---------- CLS-06 ----------
def test_cls_06_selo_na_pagina_e_na_api(app, catalogo, client):
    html = client.get("/jogos/teen").get_data(as_text=True)
    assert "selo-14" in html and cls.NOTA in html
    assert "Classificação não informada" in client.get("/jogos/sem-faixa").get_data(as_text=True)
    assert client.get("/api/jogos/adulto").get_json()["classificacao"] == "18"
    assert client.get("/api/v2/jogos/sem-faixa").get_json()["classificacao"] is None


# ---------- CLS-07 ----------
@pytest.mark.parametrize("anos,esperados", [(12, {"Livre", "Sem Faixa"}), (16, {"Livre", "Teen", "Sem Faixa"}),
                                            (None, set(JOGOS))])
def test_cls_07_listagens_filtradas_pela_idade(app, catalogo, anos, esperados):
    conta(app, "ana", anos)
    with app.app_context():
        lista = GameList(user_id=catalogo, name="Todos")
        db.session.add(lista)
        db.session.flush()
        db.session.add_all(ListItem(list_id=lista.id, game_id=g.id, position=i)
                           for i, g in enumerate(Game.query.order_by(Game.id)))
        db.session.commit()
        lid = lista.id
    c = cliente(app, "ana")
    for url in ("/", "/buscar", "/buscar?q=a", "/generos/acao", f"/listas/{lid}", "/recomendacoes", "/estatisticas"):
        html = c.get(url).get_data(as_text=True)
        assert vistos(html) <= esperados, url
        if url in ("/buscar", "/generos/acao", f"/listas/{lid}"):
            assert vistos(html) == esperados, url
    api = c.get("/api/jogos?per_page=50").get_json()
    assert {j["titulo"] for j in api["items"]} == esperados
    assert c.get("/api/jogos/adulto").status_code == (200 if anos is None else 404)


def test_cls_07_contagem_e_paginacao_ja_filtradas(app):
    for i in range(25):
        slug = make_game(app, f"Jogo {i:02d}")
        with app.app_context():
            Game.query.filter_by(slug=slug).one().classificacao = "18" if i % 2 else "L"
            db.session.commit()
    conta(app, "ana", 12)
    html = cliente(app, "ana").get("/buscar").get_data(as_text=True)
    assert "13 jogos" in html or "13 resultado" in html
    assert "Próxima" not in html and "Jogo 01" not in html


# ---------- CLS-08 ----------
def test_cls_08_pagina_direta_com_aviso(app, catalogo):
    conta(app, "ana", 12)
    r = cliente(app, "ana").get("/jogos/adulto")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Este jogo é classificado para 18 anos" in html
    assert "/jogos/adulto/status" not in html and "Avaliar" not in html and "Sem Faixa" not in html
    assert "Este jogo é classificado" not in cliente(app, "ana").get("/jogos/sem-faixa").get_data(as_text=True)


# ---------- CLS-09 ----------
def test_cls_09_classificacao_max_para_todos_e_nunca_acima_da_idade(app, catalogo, client):
    assert vistos(client.get("/buscar?classificacao_max=14").get_data(as_text=True)) == {"Livre", "Teen", "Sem Faixa"}
    assert vistos(client.get("/buscar?classificacao_max=99").get_data(as_text=True)) == set(JOGOS)
    api = client.get("/api/jogos?classificacao_max=L").get_json()
    assert {j["titulo"] for j in api["items"]} == {"Livre", "Sem Faixa"}
    conta(app, "ana", 12)
    assert vistos(cliente(app, "ana").get("/buscar?classificacao_max=18").get_data(as_text=True)) == {
        "Livre", "Sem Faixa"}


# ---------- CLS-10 ----------
def test_cls_10_idade_sem_data_vale_12_e_adulto_nao_filtra(app):
    from app.models import User

    with app.app_context():
        assert cls.idade_limite(None) is None
        sem_data = User(nascimento=None)
        assert cls.idade_limite(sem_data) == 12
        assert cls.idade_limite(User(nascimento=anos_atras(15))) == 15
        assert cls.idade_limite(User(nascimento=anos_atras(30))) is None
        assert cls.faixa_maxima("99", None) is None and cls.faixa_maxima("16", User(nascimento=anos_atras(14))) == "14"
