import io
import json
from datetime import datetime, timedelta
from urllib.error import HTTPError

import pytest

from app import precos, tempo
from app.models import Game, JogoFonte, PrecoLoja, Status, db
from conftest import http, make_game, make_user

AGORA = datetime(2026, 11, 20, 15, 0)
STEAM = "appdetails?appids=10&"
GOG = "api.gog.com/products/20/prices"


def steam(price=None, is_free=False, success=True, appid="10"):
    data = {"type": "game", "is_free": is_free, **({"price_overview": price} if price else {})}
    return {appid: {"success": success, "data": data}}


def preco_steam(final, initial, desconto, moeda="BRL"):
    return {"currency": moeda, "initial": initial, "final": final, "discount_percent": desconto}


def gog(*precos_):
    return {"_embedded": {"prices": [{"currency": {"code": m}, "basePrice": f"{b} {m}", "finalPrice": f"{f} {m}"}
                                     for f, b, m in precos_]}}


@pytest.fixture
def relogio(monkeypatch):
    estado = {"agora": AGORA}
    monkeypatch.setattr(precos, "agora_utc", lambda: estado["agora"])
    monkeypatch.setattr(tempo, "agora", lambda: estado["agora"].replace(tzinfo=tempo.timezone.utc).astimezone(tempo.FUSO))
    return estado


@pytest.fixture
def jogo(app):
    """Jogo com vínculos steam=10, gog=20 e epic; devolve o id."""
    slug = make_game(app, "Hades")
    with app.app_context():
        g = Game.query.filter_by(slug=slug).one()
        db.session.add_all([JogoFonte(game=g, fonte="steam", external_id="10", url_loja="https://store.steampowered.com/app/10"),
                            JogoFonte(game=g, fonte="gog", external_id="20", url_loja="https://www.gog.com/game/hades"),
                            JogoFonte(game=g, fonte="epic", external_id="hades")])
        db.session.commit()
        return g.id


def linhas(app, game_id):
    with app.app_context():
        return [(p.fonte, p.valores, p.coletado_em, p.verificado_em)
                for p in PrecoLoja.query.filter_by(game_id=game_id).order_by(PrecoLoja.id)]


def coletar(app, fonte, **kw):
    with app.app_context():
        return precos.coletar(fonte, **kw)


# ---------- PRC-01 ----------
def test_prc_01_so_brl_e_lojas_com_preco(app, jogo):
    with app.app_context():
        with pytest.raises(ValueError):
            PrecoLoja(game_id=jogo, fonte="epic")
        with pytest.raises(ValueError):
            PrecoLoja(game_id=jogo, fonte="steam", moeda="USD")
        p = PrecoLoja(game_id=jogo, fonte="steam", moeda="BRL", preco_centavos=1999, preco_base_centavos=1999,
                      desconto_pct=0, coletado_em=AGORA, verificado_em=AGORA)
        db.session.add(p)
        db.session.commit()
        assert p.valores == (1999, 1999, 0)


def test_prc_01_outra_moeda_conta_sem_preco(app, jogo, api, relogio):
    api.rotas[STEAM] = steam(preco_steam(500, 500, 0, "USD"))
    assert coletar(app, "steam") == (1, 0, 0, 1, 0)
    api.rotas[GOG] = gog((500, 500, "USD"))
    assert coletar(app, "gog") == (1, 0, 0, 1, 0)
    assert linhas(app, jogo) == []


# ---------- PRC-02 ----------
def test_prc_02_steam_com_desconto(app, jogo, api, relogio):
    api.rotas[STEAM] = steam(preco_steam(5999, 9999, 40))
    assert coletar(app, "steam") == (1, 1, 0, 0, 0)
    assert linhas(app, jogo) == [("steam", (5999, 9999, 40), AGORA, AGORA)]
    assert "cc=br" in api.urls()[0]


def test_prc_02_steam_sem_desconto_e_gratuito(app, jogo, api, relogio):
    api.rotas[STEAM] = steam(preco_steam(4999, 4999, 0))
    coletar(app, "steam")
    relogio["agora"] = AGORA + timedelta(days=1)
    api.rotas[STEAM] = steam(is_free=True)
    coletar(app, "steam")
    assert [v for _, v, _, _ in linhas(app, jogo)] == [(4999, 4999, 0), (0, 0, 0)]


def test_prc_02_steam_sem_price_overview_nao_grava(app, jogo, api, relogio):
    api.rotas[STEAM] = steam()
    assert coletar(app, "steam") == (1, 0, 0, 1, 0)
    api.rotas[STEAM] = steam(success=False)
    assert coletar(app, "steam") == (1, 0, 0, 1, 0)
    assert linhas(app, jogo) == []


@pytest.mark.parametrize("corpo", [{}, [], {"10": {"success": True, "data": {"price_overview": {"currency": "BRL"}}}}])
def test_prc_02_steam_malformada_conta_falha(app, jogo, api, relogio, corpo):
    api.rotas[STEAM] = corpo
    assert coletar(app, "steam") == (1, 0, 0, 0, 1)


# ---------- PRC-03 ----------
def test_prc_03_gog_so_brl_e_desconto_arredondado(app, jogo, api, relogio):
    api.rotas[GOG] = gog((1000, 1000, "USD"), (1979, 3299, "BRL"))
    assert coletar(app, "gog") == (1, 1, 0, 0, 0)
    assert linhas(app, jogo) == [("gog", (1979, 3299, 40), AGORA, AGORA)]  # 40,006% -> 40
    assert "countryCode=BR" in api.urls()[0]


def test_prc_03_gog_sem_desconto_e_meio_para_cima(app, jogo, api, relogio):
    api.rotas[GOG] = gog((2999, 2999, "BRL"))
    coletar(app, "gog")
    api.rotas[GOG] = gog((1, 2, "BRL"))  # 50% exatos
    coletar(app, "gog")
    api.rotas[GOG] = gog((3, 8, "BRL"))  # 62,5% -> 63
    coletar(app, "gog")
    assert [v for _, v, _, _ in linhas(app, jogo)] == [(2999, 2999, 0), (1, 2, 50), (3, 8, 63)]


def test_prc_03_gog_prices_not_found_e_id_nao_numerico(app, jogo, api, relogio):
    api.rotas[GOG] = {"reason": "PRICES_NOT_FOUND"}
    assert coletar(app, "gog") == (1, 0, 0, 1, 0)
    corpo = io.BytesIO(json.dumps({"error": "not_found", "reason": "PRICES_NOT_FOUND"}).encode())
    api.rotas[GOG] = HTTPError("https://x", 404, "erro", {}, corpo)
    assert coletar(app, "gog") == (1, 0, 0, 1, 0)
    with app.app_context():
        JogoFonte.query.filter_by(fonte="gog").one().external_id = "hades"
        db.session.commit()
    api.pedidos.clear()
    assert coletar(app, "gog") == (1, 0, 0, 1, 0)
    assert api.pedidos == []


@pytest.mark.parametrize("corpo", [{}, {"_embedded": {"prices": [{"currency": {"code": "BRL"}, "basePrice": "x",
                                                                   "finalPrice": "1 BRL"}]}}, http(404)])
def test_prc_03_gog_malformada_conta_falha(app, jogo, api, relogio, corpo):
    api.rotas[GOG] = corpo
    assert coletar(app, "gog") == (1, 0, 0, 0, 1)


# ---------- PRC-04 ----------
def test_prc_04_epic_e_rawg_nao_tem_preco(app, jogo, api):
    assert set(precos.COLETORES) == {"steam", "gog"}
    with app.app_context():
        with pytest.raises(ValueError):
            precos.registrar(jogo, "epic", (1, 1, 0), AGORA)
            db.session.flush()


# ---------- PRC-05 ----------
def test_prc_05_linha_por_mudanca(app, jogo, api, relogio):
    api.rotas[STEAM] = steam(preco_steam(5999, 9999, 40))
    coletar(app, "steam")
    depois = AGORA + timedelta(hours=6)
    relogio["agora"] = depois
    assert coletar(app, "steam") == (1, 0, 1, 0, 0)
    assert linhas(app, jogo) == [("steam", (5999, 9999, 40), AGORA, depois)]
    mais_tarde = AGORA + timedelta(days=2)
    relogio["agora"] = mais_tarde
    api.rotas[STEAM] = steam(preco_steam(9999, 9999, 0))
    mudaram = set()
    assert coletar(app, "steam", mudaram=mudaram) == (1, 1, 0, 0, 0)
    assert linhas(app, jogo)[1] == ("steam", (9999, 9999, 0), mais_tarde, mais_tarde) and mudaram == {jogo}


# ---------- PRC-06 ----------
def test_prc_06_planejados_primeiro_falha_nao_para_e_429_interrompe(app, jogo, api, relogio):
    outro = make_game(app, "Celeste")
    uid = make_user(app)
    with app.app_context():
        g = Game.query.filter_by(slug=outro).one()
        db.session.add_all([JogoFonte(game=g, fonte="steam", external_id="11"),
                            Status(user_id=uid, game_id=g.id, value="planejado")])
        db.session.commit()
        assert [gid for gid, _ in precos.vinculos("steam")] == [g.id, jogo]
        assert precos.vinculos("steam", slug="hades") == [(jogo, "10")]
        assert len(precos.vinculos("steam", limite=1)) == 1
    api.rotas["appdetails?appids=11&"] = http(500)
    api.rotas[STEAM] = steam(preco_steam(100, 100, 0))
    assert coletar(app, "steam") == (2, 1, 0, 0, 1)
    api.rotas["appdetails?appids=11&"] = steam(preco_steam(100, 100, 0), appid="11")
    api.rotas[STEAM] = http(429)
    with pytest.raises(precos.Interrompido) as exc:
        coletar(app, "steam")
    assert exc.value.parcial == (1, 1, 0, 0, 0)
    with app.app_context():
        assert PrecoLoja.query.count() == 2


def atualizar(app, *args):
    return app.test_cli_runner().invoke(args=["atualizar-precos", *args])


def test_prc_06_comando_resume_por_fonte(app, jogo, api, relogio):
    api.rotas[STEAM] = steam(preco_steam(5999, 9999, 40))
    api.rotas[GOG] = http(429)
    r = atualizar(app)
    assert r.exit_code == 0
    assert r.output == ("steam: jogos=1 novos=1 sem_mudanca=0 sem_preco=0 falhas=0\n"
                        "gog: jogos=0 novos=0 sem_mudanca=0 sem_preco=0 falhas=0 interrompido=rate-limit\n")
    r = atualizar(app, "--fonte", "steam", "--jogo", "hades", "--limite", "5")
    assert r.output == "jogos=1 novos=0 sem_mudanca=1 sem_preco=0 falhas=0\n"
    assert not any("epic" in u for u in api.urls())


@pytest.mark.parametrize("fonte", ["epic", "rawg", "arquivo", "xyz"])
def test_prc_06_fonte_desconhecida_e_erro_de_uso(app, fonte):
    r = atualizar(app, "--fonte", fonte)
    assert r.exit_code != 0 and "fonte desconhecida" in r.output


def gravar(app, game_id, *linhas_):
    """Linhas (fonte, preço, base, desconto, coletado_em[, verificado_em]) direto no banco."""
    with app.app_context():
        for fonte, p, b, d, coletado, *verificado in linhas_:
            db.session.add(PrecoLoja(game_id=game_id, fonte=fonte, moeda="BRL", preco_centavos=p, preco_base_centavos=b,
                                     desconto_pct=d, coletado_em=coletado, verificado_em=(verificado or [coletado])[0]))
        db.session.commit()


def pagina(client, url="/jogos/hades"):
    r = client.get(url)
    assert r.status_code == 200
    return r.get_data(as_text=True)


# ---------- PRC-08 ----------
@pytest.mark.parametrize("centavos, texto", [(123456, "R$ 1.234,56"), (5, "R$ 0,05"), (0, "Gratuito"),
                                             (100000000, "R$ 1.000.000,00"), (5999, "R$ 59,99")])
def test_prc_08_formato_reais(centavos, texto):
    assert precos.reais(centavos) == texto


def test_prc_08_preco_atual_com_desconto_e_gratuito(app, client, jogo, relogio):
    gravar(app, jogo, ("steam", 5999, 9999, 40, AGORA - timedelta(days=1)), ("gog", 0, 0, 0, AGORA))
    html = pagina(client)
    assert '<span class="preco"><strong>−40%</strong> <del>R$ 99,99</del> R$ 59,99</span>' in html
    assert '<span class="preco">Gratuito</span>' in html
    assert ">Epic Games Store</span></p>" in html  # loja sem preço: só o link (MRG-12)


def test_prc_08_preco_desatualizado(app, client, jogo, relogio):
    antigo = AGORA - timedelta(days=8)
    gravar(app, jogo, ("steam", 5999, 9999, 40, antigo), ("gog", 2999, 2999, 0, antigo, AGORA - timedelta(days=7)))
    html = pagina(client)
    assert "R$ 59,99, preço de 12/11/2026" in html and "−40%" not in html
    assert '<span class="preco">R$ 29,99</span>' in html  # 7 dias exatos ainda é atual


# ---------- PRC-09 ----------
def test_prc_09_menor_preco_com_empate(app, client, jogo, relogio):
    pagina_sem = pagina(client)
    assert "Menor preço" not in pagina_sem
    d = AGORA - timedelta(days=5)
    gravar(app, jogo, ("steam", 1999, 1999, 0, d), ("steam", 999, 1999, 50, AGORA), ("gog", 999, 999, 0, d),
           ("gog", 1500, 1500, 0, AGORA))
    with app.app_context():
        m = precos.menor_preco(jogo)
        assert (m.fonte, m.preco_centavos, m.coletado_em) == ("gog", 999, d)  # empate: a coleta mais antiga
    gravar(app, jogo, ("steam", 999, 999, 0, d))
    with app.app_context():
        assert precos.menor_preco(jogo).fonte == "steam"  # mesma data: Steam antes de GOG
    assert "Menor preço já visto: R$ 9,99 na Steam em 15/11/2026" in pagina(client)


# ---------- PRC-10 ----------
def test_prc_10_historico_com_grafico_e_tabela_paginada(app, client, jogo, relogio):
    gravar(app, jogo, *[("steam", 1000 + i, 5000, 0, AGORA - timedelta(days=30 - i)) for i in range(25)],
           ("gog", 2000, 2000, 0, AGORA - timedelta(days=2)))
    html = pagina(client, "/jogos/hades/precos")
    assert html.count("<svg") == 1 and 'class="serie-0"' in html and 'class="serie-1"' in html
    assert html.index("R$ 20,00") < html.index("R$ 10,24") < html.index("R$ 10,23")  # mais recente primeiro
    assert "Página 1 de 2" in html
    assert "R$ 10,00" in pagina(client, "/jogos/hades/precos?page=2")


def test_prc_10_sem_historico_removido_e_mesclado(app, client, jogo):
    assert "Ainda não temos preços deste jogo" in pagina(client, "/jogos/hades/precos")
    outro = make_game(app, "Hades Antigo")
    with app.app_context():
        g = Game.query.filter_by(slug=outro).one()
        g.mesclado_em_id, g.removed_at = jogo, AGORA
        db.session.commit()
    r = client.get("/jogos/hades-antigo/precos?page=2")
    assert r.status_code == 301 and r.headers["Location"].endswith("/jogos/hades/precos?page=2")
    with app.app_context():
        db.session.get(Game, jogo).removed_at = AGORA
        db.session.commit()
    assert client.get("/jogos/hades/precos").status_code == 404


def test_prc_10_grafico_repete_o_ultimo_preco_conhecido(app, jogo):
    gravar(app, jogo, ("steam", 1000, 1000, 0, AGORA - timedelta(days=2)), ("gog", 500, 500, 0, AGORA))
    with app.app_context():
        svg = precos.grafico(precos.historico(jogo).all())
    assert "Steam — 18/11: 10" in svg and "Steam — 20/11: 10" in svg and "GOG — 18/11: 5" in svg


# ---------- PRC-13 ----------
def test_prc_13_orcamento_de_consultas(app, client, jogo, relogio, monkeypatch):
    from sqlalchemy import event

    from app import jogo as pagina_do_jogo

    def contar():
        contadas = []

        def conta(*a):
            contadas.append(1)
        with app.app_context():
            event.listen(db.engine, "before_cursor_execute", conta)
            pagina(client)
            event.remove(db.engine, "before_cursor_execute", conta)
        return len(contadas)

    with monkeypatch.context() as m:  # a página como era antes desta spec
        m.setattr(pagina_do_jogo, "atuais", lambda _: {})
        m.setattr(pagina_do_jogo, "menor_preco", lambda _: None)
        antes = contar()
    gravar(app, jogo, ("steam", 999, 999, 0, AGORA))
    uma = contar()
    gravar(app, jogo, *[(f, 1000 + i, 5000, i % 50, AGORA - timedelta(hours=i)) for i in range(1, 40) for f in ("steam", "gog")])
    muitas = contar()
    assert uma == muitas <= antes + 2
