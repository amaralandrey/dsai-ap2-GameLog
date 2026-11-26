import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import date, datetime, time, timezone

import click
from flask import Blueprint, Response, abort, current_app, redirect, render_template, request, session
from sqlalchemy import Date, DateTime, Time, delete, func, insert, select, text
from sqlalchemy.orm import joinedload, selectinload

from . import serializadores, tempo
from .admin import audit
from .jobs import ErroPermanente, agenda_padrao, tarefa
from .models import AuditLog, Game, GameList, Review, SessaoJogo, User, db
from .privacidade.mapa import BACKUPS_MANTIDOS
from .util import admin_required, current_user

bp = Blueprint("backup", __name__, cli_group="backup")
FORMATO = "gamelog-backup"
VERSAO_FORMATO = 1
EXCLUIDAS = frozenset({"login_attempt", "limite_taxa_token"})
FILTROS = {"job": lambda t: t.c.status.in_(("pendente", "rodando", "falhou"))}
COLUNAS_VOLATEIS = {"user": frozenset({"session_version"})}
TAMANHO_MAXIMO = 20 * 1024 * 1024
PRAZO_PREVIA_S = 24 * 3600
PREVIA_RE = re.compile(r"[0-9a-f]{32}")
SEM_CHAVE = "BACKUP_KEY não configurada"
CHAVES_DE_TOPO = {"formato", "versao_formato", "gerado_em", "versao_app", "tabelas", "contagens", "assinatura"}


class ErroBackup(Exception):
    pass


def _lista_api(gl):
    return serializadores.lista(gl.name, f"/listas/{gl.id}", gl.public, [])


VISOES_API = {
    "game": (Game, (joinedload(Game.developer), selectinload(Game.genres), selectinload(Game.platforms)),
             serializadores.jogo),
    "review": (Review, (joinedload(Review.user), joinedload(Review.game)), serializadores.avaliacao),
    "sessao_jogo": (SessaoJogo, (joinedload(SessaoJogo.game), joinedload(SessaoJogo.plataforma)),
                    serializadores.sessao),
    "game_list": (GameList, (), _lista_api),
}


def chave():
    return os.environ.get("BACKUP_KEY") or None


def incluidas():
    return [t for t in db.metadata.sorted_tables if t.name not in EXCLUIDAS]


def _consulta(tabela):
    q = select(tabela)
    if tabela.name in FILTROS:
        q = q.where(FILTROS[tabela.name](tabela))
    return q.order_by(*tabela.primary_key.columns)


def _valor(valor):
    return valor.isoformat() if isinstance(valor, (date, datetime, time)) else valor


def _linhas(tabela):
    volateis = COLUNAS_VOLATEIS.get(tabela.name, frozenset())
    linhas = [{k: _valor(v) for k, v in row.items() if k not in volateis}
              for row in db.session.execute(_consulta(tabela)).mappings()]
    if tabela.name in VISOES_API and linhas:
        modelo, opcoes, serializar = VISOES_API[tabela.name]
        objetos = {o.id: o for o in modelo.query.options(*opcoes)}
        for linha in linhas:
            linha["_api"] = serializar(objetos[linha["id"]])
    return linhas


def contagens():
    return {t.name: db.session.scalar(select(func.count()).select_from(_consulta(t).subquery())) for t in incluidas()}


def gerar():
    tabelas = {t.name: _linhas(t) for t in incluidas()}
    return {
        "formato": FORMATO,
        "versao_formato": VERSAO_FORMATO,
        "gerado_em": tempo.agora().astimezone(timezone.utc).isoformat(timespec="seconds"),
        "versao_app": os.environ.get("RENDER_GIT_COMMIT") or None,
        "tabelas": tabelas,
        "contagens": {nome: len(linhas) for nome, linhas in tabelas.items()},
    }


def canonico(conteudo):
    return json.dumps(conteudo, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def assinar(conteudo, segredo):
    return hmac.new(segredo.encode(), canonico(conteudo), hashlib.sha256).hexdigest()


def exportar(segredo):
    conteudo = gerar()
    return {**conteudo, "assinatura": assinar(conteudo, segredo)}


def conferir(dados, segredo):
    if (not isinstance(dados, dict) or set(dados) != CHAVES_DE_TOPO or not isinstance(dados["tabelas"], dict)
            or not isinstance(dados["contagens"], dict) or not isinstance(dados["assinatura"], str)
            or not all(isinstance(linhas, list) for linhas in dados["tabelas"].values())):
        raise ErroBackup("arquivo malformado")
    if dados["formato"] != FORMATO:
        raise ErroBackup("formato desconhecido")
    if dados["versao_formato"] != VERSAO_FORMATO:
        raise ErroBackup(f"versão do formato não suportada: {dados['versao_formato']}")
    conteudo = {k: v for k, v in dados.items() if k != "assinatura"}
    if not hmac.compare_digest(assinar(conteudo, segredo), dados["assinatura"]):
        raise ErroBackup("assinatura inválida")


def previa(dados):
    return len(dados["tabelas"]), sum(len(linhas) for linhas in dados["tabelas"].values())


def _ler(coluna, valor):
    if valor is None or not isinstance(valor, str):
        return valor
    if isinstance(coluna.type, DateTime):
        return datetime.fromisoformat(valor)
    if isinstance(coluna.type, Date):
        return date.fromisoformat(valor)
    if isinstance(coluna.type, Time):
        return time.fromisoformat(valor)
    return valor


def _obrigatoria_sem_padrao(coluna):
    return (not coluna.nullable and coluna.default is None and coluna.server_default is None
            and not (coluna.primary_key and coluna.autoincrement in (True, "auto")))


def _preparar(tabela, linhas, avisar, avisadas):
    colunas, volateis = tabela.columns, COLUNAS_VOLATEIS.get(tabela.name, frozenset())
    grupos = {}
    for linha in linhas:
        for nome in linha:
            if not nome.startswith("_") and nome not in colunas and (tabela.name, nome) not in avisadas:
                avisadas.add((tabela.name, nome))
                avisar(f"coluna {tabela.name}.{nome} ignorada")
        valores = {c.name: _ler(c, linha[c.name]) for c in colunas if c.name in linha}
        for c in colunas:
            if c.name in volateis:
                valores[c.name] = secrets.randbelow(2 ** 31)
            elif c.name not in valores and _obrigatoria_sem_padrao(c):
                raise ErroBackup(f"coluna obrigatória ausente: {tabela.name}.{c.name}")
        grupos.setdefault(tuple(valores), []).append(valores)
    return grupos.values()


def restaurar(dados, avisar):
    db.session.execute(text("PRAGMA defer_foreign_keys = ON"))
    nomes = {t.name for t in db.metadata.sorted_tables}
    for nome in dados["tabelas"]:
        if nome not in nomes and nome not in EXCLUIDAS:
            avisar(f"tabela {nome} ignorada")
    for tabela in reversed(db.metadata.sorted_tables):
        db.session.execute(delete(tabela))
    avisadas, tabelas, linhas = set(), 0, 0
    for tabela in incluidas():
        if tabela.name not in dados["tabelas"]:
            continue
        tabelas += 1
        for grupo in _preparar(tabela, dados["tabelas"][tabela.name], avisar, avisadas):
            db.session.execute(insert(tabela), grupo)
            linhas += len(grupo)
    db.session.flush()
    atuais = contagens()
    if any(atuais[nome] != dados["contagens"].get(nome) for nome in dados["tabelas"] if nome in atuais):
        raise ErroBackup("contagens não conferem")
    return tabelas, linhas


def _sair(motivo):
    click.echo(f"erro={motivo}")
    raise click.exceptions.Exit(1)


def _chave_ou_sair():
    segredo = chave()
    if segredo is None:
        _sair(SEM_CHAVE)
    return segredo


def gravar(caminho, dados):
    temporario = f"{caminho}.tmp"
    with open(temporario, "w", encoding="utf-8") as arquivo:
        json.dump(dados, arquivo, ensure_ascii=False)
    os.replace(temporario, caminho)


def ler_arquivo(caminho):
    try:
        with open(caminho, encoding="utf-8") as arquivo:
            return json.load(arquivo)
    except FileNotFoundError:
        raise ErroBackup("arquivo não encontrado") from None
    except (ValueError, UnicodeDecodeError):
        raise ErroBackup("arquivo malformado") from None


def motivo(exc):
    return str(exc) if isinstance(exc, ErroBackup) else f"restauração desfeita: {type(exc).__name__}"


def restaurar_e_gravar(dados, avisar):
    try:
        resultado = restaurar(dados, avisar)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        raise ErroBackup(motivo(exc)) from None
    return resultado


def _aviso(texto):
    click.echo(f"aviso={texto}", err=True)


@bp.cli.command("exportar", help="Grava o backup assinado do banco num arquivo JSON (BACKUP_KEY obrigatória).")
@click.option("--saida", required=True, type=click.Path(dir_okay=False))
def exportar_cmd(saida):
    dados = exportar(_chave_ou_sair())
    gravar(saida, dados)
    click.echo(f"tabelas={len(dados['tabelas'])} linhas={sum(dados['contagens'].values())} arquivo={saida}")


@bp.cli.command("importar", help="Confere e mostra o backup; com --confirmar, restaura o banco numa transação.")
@click.option("--arquivo", required=True, type=click.Path(dir_okay=False))
@click.option("--confirmar", is_flag=True)
def importar_cmd(arquivo, confirmar):
    segredo = _chave_ou_sair()
    try:
        dados = ler_arquivo(arquivo)
        conferir(dados, segredo)
        if not confirmar:
            tabelas, linhas = previa(dados)
            click.echo(f"tabelas={tabelas} linhas={linhas}")
            return
        tabelas, linhas = restaurar_e_gravar(dados, _aviso)
    except ErroBackup as exc:
        _sair(exc)
    click.echo(f"restaurado tabelas={tabelas} linhas={linhas}")


def backup_dir():
    pasta = os.environ.get("BACKUP_DIR") or os.path.join(current_app.instance_path, "backups")
    os.makedirs(pasta, exist_ok=True)
    return pasta


def _resumo(tabelas, linhas, resultado="ok"):
    return f"tabelas={tabelas} linhas={linhas} resultado={resultado}"


def _painel(erro=None, status=200):
    return render_template("admin/backup.html", contagens=contagens(), tem_chave=chave() is not None,
                           erro=erro), status


def _tela_previa(previa_id, dados, erro=None, status=200):
    tabelas, linhas = previa(dados)
    return render_template("admin/backup_previa.html", previa=previa_id, tabelas=tabelas, linhas=linhas,
                           gerado_em=dados["gerado_em"], erro=erro), status


@bp.route("/admin/backup")
@admin_required
def painel():
    return _painel()


@bp.route("/admin/backup/baixar", methods=["POST"])
@admin_required
def baixar():
    segredo = chave()
    if segredo is None:
        return _painel(SEM_CHAVE, 400)
    dados = exportar(segredo)
    audit("backup.exportar", _resumo(len(dados["tabelas"]), sum(dados["contagens"].values())))
    db.session.commit()
    nome = f"gamelog-backup-{tempo.agora().strftime('%Y%m%d-%H%M')}.json"
    return Response(json.dumps(dados, ensure_ascii=False), mimetype="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{nome}"', "Cache-Control": "no-store"})


def _ler_envio():
    arquivo = request.files.get("arquivo")
    if arquivo is None:
        raise ErroBackup("envie o arquivo do backup")
    try:
        return json.loads(arquivo.read())
    except (ValueError, UnicodeDecodeError):
        raise ErroBackup("arquivo malformado") from None


@bp.route("/admin/backup/previa", methods=["POST"])
@admin_required
def enviar_previa():
    if (request.content_length or 0) > TAMANHO_MAXIMO:
        abort(413)
    segredo = chave()
    if segredo is None:
        return _painel(SEM_CHAVE, 400)
    try:
        dados = _ler_envio()
        conferir(dados, segredo)
    except ErroBackup as exc:
        audit("backup.previa", f"resultado=erro motivo={exc}")
        db.session.commit()
        return _painel(str(exc), 400)
    previa_id = secrets.token_hex(16)
    gravar(os.path.join(backup_dir(), f"previa-{previa_id}.json"), dados)
    audit("backup.previa", _resumo(*previa(dados)))
    db.session.commit()
    return _tela_previa(previa_id, dados)


@bp.route("/admin/backup/restaurar", methods=["POST"])
@admin_required
def restaurar_rota():
    previa_id = request.form.get("previa", "")
    if not PREVIA_RE.fullmatch(previa_id):
        abort(400)
    segredo = chave()
    if segredo is None:
        return _painel(SEM_CHAVE, 400)
    caminho = os.path.join(backup_dir(), f"previa-{previa_id}.json")
    try:
        dados = ler_arquivo(caminho)
        conferir(dados, segredo)
    except ErroBackup as exc:
        return _painel(str(exc), 400)
    if not request.form.get("confirmar"):
        return _tela_previa(previa_id, dados, "Marque a confirmação para restaurar.", 400)
    nome, chave_nome = current_user().username, current_user().username_lower
    try:
        tabelas, linhas = restaurar(dados, current_app.logger.warning)
        admin = User.query.filter_by(username_lower=chave_nome).first()
        db.session.add(AuditLog(admin_id=admin.id if admin else None, action="backup.restaurar",
                                detail=f"admin={nome} " + _resumo(tabelas, linhas)))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        audit("backup.restaurar", f"admin={nome} resultado=erro motivo={motivo(exc)}"[:300])
        db.session.commit()
        return _painel(motivo(exc), 400)
    os.remove(caminho)
    if admin is None:
        session.clear()
        return redirect("/entrar")
    from .auth import start_session

    start_session(admin)
    return redirect("/admin/backup")


def rotacionar(pasta, manter=BACKUPS_MANTIDOS):
    arquivos = sorted(n for n in os.listdir(pasta) if n.startswith("gamelog-backup-") and n.endswith(".json"))
    limite = tempo.agora().timestamp() - PRAZO_PREVIA_S
    previas = [n for n in os.listdir(pasta)
               if n.startswith("previa-") and os.path.getmtime(os.path.join(pasta, n)) < limite]
    velhos = (arquivos[:-manter] if len(arquivos) > manter else []) + previas
    for nome in velhos:
        os.remove(os.path.join(pasta, nome))
    return len(velhos)


@tarefa("backup")
def backup_agendado():
    segredo = chave()
    if segredo is None:
        raise ErroPermanente(SEM_CHAVE)
    pasta = backup_dir()
    nome = f"gamelog-backup-{tempo.agora().strftime('%Y%m%d-%H%M%S')}.json"
    gravar(os.path.join(pasta, nome), exportar(segredo))
    rotacionar(pasta)


agenda_padrao("backup", "0 3 * * *", "backup")


def obter_inicial(origem):
    if "://" not in origem:
        return ler_arquivo(origem)
    if not origem.startswith("https://"):
        raise ErroBackup("URL precisa ser https")
    from .fontes import base

    try:
        _, corpo = base.baixar(origem, TAMANHO_MAXIMO)
    except Exception as exc:
        raise ErroBackup(f"download falhou: {type(exc).__name__}") from None
    try:
        return json.loads(corpo)
    except (ValueError, UnicodeDecodeError):
        raise ErroBackup("arquivo malformado") from None


@bp.cli.command("restaurar-inicial", help="Na subida, restaura BACKUP_RESTAURAR_DE se o banco não tem usuários.")
def restaurar_inicial_cmd():
    origem = os.environ.get("BACKUP_RESTAURAR_DE")
    if not origem or db.session.query(User.id).first() is not None:
        click.echo("restaurado=0")
        return
    try:
        segredo = chave()
        if segredo is None:
            raise ErroBackup(SEM_CHAVE)
        dados = obter_inicial(origem)
        conferir(dados, segredo)
        tabelas, linhas = restaurar_e_gravar(dados, _aviso)
    except ErroBackup as exc:
        click.echo(f"erro={exc}")
        return
    click.echo(f"restaurado=1 tabelas={tabelas} linhas={linhas}")
