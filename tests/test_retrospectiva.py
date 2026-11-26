"""Retrospectiva e gráficos SVG (RET-01..12)."""
import re
from datetime import date, datetime

import pytest
from sqlalchemy import event

from app import graficos_svg as g
from app import tempo
from app.models import Follow, Game, Genre, SessaoJogo, Status, User, db, now
from conftest import login, make_game, make_user


@pytest.fixture(autouse=True)
def relogio(monkeypatch):
    monkeypatch.setattr(tempo, "agora", lambda: datetime(2026, 11, 16, 12, 0, tzinfo=tempo.FUSO))


# ---------- RET-01..03: o módulo de gráficos ----------
def test_ret_01_estrutura_e_acessibilidade():
    for svg in (g.barras([("Horas", [1, 2])], ["jan", "fev"], "Horas por mês"),
                g.linha([("A", [1, 2]), ("B", [2, 1])], ["jan", "fev"], "Comparação")):
        assert svg.startswith("<svg") and 'viewBox="' in svg and 'role="img"' in svg
        assert re.search(r"<title>(Horas por mês|Comparação)</title>", svg)
    barras = g.barras([("Horas", [1, 2.5])], ["jan", "fev"], "t")
    assert "<title>jan: 1</title>" in barras and "<title>fev: 2,5</title>" in barras
    assert "<title>A — fev: 2</title>" in g.linha([("A", [1, 2])], ["jan", "fev"], "t")
    spark = g.sparkline([1, 2, 3], "Últimas semanas")
    assert 'role="img"' in spark and "<title>Últimas semanas</title>" in spark and "<polyline" in spark


def test_ret_01_linha_aceita_ate_4_series():
    series = [(f"s{i}", [1]) for i in range(5)]
    assert g.linha(series[:4], ["x"], "t").count("<circle") == 4
    with pytest.raises(ValueError):
        g.linha(series, ["x"], "t")


def test_ret_02_rotulo_malicioso_vira_texto():
    malicioso = "<script>alert(1)</script>"
    for svg in (g.barras([(malicioso, [3])], [malicioso], malicioso), g.linha([(malicioso, [3])], [malicioso], malicioso),
                g.sparkline([1], malicioso)):
        assert "<script" not in svg and "&lt;script&gt;" in svg
        assert not re.search(r"\son\w+=", svg) and "foreignObject" not in svg and "href" not in svg
        assert "currentColor" in svg or 'class="serie-0"' in svg


def test_ret_03_deterministico():
    args = ([("Horas", [3, 0, 7])], ["a", "b", "c"], "t")
    assert g.barras(*args) == g.barras(*args)
    assert g.linha(*args) == g.linha(*args)


def test_ret_04_todo_grafico_de_pagina_tem_a_tabela(base, client):
    from app.models import PrecoLoja

    sessao(base, "ana", "hades", date(2026, 11, 16), 90)
    with base.app_context():
        db.session.add(PrecoLoja(game_id=jogo_id(base, "hades"), fonte="steam", moeda="BRL", preco_centavos=4990,
                                 preco_base_centavos=4990, desconto_pct=0, coletado_em=now(), verificado_em=now()))
        db.session.commit()
    for url in ("/u/ana", "/u/ana/retrospectiva/2026", "/jogos/hades/precos"):
        html = client.get(url).get_data(as_text=True)
        graficos = html.count('<svg xmlns="http://www.w3.org/2000/svg"')
        assert graficos and html.count("<summary>Ver como tabela</summary>") == graficos, url
    perfil = client.get("/u/ana").get_data(as_text=True)
    assert "<td>Semana de 16/11</td><td>1.5</td>" in perfil
    assert "<td>R$ 49,90</td>" in client.get("/jogos/hades/precos").get_data(as_text=True).split("</details>")[0]


def test_ret_03_casos_de_borda():
    assert "Sem dados" in g.barras([], [], "t") and "Sem dados" in g.linha([("a", [])], [], "t")
    assert "Sem dados" in g.sparkline([], "t")
    zeros = g.barras([("Horas", [0, 0])], ["a", "b"], "t")
    assert 'class="eixo"' in zeros and "<rect" not in zeros
    um = g.linha([("A", [5])], ["jan"], "t")
    assert um.count("<circle") == 1 and "<polyline" not in um
    for f in (lambda: g.barras([("a", [-1])], ["x"], "t"), lambda: g.linha([("a", [1, -2])], ["x", "y"], "t"),
              lambda: g.sparkline([-3], "t")):
        with pytest.raises(ValueError):
            f()


@pytest.mark.parametrize("maximo,topo", [(0, 1), (1, 1), (1.2, 2), (3, 5), (7, 10), (11, 20), (45, 50), (51, 100),
                                         (230, 500)])
def test_ret_03_escala_1_2_5(maximo, topo):
    assert g.escala(maximo) == topo


# ---------- retrospectiva ----------
def uid(app, nome):
    with app.app_context():
        return User.query.filter_by(username=nome).one().id


def jogo_id(app, slug):
    with app.app_context():
        return Game.query.filter_by(slug=slug).one().id


def sessao(app, nome, slug, dia, minutos, origem="manual"):
    with app.app_context():
        db.session.add(SessaoJogo(user_id=uid(app, nome), game_id=jogo_id(app, slug), jogada_em=dia,
                                  duracao_min=minutos, origem=origem))
        db.session.commit()


@pytest.fixture
def base(app):
    make_user(app, "ana")
    make_game(app, "Hades", genre="Ação")
    make_game(app, "Celeste", genre="Plataforma")
    with app.app_context():  # Hades também é de Plataforma: as horas contam nos dois gêneros
        hades = Game.query.filter_by(slug="hades").one()
        hades.genres.append(Genre.query.filter_by(name="Plataforma").one())
        db.session.commit()
    return app


def test_ret_05_totais_zerados_e_redirect(base, client):
    sessao(base, "ana", "hades", date(2026, 1, 31), 120)
    sessao(base, "ana", "celeste", date(2026, 2, 1), 300)
    sessao(base, "ana", "celeste", date(2025, 12, 31), 999)
    with base.app_context():
        db.session.add(Status(user_id=uid(base, "ana"), game_id=jogo_id(base, "celeste"), value="jogado",
                              zerado_em=date(2026, 3, 1)))
        db.session.add(Status(user_id=uid(base, "ana"), game_id=jogo_id(base, "hades"), value="jogado",
                              zerado_em=date(2026, 2, 1)))
        db.session.commit()
    assert client.get("/u/ana/retrospectiva").headers["Location"].endswith("/u/ana/retrospectiva/2026")
    html = client.get("/u/ana/retrospectiva/2026").get_data(as_text=True)
    assert "<strong>7h</strong>" in html and "<strong>2</strong>" in html  # 2 sessões, 2 jogos, 2 zerados
    assert html.index("zerado em 01/02/2026") < html.index("zerado em 01/03/2026")
    assert "5h de <a href=\"/jogos/celeste\">Celeste</a>" in html


@pytest.mark.parametrize("ano", [1999, 2027])
def test_ret_05_ano_invalido_404(base, client, ano):
    assert client.get(f"/u/ana/retrospectiva/{ano}").status_code == 404


def test_ret_06_soma_por_mes_e_genero_na_virada(base):
    from app.retrospectiva import minutos_por_genero, minutos_por_mes

    sessao(base, "ana", "hades", date(2026, 1, 31), 60)
    sessao(base, "ana", "hades", date(2026, 2, 1), 30)
    sessao(base, "ana", "celeste", date(2025, 12, 31), 500)
    sessao(base, "ana", "celeste", date(2026, 12, 31), 15)
    with base.app_context():
        a = uid(base, "ana")
        assert minutos_por_mes([a], 2026) == {(a, 1): 60, (a, 2): 30, (a, 12): 15}
        assert minutos_por_genero([a], 2026) == [("Plataforma", 105), ("Ação", 90)]


def test_ret_06_grafico_com_12_meses_e_tabela(base, client):
    sessao(base, "ana", "hades", date(2026, 3, 10), 90)
    html = client.get("/u/ana/retrospectiva/2026").get_data(as_text=True)
    mensal = html[html.index("Horas por mês"):html.index("Gêneros com mais horas")]
    assert mensal.count("<td>") == 24 and "<title>mar: 1,5</title>" in mensal and "Ver como tabela" in mensal
    assert mensal.count("<rect") == 1  # meses vazios ficam com zero, sem barra (RET-04 e RET-06)


def test_ret_07_steam_fora_do_grafico_mensal_e_da_sessao_mais_longa(base, client):
    sessao(base, "ana", "hades", date(2026, 5, 1), 60)
    sessao(base, "ana", "celeste", date(2026, 11, 16), 3000, origem="steam")
    html = client.get("/u/ana/retrospectiva/2026").get_data(as_text=True)
    assert "Inclui 50h importadas da Steam sem data" in html
    assert "<strong>51h</strong>" in html and "<title>nov:" not in html
    assert "Celeste</a> · 50h" in html  # o mais jogado conta a Steam
    assert "1h de <a href=\"/jogos/hades\">" in html


def test_ret_09_jogo_removido_nao_e_a_sessao_mais_longa(base, client):
    sessao(base, "ana", "hades", date(2026, 5, 1), 60)
    sessao(base, "ana", "celeste", date(2026, 5, 2), 600)
    with base.app_context():
        Game.query.filter_by(slug="celeste").one().removed_at = datetime(2026, 6, 1)
        db.session.commit()
    html = client.get("/u/ana/retrospectiva/2026").get_data(as_text=True)
    assert "1h de <a href=\"/jogos/hades\">" in html and "/jogos/celeste" not in html


def test_ret_08_ano_vazio(base, client):
    html = client.get("/u/ana/retrospectiva/2024").get_data(as_text=True)
    assert "Nenhuma sessão nem jogo zerado em 2024" in html and "<svg" not in html


def test_ret_09_perfil_privado_e_jogo_removido(base, client):
    sessao(base, "ana", "hades", date(2026, 5, 1), 600)
    sessao(base, "ana", "celeste", date(2026, 5, 2), 60)
    with base.app_context():
        Game.query.filter_by(slug="hades").one().removed_at = now()
        db.session.commit()
    html = client.get("/u/ana/retrospectiva/2026").get_data(as_text=True)
    assert "<strong>11h</strong>" in html and "Celeste</a> · 1h" in html  # removido conta nas horas, não no topo
    with base.app_context():
        User.query.filter_by(username="ana").one().perfil_privado = True
        db.session.commit()
    assert client.get("/u/ana/retrospectiva/2026").status_code == 404


def test_ret_10_media_dos_seguidos_ignora_quem_nao_pode_ver(base):
    for n in ("bia", "carla", "duda"):
        make_user(base, n)
    sessao(base, "ana", "hades", date(2026, 1, 5), 60)
    sessao(base, "bia", "hades", date(2026, 1, 5), 120)
    sessao(base, "carla", "celeste", date(2026, 1, 6), 240)
    sessao(base, "duda", "celeste", date(2026, 1, 6), 6000)  # privada e não segue a ana
    with base.app_context():
        for n in ("bia", "carla", "duda"):
            db.session.add(Follow(follower_id=uid(base, "ana"), followed_id=uid(base, n)))
        User.query.filter_by(username="duda").one().perfil_privado = True
        db.session.commit()
    ana = base.test_client()
    login(ana, "ana")
    html = ana.get("/u/ana/retrospectiva/2026").get_data(as_text=True)
    assert "<title>Média de quem você segue — jan: 3</title>" in html
    assert '<span class="tag">Ação</span>' in html
    outro = base.test_client()
    login(outro, "bia")
    assert "Você e quem você segue" not in outro.get("/u/ana/retrospectiva/2026").get_data(as_text=True)


def test_ret_10_seguido_so_com_saldo_da_steam_fica_fora_da_media(base):
    for n in ("bia", "carla"):
        make_user(base, n)
    sessao(base, "ana", "hades", date(2026, 1, 5), 60)
    sessao(base, "bia", "hades", date(2026, 1, 5), 120)
    sessao(base, "carla", "celeste", date(2026, 3, 1), 6000, origem="steam")
    with base.app_context():
        for n in ("bia", "carla"):
            db.session.add(Follow(follower_id=uid(base, "ana"), followed_id=uid(base, n)))
        db.session.commit()
    ana = base.test_client()
    login(ana, "ana")
    assert "<title>Média de quem você segue — jan: 2</title>" in ana.get("/u/ana/retrospectiva/2026").get_data(as_text=True)


def test_ret_10_sem_seguidos(base):
    sessao(base, "ana", "hades", date(2026, 1, 5), 60)
    ana = base.test_client()
    login(ana, "ana")
    assert "Siga pessoas para comparar" in ana.get("/u/ana/retrospectiva/2026").get_data(as_text=True)


def test_ret_11_sparkline_das_12_semanas(base, client):
    from app.retrospectiva import horas_por_semana

    sessao(base, "ana", "hades", date(2026, 11, 16), 60)  # semana atual
    sessao(base, "ana", "hades", date(2026, 8, 31), 120)  # segunda da 12ª semana para trás
    sessao(base, "ana", "hades", date(2026, 8, 30), 999)  # fora da janela
    sessao(base, "ana", "hades", date(2026, 11, 10), 999, origem="steam")
    with base.app_context():
        valores = horas_por_semana(uid(base, "ana"))
    assert len(valores) == 12 and valores[0] == 2 and valores[-1] == 1 and sum(valores) == 3
    assert 'class="sparkline"' in client.get("/u/ana").get_data(as_text=True)


def contar(app, client, url):
    consultas = []

    def conta(*a):
        consultas.append(1)
    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", conta)
        assert client.get(url).status_code == 200
        event.remove(db.engine, "before_cursor_execute", conta)
    return len(consultas)


def test_ret_11_orcamento_de_consultas(base):
    make_user(base, "bia")
    with base.app_context():
        db.session.add(Follow(follower_id=uid(base, "ana"), followed_id=uid(base, "bia")))
        db.session.commit()
    ana = base.test_client()
    login(ana, "ana")
    for mes in range(1, 13):
        sessao(base, "ana", "hades", date(2026, mes, 3), 60)
        sessao(base, "bia", "celeste", date(2026, mes, 4), 30)
    poucas = contar(base, ana, "/u/ana/retrospectiva/2026")
    for mes in range(1, 13):
        sessao(base, "ana", "celeste", date(2026, mes, 10), 45)
    assert poucas <= 12 and contar(base, ana, "/u/ana/retrospectiva/2026") == poucas
