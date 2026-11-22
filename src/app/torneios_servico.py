"""Regras dos torneios (TRN-01..14), sem Flask: as rotas de `torneios.py` leem o formulário e chamam estas funções.

A chave nunca é gravada pronta. A cada leitura o CHV a refaz a partir das sementes congeladas nas inscrições e dos
resultados confirmados (CHV-07), então a página, o avanço e a correção usam o mesmo algoritmo. Só são gravadas as
partidas jogáveis, as que já têm os dois participantes definidos. Quem chama faz o commit e, depois dele,
`gamificacao.despachar_adiados()` para os eventos do encerramento (TRN-11).
"""
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from . import chaveamento, gamificacao, rankings, tempo
from .bloqueios import com_bloqueio
from .chaveamento import BYE, CorrecaoBloqueada
from .menores.idade import VINCULADO, faixa_etaria
from .menores.politica import pode
from .models import (
    Activity, Clube, InscricaoTorneio, MembroClube, PartidaTorneio, RegistroTorneio, ReporteResultado, Temporada,
    Torneio, db, visible_games,
)
from .notificacoes import notificar
from .util import slugify

FORMATOS = {"eliminacao_simples": "Eliminação simples", "eliminacao_dupla": "Eliminação dupla",
            "suico": "Suíço", "pontos_corridos": "Pontos corridos"}
SITUACOES = {"aberto": "Inscrições abertas", "andamento": "Em andamento", "encerrado": "Encerrado",
             "cancelado": "Cancelado"}
SEMENTES = {"ranking": "Ranking de XP da temporada", "sorteio": "Sorteio"}
EMPATE_PERMITIDO = ("suico", "pontos_corridos")  # CHV-10
MIN_VAGAS, MAX_VAGAS = 2, 64
MAX_PRAZO_RODADA = 30
ATIVAS = ("inscrito", "pendente_responsavel")
CONFIRMACAO_AUTOMATICA = timedelta(hours=48)  # TRN-06


class ErroTorneio(ValueError):
    """Mensagem para quem tentou a ação."""


def url(torneio, partida=None):
    return f"/torneios/{torneio.slug}" + (f"/partidas/{partida.id}" if partida else "")


def avisar(ids, torneio, texto, partida=None, ator_id=None):
    """Notificação `torneio` a cada id (TRN-10); `notificar` aplica preferências, bloqueio e menores."""
    for user_id in sorted(set(ids)):
        notificar(user_id, "torneio", texto=texto, alvo_url=url(torneio, partida), ator_id=ator_id)


def organizadores(torneio):
    """Quem criou e, no torneio de clube, a moderação ativa do clube (TRN-02)."""
    ids = {torneio.organizador_id}
    if torneio.clube_id:
        ids |= set(db.session.scalars(select(MembroClube.user_id).where(
            MembroClube.clube_id == torneio.clube_id, MembroClube.situacao == "ativo",
            MembroClube.papel.in_(("dono", "moderador")))))
    return ids


def registrar(torneio, autor, acao, justificativa, partida=None):
    """Auditoria do torneio, mostrada na página com autor, data e justificativa (TRN-07)."""
    db.session.add(RegistroTorneio(torneio_id=torneio.id, autor_id=autor.id, acao=acao, justificativa=justificativa,
                                   partida_id=partida.id if partida else None))


def _texto_obrigatorio(texto, nome, maximo=300):
    texto = (texto or "").strip()
    if not 1 <= len(texto) <= maximo:
        raise ErroTorneio(f"Informe {nome} (até {maximo} caracteres).")
    return texto


# ---------- criação e edição (TRN-01, TRN-02, TRN-12) ----------
def clubes_que_modera(user):
    """Clubes ativos em que `user` é dono ou moderador: os únicos em nome dos quais ele cria torneio (TRN-02)."""
    return (Clube.query.join(MembroClube, MembroClube.clube_id == Clube.id)
            .filter(MembroClube.user_id == user.id, MembroClube.situacao == "ativo",
                    MembroClube.papel.in_(("dono", "moderador")), Clube.removido_em.is_(None))
            .order_by(Clube.nome_lower).all())


def ler_prazo(texto):
    """`AAAA-MM-DDTHH:MM` no fuso de Belém (GLOBAL-09), devolvido em UTC como o banco guarda; None se inválido."""
    try:
        local = datetime.strptime((texto or "").strip(), "%Y-%m-%dT%H:%M")
    except ValueError:
        return None
    return local.replace(tzinfo=tempo.FUSO).astimezone(timezone.utc).replace(tzinfo=None)


def _inteiro(texto, minimo, maximo):
    try:
        valor = int(texto)
    except (TypeError, ValueError):
        return None
    return valor if minimo <= valor <= maximo else None


def validar(form, user=None):
    """(dados, erros) do formulário, validado no servidor (GLOBAL-03). Sem `user`, só os campos da edição."""
    erros, dados = {}, {}
    dados["nome"] = (form.get("nome") or "").strip()
    if not 3 <= len(dados["nome"]) <= 80:
        erros["nome"] = "O nome deve ter de 3 a 80 caracteres."
    dados["regras"] = (form.get("regras") or "").strip()
    if len(dados["regras"]) > 2000:
        erros["regras"] = "As regras devem ter até 2.000 caracteres."
    dados["prazo_inscricao"] = ler_prazo(form.get("prazo_inscricao"))
    if dados["prazo_inscricao"] is None or dados["prazo_inscricao"] <= tempo.agora_utc():
        erros["prazo_inscricao"] = "Informe um prazo de inscrição no futuro."
    dados["prazo_rodada_dias"] = _inteiro(form.get("prazo_rodada_dias") or 7, 1, MAX_PRAZO_RODADA)
    if dados["prazo_rodada_dias"] is None:
        erros["prazo_rodada_dias"] = f"O prazo da rodada deve ser de 1 a {MAX_PRAZO_RODADA} dias."
    if user is None:
        return dados, erros
    game = visible_games().filter_by(slug=(form.get("jogo") or "").strip()).first()
    if game is None:
        erros["jogo"] = "Escolha um jogo do catálogo."
    dados["game_id"] = game.id if game else None
    dados["formato"] = form.get("formato")
    if dados["formato"] not in FORMATOS:
        erros["formato"] = "Escolha o formato."
    dados["limite"] = _inteiro(form.get("limite"), MIN_VAGAS, MAX_VAGAS)
    if dados["limite"] is None:
        erros["limite"] = f"O limite deve ser de {MIN_VAGAS} a {MAX_VAGAS} participantes."
    dados["sementes"] = form.get("sementes")
    if dados["sementes"] not in SEMENTES:
        erros["sementes"] = "Escolha como definir as sementes."
    dados["aberto_a_menores"] = bool(form.get("aberto_a_menores"))  # PMN-11: desligado por padrão
    dados["clube_id"] = None
    if form.get("clube"):
        clube = next((c for c in clubes_que_modera(user) if c.slug == form["clube"]), None)
        if clube is None:
            erros["clube"] = "Só o dono ou um moderador cria torneio em nome do clube."
        else:
            dados["clube_id"] = clube.id
    return dados, erros


def slug_livre(nome):
    base = slugify(nome) or "torneio"
    slug, n = base, 2
    while Torneio.query.filter_by(slug=slug).first():
        slug, n = f"{base}-{n}", n + 1
    return slug


def criar(user, dados):
    torneio = Torneio(slug=slug_livre(dados["nome"]), organizador_id=user.id, **dados)
    db.session.add(torneio)
    return torneio


def editar(torneio, dados):
    """Nome, regras e prazos, só antes do início (TRN-12)."""
    if torneio.situacao != "aberto":
        raise ErroTorneio("O torneio só pode ser editado antes do início.")
    for campo, valor in dados.items():
        setattr(torneio, campo, valor)


def cancelar(torneio, autor, motivo):
    """Organizador ou admin, com motivo, antes do encerramento (TRN-12)."""
    if torneio.situacao in ("encerrado", "cancelado"):
        raise ErroTorneio("Este torneio já terminou.")
    torneio.motivo_cancelamento = _texto_obrigatorio(motivo, "o motivo do cancelamento")
    torneio.situacao = "cancelado"
    registrar(torneio, autor, "cancelamento", torneio.motivo_cancelamento)
    ativos = db.session.scalars(select(InscricaoTorneio.user_id).where(
        InscricaoTorneio.torneio_id == torneio.id, InscricaoTorneio.situacao.in_(ATIVAS + ("desistiu",))))
    avisar(ativos, torneio, f"O torneio {torneio.nome} foi cancelado: {torneio.motivo_cancelamento}")


# ---------- inscrição (TRN-04) ----------
def vagas_ocupadas(torneio):
    """A inscrição pendente do responsável não ocupa vaga (RSP-08)."""
    return InscricaoTorneio.query.filter_by(torneio_id=torneio.id, situacao="inscrito").count()


def inscricao_de(torneio, user_id):
    return InscricaoTorneio.query.filter_by(torneio_id=torneio.id, user_id=user_id).first()


def inscrever(torneio, user, membro_ativo):
    """Login, prazo, vagas, clube e política de menores; inscrever-se de novo devolve a mesma inscrição."""
    if torneio.situacao != "aberto" or tempo.agora_utc() > torneio.prazo_inscricao:
        raise ErroTorneio("As inscrições deste torneio estão encerradas.")
    if torneio.clube_id and not membro_ativo:
        raise ErroTorneio("Só membros do clube se inscrevem neste torneio.")
    if not pode(user, "torneio_inscrever", torneio):
        raise ErroTorneio("Este torneio não está disponível para a sua conta.")
    insc = inscricao_de(torneio, user.id)
    if insc is not None and insc.situacao in ATIVAS:
        return insc
    if insc is not None and insc.situacao == "removido":
        raise ErroTorneio("O organizador removeu sua inscrição deste torneio.")
    # de 12 a 15 anos, a inscrição espera o responsável e não ocupa vaga. Extensão de RSP-08: o responsável a
    # aprova por `autorizar_inscricao`
    situacao = "pendente_responsavel" if faixa_etaria(user) == VINCULADO else "inscrito"
    if situacao == "inscrito" and vagas_ocupadas(torneio) >= torneio.limite:
        raise ErroTorneio("Não há mais vagas neste torneio.")
    if insc is None:
        insc = InscricaoTorneio(torneio_id=torneio.id, user_id=user.id)
        db.session.add(insc)
    insc.situacao = situacao
    return insc


def autorizar_inscricao(inscricao):
    """Ponto de entrada de RSP-08: o responsável aprova a inscrição pendente; falso sem vaga ou fora do prazo."""
    torneio = db.session.get(Torneio, inscricao.torneio_id)
    if (inscricao.situacao != "pendente_responsavel" or torneio.situacao != "aberto"
            or vagas_ocupadas(torneio) >= torneio.limite):
        return False
    inscricao.situacao = "inscrito"
    return True


def remover(torneio, autor, user_id, motivo):
    """Antes do início, com motivo notificado ao removido (TRN-04)."""
    if torneio.situacao != "aberto":
        raise ErroTorneio("Só dá para remover inscritos antes do início.")
    motivo = _texto_obrigatorio(motivo, "o motivo da remoção")
    insc = inscricao_de(torneio, user_id)
    if insc is None or insc.situacao not in ATIVAS:
        raise ErroTorneio("Essa pessoa não está inscrita.")
    insc.situacao, insc.motivo = "removido", motivo
    registrar(torneio, autor, "remocao", motivo)
    avisar([user_id], torneio, f"Sua inscrição no torneio {torneio.nome} foi removida: {motivo}", ator_id=autor.id)


def desistir(torneio, user):
    """Antes do início, sai da lista; durante, perde por W.O. o que falta e o histórico fica (TRN-04, TRN-09)."""
    insc = inscricao_de(torneio, user.id)
    if insc is None or insc.situacao not in ATIVAS or torneio.situacao not in ("aberto", "andamento"):
        raise ErroTorneio("Você não está inscrito neste torneio.")
    insc.situacao = "desistiu"
    if torneio.situacao == "andamento":
        avancar(torneio)


# ---------- início (TRN-05) ----------
def pontuacao_xp(ids):
    """XP da temporada atual do RNK, para `sementes()` (CHV-11)."""
    _, de, ate = rankings.janela("atual", Temporada.query.all())
    sub = rankings.consulta("xp", de, ate).subquery()
    return dict(db.session.query(sub.c.user_id, sub.c.valor).filter(sub.c.user_id.in_(ids)).all())


def pares_com_bloqueio(ids):
    """Pares de inscritos com bloqueio entre si, pela regra única de BLQ (GLOBAL-13)."""
    return {frozenset((a, b)) for a in ids for b in com_bloqueio(a) if b in ids}


def _primeira_rodada(formato, ordem):
    if formato == "pontos_corridos":
        partidas = chaveamento.pontos_corridos(ordem)
    else:
        partidas = chaveamento.FORMATOS_DE_ELIMINACAO[formato](ordem).partidas.values()
    return [frozenset((p.a, p.b)) for p in partidas if p.rodada == 1 and BYE not in (p.a, p.b)
            and None not in (p.a, p.b) and p.chave in ("eliminacao", "vencedores", "liga")]


def _conflitos(formato, ordem, pares):
    return [par for par in _primeira_rodada(formato, ordem) if par in pares]


def afastar_bloqueados(formato, ordem, pares):
    """Troca com a semente vizinha quem enfrentaria na 1ª rodada alguém com quem tem bloqueio (TRN-05, BLQ-06).

    A pior semente do par troca primeiro; a troca só vale se diminuir os confrontos com bloqueio.
    """
    ordem = list(ordem)
    conflitos = _conflitos(formato, ordem, pares)
    melhorou = True
    while conflitos and melhorou:
        melhorou = False
        for x in sorted(conflitos[0], key=ordem.index, reverse=True):
            i = ordem.index(x)
            for j in (i + 1, i - 1):
                if not 0 <= j < len(ordem):
                    continue
                tentativa = list(ordem)
                tentativa[i], tentativa[j] = tentativa[j], tentativa[i]
                if len(novos := _conflitos(formato, tentativa, pares)) < len(conflitos):
                    ordem, conflitos, melhorou = tentativa, novos, True
                    break
            if melhorou:
                break
    return ordem


def iniciar(torneio, autor):
    """Congela os inscritos, calcula as sementes e grava as partidas que o CHV gerou (TRN-05)."""
    if torneio.situacao != "aberto":
        raise ErroTorneio("O torneio já começou ou foi cancelado.")
    inscricoes = InscricaoTorneio.query.filter_by(torneio_id=torneio.id).all()
    inscritos = {i.user_id: i for i in inscricoes if i.situacao == "inscrito"}
    if len(inscritos) < MIN_VAGAS:
        raise ErroTorneio("São precisos ao menos 2 inscritos para iniciar.")
    if tempo.agora_utc() <= torneio.prazo_inscricao and len(inscritos) < torneio.limite:
        raise ErroTorneio("Inicie depois do prazo de inscrição ou com as vagas preenchidas.")
    for i in inscricoes:
        if i.situacao == "pendente_responsavel":
            i.situacao = "descartada"  # o torneio começou antes da aprovação (TRN-04)
    ids = sorted(inscritos)
    if torneio.sementes == "sorteio":
        torneio.semente_sorteio = secrets.randbelow(2 ** 31)
        ordem = chaveamento.sementes(ids, {}, aleatorio=torneio.semente_sorteio)
    else:
        ordem = chaveamento.sementes(ids, pontuacao_xp(ids))
    pares = pares_com_bloqueio(set(ids))
    torneio.pares_evitados = json.dumps(sorted(sorted(p) for p in pares))
    if torneio.formato != "suico":  # no suíço, o CHV evita os pares em toda rodada
        ordem = afastar_bloqueados(torneio.formato, ordem, pares)
    for n, user_id in enumerate(ordem, start=1):
        inscritos[user_id].semente = n
    torneio.rodadas = chaveamento.rodadas_suico(len(ordem)) if torneio.formato == "suico" else None
    torneio.situacao, torneio.iniciado_em = "andamento", tempo.agora_utc()
    avisar(ordem, torneio, f"O torneio {torneio.nome} começou", ator_id=autor.id)
    avancar(torneio)


# ---------- chave e classificação (TRN-03, TRN-13) ----------
@dataclass
class Estado:
    participantes: list
    partidas: list  # do CHV, com os resultados aplicados
    chave: object  # chaveamento.Chave; na liga e no suíço, as partidas embrulhadas para o SVG
    tabela: list
    rodada: int  # suíço: a rodada em curso


def evitados(torneio):
    return frozenset(frozenset(par) for par in json.loads(torneio.pares_evitados or "[]"))


def resultados(partidas):
    return [(p.codigo, p.vencedor_id, p.placar_a, p.placar_b) for p in partidas if p.situacao == "confirmada"]


def _rodada_completa(partidas, rodada):
    return all(p.jogada or p.b is BYE for p in partidas if p.rodada == rodada)


def estado(torneio, inscricoes, partidas):
    """Chave e classificação refeitas pelo CHV, sem consultar o banco; None antes do início."""
    ids = [i.user_id for i in sorted((i for i in inscricoes if i.semente), key=lambda i: i.semente)]
    if not ids:
        return None
    res, evitar, formato = resultados(partidas), evitados(torneio), torneio.formato
    if formato in chaveamento.FORMATOS_DE_ELIMINACAO:
        chave = chaveamento.montar(formato, ids, res)
        return Estado(ids, list(chave.partidas.values()), chave, chaveamento.classificacao(formato, ids, res), 1)
    rodada = max((p.rodada for p in partidas), default=1)
    lista = chaveamento.partidas_com_resultados(formato, ids, res, evitar=evitar, ate=rodada)
    if formato == "suico" and rodada < torneio.rodadas and _rodada_completa(lista, rodada):
        rodada += 1  # a próxima rodada do suíço só existe com a anterior completa (CHV-05)
        lista = chaveamento.partidas_com_resultados(formato, ids, res, evitar=evitar, ate=rodada)
    chave = chaveamento.Chave(formato, ids, {p.id: p for p in lista})
    return Estado(ids, lista, chave, chaveamento.classificacao(formato, ids, res, evitar=evitar), rodada)


def terminou(torneio, est):
    if torneio.formato in chaveamento.FORMATOS_DE_ELIMINACAO:
        return chaveamento.campeao(est.chave) is not None
    completo = all(p.jogada or p.b is BYE for p in est.partidas)
    return completo and (torneio.formato != "suico" or est.rodada == torneio.rodadas)


def ordem_final(torneio, est):
    """Classificação final (TRN-11). Na eliminação, o campeão e depois quem caiu mais tarde; empate pela tabela."""
    tabela = [linha.participante for linha in est.tabela]
    if torneio.formato not in chaveamento.FORMATOS_DE_ELIMINACAO:
        return tabela
    queda = {}
    for p in est.partidas:  # na ordem da chave: a última derrota é a que eliminou
        if p.jogada:
            queda[p.perdedor] = (p.chave == "final", p.rodada)
    campeao, posicao = chaveamento.campeao(est.chave), {x: i for i, x in enumerate(tabela)}
    return sorted(tabela, key=lambda x: (x != campeao, *(-v for v in queda.get(x, (0, 0))), posicao[x]))


def encerrar(torneio, est, inscricoes):
    """Grava a classificação, avisa, põe a atividade no feed e adia os eventos de GAM (TRN-11)."""
    ordem = ordem_final(torneio, est)
    por_user = {i.user_id: i for i in inscricoes}
    for posicao, user_id in enumerate(ordem, start=1):
        por_user[user_id].posicao_final = posicao
        gamificacao.adiar_evento(user_id, "torneio", posicao=posicao, torneio_id=torneio.id)
    torneio.situacao, torneio.encerrado_em = "encerrado", tempo.agora_utc()
    avisar(ordem, torneio, f"O torneio {torneio.nome} terminou: veja a classificação final")
    if torneio.clube is None or torneio.clube.publico:  # torneio de clube privado não vai ao feed
        db.session.add(Activity(user_id=ordem[0], kind="torneio", texto=f"venceu o torneio {torneio.nome}"))


def _carregar(torneio):
    return (InscricaoTorneio.query.filter_by(torneio_id=torneio.id).all(),
            PartidaTorneio.query.filter_by(torneio_id=torneio.id).all())


def avancar(torneio):
    """Grava as partidas novas, aplica o W.O. de quem desistiu e encerra quando o CHV termina (TRN-05, TRN-11)."""
    while True:
        inscricoes, partidas = _carregar(torneio)
        est = estado(torneio, inscricoes, partidas)
        if terminou(torneio, est):
            encerrar(torneio, est, inscricoes)
            return est
        if not sincronizar(torneio, est, partidas, inscricoes):
            return est


def _apagar(linha):
    ReporteResultado.query.filter_by(partida_id=linha.id).delete()
    db.session.delete(linha)


def sincronizar(torneio, est, partidas, inscricoes):
    """Grava as partidas que o CHV definiu e acerta as não confirmadas que uma correção mudou; True se mudou."""
    jogaveis = {p.id: p for p in est.partidas if p.a not in (None, BYE) and p.b not in (None, BYE)
                and not (p.decidida and not p.jogada)}  # sem o reset dispensado (CHV-03)
    gravadas, mudou = {p.codigo: p for p in partidas}, False
    for linha in partidas:
        p = jogaveis.get(linha.codigo)
        if linha.situacao == "confirmada" or (p is not None and (p.a, p.b) == (linha.a_id, linha.b_id)):
            continue
        if p is None:
            _apagar(linha)
            del gravadas[linha.codigo]
        else:  # a correção trocou um dos lados: os resultados informados não valem mais
            ReporteResultado.query.filter_by(partida_id=linha.id).delete()
            linha.a_id, linha.b_id, linha.situacao = p.a, p.b, "aguardando"
        mudou = True
    desistiram = {i.user_id for i in inscricoes if i.situacao == "desistiu"}
    for codigo, p in jogaveis.items():
        if codigo in gravadas:
            continue
        rodadas = p.rodada if torneio.formato == "pontos_corridos" else 1  # a liga grava todas as rodadas no início
        prazo = tempo.agora_utc() + timedelta(days=torneio.prazo_rodada_dias * rodadas)
        linha = gravadas[codigo] = PartidaTorneio(torneio_id=torneio.id, codigo=codigo, rodada=p.rodada, a_id=p.a,
                                                   b_id=p.b, prazo=prazo)
        db.session.add(linha)
        db.session.flush()
        if not {p.a, p.b} & desistiram:
            avisar([p.a, p.b], torneio, f"Nova partida no torneio {torneio.nome}", linha)
        mudou = True
    for linha in gravadas.values():  # quem desistiu perde por W.O. o que falta (TRN-09)
        if linha.situacao != "confirmada" and {linha.a_id, linha.b_id} & desistiram:
            vencedor = linha.b_id if linha.a_id in desistiram else linha.a_id
            if {linha.a_id, linha.b_id} <= desistiram and torneio.formato in EMPATE_PERMITIDO:
                vencedor = None
            confirmar(linha, vencedor, None, None, wo=True)
            mudou = True
    return mudou


# ---------- resultados (TRN-06..09) ----------
def confirmar(linha, vencedor_id, placar_a, placar_b, wo=False):
    linha.vencedor_id, linha.placar_a, linha.placar_b, linha.wo = vencedor_id, placar_a, placar_b, wo
    linha.situacao, linha.confirmada_em = "confirmada", tempo.agora_utc()


def validar_resultado(torneio, linha, vencedor_id, placar_a, placar_b):
    if vencedor_id is None and torneio.formato not in EMPATE_PERMITIDO:
        raise ErroTorneio("Informe o vencedor: este formato não tem empate.")
    if vencedor_id is not None and vencedor_id not in (linha.a_id, linha.b_id):
        raise ErroTorneio("O vencedor precisa ser um dos dois participantes.")
    if (placar_a is None) != (placar_b is None) or min(placar_a or 0, placar_b or 0) < 0:
        raise ErroTorneio("Informe o placar dos dois lados, sem números negativos, ou deixe os dois em branco.")
    if placar_a is not None and placar_a != placar_b and vencedor_id != (linha.a_id if placar_a > placar_b else linha.b_id):
        raise ErroTorneio("O placar não combina com o vencedor informado.")


def _em_jogo(torneio, linha, situacao):
    if torneio.situacao != "andamento" or linha.situacao != situacao:
        raise ErroTorneio("Esta partida não aceita essa ação agora.")


def concordam(r1, r2):
    """(vencedor, placar_a, placar_b) combinado, ou None; o placar de um só lado vale para os dois."""
    if r1.vencedor_id != r2.vencedor_id:
        return None
    p1, p2 = (r1.placar_a, r1.placar_b), (r2.placar_a, r2.placar_b)
    if None not in p1 and None not in p2 and p1 != p2:
        return None
    return (r1.vencedor_id, *(p1 if None not in p1 else p2))


def informar(torneio, linha, user, vencedor_id, placar_a, placar_b):
    """O resultado de um dos lados; devolve a situação da partida depois dele (TRN-06, TRN-07, TRN-10)."""
    _em_jogo(torneio, linha, "aguardando")
    validar_resultado(torneio, linha, vencedor_id, placar_a, placar_b)
    meu = ReporteResultado.query.filter_by(partida_id=linha.id, user_id=user.id).first()
    if meu is None:
        meu = ReporteResultado(partida_id=linha.id, user_id=user.id)
        db.session.add(meu)
    meu.vencedor_id, meu.placar_a, meu.placar_b, meu.created_at = vencedor_id, placar_a, placar_b, tempo.agora_utc()
    outro_id = linha.b_id if user.id == linha.a_id else linha.a_id
    outro = ReporteResultado.query.filter_by(partida_id=linha.id, user_id=outro_id).first()
    if outro is None:
        avisar([outro_id], torneio, f"Seu adversário informou o resultado de uma partida do torneio {torneio.nome}",
               linha, ator_id=user.id)
        return "aguardando"
    if combinado := concordam(meu, outro):
        confirmar(linha, *combinado)
        avancar(torneio)
        return "confirmada"
    linha.situacao = "contestada"
    avisar(organizadores(torneio) | {linha.a_id, linha.b_id}, torneio,
           f"Resultado contestado numa partida do torneio {torneio.nome}", linha)
    return "contestada"


def confirmar_vencidas():
    """Um lado informou e o outro não respondeu em 48 horas: vale o informado (TRN-06). Devolve quantas."""
    limite = tempo.agora_utc() - CONFIRMACAO_AUTOMATICA
    ids = db.session.scalars(
        select(PartidaTorneio.id).join(Torneio, Torneio.id == PartidaTorneio.torneio_id)
        .join(ReporteResultado, ReporteResultado.partida_id == PartidaTorneio.id)
        .where(Torneio.situacao == "andamento", PartidaTorneio.situacao == "aguardando")
        .group_by(PartidaTorneio.id)
        .having(func.count(ReporteResultado.id) == 1, func.max(ReporteResultado.created_at) <= limite)).all()
    for pid in ids:
        linha = db.session.get(PartidaTorneio, pid)
        torneio = db.session.get(Torneio, linha.torneio_id)
        if linha.situacao != "aguardando" or torneio.situacao != "andamento":
            continue  # uma confirmação anterior do laço mexeu nela
        r = ReporteResultado.query.filter_by(partida_id=pid).one()
        confirmar(linha, r.vencedor_id, r.placar_a, r.placar_b)
        avisar([linha.a_id, linha.b_id], torneio,
               f"Resultado confirmado depois de 48 horas sem resposta no torneio {torneio.nome}", linha)
        avancar(torneio)
    return len(ids)


def decidir(torneio, linha, autor, resultado, justificativa, wo=False):
    """Contestação decidida pelo organizador: um dos resultados, outro ou W.O., com justificativa (TRN-07)."""
    _em_jogo(torneio, linha, "contestada")
    justificativa = _texto_obrigatorio(justificativa, "a justificativa", 500)
    validar_resultado(torneio, linha, *resultado)
    confirmar(linha, *resultado, wo=wo)
    registrar(torneio, autor, "decisao", justificativa, linha)
    avisar([linha.a_id, linha.b_id], torneio, f"O organizador decidiu a partida contestada do torneio {torneio.nome}",
           linha, ator_id=autor.id)
    avancar(torneio)


def dar_wo(torneio, linha, autor, vencedor_id, justificativa):
    """Depois do prazo da rodada, contra quem não informou resultado (TRN-09)."""
    _em_jogo(torneio, linha, "aguardando")
    justificativa = _texto_obrigatorio(justificativa, "a justificativa", 500)
    if tempo.agora_utc() <= linha.prazo:
        raise ErroTorneio("O W.O. só vale depois do prazo da rodada.")
    validar_resultado(torneio, linha, vencedor_id, None, None)
    perdedor = linha.b_id if vencedor_id == linha.a_id else linha.a_id
    if vencedor_id is None or ReporteResultado.query.filter_by(partida_id=linha.id, user_id=perdedor).first():
        raise ErroTorneio("O W.O. vai contra quem não informou o resultado.")
    confirmar(linha, vencedor_id, None, None, wo=True)
    registrar(torneio, autor, "wo", justificativa, linha)
    avisar([linha.a_id, linha.b_id], torneio, f"W.O. numa partida do torneio {torneio.nome}", linha, ator_id=autor.id)
    avancar(torneio)


def corrigir(torneio, linha, autor, resultado, justificativa):
    """Refaz uma partida confirmada pelo `corrigir` do CHV; partida seguinte já jogada recusa (TRN-08, CHV-09)."""
    _em_jogo(torneio, linha, "confirmada")
    justificativa = _texto_obrigatorio(justificativa, "a justificativa", 500)
    validar_resultado(torneio, linha, *resultado)
    inscricoes, partidas = _carregar(torneio)
    est = estado(torneio, inscricoes, partidas)
    try:
        if torneio.formato in chaveamento.FORMATOS_DE_ELIMINACAO:
            chaveamento.corrigir(est.chave, linha.codigo, (linha.codigo, *resultado))
        elif torneio.formato == "suico":  # as rodadas seguintes do suíço dependem desta pontuação
            jogadas = [p.codigo for p in partidas if p.rodada > linha.rodada and p.situacao == "confirmada"]
            if jogadas:
                raise CorrecaoBloqueada(jogadas)
    except CorrecaoBloqueada as e:
        raise ErroTorneio(f"Correção recusada: as partidas seguintes já foram jogadas ({', '.join(e.partidas)}).")
    confirmar(linha, *resultado)
    registrar(torneio, autor, "correcao", justificativa, linha)
    avisar([linha.a_id, linha.b_id], torneio, f"O organizador corrigiu uma partida do torneio {torneio.nome}", linha,
           ator_id=autor.id)
    avancar(torneio)


# ---------- conquistas (TRN-11, GAM-03) ----------
def _contagem(*filtros):
    def calcular(user_id):
        return db.session.query(func.count(InscricaoTorneio.id)).join(
            Torneio, Torneio.id == InscricaoTorneio.torneio_id).filter(
            InscricaoTorneio.user_id == user_id, Torneio.situacao == "encerrado",
            InscricaoTorneio.posicao_final.isnot(None), *filtros).scalar() or 0
    return calcular


gamificacao.registrar_metrica("torneios_jogados", ("torneio",), _contagem())
gamificacao.registrar_metrica("torneios_podio", ("torneio",), _contagem(InscricaoTorneio.posicao_final <= 3))
gamificacao.registrar_metrica("torneios_vencidos", ("torneio",), _contagem(InscricaoTorneio.posicao_final == 1))
