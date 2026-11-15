"""Deduplicação do catálogo multi-loja: semelhança, reconhecimento na importação, pendências e mesclagem (MRG-01..14)."""
import re
from collections import namedtuple
from difflib import SequenceMatcher
from itertools import combinations

from sqlalchemy import text

from .fontes import FONTES, external_id_da_url
from .models import (
    LOJAS, Activity, Dismissed, Game, JogoFonte, ListItem, PendenciaMerge, Review, Status, db, now, visible_games,
)
from .util import fold, slugify

SUFIXOS_DE_EDICAO = (  # lista fixa, já normalizada; a mais longa primeiro (MRG-01)
    "game of the year edition", "game of the year", "goty edition", "goty", "definitive edition",
    "directors cut", "director s cut", "complete edition", "enhanced edition", "deluxe edition",
    "ultimate edition", "gold edition", "anniversary edition", "special edition",
)
_SUFIXOS = re.compile(r"\b(?:" + "|".join(re.escape(s) for s in SUFIXOS_DE_EDICAO) + r")\b")
DESENVOLVEDORA_DESCONHECIDA = slugify("Desenvolvedora desconhecida")  # não conta como "mesma desenvolvedora"
PESO_TITULO, PESO_ANO, PESO_ANO_VIZINHO, PESO_DEV = 70, 20, 10, 10
MESMO_JOGO, PARECIDO, ANOS_MAX = 90, 70, 2  # MRG-02, MRG-03

Perfil = namedtuple("Perfil", "titulo ano dev")


def normalizar_titulo(titulo):
    """Minúsculas, sem acentos, símbolos nem sufixos de edição (MRG-01)."""
    s = re.sub(r"[™®©]", "", titulo or "")  # NFKD faria de ™ um "tm"
    s = re.sub(r"[^a-z0-9]+", " ", fold(s))
    return " ".join(_SUFIXOS.sub(" ", s).split())


def _dev(nome):
    s = slugify(nome or "")
    return "" if s == DESENVOLVEDORA_DESCONHECIDA else s


def perfil_jogo(game):
    return Perfil(normalizar_titulo(game.title), game.release_date.year, _dev(game.developer.name if game.developer else ""))


def perfil_item(j, titulo, data):
    return Perfil(normalizar_titulo(titulo), data.year, _dev(j.get("developer")))


def _pontos_extra(a, b):
    anos = abs(a.ano - b.ano)
    extra = PESO_ANO if anos == 0 else PESO_ANO_VIZINHO if anos == 1 else 0
    return extra + (PESO_DEV if a.dev and a.dev == b.dev else 0)


def semelhanca(a, b):
    """Número de 0 a 100: título até 70, mesmo ano 20 (vizinho 10), mesma desenvolvedora 10; determinístico (MRG-01)."""
    return round(PESO_TITULO * SequenceMatcher(None, a.titulo, b.titulo).ratio()) + _pontos_extra(a, b)


def _pode_chegar(a, b, minimo):
    """Corte barato antes do cálculo completo: quick_ratio é um teto para ratio."""
    teto = round(PESO_TITULO * SequenceMatcher(None, a.titulo, b.titulo).quick_ratio()) + _pontos_extra(a, b)
    return teto >= minimo


def mais_parecido(perfil, jogos):
    """(semelhança, jogo) do jogo mais parecido com semelhança ≥ 70, ou None; empate fica com o menor id."""
    melhor = None
    for game in jogos:
        outro = perfil_jogo(game)
        if not _pode_chegar(perfil, outro, PARECIDO):
            continue
        nota = semelhanca(perfil, outro)
        if nota >= PARECIDO and (melhor is None or (-nota, game.id) < (-melhor[0], melhor[1].id)):
            melhor = (nota, game)
    return melhor


# ---------- reconhecimento na importação e na sincronização (MRG-02, MRG-03, MRG-14) ----------
Decisao = namedtuple("Decisao", "acao jogo semelhanca")  # acao: vincular | repetido | pendencia | criar


def _fontes_do_item(fonte):
    """A fonte e a loja que ela copia (arquivo ← Steam): os vínculos que o item vai criar (FNT-05)."""
    loja = getattr(FONTES.get(fonte), "LOJA_DE_ORIGEM", None)
    return {fonte, loja} - {None}


def provisorio(v, fonte):
    """Vínculo GOG que a RAWG criou com o slug da URL; a fonte GOG troca pelo id numérico (MRG-14)."""
    return v.fonte == fonte == "gog" and not v.external_id.isdigit()


def ocupado(game, fonte, external_id):
    """O jogo já tem outro vínculo das fontes do item, que não pode ser trocado (FNT-04)."""
    return any(v.fonte in _fontes_do_item(fonte) and v.external_id != str(external_id) and not provisorio(v, fonte)
               for v in game.fontes)


def _links_do_item(fonte, j):
    """(loja, id) dos links de loja do item: os da RAWG e o da própria fonte, se ela é uma loja (GOG-06)."""
    links = list(j.get("lojas") or [])
    if fonte in LOJAS and j.get("url_loja"):
        links.append((fonte, j["url_loja"]))
    for loja, url in links:
        eid = external_id_da_url(loja, url)
        if eid:
            yield loja, eid


def jogo_por_link(fonte, j):
    """Jogo (já seguindo a mesclagem) a que pertence um link de loja do item, ou None (MRG-02)."""
    for loja, eid in _links_do_item(fonte, j):
        v = JogoFonte.query.filter_by(fonte=loja, external_id=eid).first()
        if v:
            return v.game.principal
    return None


def reconhecer(fonte, j, titulo, data):
    """O que fazer com um item novo da fonte: ligar a um jogo, criar com pendência ou só criar (MRG-02, MRG-03)."""
    external_id = str(j.get("external_id"))
    game = jogo_por_link(fonte, j)
    if game is not None:
        return Decisao("repetido" if ocupado(game, fonte, external_id) else "vincular", game, None)
    achado = mais_parecido(perfil_item(j, titulo, data), visible_games().filter(Game.mesclado_em_id.is_(None)))
    if achado is None:
        return Decisao("criar", None, None)
    nota, game = achado
    perto = abs(game.release_date.year - data.year) <= ANOS_MAX
    if nota >= MESMO_JOGO and perto and not ocupado(game, fonte, external_id):
        return Decisao("vincular", game, nota)
    return Decisao("pendencia", game, nota)


def assumir_vinculo(game, fonte, external_id):
    """O vínculo provisório da GOG passa a usar o id numérico, sem criar um segundo vínculo (MRG-14)."""
    for v in game.fontes:
        if provisorio(v, fonte) and v.external_id != str(external_id):
            v.external_id, v.url_loja = str(external_id)[:80], None  # a URL da GOG entra em vincular_origem


def registrar_pendencia(g1, g2, nota):
    """Uma pendência por par, em qualquer ordem e qualquer status; devolve a nova ou None (MRG-03, MRG-10)."""
    a, b = sorted((g1.id, g2.id))
    if a == b or PendenciaMerge.query.filter_by(jogo_a_id=a, jogo_b_id=b).first():
        return None
    p = PendenciaMerge(jogo_a_id=a, jogo_b_id=b, semelhanca=nota)
    db.session.add(p)
    return p


def sugerir_duplicatas():
    """Compara os jogos do catálogo dois a dois e registra pendências, sem mesclar nada (MRG-04)."""
    jogos = visible_games().filter(Game.mesclado_em_id.is_(None)).order_by(Game.id).all()
    perfis = {g.id: perfil_jogo(g) for g in jogos}
    novas = 0
    for g1, g2 in combinations(jogos, 2):
        p1, p2 = perfis[g1.id], perfis[g2.id]
        if not _pode_chegar(p1, p2, PARECIDO):
            continue
        nota = semelhanca(p1, p2)
        if nota >= PARECIDO and registrar_pendencia(g1, g2, nota):
            novas += 1
    db.session.commit()
    return novas


def principal_do_mesclado(slug):
    """Jogo visível que responde pelo slug de um jogo mesclado, ou None; as URLs antigas viram 301 (MRG-08)."""
    game = Game.query.filter(Game.slug == slug, Game.mesclado_em_id.isnot(None)).first()
    principal = game.principal if game else None
    return principal if principal is not None and principal.removed_at is None else None


# ---------- mesclagem (MRG-06..09) ----------
class MesclagemRecusada(ValueError):
    """A pendência não pode ser mesclada; a mensagem vai para o admin."""


def _trocar(vencedor, perdedor, principal, secundario):
    """O registro mais recente vai para o principal e o outro para o secundário (MRG-07).

    A unicidade (usuário, jogo) é conferida linha a linha no SQLite, então a troca passa por um id de jogo
    provisório; a chave estrangeira é adiada até o commit da transação.
    """
    db.session.execute(text("PRAGMA defer_foreign_keys = ON"))
    perdedor.game_id = -perdedor.id
    db.session.flush()
    vencedor.game_id = principal.id
    db.session.flush()
    perdedor.game_id = secundario.id
    db.session.flush()


def _levar_por_usuario(modelo, principal, secundario, quando):
    """Leva os registros do secundário; com o mesmo usuário nos dois, fica no principal o mais recente."""
    do_principal = {r.user_id: r for r in modelo.query.filter_by(game_id=principal.id)}
    for r in modelo.query.filter_by(game_id=secundario.id).all():
        atual = do_principal.get(r.user_id)
        if atual is None:
            r.game_id = principal.id
        elif quando(r) > quando(atual):
            _trocar(r, atual, principal, secundario)
    db.session.flush()


def _levar_itens_de_lista(principal, secundario):
    """Item de uma lista que já tem o principal fica no secundário (LIS-06)."""
    listas = {i.list_id for i in ListItem.query.filter_by(game_id=principal.id)}
    for item in ListItem.query.filter_by(game_id=secundario.id):
        if item.list_id not in listas:
            item.game_id = principal.id


def _levar_dispensas(principal, secundario):
    """Quem dispensou os dois fica com a dispensa do principal; a do secundário fica onde está (REC-07)."""
    usuarios = {d.user_id for d in Dismissed.query.filter_by(game_id=principal.id)}
    for d in Dismissed.query.filter_by(game_id=secundario.id):
        if d.user_id not in usuarios:
            d.game_id = principal.id


def _levar_atividades(principal, secundario):
    """Atividade de avaliação acompanha a avaliação; as demais vão para o principal."""
    for a in Activity.query.filter_by(game_id=secundario.id):
        a.game_id = a.review.game_id if a.review_id else principal.id


def _levar_vinculos(principal, secundario):
    """Vínculo de uma loja que o principal já tem fica no secundário (FNT-04)."""
    tem = {v.fonte for v in principal.fontes}
    for v in list(secundario.fontes):
        if v.fonte not in tem:
            v.game = principal


def mesclar(pendencia, principal_id, admin_id):
    """Mescla a pendência levando o histórico do secundário para o principal; devolve (principal, secundario)."""
    if pendencia.status != "pendente":
        raise MesclagemRecusada("Esta pendência já foi decidida.")
    par = {pendencia.jogo_a_id: pendencia.jogo_a, pendencia.jogo_b_id: pendencia.jogo_b}
    if principal_id not in par:
        raise MesclagemRecusada("Escolha um dos dois jogos da pendência como principal.")
    principal = par.pop(principal_id)
    secundario = par.popitem()[1]
    if principal.mesclado_em_id or secundario.mesclado_em_id:
        raise MesclagemRecusada("Um dos jogos já foi mesclado em outro; ignore esta pendência.")

    secundario.removed_at = secundario.removed_at or now()  # soft delete (CAT-07)
    secundario.mesclado_em_id = principal.id
    db.session.flush()  # abre a transação antes do PRAGMA de _trocar, que só vale dentro dela
    _levar_vinculos(principal, secundario)
    _levar_por_usuario(Review, principal, secundario, lambda r: (r.created_at, r.id))
    _levar_por_usuario(Status, principal, secundario, lambda s: (s.updated_at, s.id))
    _levar_itens_de_lista(principal, secundario)
    _levar_dispensas(principal, secundario)
    _levar_atividades(principal, secundario)
    pendencia.status, pendencia.decidida_em, pendencia.decidida_por = "mesclada", now(), admin_id
    return principal, secundario


def ignorar(pendencia, admin_id):
    """O par nunca volta a ser sugerido, porque a pendência continua registrada (MRG-10)."""
    if pendencia.status != "pendente":
        raise MesclagemRecusada("Esta pendência já foi decidida.")
    pendencia.status, pendencia.decidida_em, pendencia.decidida_por = "ignorada", now(), admin_id
