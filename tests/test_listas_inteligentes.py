"""Listas inteligentes, progresso, exportação e ordem completa (LSA-01..12)."""
import csv
import io
import json
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import event

from app import tempo
from app.models import (
    Game, GameList, JogoFonte, ListaInteligente, ListItem, Review, SessaoJogo, Status, User, db, now,
)
from conftest import login, make_game, make_user

HOJE = date(2026, 11, 16)


@pytest.fixture(autouse=True)
def relogio(monkeypatch):
    monkeypatch.setattr(tempo, "agora", lambda: datetime(2026, 11, 16, 12, 0, tzinfo=tempo.FUSO))


def uid(app, nome="ana"):
    with app.app_context():
        return User.query.filter_by(username=nome).one().id


def gid(app, slug):
    with app.app_context():
        return Game.query.filter_by(slug=slug).one().id


def status(app, slug, valor, nome="ana", zerado_em=None, atualizado=None):
    with app.app_context():
        db.session.add(Status(user_id=uid(app, nome), game_id=gid(app, slug), value=valor, zerado_em=zerado_em,
                              updated_at=atualizado or datetime(2026, 11, 16)))
        db.session.commit()


def sessao(app, slug, dia, minutos=60, nome="ana"):
    with app.app_context():
        db.session.add(SessaoJogo(user_id=uid(app, nome), game_id=gid(app, slug), jogada_em=dia, duracao_min=minutos))
        db.session.commit()


@pytest.fixture
def ana(app, client):
    make_user(app, "ana")
    for titulo, genero in (("Alfa", "Ação"), ("Beta", "RPG"), ("Gama", "Ação"), ("Delta", "RPG")):
        make_game(app, titulo, genre=genero)
    login(client, "ana")
    return client


def criar(client, tipo, nome="Minha", **params):
    return client.post("/listas/inteligentes/nova", data={"nome": nome, "tipo": tipo, **params})


def jogos(client, lid=None, url=None):
    html = client.get(url or f"/listas/inteligentes/{lid}").get_data(as_text=True)
    return [t for t in ("Alfa", "Beta", "Gama", "Delta") if f"<strong>{t}</strong>" in html], html


def ultima(app):
    with app.app_context():
        return ListaInteligente.query.order_by(ListaInteligente.id.desc()).first().id


# ---------- LSA-01, LSA-02 ----------
def test_lsa_01_cria_e_limita_em_20(app, ana):
    assert criar(ana, "jogados_sem_avaliacao").status_code == 302
    for i in range(19):
        criar(ana, "jogados_sem_avaliacao", nome=f"L{i}")
    r = criar(ana, "jogados_sem_avaliacao", nome="21ª")
    assert r.status_code == 400 and "Limite de 20 listas inteligentes" in r.get_data(as_text=True)


def test_lsa_01_exige_login(client):
    assert client.get("/listas/inteligentes/nova").status_code == 302


@pytest.mark.parametrize("tipo,params,msg", [
    ("inexistente", {}, "Regra desconhecida"), ("largados", {"dias": "6"}, "entre 7 e 365"),
    ("largados", {"dias": "366"}, "entre 7 e 365"), ("largados", {"dias": "x"}, "inteiro"),
    ("wishlist_na_loja", {"loja": "psn"}, "Escolha a loja"), ("por_genero", {"genero": "nao-existe"}, "gênero existente"),
    ("por_genero", {"genero": "acao", "status": "zerado"}, "Status desconhecido"),
    ("mais_jogados", {"n": "4"}, "entre 5 e 50"), ("zerados_no_ano", {"ano": "2027"}, "entre 2000"),
])
def test_lsa_02_parametros_invalidos_400(app, ana, tipo, params, msg):
    r = criar(ana, tipo, **params)
    assert r.status_code == 400 and msg in r.get_data(as_text=True)
    with app.app_context():
        assert ListaInteligente.query.count() == 0


def test_lsa_02_largados_respeita_n_dias(app, ana):
    status(app, "alfa", "jogando")
    sessao(app, "alfa", HOJE - timedelta(days=30))  # exatamente 30 dias: entra
    status(app, "beta", "jogando")
    sessao(app, "beta", HOJE - timedelta(days=29))  # fica de fora
    status(app, "gama", "abandonado")
    status(app, "delta", "jogando", atualizado=datetime(2026, 9, 1))  # sem sessão, parado desde setembro
    criar(ana, "largados", dias="30")
    achados, html = jogos(ana, ultima(app))
    assert set(achados) == {"Alfa", "Gama", "Delta"}
    assert html.index("Delta") < html.index("Alfa")  # a sessão (ou parada) mais antiga primeiro
    assert "Jogando sem sessão há 30 dias ou abandonados" in html


def test_lsa_02_wishlist_na_loja_certa(app, ana):
    status(app, "alfa", "planejado")
    status(app, "beta", "planejado")
    status(app, "gama", "jogando")
    with app.app_context():
        db.session.add(JogoFonte(game_id=gid(app, "alfa"), fonte="gog", external_id="1"))
        db.session.add(JogoFonte(game_id=gid(app, "beta"), fonte="steam", external_id="2"))
        db.session.add(JogoFonte(game_id=gid(app, "gama"), fonte="gog", external_id="3"))
        db.session.commit()
    criar(ana, "wishlist_na_loja", loja="gog")
    assert jogos(ana, ultima(app))[0] == ["Alfa"]


def test_lsa_02_zerados_no_ano_por_data(app, ana):
    status(app, "beta", "jogado", zerado_em=date(2026, 3, 1))
    status(app, "alfa", "jogado", zerado_em=date(2026, 5, 1))
    status(app, "gama", "jogado", zerado_em=date(2025, 12, 31))
    criar(ana, "zerados_no_ano")
    achados, html = jogos(ana, ultima(app))
    assert set(achados) == {"Alfa", "Beta"} and html.index("Beta") < html.index("Alfa")


def test_lsa_02_por_genero_com_status(app, ana):
    status(app, "alfa", "jogado")
    status(app, "gama", "planejado")
    status(app, "beta", "jogado")
    criar(ana, "por_genero", genero="acao")
    assert jogos(ana, ultima(app))[0] == ["Alfa", "Gama"]
    criar(ana, "por_genero", nome="Ação jogados", genero="acao", status="jogado")
    assert jogos(ana, ultima(app))[0] == ["Alfa"]


def test_lsa_02_mais_jogados_top_n_por_horas(app, ana):
    for slug, m in (("alfa", 10), ("beta", 300), ("gama", 200), ("delta", 50)):
        sessao(app, slug, HOJE, m)
    sessao(app, "alfa", HOJE - timedelta(days=1), 500)  # Alfa soma 510
    criar(ana, "mais_jogados", n="5")
    _, html = jogos(ana, ultima(app))
    ordem = sorted(("Alfa", "Beta", "Gama", "Delta"), key=html.index)
    assert ordem == ["Alfa", "Beta", "Gama", "Delta"]


def test_lsa_02_jogados_sem_avaliacao(app, ana):
    status(app, "alfa", "jogado")
    status(app, "beta", "jogado")
    status(app, "gama", "jogando")
    with app.app_context():
        db.session.add(Review(user_id=uid(app), game_id=gid(app, "beta"), score=7))
        db.session.commit()
    criar(ana, "jogados_sem_avaliacao")
    assert jogos(ana, ultima(app))[0] == ["Alfa"]


# ---------- LSA-03, LSA-04 ----------
def test_lsa_03_calculada_a_cada_acesso_e_gravada_como_json(app, ana):
    criar(ana, "jogados_sem_avaliacao")
    lid = ultima(app)
    assert jogos(ana, lid)[0] == []
    ana.post("/jogos/delta/status", data={"status": "jogado"})
    assert jogos(ana, lid)[0] == ["Delta"]
    with app.app_context():
        assert json.loads(db.session.get(ListaInteligente, lid).regra) == {"tipo": "jogados_sem_avaliacao",
                                                                          "parametros": {}}
        assert ListItem.query.count() == 0


def test_lsa_04_sem_removidos_nem_mesclados(app, ana):
    for slug in ("alfa", "beta", "gama"):
        status(app, slug, "jogado")
    with app.app_context():
        Game.query.filter_by(slug="alfa").one().removed_at = now()
        Game.query.filter_by(slug="beta").one().mesclado_em_id = gid(app, "gama")
        db.session.commit()
    criar(ana, "jogados_sem_avaliacao")
    assert jogos(ana, ultima(app))[0] == ["Gama"]


# ---------- LSA-05, LSA-06 ----------
def test_lsa_05_privada_404_e_escrita_403(app, ana):
    criar(ana, "jogados_sem_avaliacao", nome="Privada", visibilidade="privada")
    privada = ultima(app)
    criar(ana, "jogados_sem_avaliacao", nome="Pública")
    publica = ultima(app)
    make_user(app, "bia")
    bia = app.test_client()
    assert bia.get(f"/listas/inteligentes/{privada}").status_code == 404
    login(bia, "bia")
    assert bia.get(f"/listas/inteligentes/{privada}").status_code == 404
    assert bia.get(f"/listas/inteligentes/{publica}").status_code == 200
    for acao in ("editar", "apagar", "congelar"):
        assert bia.post(f"/listas/inteligentes/{publica}/{acao}", data={"nome": "x"}).status_code == 403
    with app.app_context():
        User.query.filter_by(username="ana").one().perfil_privado = True
        db.session.commit()
    assert bia.get(f"/listas/inteligentes/{publica}").status_code == 404
    assert bia.get(f"/listas/inteligentes/{publica}/exportar.json").status_code == 404


def test_lsa_05_dono_edita_parametros(app, ana):
    criar(ana, "largados", dias="30")
    lid = ultima(app)
    ana.post(f"/listas/inteligentes/{lid}/editar", data={"nome": "Largados 60", "dias": "60"})
    with app.app_context():
        li = db.session.get(ListaInteligente, lid)
        assert li.nome == "Largados 60" and json.loads(li.regra)["parametros"] == {"dias": 60}


@pytest.mark.parametrize("dados,msg", [({"nome": "X", "dias": "3"}, "7"), ({"nome": "", "dias": "60"}, "O nome é obrigatório")])
def test_lsa_02_edicao_invalida_volta_com_400(app, ana, dados, msg):
    criar(ana, "largados", dias="30", nome="Largados")
    lid = ultima(app)
    r = ana.post(f"/listas/inteligentes/{lid}/editar", data=dados)
    assert r.status_code == 400 and msg in r.get_data(as_text=True)
    with app.app_context():
        li = db.session.get(ListaInteligente, lid)
        assert li.nome == "Largados" and json.loads(li.regra)["parametros"] == {"dias": 30}


def test_lsa_06_aparece_em_listas_com_selo(app, ana):
    criar(ana, "mais_jogados", nome="Top", n="10")
    html = ana.get("/listas").get_data(as_text=True)
    assert "Top" in html and "automática" in html and "Os 10 jogos com mais horas no diário" in html


# ---------- LSA-07 ----------
def test_lsa_07_congelar_com_corte_em_200(app, ana):
    with app.app_context():
        a = uid(app)
        for i in range(205):
            make_game(app, f"Extra {i:03d}")
        for g in Game.query.filter(Game.slug.like("extra-%")):
            db.session.add(Status(user_id=a, game_id=g.id, value="jogado"))
        db.session.commit()
    criar(ana, "jogados_sem_avaliacao", nome="Muitos")
    lid = ultima(app)
    r = ana.post(f"/listas/inteligentes/{lid}/congelar", follow_redirects=True)
    assert "cortada nos primeiros 200" in r.get_data(as_text=True)
    with app.app_context():
        gl = GameList.query.one()
        assert len(gl.items) == 200 and gl.items[0].game.title == "Extra 000"
        assert db.session.get(ListaInteligente, lid) is not None


# ---------- LSA-08 ----------
def test_lsa_08_barra_de_progresso_e_orcamento(app, ana):
    from app.listas_inteligentes import progresso_comum

    ana.post("/listas", data={"name": "Comum"})
    with app.app_context():
        lid = GameList.query.one().id
    assert "0 de 0" in ana.get(f"/listas/{lid}").get_data(as_text=True)
    for slug in ("alfa", "beta", "gama"):
        ana.post(f"/jogos/{slug}/adicionar-a-lista", data={"list_id": lid})
    status(app, "alfa", "jogado")
    status(app, "beta", "jogando")
    sessao(app, "alfa", HOJE, 90)
    sessao(app, "delta", HOJE, 600)  # fora da lista
    html = ana.get(f"/listas/{lid}").get_data(as_text=True)
    assert "1 de 3</strong> jogados, 1 jogando" in html and "1h 30min registradas" in html
    consultas = []

    def f(*a):
        consultas.append(1)
    with app.app_context():
        gl = db.session.get(GameList, lid)
        event.listen(db.engine, "before_cursor_execute", f)
        prog = progresso_comum(gl, True)
        event.remove(db.engine, "before_cursor_execute", f)
    assert prog["total"] == 3 and len(consultas) <= 3


# ---------- LSA-09 ----------
def test_lsa_09_exportacao_json_e_csv_sem_formula(app, ana):
    make_game(app, "=HYPERLINK(1)")
    ana.post("/listas", data={"name": "Exportar"})
    with app.app_context():
        lid = GameList.query.one().id
    for slug in ("alfa", "hyperlink-1"):
        ana.post(f"/jogos/{slug}/adicionar-a-lista", data={"list_id": lid})
    status(app, "alfa", "jogado")
    sessao(app, "alfa", HOJE, 90)
    dados = ana.get(f"/listas/{lid}/exportar.json").get_json()
    item = dados["itens"][0]
    assert item["posicao"] == 1 and item["status"] == "jogado" and item["horas"] == 1.5
    assert set(item["jogo"]) == {"slug", "titulo", "lancamento", "desenvolvedora", "generos", "plataformas", "capa",
                                 "url"}
    assert "email" not in json.dumps(dados)
    linhas = list(csv.reader(io.StringIO(ana.get(f"/listas/{lid}/exportar.csv").get_data(as_text=True))))
    assert linhas[0] == ["posicao", "slug", "titulo", "lancamento", "status", "horas"]
    assert linhas[2][2] == "'=HYPERLINK(1)"
    criar(ana, "jogados_sem_avaliacao", nome="Auto", visibilidade="privada")
    assert ana.get(f"/listas/inteligentes/{ultima(app)}/exportar.csv").status_code == 200
    assert app.test_client().get(f"/listas/inteligentes/{ultima(app)}/exportar.csv").status_code == 404


def test_lsa_09_lista_comum_privada_nao_exporta(app, ana):
    ana.post("/listas", data={"name": "Secreta", "visibility": "privada"})
    with app.app_context():
        lid = GameList.query.one().id
    assert app.test_client().get(f"/listas/{lid}/exportar.json").status_code == 404


def test_lsa_09_consultas_da_exportacao_nao_crescem_com_os_itens(app, ana):
    ana.post("/listas", data={"name": "Exportar"})
    with app.app_context():
        lid = GameList.query.one().id

    def consultas():
        n = []

        def conta(*a):
            n.append(1)
        with app.app_context():
            event.listen(db.engine, "before_cursor_execute", conta)
            assert ana.get(f"/listas/{lid}/exportar.json").status_code == 200
            event.remove(db.engine, "before_cursor_execute", conta)
        return len(n)
    ana.post("/jogos/alfa/adicionar-a-lista", data={"list_id": lid})
    com_um = consultas()
    for slug in ("beta", "gama", "delta"):
        ana.post(f"/jogos/{slug}/adicionar-a-lista", data={"list_id": lid})
    assert consultas() == com_um


# ---------- LSA-10, LSA-11 ----------
@pytest.fixture
def lista3(app, ana):
    ana.post("/listas", data={"name": "Ordem"})
    with app.app_context():
        lid = GameList.query.one().id
    for slug in ("alfa", "beta", "gama"):
        ana.post(f"/jogos/{slug}/adicionar-a-lista", data={"list_id": lid})
    return lid


def ordem_atual(app, lid):
    with app.app_context():
        return [i.game.slug for i in db.session.get(GameList, lid).items]


def test_lsa_10_ordem_completa(app, ana, lista3):
    ids = [gid(app, s) for s in ("gama", "alfa", "beta")]
    assert ana.post(f"/listas/{lista3}/ordem", data={"ordem": ",".join(map(str, ids))}).status_code == 302
    assert ordem_atual(app, lista3) == ["gama", "alfa", "beta"]
    with app.app_context():
        assert [i.position for i in db.session.get(GameList, lista3).items] == [0, 1, 2]


@pytest.mark.parametrize("ordem", ["faltando", "repetido", "fora", "texto"])
def test_lsa_10_ordem_invalida_400_sem_mudar(app, ana, lista3, ordem):
    a, b, g, d = (gid(app, s) for s in ("alfa", "beta", "gama", "delta"))
    valores = {"faltando": [g, a], "repetido": [g, a, a], "fora": [g, a, d], "texto": None}[ordem]
    corpo = ",".join(map(str, valores)) if valores else "1,x,2"
    assert ana.post(f"/listas/{lista3}/ordem", data={"ordem": corpo}).status_code == 400
    assert ordem_atual(app, lista3) == ["alfa", "beta", "gama"]


def test_lsa_10_so_o_dono(app, ana, lista3):
    make_user(app, "bia")
    bia = app.test_client()
    login(bia, "bia")
    assert bia.post(f"/listas/{lista3}/ordem", data={"ordem": "1,2,3"}).status_code == 403


def test_lsa_11_script_de_arrastar_so_para_o_dono_e_botoes_continuam(app, ana, lista3):
    html = ana.get(f"/listas/{lista3}").get_data(as_text=True)
    assert "ordem.js" in html and 'draggable="true"' in html and "/mover" in html
    assert "ordem.js" not in app.test_client().get(f"/listas/{lista3}").get_data(as_text=True)
    assert ana.get("/static/ordem.js").status_code == 200
