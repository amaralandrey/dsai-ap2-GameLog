"""JSON das entidades, num lugar só (AP2-07, GLOBAL-14): datas em ISO-8601 e URLs absolutas, sem campos de conta.

Por enquanto só `jogo` e `lista`, que a exportação das listas usa (LSA-09); a API v2 acrescenta as demais.
"""
import os

from flask import has_request_context, request

from .models import LOJAS_COM_PRECO
from .precos import atual, atuais, menor_preco


def absoluta(url):
    if not url.startswith("/"):
        return url
    if has_request_context():
        return request.url_root.rstrip("/") + url
    return (os.environ.get("URL_PUBLICA") or "http://localhost:5000").rstrip("/") + url


def _iso(valor):
    return valor.isoformat() if valor is not None else None


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


def fontes(game):
    """Vínculos do jogo, só fonte, id externo e link, ordenados por fonte (MRG-13, API-07)."""
    return [{"fonte": v.fonte, "external_id": v.external_id, "url": v.url_loja}
            for v in sorted(game.fontes, key=lambda v: (v.fonte, v.id))]


def usuario_publico(user):
    return {
        "username": user.username,
        "nome": user.display_name,
        "bio": user.bio,
        "perfil_privado": user.perfil_privado,
        "membro_desde": user.created_at.date().isoformat(),
        "url": absoluta(f"/u/{user.username}"),
    }


def avaliacao(review, curtidas=0):
    return {
        "id": review.id,
        "jogo": review.game.slug,
        "usuario": review.user.username,
        "nota": review.score,
        "texto": review.text,
        "spoiler": review.spoiler,
        "editada": review.edited,
        "data": review.created_at.isoformat(),
        "curtidas": curtidas,
    }


def sessao(s):
    return {
        "id": s.id,
        "jogo": s.game.slug,
        "titulo": s.game.title,
        "jogada_em": s.jogada_em.isoformat(),
        "duracao_min": s.duracao_min,
        "inicio": s.inicio.strftime("%H:%M") if s.inicio else None,
        "plataforma": s.plataforma.name if s.plataforma else None,
        "zerou": s.zerou,
        "spoiler": s.spoiler,
        "anotacao": s.anotacao,
        "origem": s.origem,
    }


def conquista(uc):
    c, revelada = uc.conquista, not uc.conquista.secreta or uc.desbloqueada_em is not None
    return {
        "codigo": c.codigo,
        "nome": c.nome if revelada else "Conquista secreta",
        "descricao": c.descricao if revelada else None,
        "categoria": c.categoria,
        "raridade": c.raridade,
        "icone": c.icone,
        "progresso": uc.progresso,
        "meta": c.meta,
        "desbloqueada_em": _iso(uc.desbloqueada_em),
    }


def status(st):
    return {"jogo": jogo(st.game), "status": st.value, "atualizado_em": _iso(st.updated_at),
            "zerado_em": _iso(st.zerado_em)}
