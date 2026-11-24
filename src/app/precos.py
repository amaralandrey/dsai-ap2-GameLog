"""Preços por loja (PRC-01..13): coleta na Steam e na GOG, histórico com uma linha por mudança e menor preço."""
import json
from datetime import datetime, timedelta

from sqlalchemy import case, exists, func

from .fontes import Interrompido, base, gog, steam
from .graficos_svg import linha
from .models import LOJAS_COM_PRECO, NOMES_DAS_LOJAS, Game, JogoFonte, PrecoLoja, Status, db
from .tempo import agora_utc, dia_de

COLETORES = {"steam": steam.preco, "gog": gog.preco}  # Epic e RAWG não têm preço (PRC-04)
VALIDADE = timedelta(days=7)  # depois disso o preço não é mais atual (PRC-08)
ORDEM_DAS_LOJAS = case((PrecoLoja.fonte == "steam", 0), else_=1)


def reais(centavos):
    """`R$ 1.234,56` a partir de centavos; 0 é "Gratuito" (PRC-08)."""
    if centavos == 0:
        return "Gratuito"
    inteiro, resto = divmod(centavos, 100)
    return f"R$ {inteiro:,}".replace(",", ".") + f",{resto:02d}"


def atual(preco, momento=None):
    return (momento or agora_utc()) - preco.verificado_em <= VALIDADE


def registrar(game_id, fonte, valores, momento):
    """Grava uma coleta: linha nova se algo mudou, senão só renova `verificado_em` (PRC-05). True se criou."""
    ultima = (PrecoLoja.query.filter_by(game_id=game_id, fonte=fonte)
              .order_by(PrecoLoja.coletado_em.desc(), PrecoLoja.id.desc()).first())
    if ultima is not None and ultima.valores == tuple(valores):
        ultima.verificado_em = momento
        return False
    preco, preco_base, desconto = valores
    db.session.add(PrecoLoja(game_id=game_id, fonte=fonte, moeda="BRL", preco_centavos=preco,
                             preco_base_centavos=preco_base, desconto_pct=desconto,
                             coletado_em=momento, verificado_em=momento))
    return True


def vinculos(fonte, limite=100, slug=None):
    """(game_id, id na loja) dos jogos com vínculo da fonte; primeiro os planejados por alguém (PRC-06)."""
    planejado = exists().where(Status.game_id == JogoFonte.game_id, Status.value == "planejado")
    q = (db.session.query(JogoFonte.game_id, JogoFonte.external_id).join(Game, Game.id == JogoFonte.game_id)
         .filter(JogoFonte.fonte == fonte, Game.removed_at.is_(None)))
    if slug:
        q = q.filter(Game.slug == slug)
    return q.order_by(case((planejado, 0), else_=1), JogoFonte.game_id).limit(limite).all()


def coletar(fonte, limite=100, slug=None, mudaram=None):
    """Devolve (jogos, novos, sem_mudanca, sem_preco, falhas); `mudaram` recebe os ids com preço novo.

    Falha num jogo não para o lote; 429 persistente levanta Interrompido com o que já foi gravado (GOG-11).
    """
    coletor, contagem = COLETORES[fonte], [0, 0, 0, 0, 0]
    for game_id, external_id in vinculos(fonte, limite, slug):
        try:
            valores = coletor(external_id)
            if valores is None:
                situacao = 3
            else:
                situacao = 1 if registrar(game_id, fonte, valores, agora_utc()) else 2
                db.session.commit()
        except Exception as exc:
            db.session.rollback()
            if base.limitado(exc):
                raise Interrompido("rate-limit", tuple(contagem)) from exc
            situacao = 4
        contagem[0] += 1
        contagem[situacao] += 1
        if situacao == 1 and mudaram is not None:
            mudaram.add(game_id)
    return tuple(contagem)


def atuais(game_id):
    """Linha mais recente de cada loja do jogo, numa consulta só (PRC-08, PRC-13)."""
    ordem = func.row_number().over(partition_by=PrecoLoja.fonte,
                                   order_by=(PrecoLoja.coletado_em.desc(), PrecoLoja.id.desc()))
    sub = db.session.query(PrecoLoja.id, ordem.label("n")).filter(PrecoLoja.game_id == game_id).subquery()
    linhas = PrecoLoja.query.join(sub, sub.c.id == PrecoLoja.id).filter(sub.c.n == 1).all()
    return {p.fonte: p for p in linhas}


def menor_preco(game_id):
    """Menor preço de todo o histórico; no empate, a coleta mais antiga e depois Steam antes de GOG (PRC-09)."""
    return (PrecoLoja.query.filter_by(game_id=game_id)
            .order_by(PrecoLoja.preco_centavos, PrecoLoja.coletado_em, ORDEM_DAS_LOJAS).first())


def historico(game_id):
    return (PrecoLoja.query.filter_by(game_id=game_id)
            .order_by(PrecoLoja.coletado_em.desc(), PrecoLoja.id.desc()))


def dia(momento_utc):
    """dd/mm/aaaa em Belém de um instante gravado em UTC."""
    return dia_de(momento_utc).strftime("%d/%m/%Y")


def grafico(linhas, ultimos_dias=12):
    """SVG com uma série por loja nos dias em que algum preço mudou (PRC-10), com cabeçalho e linhas da tabela (RET-04).

    Cada série repete o último preço conhecido; antes da primeira coleta da loja, repete o primeiro.
    """
    dias = sorted({dia_de(p.coletado_em) for p in linhas})[-ultimos_dias:]
    series, centavos = [], []
    for fonte in LOJAS_COM_PRECO:
        da_loja = sorted((p for p in linhas if p.fonte == fonte), key=lambda p: (p.coletado_em, p.id))
        if not da_loja:
            continue
        valores, i, vigente = [], 0, da_loja[0].preco_centavos
        for d in dias:
            while i < len(da_loja) and dia_de(da_loja[i].coletado_em) <= d:
                vigente, i = da_loja[i].preco_centavos, i + 1
            valores.append(vigente)
        series.append((NOMES_DAS_LOJAS[fonte], [v / 100 for v in valores]))
        centavos.append(valores)
    rotulos = [d.strftime("%d/%m") for d in dias]
    tabela = [[r, *(reais(c[i]) for c in centavos)] for i, r in enumerate(rotulos)]
    return linha(series, rotulos, "Preço por loja (R$)"), ["Dia", *(n for n, _ in series)], tabela


def importar_do_snapshot(caminho):
    """Linhas de PrecoLoja a partir de `precos` do snapshot, sem rede (PRC-11); loja sem vínculo ou que já tem
    histórico fica de fora. Devolve quantas linhas criou."""
    with open(caminho, encoding="utf-8") as f:
        jogos = [j for j in json.load(f) if j.get("precos")]
    if not jogos:
        return 0
    lojas = {(v.fonte, v.external_id): v.game_id for v in JogoFonte.query.filter(JogoFonte.fonte.in_(COLETORES))}
    vinculados = {(gid, fonte) for (fonte, _), gid in lojas.items()}
    com_historico = set(db.session.query(PrecoLoja.game_id, PrecoLoja.fonte).distinct())
    criadas = 0
    for j in jogos:
        game_id = lojas.get(("steam", str(j["external_id"])))
        for fonte, preco, preco_base, desconto, coletado in j["precos"]:
            if game_id is None or (game_id, fonte) not in vinculados or (game_id, fonte) in com_historico:
                continue
            momento = datetime.fromisoformat(coletado)
            registrar(game_id, fonte, (preco, preco_base, desconto), momento)
            com_historico.add((game_id, fonte))
            criadas += 1
    db.session.commit()
    return criadas
