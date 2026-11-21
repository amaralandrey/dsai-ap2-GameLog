"""Importação de horas e do diário (HRS-01..12): horas da Steam pela Web API e CSV com prévia antes de gravar.

A chave da Steam vem só de `STEAM_API_KEY` e nunca sai deste módulo: não vai para o banco, para templates,
para mensagens nem para logs (HRS-02).
"""
import csv
import io
import json
import os
import re
from collections import defaultdict
from datetime import date, timedelta

from flask import Blueprint, Response, abort, flash, redirect, render_template, request
from sqlalchemy import func

from . import diario
from .fontes import base
from .gamificacao import emitir_evento
from .models import Activity, Game, JogoFonte, PreviaImportacao, SessaoJogo, db, now
from .tempo import hoje
from .util import current_user, fold, login_required

bp = Blueprint("diario_import", __name__)
URL_BIBLIOTECA = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
STEAM_ID_RE = re.compile(r"^7656119\d{10}$")
PERFIL_RE = re.compile(r"^https?://steamcommunity\.com/profiles/(\d{17})/?$")
MAX_BYTES, MAX_LINHAS = 1024 * 1024, 5000
VALIDADE_PREVIA = timedelta(hours=1)
OBRIGATORIAS = ("data", "jogo", "duracao")
COLUNAS = ("data", "jogo", "duracao", "inicio", "plataforma", "zerei", "anotacao", "origem")
SIM, NAO = {"sim", "true", "1"}, {"nao", "false", "0", ""}

INDISPONIVEL = "Importação da Steam indisponível neste servidor"
PERFIL_PRIVADO = "Não foi possível ler a biblioteca: o perfil da Steam precisa ser público"
FALHA = "Não foi possível falar com a Steam agora. Tente de novo mais tarde."


class ErroImportacao(Exception):
    pass


def chave_steam():
    return os.environ.get("STEAM_API_KEY") or None


def ler_steam_id(texto):
    """SteamID64 ou URL do perfil (`/profiles/<id64>`); devolve o id, "" para remover ou None se inválido."""
    texto = (texto or "").strip()
    if not texto:
        return ""
    m = PERFIL_RE.match(texto)
    candidato = m.group(1) if m else texto
    return candidato if STEAM_ID_RE.match(candidato) else None


# ---------- horas da Steam (HRS-02..05) ----------
def biblioteca_steam(steam_id):
    """{appid: minutos} da biblioteca; levanta ErroImportacao com a mensagem para o usuário."""
    chave = chave_steam()
    if not chave:
        raise ErroImportacao(INDISPONIVEL)
    params = {"key": chave, "steamid": steam_id, "include_appinfo": 1, "include_played_free_games": 1}
    try:
        dados = base.get_json(URL_BIBLIOTECA, params)
    except Exception:  # rede, HTTP (inclusive 429) ou JSON inválido: mensagem genérica, sem a URL com a chave
        raise ErroImportacao(FALHA) from None
    resposta = dados.get("response") if isinstance(dados, dict) else None
    jogos = resposta.get("games") if isinstance(resposta, dict) else None
    if not jogos:
        raise ErroImportacao(PERFIL_PRIVADO)
    try:
        return {str(j["appid"]): int(j.get("playtime_forever") or 0) for j in jogos if "appid" in j}
    except (TypeError, ValueError, AttributeError):  # resposta malformada: como falha da Steam (HRS-05)
        raise ErroImportacao(FALHA) from None


def jogos_do_catalogo(appids):
    """{appid: Game} pelo vínculo `steam` (FNT-04) ou pelo jogo importado da Steam, só jogos visíveis."""
    if not appids:
        return {}
    achados = {}
    por_vinculo = (
        db.session.query(JogoFonte.external_id, Game)
        .join(Game, Game.id == JogoFonte.game_id)
        .filter(JogoFonte.fonte == "steam", JogoFonte.external_id.in_(appids), Game.removed_at.is_(None))
    )
    for appid, game in por_vinculo:
        achados[appid] = game
    faltam = [a for a in appids if a not in achados]
    if faltam:
        for game in Game.query.filter(Game.source == "steam", Game.external_id.in_(faltam), Game.removed_at.is_(None)):
            achados[game.external_id] = game
    return achados


def registrar_importacao(user_id, sessoes):
    """Uma atividade por importação com sessões novas (HRS-10)."""
    if sessoes:
        db.session.add(Activity(user_id=user_id, kind="importacao", valor=sessoes))


def importar_steam(user):
    """Cria as sessões de saldo da Steam e devolve as contagens (HRS-03, HRS-04)."""
    biblioteca = {a: m for a, m in biblioteca_steam(user.steam_id).items() if m > 0}
    catalogo = jogos_do_catalogo(list(biblioteca))
    ja = dict(
        db.session.query(SessaoJogo.game_id, func.sum(SessaoJogo.duracao_min))
        .filter_by(user_id=user.id, origem="steam")
        .group_by(SessaoJogo.game_id)
        .all()
    )
    contagem = {"jogos": len(biblioteca), "sessoes": 0, "sem_novidade": 0, "fora_do_catalogo": 0}
    for appid, minutos in biblioteca.items():
        game = catalogo.get(appid)
        if game is None:
            contagem["fora_do_catalogo"] += 1
            continue
        diferenca = minutos - (ja.get(game.id) or 0)
        if diferenca <= 0:
            contagem["sem_novidade"] += 1
            continue
        diario.gravar_sessao(user, game, {"jogada_em": hoje(), "duracao_min": diferenca}, origem="steam")
        contagem["sessoes"] += 1
    registrar_importacao(user.id, contagem["sessoes"])
    db.session.commit()
    if contagem["sessoes"]:
        emitir_evento(user.id, "importacao", origem="steam")  # GAM-03
    return contagem


# ---------- CSV (HRS-06..08, HRS-11) ----------
def ler_csv(conteudo):
    """Linhas do CSV como dicionários de COLUNAS; levanta ErroImportacao quando o arquivo é recusado."""
    if len(conteudo) > MAX_BYTES:
        raise ErroImportacao("O arquivo passa de 1 MB.")
    try:
        texto = conteudo.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ErroImportacao("O arquivo não é um CSV em UTF-8.") from None
    if "\x00" in texto:
        raise ErroImportacao("O arquivo não é um CSV em UTF-8.")
    cabecalho = texto.split("\n", 1)[0]
    sep = ";" if cabecalho.count(";") > cabecalho.count(",") else ","
    try:
        leitor = csv.DictReader(io.StringIO(texto, newline=""), delimiter=sep)
        campos = [fold(c).strip() for c in (leitor.fieldnames or [])]
        if any(c not in campos for c in OBRIGATORIAS):
            raise ErroImportacao("Faltam colunas obrigatórias: data, jogo e duracao.")
        linhas = []
        for bruta in leitor:
            linhas.append({fold(k or "").strip(): (v or "").strip() for k, v in bruta.items() if k is not None})
            if len(linhas) > MAX_LINHAS:
                raise ErroImportacao(f"O arquivo passa de {MAX_LINHAS} linhas.")
    except csv.Error:
        raise ErroImportacao("O arquivo não é um CSV válido.") from None
    return linhas


def achar_jogo(texto):
    """Jogo visível pelo slug ou pelo título exato, sem diferenciar maiúsculas."""
    texto = texto.strip()
    if not texto:
        return None
    jogo = Game.query.filter(Game.slug == texto.lower(), Game.removed_at.is_(None)).first()
    return jogo or Game.query.filter(func.lower(Game.title) == texto.lower(), Game.removed_at.is_(None)).first()


def _interpretar(linha, achar=achar_jogo):
    """(dados da sessão, jogo) ou levanta ErroImportacao com o motivo da linha."""
    jogada_em = diario.ler_data(linha.get("data"))
    if jogada_em is None:
        raise ErroImportacao("data inválida")
    jogo = achar(linha.get("jogo", ""))
    if jogo is None:
        raise ErroImportacao("jogo não encontrado")
    duracao = diario.ler_duracao(linha.get("duracao"))
    if duracao is None:
        raise ErroImportacao("duração inválida")
    inicio = None
    if linha.get("inicio"):
        inicio = diario.ler_hora(linha["inicio"])
        if inicio is None:
            raise ErroImportacao("início inválido (use HH:MM)")
    plataforma_id = None
    if linha.get("plataforma"):
        p = next((p for p in jogo.platforms if fold(p.name) == fold(linha["plataforma"])), None)
        if p is None:
            raise ErroImportacao("plataforma não é do jogo")
        plataforma_id = p.id
    zerei = fold(linha.get("zerei", ""))
    if zerei not in SIM | NAO:
        raise ErroImportacao("zerei deve ser sim ou não")
    anotacao = com_formula(linha.get("anotacao", ""))
    if len(anotacao) > diario.MAX_ANOTACAO:
        raise ErroImportacao("anotação passa de 1000 caracteres")
    return {"jogada_em": jogada_em, "duracao_min": duracao, "inicio": inicio, "plataforma_id": plataforma_id,
            "zerou": zerei in SIM, "anotacao": anotacao, "spoiler": False}, jogo


def sessao_existe(user_id, game_id, dados):
    return SessaoJogo.query.filter_by(user_id=user_id, game_id=game_id, jogada_em=dados["jogada_em"],
                                      inicio=dados["inicio"], duracao_min=dados["duracao_min"]).first() is not None


def _sessoes_entre(user_id, datas):
    """Sessões do usuário do dia anterior à primeira data até o dia seguinte à última, numa consulta."""
    if not datas:
        return []
    de, ate = _tres_dias(min(datas))[0], _tres_dias(max(datas))[-1]
    return SessaoJogo.query.filter(SessaoJogo.user_id == user_id, SessaoJogo.jogada_em.between(de, ate)).all()


def _tres_dias(dia):
    """O dia anterior, o dia e o seguinte, sem passar dos limites de `date`."""
    o = dia.toordinal()
    return [date.fromordinal(x) for x in (o - 1, o, o + 1) if 1 <= x <= date.max.toordinal()]


def classificar(user_id, linhas):
    """Cada linha vira `nova`, `duplicada`, `erro` (com motivo) ou `steam` (ignorada, HRS-07, HRS-08).

    Jogos e sessões são carregados uma vez para o arquivo inteiro; a regra de HRS-09 roda em memória.
    """
    jogos = {}

    def achar(texto):
        chave = texto.strip().lower()
        if chave not in jogos:
            jogos[chave] = achar_jogo(texto)
        return jogos[chave]

    lidas = []
    for n, linha in enumerate(linhas, start=2):  # a linha 1 é o cabeçalho
        item = {"linha": n, "jogo": linha.get("jogo", ""), "data": linha.get("data", ""),
                "duracao": linha.get("duracao", "")}
        if fold(linha.get("origem", "")) == "steam":
            lidas.append(({**item, "classe": "steam", "motivo": "horas da Steam só entram pela importação da Steam"},))
            continue
        try:
            lidas.append((item, *_interpretar(linha, achar)))
        except ErroImportacao as exc:
            lidas.append(({**item, "classe": "erro", "motivo": str(exc)},))

    existentes = _sessoes_entre(user_id, [x[1]["jogada_em"] for x in lidas if len(x) == 3])
    vistas = {(s.game_id, s.jogada_em, s.inicio, s.duracao_min) for s in existentes}
    por_dia = defaultdict(list)
    for s in existentes:
        if s.origem in ("manual", "csv"):
            por_dia[s.jogada_em].append((s.jogada_em, s.duracao_min, s.inicio))

    resultado = []
    for lida in lidas:
        if len(lida) == 1:
            resultado.append(lida[0])
            continue
        item, dados, jogo = lida
        dia = dados["jogada_em"]
        chave = (jogo.id, dia, dados["inicio"], dados["duracao_min"])
        if chave in vistas:
            resultado.append({**item, "classe": "duplicada", "motivo": ""})
            continue
        outras = [s for vizinho in _tres_dias(dia) for s in por_dia[vizinho]]
        problema = diario.problema_entre(dia, dados["duracao_min"], dados["inicio"], outras)
        if problema:
            resultado.append({**item, "classe": "erro", "motivo": problema})
            continue
        vistas.add(chave)
        por_dia[dia].append((dia, dados["duracao_min"], dados["inicio"]))
        resultado.append({**item, "classe": "nova", "motivo": "", "game_id": jogo.id, "dados": _para_json(dados)})
    return resultado


def _para_json(dados):
    return {**dados, "jogada_em": dados["jogada_em"].isoformat(),
            "inicio": dados["inicio"].strftime("%H:%M") if dados["inicio"] else None}


def _de_json(dados):
    return {**dados, "jogada_em": diario.ler_data(dados["jogada_em"]),
            "inicio": diario.ler_hora(dados["inicio"]) if dados["inicio"] else None}


def totais(linhas):
    contagem = {"nova": 0, "duplicada": 0, "erro": 0, "steam": 0}
    for linha in linhas:
        contagem[linha["classe"]] += 1
    return contagem


def previa_do_dono(pid):
    """Prévia do próprio usuário e ainda válida; outra pessoa recebe 404 (HRS-07)."""
    previa = db.session.get(PreviaImportacao, pid)
    if previa is None or previa.user_id != current_user().id or previa.created_at < now() - VALIDADE_PREVIA:
        abort(404)
    return previa


def confirmar(previa, user):
    """Grava as linhas `nova` numa única transação e devolve (criadas, puladas) (HRS-07, HRS-08).

    O diário pode ter mudado depois da prévia (outra prévia confirmada, sessão manual), então a duplicata e
    HRS-09 são conferidas de novo; o flush de gravar_sessao deixa as linhas anteriores visíveis.
    """
    criadas = puladas = 0
    for linha in json.loads(previa.linhas):
        if linha["classe"] != "nova":
            continue
        game = db.session.get(Game, linha["game_id"])
        dados = _de_json(linha["dados"])
        if (game is None or game.removed_at is not None or sessao_existe(user.id, game.id, dados)
                or diario.consistencia(user.id, dados["jogada_em"], dados["duracao_min"], dados["inicio"])):
            puladas += 1
            continue
        diario.gravar_sessao(user, game, dados, origem="csv")
        criadas += 1
    registrar_importacao(user.id, criadas)
    db.session.delete(previa)
    db.session.commit()
    if criadas:
        emitir_evento(user.id, "importacao", origem="csv")
    return criadas, puladas


INICIO_DE_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def sem_formula(texto):
    """Prefixa com aspas simples o texto que viraria fórmula na planilha."""
    texto = str(texto)
    return "'" + texto if texto[:1] in INICIO_DE_FORMULA else texto


def com_formula(texto):
    """Desfaz sem_formula, para o arquivo exportado voltar igual (HRS-11)."""
    return texto[1:] if texto[:1] == "'" and texto[1:2] in INICIO_DE_FORMULA else texto


def exportar_csv(user_id):
    """O diário inteiro no formato de importação, com o jogo como slug e a origem (HRS-11)."""
    saida = io.StringIO()
    escritor = csv.writer(saida)
    escritor.writerow(COLUNAS)
    sessoes = (SessaoJogo.query.filter_by(user_id=user_id).join(Game, Game.id == SessaoJogo.game_id)
               .order_by(SessaoJogo.jogada_em, SessaoJogo.id))
    for s in sessoes:
        escritor.writerow([
            s.jogada_em.isoformat(), s.game.slug, s.duracao_min, s.inicio.strftime("%H:%M") if s.inicio else "",
            s.plataforma.name if s.plataforma else "", "sim" if s.zerou else "nao", sem_formula(s.anotacao), s.origem,
        ])
    return saida.getvalue()


# ---------- rotas ----------
@bp.route("/diario/importar", methods=["GET", "POST"])
@login_required
def importar():
    me = current_user()
    if request.method == "POST":
        arquivo = request.files.get("arquivo")
        try:
            if arquivo is None or not arquivo.filename:
                raise ErroImportacao("Escolha um arquivo CSV.")
            linhas = ler_csv(arquivo.read(MAX_BYTES + 1))
        except ErroImportacao as exc:
            return render_template("diario/importar.html", erro=str(exc), steam=bool(chave_steam())), 400
        classificadas = classificar(me.id, linhas)
        previa = PreviaImportacao(user_id=me.id, linhas=json.dumps(classificadas, ensure_ascii=False))
        db.session.add(previa)
        db.session.commit()
        return redirect(f"/diario/importar/{previa.id}")
    return render_template("diario/importar.html", erro=None, steam=bool(chave_steam()))


@bp.route("/diario/importar/<int:pid>")
@login_required
def ver_previa(pid):
    previa = previa_do_dono(pid)
    linhas = json.loads(previa.linhas)
    return render_template("diario/previa.html", previa=previa, linhas=linhas, totais=totais(linhas))


@bp.route("/diario/importar/<int:pid>/confirmar", methods=["POST"])
@login_required
def confirmar_previa(pid):
    criadas, puladas = confirmar(previa_do_dono(pid), current_user())
    aviso = f" Ficaram de fora {puladas}, que viraram duplicadas ou conflitantes desde a prévia." if puladas else ""
    flash(f"Importação concluída: {criadas} sessões gravadas no diário.{aviso}", "ok")
    return redirect("/diario")


@bp.route("/diario/importar/steam", methods=["POST"])
@login_required
def importar_da_steam():
    me = current_user()
    if not chave_steam():
        flash(INDISPONIVEL, "erro")
        return redirect("/diario/importar")
    if not me.steam_id:
        flash("Vincule sua conta da Steam no perfil antes de importar.", "erro")
        return redirect(f"/u/{me.username}/editar")
    try:
        c = importar_steam(me)
    except ErroImportacao as exc:
        db.session.rollback()
        flash(str(exc), "erro")
        return redirect("/diario/importar")
    flash(f"jogos={c['jogos']} sessoes={c['sessoes']} sem_novidade={c['sem_novidade']} "
          f"fora_do_catalogo={c['fora_do_catalogo']}", "ok")
    return redirect("/diario/importar")


@bp.route("/diario/exportar.csv")
@login_required
def exportar():
    me = current_user()
    return Response(exportar_csv(me.id), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="diario-{me.username}.csv"'})
