"""Enriquece o snapshot do seed com os links de GOG e Epic e o id da RAWG; roda local, com a chave (SNP-01..05).

Com `--precos`, grava o preço atual de cada loja, sem chave (PRC-11).
"""
import json
import os

from .fontes import Interrompido, base, external_id_da_url, normalizar_url_loja, rawg

CANDIDATOS = 3  # resultados da busca da RAWG conferidos por jogo (SNP-01)
LOJAS_EXTRAS = ("gog", "epic")  # a Steam já vem do próprio snapshot (FNT-05)


def correspondencia(j, modulo=rawg):
    """(id da RAWG, lojas) do resultado cujo link da Steam tem o id do jogo, ou None; sem semelhança de título."""
    for r in modulo.buscar(j["title"], CANDIDATOS):
        lojas = modulo.lojas(r["external_id"])
        if any(loja == "steam" and external_id_da_url("steam", url) == j["external_id"] for loja, url in lojas):
            links = ((loja, normalizar_url_loja(loja, url)) for loja, url in lojas if loja in LOJAS_EXTRAS)
            return r["external_id"], [[loja, url] for loja, url in links if url]  # só links que passam por FNT-07
    return None


def _gravar(caminho, jogos):
    """Grava num temporário ao lado e troca de uma vez: uma falha no meio não corrompe o snapshot."""
    temporario = f"{caminho}.tmp"
    try:
        with open(temporario, "w", encoding="utf-8") as f:
            json.dump(jogos, f, ensure_ascii=False, indent=1)  # mesmo formato de export-games
        os.replace(temporario, caminho)
    finally:
        if os.path.exists(temporario):
            os.remove(temporario)


def _percorrer(caminho, tratar):
    """Aplica `tratar` a cada jogo e grava; devolve (jogos, sim, não, falhas) pelo que `tratar` responde.

    Falha num jogo deixa o jogo como estava; 429 persistente grava o que já foi feito e levanta
    Interrompido com as contagens (SNP-05).
    """
    with open(caminho, encoding="utf-8") as f:
        jogos = json.load(f)
    sim = nao = falhas = 0
    for j in jogos:
        try:
            achou = tratar(j)
        except Exception as exc:
            if base.limitado(exc):
                _gravar(caminho, jogos)
                raise Interrompido("rate-limit", (len(jogos), sim, nao, falhas)) from exc
            falhas += 1
            continue
        sim, nao = sim + bool(achou), nao + (not achou)
    _gravar(caminho, jogos)
    return len(jogos), sim, nao, falhas


def enriquecer(caminho, modulo=rawg):
    """Devolve (jogos, vinculados, sem_correspondencia, falhas) e grava o arquivo."""
    def tratar(j):
        achado = correspondencia(j, modulo)
        if achado is not None:
            j["lojas"], j["origens"] = achado[1], [["rawg", achado[0]]]
        return achado is not None
    return _percorrer(caminho, tratar)


def lojas_com_preco(j):
    """(loja, id) de cada loja com preço do item: a Steam de origem e a GOG de `origens` ou do link (PRC-11)."""
    pares = [("steam", j["external_id"]), *((f, e) for f, e in j.get("origens") or [] if f == "gog"),
             *((loja, external_id_da_url(loja, url)) for loja, url in j.get("lojas") or [] if loja == "gog")]
    primeiros = {}
    for loja, eid in pares:
        if eid:
            primeiros.setdefault(loja, str(eid))
    return list(primeiros.items())


def enriquecer_precos(caminho, coletores=None):
    """Grava `precos` com o preço atual de cada loja; devolve (jogos, com_preco, sem_preco, falhas) (PRC-11)."""
    from .precos import COLETORES
    from .tempo import agora_utc

    coletores = coletores or COLETORES

    def tratar(j):
        precos = []
        for loja, eid in lojas_com_preco(j):
            valores = coletores[loja](eid)
            if valores is not None:
                precos.append([loja, *valores, agora_utc().isoformat(timespec="seconds")])
        j["precos"] = precos  # só depois de todas as lojas: uma falha no meio deixa o jogo como estava
        return precos
    return _percorrer(caminho, tratar)
