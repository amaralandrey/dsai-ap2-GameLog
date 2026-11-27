"""Fonte RAWG: metadados multiplataforma e links de loja; exige RAWG_API_KEY, lida do ambiente (GOG-03..07)."""
import os

from ..menores.classificacao import de_rawg
from . import base
from .normalizar import ler_data, texto_puro

NOME = "RAWG"
API = "https://api.rawg.io/api"
CHAVE = "RAWG_API_KEY"
SEM_CHAVE = f"{CHAVE} não configurada"
LOJAS = {1: "steam", 5: "gog", 11: "epic"}  # store_id da RAWG -> loja do GameLog; as demais são ignoradas (GOG-06)
POR_PAGINA = 40


def _chave():
    return os.environ.get(CHAVE, "").strip()


def indisponivel():
    """Sem chave a RAWG fica desligada, sem erro (GOG-04, GOG-07)."""
    return None if _chave() else SEM_CHAVE


def _get(caminho, **params):
    """GET na API com a chave; a chave nunca sai numa mensagem de erro (GOG-05)."""
    chave = _chave()
    if not chave:
        raise RuntimeError(SEM_CHAVE)
    try:
        return base.get_json(f"{API}/{caminho}", {**params, "key": chave})
    except Exception as exc:
        if chave in str(exc):
            raise RuntimeError(str(exc).replace(chave, "***")) from None
        if isinstance(exc, OSError) and exc.filename:
            exc.filename = str(exc.filename).replace(chave, "***")  # HTTPError guarda a URL pedida
        if isinstance(getattr(exc, "url", None), str):
            exc.url = exc.url.replace(chave, "***")  # e de novo em `url`/`geturl()`
        raise


def _resultados(dados):
    if not isinstance(dados, dict) or not isinstance(dados.get("results"), list):
        raise ValueError("resposta inesperada da RAWG")
    return dados["results"]


def _busca(termo, limite, pagina=1):
    filtro = {"search": termo} if termo else {"ordering": "-added"}
    dados = _get("games", page=pagina, page_size=min(limite, POR_PAGINA), **filtro)
    return _resultados(dados), bool(dados.get("next"))


def _ids(limite, termo):
    def pagina(n):
        resultados, mais = _busca(termo, limite, n)
        return [str(r["id"]) for r in resultados], mais
    return base.paginar(pagina, limite)


def lojas(external_id):
    """(loja, url) de Steam, GOG e Epic pelo endpoint de lojas do jogo (GOG-06)."""
    return [(LOJAS[r["store_id"]], r.get("url")) for r in _resultados(_get(f"games/{external_id}/stores"))
            if r.get("store_id") in LOJAS]


def detalhe(external_id):
    eid = str(external_id).strip()
    if not eid.isdigit():
        return None
    d = _get(f"games/{eid}")
    if not d.get("name"):
        return None
    data, aproximada = ler_data(d.get("released"))
    return {
        "external_id": eid,
        "title": d["name"],
        "synopsis": (d.get("description_raw") or "").strip() or texto_puro(d.get("description")),
        "release_date": data,
        "data_aproximada": aproximada,
        "developer": next((x["name"] for x in d.get("developers") or [] if x.get("name")), ""),
        "genres": [g.get("name") for g in d.get("genres") or []],
        "platforms": [(p.get("platform") or {}).get("name") for p in d.get("platforms") or []],
        "cover_url": d.get("background_image"),
        "lojas": lojas(eid),
        "classificacao": de_rawg(d.get("esrb_rating")),
    }


def buscar(termo, limite=10):
    return [{"external_id": str(r["id"]), "title": r["name"], "cover_url": r.get("background_image"),
             "classificacao": de_rawg(r.get("esrb_rating"))} for r in _busca(termo, limite)[0][:limite]]


def listar(limite, termo=None):
    return base.percorrer(_ids(limite, termo), detalhe)
