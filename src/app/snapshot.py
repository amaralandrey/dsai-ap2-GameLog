"""Enriquece o snapshot do seed com os links de GOG e Epic e o id da RAWG; roda local, com a chave (SNP-01..05)."""
import json

from .fontes import Interrompido, base, external_id_da_url, normalizar_url_loja, rawg

CANDIDATOS = 3  # resultados da busca da RAWG conferidos por jogo (SNP-01)
LOJAS_EXTRAS = ("gog", "epic")  # a Steam já vem do próprio snapshot (FNT-05)


def correspondencia(j, modulo=rawg):
    """(id da RAWG, lojas) do resultado cujo link da Steam tem o id do jogo, ou None; sem semelhança de título."""
    for r in modulo.buscar(j["title"], CANDIDATOS)[:CANDIDATOS]:
        lojas = modulo.lojas(r["external_id"])
        if any(loja == "steam" and external_id_da_url("steam", url) == j["external_id"] for loja, url in lojas):
            links = ((loja, normalizar_url_loja(loja, url)) for loja, url in lojas if loja in LOJAS_EXTRAS)
            return r["external_id"], [[loja, url] for loja, url in links if url]  # só links que passam por FNT-07
    return None


def _gravar(caminho, jogos):
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(jogos, f, ensure_ascii=False, indent=1)  # mesmo formato de export-games


def enriquecer(caminho, modulo=rawg):
    """Devolve (jogos, vinculados, sem_correspondencia, falhas) e grava o arquivo.

    Falha num jogo deixa o jogo como estava; 429 persistente grava o que já foi feito e levanta
    Interrompido com as contagens (SNP-05).
    """
    with open(caminho, encoding="utf-8") as f:
        jogos = json.load(f)
    vinculados = sem = falhas = 0
    for j in jogos:
        try:
            achado = correspondencia(j, modulo)
        except Exception as exc:
            if base.limitado(exc):
                _gravar(caminho, jogos)
                raise Interrompido("rate-limit", (len(jogos), vinculados, sem, falhas)) from exc
            falhas += 1
            continue
        if achado is None:
            sem += 1
            continue
        j["lojas"], j["origens"] = achado[1], [["rawg", achado[0]]]
        vinculados += 1
    _gravar(caminho, jogos)
    return len(jogos), vinculados, sem, falhas
