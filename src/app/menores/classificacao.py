"""Classificação indicativa (CLS-01..10): faixas da ClassInd e a equivalência do GameLog a partir da Steam e da RAWG.

Não existe tabela oficial de `required_age` nem de ESRB para ClassInd: as duas tabelas abaixo são uma equivalência da
dupla, que arredonda sempre para a faixa mais restritiva. O filtro por idade entra em `visible_games()` (PMN).
"""
from flask import g, has_request_context
from sqlalchemy import or_

FAIXAS = ("L", "6", "10", "12", "14", "16", "18")
IDADES = {"L": 0, "6": 6, "10": 10, "12": 12, "14": 14, "16": 16, "18": 18}
ORIGENS = ("steam", "rawg", "admin")
ORIGEM_DA_FONTE = {"steam": "steam", "arquivo": "steam", "rawg": "rawg"}
ESRB = {"everyone": "L", "everyone-10-plus": "10", "teen": "14", "mature": "18", "adults-only": "18"}
IDADE_SEM_DATA = 12
NOTA = "Equivalência do GameLog a partir da Steam/RAWG; não é a classificação oficial."


def de_steam(required_age):
    """`required_age` positivo vira a menor faixa maior ou igual a ele (CLS-02); 0, ausente ou inválido: None."""
    try:
        anos = int(str(required_age).strip())
    except (TypeError, ValueError):
        return None
    if anos <= 0:
        return None
    return next((f for f in FAIXAS if IDADES[f] >= anos), "18")


def de_rawg(esrb):
    """`esrb_rating.slug` pela tabela ESRB (CLS-03); `rating-pending` ou ausente: None."""
    slug = esrb.get("slug") if isinstance(esrb, dict) else esrb
    return ESRB.get(slug)


def mais_restritiva(*faixas):
    validas = [f for f in faixas if f in IDADES]
    return max(validas, key=IDADES.get, default=None)


def aplicar(game, faixa, fonte):
    """Grava a faixa de uma fonte se ela for mais restritiva; a do admin nunca muda por sincronização (CLS-04)."""
    origem = ORIGEM_DA_FONTE.get(fonte, fonte if fonte in ORIGENS else None)
    if faixa not in IDADES or origem is None or game.classificacao_origem == "admin":
        return False
    if mais_restritiva(game.classificacao, faixa) == game.classificacao:
        return False
    game.classificacao, game.classificacao_origem = faixa, origem
    return True


# ---------- quem vê o quê (CLS-07..09) ----------
def idade_limite(user):
    """Anos que valem para o filtro, ou None quando não há filtro (visitante ou adulto)."""
    from .idade import ADULTO, faixa_etaria, idade

    if user is None or faixa_etaria(user) == ADULTO:
        return None
    if user.nascimento is None or user.idade_contestada_em is not None:
        return IDADE_SEM_DATA
    return idade(user.nascimento)


def permitidas(anos):
    return [f for f in FAIXAS if IDADES[f] <= anos]


def condicao(anos):
    from ..models import Game

    return or_(Game.classificacao.is_(None), Game.classificacao.in_(permitidas(anos)))


def usuario_da_requisicao():
    if not has_request_context():
        return None
    from ..util import current_user

    token = g.get("token")
    return current_user() or (token.user if token is not None else None)


def filtro_da_requisicao():
    """Condições SQL para `visible_games()`: vazio fora de requisição, para visitante e para adulto."""
    anos = idade_limite(usuario_da_requisicao())
    return [] if anos is None else [condicao(anos)]


def pode_ver(user, game):
    anos = idade_limite(user)
    return anos is None or game.classificacao is None or IDADES[game.classificacao] <= anos


def faixa_maxima(texto, user):
    """`classificacao_max` válido, nunca acima da idade de um menor; inválido vira None (BUS-08)."""
    faixa = texto if texto in IDADES else None
    anos = idade_limite(user)
    if anos is not None:
        teto = permitidas(anos)[-1]
        faixa = teto if faixa is None or IDADES[faixa] > IDADES[teto] else faixa
    return faixa
