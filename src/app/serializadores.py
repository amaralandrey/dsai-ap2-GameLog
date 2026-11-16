"""JSON das entidades, num lugar só (AP2-07, GLOBAL-14): datas em ISO-8601 e URLs absolutas, sem campos de conta.

Por enquanto só `jogo` e `lista`, que a exportação das listas usa (LSA-09); a API v2 acrescenta as demais.
"""
from flask import has_request_context, request


def absoluta(url):
    if url.startswith("/") and has_request_context():
        return request.url_root.rstrip("/") + url
    return url


def jogo(game):
    return {
        "slug": game.slug,
        "titulo": game.title,
        "lancamento": game.release_date.isoformat(),
        "desenvolvedora": game.developer.name,
        "generos": sorted(g.name for g in game.genres),
        "plataformas": sorted(p.name for p in game.platforms),
        "capa": absoluta(game.cover),
        "url": absoluta(f"/jogos/{game.slug}"),
    }


def lista(nome, url, publica, itens, automatica=False, regra=None):
    """Uma lista e seus itens; cada item é (posição, jogo, status do dono ou None, minutos registrados)."""
    return {
        "nome": nome,
        "url": absoluta(url),
        "publica": publica,
        "automatica": automatica,
        **({"regra": regra} if regra else {}),
        "itens": [
            {"posicao": pos, "jogo": jogo(game), "status": status, "horas": round((minutos or 0) / 60, 1)}
            for pos, game, status, minutos in itens
        ],
    }
