"""Política de menores (PMN-01..14): `pode(user, acao, alvo=None)` é o ponto único das regras (GLOBAL-13).

Cada ação da tabela `ACOES` diz em que faixas (IDA-03) ela é liberada e, quando precisa, olha o alvo. Sem data de
nascimento a faixa é a mais protetiva, e a conta à espera do responsável não faz nada social. Ação fora da tabela
levanta ValueError, para nunca liberar por omissão. A página `/privacidade/menores` é gerada da mesma tabela (PMN-13).
"""
from flask import Blueprint, render_template

from ..models import Follow, MembroClube, User, db
from ..notificacoes import registrar_filtro
from .idade import ADOLESCENTE, ADULTO, NOMES_DAS_FAIXAS, VINCULADO, aguarda_responsavel, faixa_etaria

bp = Blueprint("politica", __name__)
FAIXAS = (ADULTO, ADOLESCENTE, VINCULADO)
TODAS, SEM_VINCULADO, SO_ADULTO = set(FAIXAS), {ADULTO, ADOLESCENTE}, {ADULTO}
SEM_ENGAJAMENTO = ("sequencia", "oferta", "preco_alvo")  # PMN-07


def e_menor(user):
    return faixa_etaria(user) != ADULTO


def visibilidade_padrao(user):
    """Lista nova de menor nasce privada (PMN-02, LIS-04)."""
    return "privada" if e_menor(user) else "publica"


def segue_aprovado(seguidor, seguido):
    """`seguidor` segue `seguido` com o pedido aprovado (PMN-03)."""
    return Follow.query.filter_by(follower_id=seguidor.id, followed_id=seguido.id).filter(Follow.aprovado).first() is not None


class Regra:
    """Uma linha de `ACOES`: as faixas liberadas, se é social (PMN-01) e o ajuste pelo alvo.

    `ajuste(user, alvo, liberado)` devolve a decisão final; `nota` explica o ajuste na página de PMN-13.
    """

    def __init__(self, descricao, faixas, social=False, ajuste=None, nota=""):
        self.descricao, self.faixas, self.social, self.ajuste, self.nota = descricao, faixas, social, ajuste, nota

    def __call__(self, user, alvo=None):
        if user is None or (self.social and aguarda_responsavel(user)):
            return False
        liberado = faixa_etaria(user) in self.faixas
        return self.ajuste(user, alvo, liberado) if self.ajuste else liberado


def _seguir(user, alvo, liberado):
    return liberado and (alvo is None or not aguarda_responsavel(alvo))


def _comentar(user, review, liberado):
    """Avaliação de menor: só o próprio menor e quem o segue com o pedido aprovado (PMN-10).

    Extensão de RSP-07: os comentários desligados pelo responsável também negam aqui, para o menor e para todos.
    """
    if not liberado or review is None or not e_menor(review.user):
        return liberado
    return user.id == review.user_id or segue_aprovado(user, review.user)


def _torneio(user, torneio, liberado):
    """Menor só em torneio de clube do qual é membro ativo ou aberto a menores (PMN-11, TRN-01).

    O torneio vem da spec TRN, com `clube_id` e `aberto_a_menores`. Extensão de RSP-07/RSP-08: os controles do
    responsável e a inscrição `pendente_responsavel` de quem tem de 12 a 15 anos ficam com a spec TRN.
    """
    if liberado or torneio is None:
        return liberado
    if getattr(torneio, "aberto_a_menores", False):
        return True
    clube_id = getattr(torneio, "clube_id", None)
    return clube_id is not None and MembroClube.query.filter_by(
        clube_id=clube_id, user_id=user.id, situacao="ativo").first() is not None


# Extensão de RSP-07: entrar em clube, postar no mural e comentar respeitam também os controles do responsável.
ACOES = {
    "seguir": Regra("Seguir outras pessoas", TODAS, social=True, ajuste=_seguir,
                    nota="Seguir um menor vira um pedido, aprovado por ele (16 e 17 anos) ou pelo responsável."),
    "aprovar_seguidor": Regra("Aprovar quem pediu para seguir", SEM_VINCULADO,
                              nota="De 12 a 15 anos, quem aprova é o responsável."),
    "comentar": Regra("Comentar avaliações", TODAS, social=True, ajuste=_comentar,
                      nota="Na avaliação de um menor só comentam ele e quem ele aprovou como seguidor."),
    "perfil_publico": Regra("Tornar o próprio perfil público", SEM_VINCULADO,
                            nota="O perfil de um menor nasce privado; de 12 a 15 anos, só o responsável o abre."),
    "ranking_global": Regra("Aparecer no ranking global", SO_ADULTO,
                            nota="Nos rankings de quem segue e do clube, só para quem pode ver o perfil."),
    "conquista_por_tempo": Regra("Ganhar conquistas por tempo de jogo", SO_ADULTO),
    "aviso_engajamento": Regra("Receber aviso de sequência prestes a quebrar", SO_ADULTO,
                               nota="O widget de sequência também não aparece."),
    # PMN-08 vive em `emails.py` (EML), que aplica a regra por faixa e também consulta esta ação: aqui ela fica
    # liberada, senão quem tem 16 ou 17 anos nunca conseguiria ligar o resumo semanal
    "email_engajamento": Regra("Ligar e-mails de engajamento (resumo semanal)", TODAS,
                               nota="Desligados por padrão para menores; de 12 a 15 anos, só o responsável liga."),
    # Extensão de OFR (PMN-05): /ofertas e o POST de alerta respondem 403; o preço por loja continua visível
    "ver_ofertas": Regra("Ver ofertas", SO_ADULTO),
    "alerta_preco": Regra("Criar alerta de preço", SO_ADULTO),
    "historico_para_terceiros": Regra("Ter o histórico usado nas recomendações e comparações de outras pessoas",
                                      SO_ADULTO),
    "entrar_clube": Regra("Entrar em clubes", TODAS, social=True),
    "publicar_clube": Regra("Postar no mural do clube", TODAS, social=True),
    "torneio_inscrever": Regra("Se inscrever em torneios", SO_ADULTO, social=True, ajuste=_torneio,
                               nota="Menores, só em torneio do próprio clube ou aberto a menores."),
}


def pode(user, acao, alvo=None):
    """PMN-01: ação desconhecida é erro, nunca liberação."""
    if acao not in ACOES:
        raise ValueError(f"ação desconhecida na política de menores: {acao}")
    return bool(ACOES[acao](user, alvo))


@registrar_filtro
def _sem_engajamento_para_menor(user_id, tipo, ctx):
    """Sequência, oferta e preço-alvo não chegam a menores (PMN-07, NTF-03).

    Extensão de EML (PMN-07): o e-mail não transacional gerado entre 22h e 7h fica para as 7h; a notificação no
    site sai na hora.
    """
    return tipo not in SEM_ENGAJAMENTO or not e_menor(db.session.get(User, user_id))


# ---------- página (PMN-13) ----------
def linhas_da_pagina():
    """(descrição, {faixa: liberada}, nota) de cada ação, na ordem da tabela."""
    return [(r.descricao, {f: f in r.faixas for f in FAIXAS}, r.nota) for r in ACOES.values()]


@bp.route("/privacidade/menores")
def pagina():
    return render_template("privacidade/menores.html", linhas=linhas_da_pagina(), faixas=FAIXAS,
                           nomes=NOMES_DAS_FAIXAS)
