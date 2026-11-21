"""Fonte GOG: catálogo público e API de jogos, sem chave (GOG-01, GOG-02)."""
import json
from urllib.error import HTTPError

from . import base
from .normalizar import ler_data, texto_puro

NOME = "GOG"
CATALOGO = "https://catalog.gog.com/v1/catalog"
JOGO = "https://api.gog.com/v2/games/{}"
PRECOS = "https://api.gog.com/products/{}/prices"
POR_PAGINA = 48


def _filtro(termo):
    """Só jogos; sem countryCode, para não depender do país de quem pede (GOG-01)."""
    return {"productType": "in:game", **({"query": f"like:{termo}"} if termo else {"order": "desc:trending"})}


def _catalogo(termo, limite, pagina=1):
    dados = base.get_json(CATALOGO, {**_filtro(termo), "limit": limite, "page": pagina})
    if not isinstance(dados, dict) or not isinstance(dados.get("products"), list):
        raise ValueError("resposta inesperada do catálogo da GOG")
    return dados


def _ids(limite, termo):
    def pagina(n):
        dados = _catalogo(termo, min(limite, POR_PAGINA), n)
        return [str(p["id"]) for p in dados["products"]], n < (dados.get("pages") or 1)
    return base.paginar(pagina, limite)


def _href(links, nome):
    return (links.get(nome) or {}).get("href")


def detalhe(external_id):
    """Jogo completo pela API de jogos; id que não é número não vai à rede."""
    eid = str(external_id).strip()
    if not eid.isdigit():
        return None
    d = base.get_json(JOGO.format(eid))
    emb, links = d.get("_embedded") or {}, d.get("_links") or {}
    produto = emb.get("product") or {}
    if not produto.get("title"):
        return None
    data, aproximada = ler_data(produto.get("globalReleaseDate"))
    return {
        "external_id": eid,
        "title": produto["title"],
        "synopsis": texto_puro(d.get("description") or d.get("overview")),
        "release_date": data,
        "data_aproximada": aproximada,
        "developer": next((x["name"] for x in emb.get("developers") or [] if x.get("name")), ""),
        "genres": [t.get("name") for t in emb.get("tags") or []],
        "platforms": [(s.get("operatingSystem") or {}).get("name") for s in emb.get("supportedOperatingSystems") or []],
        "cover_url": _href(links, "boxArtImage"),
        "url_loja": _href(links, "store"),
    }


def buscar(termo, limite=10):
    """Candidatos para a tela do admin, direto do catálogo (sem um detalhe por item)."""
    return [{"external_id": str(p["id"]), "title": p["title"], "cover_url": p.get("coverVertical")}
            for p in _catalogo(termo, limite)["products"][:limite]]


def listar(limite, termo=None):
    return base.percorrer(_ids(limite, termo), detalhe)


def _centavos(texto):
    valor, moeda = str(texto).split()
    if moeda != "BRL":
        raise ValueError(f"moeda inesperada: {texto}")
    return int(valor)


def _sem_preco_no_brasil(exc):
    """4xx com `reason: PRICES_NOT_FOUND` no corpo: o produto não é vendido no Brasil."""
    try:
        return json.loads(exc.read() or b"{}").get("reason") == "PRICES_NOT_FOUND"
    except (AttributeError, TypeError, ValueError):
        return False


def preco(external_id):
    """(preço, preço base, desconto) em centavos de BRL; id não numérico não vai à rede (PRC-03)."""
    eid = str(external_id).strip()
    if not eid.isdigit():
        return None
    try:
        d = base.get_json(PRECOS.format(eid), {"countryCode": "BR"})
    except HTTPError as exc:
        if exc.code != 429 and _sem_preco_no_brasil(exc):
            return None
        raise
    if d.get("reason") == "PRICES_NOT_FOUND":
        return None
    brl = [p for p in d["_embedded"]["prices"] if p["currency"]["code"] == "BRL"]
    if not brl:
        return None
    final, inicial = _centavos(brl[0]["finalPrice"]), _centavos(brl[0]["basePrice"])
    desconto = (200 * (inicial - final) + inicial) // (2 * inicial) if inicial else 0  # arredonda meio para cima
    return final, inicial, desconto
