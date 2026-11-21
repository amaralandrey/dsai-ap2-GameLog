"""JSON das entidades, num lugar só (AP2-07, GLOBAL-14): datas em ISO-8601 e URLs absolutas, sem campos de conta.

Por enquanto só `jogo` e `lista`, que a exportação das listas usa (LSA-09); a API v2 acrescenta as demais.
"""
from flask import has_request_context, request

from .models import LOJAS_COM_PRECO
from .precos import atual, atuais, menor_preco


def absoluta(url):
    if url.startswith("/") and has_request_context():
        return request.url_root.rstrip("/") + url
    return url


def reais(centavos):
    return round(centavos / 100, 2)


def precos(game_id):
    """Preços atuais de cada loja e o menor preço já visto, em reais (PRC-12)."""
    vigentes, menor = atuais(game_id), menor_preco(game_id)
    return {
        "precos": [{"loja": p.fonte, "preco": reais(p.preco_centavos), "preco_base": reais(p.preco_base_centavos),
                    "desconto": p.desconto_pct, "verificado_em": p.verificado_em.isoformat()}
                   for p in (vigentes.get(f) for f in LOJAS_COM_PRECO) if p and atual(p)],
        "menor_preco": {"preco": reais(menor.preco_centavos), "loja": menor.fonte,
                        "data": menor.coletado_em.isoformat()} if menor else None,
    }


def jogo(game, com_precos=False):
    """`com_precos` acrescenta `precos` e `menor_preco` (detalhe do jogo, API-05)."""
    return {
        "slug": game.slug,
        "titulo": game.title,
        "lancamento": game.release_date.isoformat(),
        "desenvolvedora": game.developer.name,
        "generos": sorted(g.name for g in game.genres),
        "plataformas": sorted(p.name for p in game.platforms),
        "capa": absoluta(game.cover),
        "url": absoluta(f"/jogos/{game.slug}"),
        **(precos(game.id) if com_precos else {}),
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
