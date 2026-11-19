from urllib.error import HTTPError, URLError

import pytest

from app.fontes import base, steam
from conftest import Resp, http


@pytest.fixture
def rede(monkeypatch):
    """Respostas falsas em ordem; cada item é um corpo (JSON ou bytes) ou uma exceção a levantar."""
    pedidos, esperas, fila = [], [], []

    def urlopen(req, timeout):
        pedidos.append((req, timeout))
        r = fila.pop(0)
        if isinstance(r, Exception):
            raise r
        return Resp(r)

    monkeypatch.setattr(base, "urlopen", urlopen)
    monkeypatch.setattr(base, "esperar", esperas.append)
    monkeypatch.delenv("FONTES_TIMEOUT", raising=False)

    def responder(*itens):
        fila.extend(itens)
        return pedidos, esperas
    return responder


def test_fnt01_sucesso_de_primeira_com_timeout_padrao(rede):
    pedidos, esperas = rede({"ok": 1})
    assert base.get_json("https://x/api", {"q": "a b"}) == {"ok": 1}
    assert pedidos[0][0].full_url == "https://x/api?q=a+b" and pedidos[0][1] == 15 and esperas == []


def test_fnt01_429_seguido_de_sucesso(rede):
    pedidos, esperas = rede(http(429), {"ok": 1})
    assert base.get_json("https://x") == {"ok": 1}
    assert len(pedidos) == 2 and esperas == [1]


@pytest.mark.parametrize("erro", [http(500), http(503), URLError("sem rede"), TimeoutError("timed out")])
def test_fnt01_tres_tentativas_com_espera_exponencial_e_depois_erro(rede, erro):
    pedidos, esperas = rede(erro, erro, erro)
    with pytest.raises(OSError):
        base.get_json("https://x")
    assert len(pedidos) == 3 and esperas == [1, 2]


@pytest.mark.parametrize("code", [400, 403, 404])
def test_fnt01_4xx_nao_e_repetido(rede, code):
    pedidos, esperas = rede(http(code), {"ok": 1})
    with pytest.raises(HTTPError):
        base.get_json("https://x")
    assert len(pedidos) == 1 and esperas == []


@pytest.mark.parametrize("corpo", [b"{quebrado", b""])
def test_fnt01_payload_malformado_ou_vazio_levanta_sem_repetir(rede, corpo):
    pedidos, _ = rede(corpo, {"ok": 1})
    with pytest.raises(ValueError):
        base.get_json("https://x")
    assert len(pedidos) == 1


@pytest.mark.parametrize("valor, esperado", [("7", 7), ("2.5", 2.5), ("abc", 15), ("0", 15), ("-3", 15)])
def test_fnt01_timeout_lido_de_fontes_timeout(rede, monkeypatch, valor, esperado):
    monkeypatch.setenv("FONTES_TIMEOUT", valor)
    pedidos, _ = rede({})
    base.get_json("https://x")
    assert pedidos[0][1] == esperado


def test_fnt02_user_agent_do_gamelog_e_nunca_de_navegador(rede):
    pedidos, _ = rede({}, {})
    base.get_json("https://x")
    base.get_json("https://x", headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    for req, _ in pedidos:
        ua = req.get_header("User-agent")
        assert ua == f"GameLog/{base.VERSAO} (+https://github.com/amaralandrey/dsai-ap2-GameLog)"
        assert "Mozilla" not in ua
    assert pedidos[1][0].get_header("Accept") == "application/json"


def test_fnt03_steam_get_repete_depois_de_429_com_user_agent(rede):
    pedidos, esperas = rede(http(429), {"items": []})
    assert steam._get("storesearch/?term=x") == {"items": []}
    assert len(pedidos) == 2 and esperas == [1]
    assert pedidos[1][0].full_url == "https://store.steampowered.com/api/storesearch/?term=x"
    assert pedidos[1][0].get_header("User-agent") == base.USER_AGENT


def test_fnt03_capa_da_steam_usa_user_agent_sem_novas_tentativas(monkeypatch):
    pedidos = []

    def cai(req, timeout):
        pedidos.append(req)
        raise URLError("sem rede")
    monkeypatch.setattr(steam, "urlopen", cai)
    assert steam.capa(42, "https://x/h.jpg") == "https://x/h.jpg"
    assert len(pedidos) == 1 and pedidos[0].get_header("User-agent") == base.USER_AGENT


def test_fnt10_na_requisicao_web_uma_tentativa_com_timeout_de_5s(app, rede):
    pedidos, esperas = rede(http(503), {"ok": 1})
    with app.test_request_context("/admin/importar"):
        assert base.timeout() == 5
        with pytest.raises(HTTPError):
            base.get_json("https://x")
    assert len(pedidos) == 1 and pedidos[0][1] == 5 and esperas == []
