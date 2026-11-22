"""Motor de conquistas orientado a eventos (GAM-01..15).

`emitir_evento(user_id, tipo, **ctx)` é o ponto único de reação às ações do usuário (GLOBAL-14). Ele avisa
os ouvintes registrados (o XP de PRG e as metas de clube) e avalia só as conquistas ativas cujas métricas
escutam aquele tipo. Uma regra é a dupla (`metrica`, `meta`); as métricas ficam no catálogo fixo `METRICAS`.
"""
import logging
from collections import namedtuple
from datetime import date

from sqlalchemy import func, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import aliased

from .menores.politica import pode
from .models import (
    LOJAS, Activity, Comment, Conquista, Follow, Game, GameList, JogoFonte, Like, ListItem, Review, SessaoJogo,
    Status, User, UsuarioConquista, db, game_genres, game_platforms, now,
)
from .tempo import hoje

log = logging.getLogger(__name__)
CATEGORIAS = {"critica": "Crítica", "colecao": "Coleção", "explorador": "Explorador", "diario": "Diário",
              "social": "Social", "comunidade": "Comunidade"}
RARIDADES = {"comum": "Comum", "rara": "Rara", "epica": "Épica", "lendaria": "Lendária"}
RECALCULO_SEM_AVISO_DIAS = 7

Metrica = namedtuple("Metrica", "nome eventos calcular datas", defaults=(None,))
_ouvintes = []


def ouvir(funcao):
    """Registra `funcao(user_id, tipo, ctx)`, chamada antes das conquistas a cada evento (PRG, CLB)."""
    if funcao not in _ouvintes:
        _ouvintes.append(funcao)
    return funcao


# ---------- métricas (GAM-03, GAM-07: só dados visíveis) ----------
def _avaliacoes(user_id):
    return (Review.query.join(Game, Game.id == Review.game_id)
            .filter(Review.user_id == user_id, Review.hidden.is_(False), Game.removed_at.is_(None)))


def _contar(q):
    return q.with_entities(func.count()).scalar() or 0


def avaliacoes_visiveis(user_id):
    return _contar(_avaliacoes(user_id))


def datas_avaliacoes(user_id):
    return [r.created_at for r in _avaliacoes(user_id).order_by(Review.created_at)]


def resenhas_longas(user_id):
    return _contar(_avaliacoes(user_id).filter(func.length(Review.text) >= 140))


def notas_dez(user_id):
    return _contar(_avaliacoes(user_id).filter(Review.score == 10))


def primeira_avaliacao(user_id):
    """Avaliações que foram as primeiras do jogo (o "Pioneiro" da AP1, CON-01)."""
    anterior = aliased(Review)
    houve = (db.session.query(anterior.id)
             .filter(anterior.game_id == Review.game_id,
                     (anterior.created_at < Review.created_at)
                     | ((anterior.created_at == Review.created_at) & (anterior.id < Review.id)))
             .exists())
    return _contar(_avaliacoes(user_id).filter(~houve))


def curtidas_recebidas(user_id):
    return _contar(Like.query.join(Review, Review.id == Like.review_id)
                   .join(Game, Game.id == Review.game_id)
                   .filter(Review.user_id == user_id, Review.hidden.is_(False), Game.removed_at.is_(None)))


def jogos_em_listas(user_id):
    return (db.session.query(func.count(func.distinct(ListItem.game_id)))
            .join(GameList, ListItem.list_id == GameList.id).join(Game, ListItem.game_id == Game.id)
            .filter(GameList.user_id == user_id, Game.removed_at.is_(None)).scalar() or 0)


def listas_criadas(user_id):
    return _contar(GameList.query.filter_by(user_id=user_id))


def _com_status(user_id, valor=None):
    q = Status.query.join(Game, Game.id == Status.game_id).filter(Status.user_id == user_id, Game.removed_at.is_(None))
    return q.filter(Status.value == valor) if valor else q


def jogos_com_status(user_id):
    return _contar(_com_status(user_id))


def planejados(user_id):
    return _contar(_com_status(user_id, "planejado"))


def abandonados(user_id):
    return _contar(_com_status(user_id, "abandonado"))


def lojas_distintas(user_id):
    """Lojas diferentes entre os jogos com status do usuário (Steam, GOG, Epic)."""
    jogos = _com_status(user_id).with_entities(Status.game_id)
    return (db.session.query(func.count(func.distinct(JogoFonte.fonte)))
            .filter(JogoFonte.fonte.in_(LOJAS), JogoFonte.game_id.in_(jogos)).scalar() or 0)


def generos_distintos(user_id):
    jogos = _com_status(user_id).with_entities(Status.game_id)
    return (db.session.query(func.count(func.distinct(game_genres.c.genre_id)))
            .filter(game_genres.c.game_id.in_(jogos)).scalar() or 0)


def plataformas_distintas(user_id):
    jogos = _com_status(user_id).with_entities(Status.game_id)
    return (db.session.query(func.count(func.distinct(game_platforms.c.platform_id)))
            .filter(game_platforms.c.game_id.in_(jogos)).scalar() or 0)


def desenvolvedoras_distintas(user_id):
    return (db.session.query(func.count(func.distinct(Game.developer_id)))
            .join(Status, Status.game_id == Game.id)
            .filter(Status.user_id == user_id, Game.removed_at.is_(None)).scalar() or 0)


def _sessoes(user_id):
    return (SessaoJogo.query.join(Game, Game.id == SessaoJogo.game_id)
            .filter(SessaoJogo.user_id == user_id, Game.removed_at.is_(None)))


def sessoes(user_id):
    return _contar(_sessoes(user_id).filter(SessaoJogo.origem != "steam"))


def datas_sessoes(user_id):
    return [s.created_at for s in _sessoes(user_id).filter(SessaoJogo.origem != "steam").order_by(SessaoJogo.created_at)]


def horas_registradas(user_id):
    return (_sessoes(user_id).with_entities(func.coalesce(func.sum(SessaoJogo.duracao_min), 0)).scalar() or 0) // 60


def maior_sessao_min(user_id):
    return (_sessoes(user_id).filter(SessaoJogo.origem != "steam")
            .with_entities(func.coalesce(func.max(SessaoJogo.duracao_min), 0)).scalar() or 0)


def dias_com_sessao(user_id):
    return (_sessoes(user_id).filter(SessaoJogo.origem != "steam")
            .with_entities(func.count(func.distinct(SessaoJogo.jogada_em))).scalar() or 0)


def zerados_no_ano(user_id):
    ano = hoje().year
    return _contar(_com_status(user_id).filter(Status.zerado_em.between(date(ano, 1, 1), date(ano, 12, 31))))


def zerados_total(user_id):
    return _contar(_com_status(user_id).filter(Status.zerado_em.isnot(None)))


def seguidores(user_id):
    return _contar(Follow.query.filter_by(followed_id=user_id).filter(Follow.aprovado))  # PMN-03


def seguindo(user_id):
    return _contar(Follow.query.filter_by(follower_id=user_id).filter(Follow.aprovado))


def comentarios_feitos(user_id):
    return _contar(Comment.query.join(Review, Review.id == Comment.review_id)
                   .filter(Comment.user_id == user_id, Review.hidden.is_(False)))


def curtidas_dadas(user_id):
    return _contar(Like.query.filter_by(user_id=user_id))


def conquistas_desbloqueadas(user_id):
    return _contar(UsuarioConquista.query.filter(UsuarioConquista.user_id == user_id,
                                                 UsuarioConquista.desbloqueada_em.isnot(None)))


def nunca(user_id):
    return 0


AVALIAR = ("avaliacao", "avaliacao_apagada")
ACERVO = ("status", "sessao", "sessao_apagada", "importacao", "avaliacao", "avaliacao_apagada")
DIARIO = ("sessao", "sessao_apagada", "importacao")
METRICAS = {m.nome: m for m in (
    Metrica("avaliacoes_visiveis", AVALIAR, avaliacoes_visiveis, datas_avaliacoes),
    Metrica("resenhas_longas", AVALIAR, resenhas_longas),
    Metrica("notas_dez", AVALIAR, notas_dez),
    Metrica("primeira_avaliacao", AVALIAR, primeira_avaliacao),
    Metrica("curtidas_recebidas", ("curtida",), curtidas_recebidas),
    Metrica("jogos_em_listas", ("lista",), jogos_em_listas),
    Metrica("listas_criadas", ("lista",), listas_criadas),
    Metrica("jogos_com_status", ACERVO, jogos_com_status),
    Metrica("planejados", ("status",), planejados),
    Metrica("abandonados", ("status",), abandonados),
    Metrica("lojas_distintas", ACERVO, lojas_distintas),
    Metrica("generos_distintos", ACERVO, generos_distintos),
    Metrica("plataformas_distintas", ACERVO, plataformas_distintas),
    Metrica("desenvolvedoras_distintas", ACERVO, desenvolvedoras_distintas),
    Metrica("sessoes", DIARIO, sessoes, datas_sessoes),
    Metrica("horas_registradas", DIARIO, horas_registradas),
    Metrica("maior_sessao_min", DIARIO, maior_sessao_min),
    Metrica("dias_com_sessao", DIARIO, dias_com_sessao),
    Metrica("zerados_no_ano", DIARIO + ("status",), zerados_no_ano),
    Metrica("zerados_total", DIARIO + ("status",), zerados_total),
    Metrica("seguidores", ("seguidor",), seguidores),
    Metrica("seguindo", ("seguir",), seguindo),
    Metrica("comentarios_feitos", ("comentario",), comentarios_feitos),
    Metrica("curtidas_dadas", ("curtir",), curtidas_dadas),
    Metrica("conquistas_desbloqueadas", ("conquista",), conquistas_desbloqueadas),
    Metrica("concedida", (), nunca),  # só por conceder(), como os pódios de temporada (RNK-08)
)}


def registrar_metrica(nome, eventos, calcular):
    """Métrica que vem de outra spec (metas de clube, CLB-08) sem que este módulo dependa dela."""
    METRICAS[nome] = Metrica(nome, tuple(eventos), calcular)


# ---------- motor ----------
def _ativas(user, metricas):
    q = Conquista.query.filter(Conquista.ativa.is_(True), Conquista.metrica.in_(metricas))
    if not pode(user, "conquista_por_tempo"):  # GAM-14
        q = q.filter(Conquista.por_tempo.is_(False))
    return q.all()


def _desbloquear(user, uc, conquista, quando=None, avisar=True):
    """Grava o desbloqueio uma vez; entre eventos simultâneos, só um desbloqueia e avisa (GAM-05).

    O INSERT ... ON CONFLICT DO NOTHING garante a linha (outro evento pode tê-la criado depois da consulta), e
    o UPDATE condicional decide quem desbloqueia. Um savepoint não serve: o begin_nested faz flush antes do
    SAVEPOINT, então a violação de unicidade escapava dele e deixava a sessão quebrada.
    """
    from .notificacoes import notificar

    if uc in db.session.new:  # ainda não gravada: quem grava é o INSERT, não o flush do ORM
        db.session.expunge(uc)
        db.session.execute(insert(UsuarioConquista).values(user_id=user.id, conquista_id=conquista.id, progresso=0)
                           .on_conflict_do_nothing())
    mudou = db.session.execute(update(UsuarioConquista).where(
        UsuarioConquista.user_id == user.id, UsuarioConquista.conquista_id == conquista.id,
        UsuarioConquista.desbloqueada_em.is_(None),
    ).values(progresso=conquista.meta, desbloqueada_em=quando or now())).rowcount
    if mudou != 1:
        return False  # já desbloqueada por outro evento
    if avisar:  # GAM-06
        notificar(user.id, "conquista", texto=conquista.nome, alvo_url=f"/u/{user.username}/conquistas")
        db.session.add(Activity(user_id=user.id, kind="conquista", texto=f"{conquista.icone} {conquista.nome}"))
    return True


def avaliar(user, metricas, avisar=True, recalculo=False):
    """Atualiza o progresso e desbloqueia o que alcançou a meta; devolve as conquistas desbloqueadas agora."""
    conquistas = [c for c in _ativas(user, metricas) if c.metrica != "concedida"]
    if not conquistas:
        return []
    ids = [c.id for c in conquistas]
    existentes = {uc.conquista_id: uc for uc in UsuarioConquista.query.filter(
        UsuarioConquista.user_id == user.id, UsuarioConquista.conquista_id.in_(ids))}
    valores = {m: METRICAS[m].calcular(user.id) for m in {c.metrica for c in conquistas}}
    novas = []
    for c in conquistas:
        uc = existentes.get(c.id)
        if uc is not None and uc.desbloqueada_em is not None:
            continue  # GAM-07: desbloqueada nunca é retirada
        if uc is None:
            uc = UsuarioConquista(user_id=user.id, conquista_id=c.id, progresso=0)
            db.session.add(uc)
        uc.progresso = min(valores[c.metrica], c.meta)  # GAM-04: pode diminuir enquanto bloqueada
        if uc.progresso < c.meta:
            continue
        quando, aviso = None, avisar
        if recalculo:  # GAM-13: data estimada e sem aviso para o que é antigo
            quando = _data_estimada(user.id, c)
            aviso = quando is not None and (now() - quando).days <= RECALCULO_SEM_AVISO_DIAS
        if _desbloquear(user, uc, c, quando, aviso):
            novas.append(c)
    return novas


def _data_estimada(user_id, conquista):
    datas = METRICAS[conquista.metrica].datas
    lista = datas(user_id) if datas else []
    return lista[conquista.meta - 1] if len(lista) >= conquista.meta else None


def emitir_evento(user_id, tipo, **ctx):
    """Reação a uma ação já gravada; uma falha aqui fica no log e nunca desfaz a ação (GAM-03)."""
    try:
        return _emitir(user_id, tipo, ctx)
    except Exception:
        log.exception("motor de conquistas falhou no evento %s do usuário %s", tipo, user_id)
        db.session.rollback()
        db.session.info.pop("eventos_adiados", None)  # eram consequência do que foi desfeito
        return []


def adiar_evento(user_id, tipo, **ctx):
    """Evento que nasce dentro de outro (nível, meta de clube): ouvintes nunca fazem commit, então ele espera
    o commit de quem o causou e sai em despachar_adiados."""
    db.session.info.setdefault("eventos_adiados", []).append((user_id, tipo, ctx))


def despachar_adiados():
    """Emite os eventos adiados; quem grava fora de emitir_evento (ex.: /admin/xp) chama depois do commit."""
    while db.session.info.get("eventos_adiados"):
        user_id, tipo, ctx = db.session.info["eventos_adiados"].pop(0)
        emitir_evento(user_id, tipo, **ctx)


def _emitir(user_id, tipo, ctx):
    for ouvinte in list(_ouvintes):
        ouvinte(user_id, tipo, ctx)
    metricas = [m.nome for m in METRICAS.values() if tipo in m.eventos]
    if not metricas:  # nenhum ouvinte de conquista: nenhuma consulta às conquistas
        db.session.commit()
        despachar_adiados()
        return []
    user = db.session.get(User, user_id)
    novas = avaliar(user, metricas) if user is not None else []
    db.session.commit()
    for c in novas:
        _emitir(user_id, "conquista", {"raridade": c.raridade, "conquista_id": c.id})
    despachar_adiados()
    return novas


def conceder(user_id, codigo):
    """Desbloqueia uma conquista `concedida` (pódios de temporada, RNK-08); idempotente."""
    user, conquista = db.session.get(User, user_id), Conquista.query.filter_by(codigo=codigo).first()
    if user is None or conquista is None or not conquista.ativa:
        return False
    uc = UsuarioConquista.query.filter_by(user_id=user_id, conquista_id=conquista.id).first()
    if uc is not None and uc.desbloqueada_em is not None:
        return False
    if uc is None:
        uc = UsuarioConquista(user_id=user_id, conquista_id=conquista.id, progresso=0)
        db.session.add(uc)
    ok = _desbloquear(user, uc, conquista)
    db.session.commit()
    if ok:  # já gravada: a falha de um ouvinte fica no log, como nos demais eventos
        emitir_evento(user_id, "conquista", raridade=conquista.raridade, conquista_id=conquista.id)
    return ok


def recalcular(username=None):
    """Reavalia o histórico de todos ou de um usuário (GAM-13); devolve (usuários, desbloqueadas)."""
    q = User.query.order_by(User.id)
    if username:
        q = q.filter_by(username_lower=username.lower())
    usuarios = q.all()
    metricas = [m for m in METRICAS if m != "concedida"]
    total = 0
    for user in usuarios:  # sem ouvintes: a conquista retroativa não dá XP (PRG, fora do escopo)
        total += len(avaliar(user, metricas, recalculo=True))
        db.session.commit()
        despachar_adiados()
    return len(usuarios), total
