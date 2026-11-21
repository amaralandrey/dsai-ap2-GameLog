"""Fila persistente de jobs sobre o SQLite (JOB-01..12), sem broker e sem agendador dentro do Flask.

Quem executa é `flask jobs rodar` ou `POST /jobs/ciclo`, chamados de fora; a trava por UPDATE condicional
garante que dois executores (ou os 2 workers do gunicorn) nunca rodem o mesmo job ao mesmo tempo.
"""
import hmac
import json
import os
import signal
import socket
import time
from datetime import timedelta

import click
from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request
from sqlalchemy.exc import IntegrityError

from . import tempo
from .cron import proxima_execucao
from .models import JOB_STATUS, Agendamento, AuditLog, Job, db
from .util import admin_required, current_user, paginate

bp = Blueprint("jobs", __name__, cli_group="jobs")
TAREFAS = {}
AGENDAS_PADRAO = {}  # nome -> (cron, tipo, args, ativo), declarados por quem registra a tarefa (JOB-09)
ESPERA_BASE, ESPERA_MAX = timedelta(seconds=30), timedelta(hours=6)
ABANDONO = timedelta(minutes=15)
MAX_ERRO = 2000


class ErroPermanente(Exception):
    """A tarefa não adianta repetir: o job vai direto a `morto` (JOB-06)."""


def tarefa(nome):
    """Registra a função como tarefa `nome`; ela recebe os `args` do job como parâmetros nomeados (JOB-03)."""
    def registrar(funcao):
        if nome in TAREFAS:
            raise RuntimeError(f"tarefa registrada duas vezes: {nome}")
        TAREFAS[nome] = funcao
        return funcao
    return registrar


def agenda_padrao(nome, cron, tipo, ativo=True, **args):
    """Declara um agendamento padrão que `flask jobs agendar` cria (JOB-09)."""
    AGENDAS_PADRAO[nome] = (cron, tipo, json.dumps(args, sort_keys=True), ativo)


def _abertos(chave):
    return Job.query.filter(Job.chave_unica == chave, Job.status.in_(("pendente", "rodando"))).first()


def enfileirar(tipo, executar_em=None, chave_unica=None, **args):
    """Cria o job pendente e o devolve; com a mesma `chave_unica` aberta, devolve o que já existe (JOB-01, JOB-02)."""
    if tipo not in TAREFAS:
        raise ValueError(f"tarefa desconhecida: {tipo}")
    try:
        texto = json.dumps(args, sort_keys=True)
    except (TypeError, ValueError):
        raise ValueError("os args do job precisam virar JSON") from None
    if chave_unica and (existente := _abertos(chave_unica)):
        return existente
    agora = tempo.agora_utc()
    job = Job(tipo=tipo, args=texto, executar_em=executar_em or agora, chave_unica=chave_unica, criado_em=agora)
    try:
        with db.session.begin_nested():
            db.session.add(job)
    except IntegrityError:  # outro executor gravou a mesma chave entre a consulta e o insert
        return _abertos(chave_unica)
    db.session.commit()
    return job


# ---------- execução (JOB-04..06) ----------
def identificacao():
    return f"{socket.gethostname()}:{os.getpid()}"[:120]


def _travar(job_id, executor):
    """Só quem muda a linha de `pendente` para `rodando` executa o job (JOB-05)."""
    mudou = (Job.query.filter(Job.id == job_id, Job.status == "pendente")
             .update({"status": "rodando", "travado_por": executor, "travado_em": tempo.agora_utc()},
                     synchronize_session=False))
    db.session.commit()
    return mudou == 1


def espera(tentativas):
    """30 s × 2^(tentativas−1), com teto de 6 h."""
    return min(ESPERA_BASE * 2 ** (tentativas - 1), ESPERA_MAX)


def _falhar(job_id, exc):
    job = db.session.get(Job, job_id)
    job.tentativas += 1
    job.ultimo_erro = f"{type(exc).__name__}: {exc}"[:MAX_ERRO]  # sem traceback nem valores de variáveis
    job.travado_por = job.travado_em = None
    if isinstance(exc, ErroPermanente) or job.tentativas >= job.max_tentativas:
        job.status, job.concluido_em = "morto", tempo.agora_utc()
    else:
        job.status, job.executar_em = "pendente", tempo.agora_utc() + espera(job.tentativas)
    db.session.commit()
    return job.status


def executar(job_id, executor=None):
    """Trava e roda um job; devolve `ok`, `pendente` (vai tentar de novo), `morto` ou None se outro pegou."""
    if not _travar(job_id, executor or identificacao()):
        return None
    job = db.session.get(Job, job_id)
    try:
        if job.tipo not in TAREFAS:  # tarefa removida do código depois de enfileirada
            raise ErroPermanente(f"tarefa desconhecida: {job.tipo}")
        TAREFAS[job.tipo](**json.loads(job.args))
        job.status, job.concluido_em = "ok", tempo.agora_utc()
        job.travado_por = job.travado_em = None
        db.session.commit()  # o que a tarefa gravou e o "ok" entram juntos; um erro aqui também é falha
    except Exception as exc:
        db.session.rollback()  # desfaz o que a tarefa gravou (JOB-06)
        return _falhar(job_id, exc)
    return "ok"


def liberar_abandonados():
    """Job `rodando` há mais de 15 minutos volta para `pendente`, contando uma tentativa (JOB-05)."""
    limite = tempo.agora_utc() - ABANDONO
    abandonados = Job.query.filter(Job.status == "rodando", Job.travado_em < limite).all()
    for job in abandonados:
        job.tentativas += 1
        job.travado_por = job.travado_em = None
        job.ultimo_erro = "abandonado: rodando há mais de 15 minutos"
        if job.tentativas >= job.max_tentativas:
            job.status, job.concluido_em = "morto", tempo.agora_utc()
        else:
            job.status = "pendente"
    db.session.commit()
    return len(abandonados)


# ---------- agendamentos (JOB-08) ----------
def _marcar(ag, antes):
    """Avança `ultima_execucao` só se ninguém avançou antes: o executor que conseguir enfileira."""
    igual = Agendamento.ultima_execucao.is_(None) if antes is None else Agendamento.ultima_execucao == antes
    mudou = (Agendamento.query.filter(Agendamento.id == ag.id, igual)
             .update({"ultima_execucao": tempo.agora_utc()}, synchronize_session=False))
    db.session.commit()
    return mudou == 1


def enfileirar_agendados():
    """Um job por agendamento vencido; execuções perdidas viram um job só. Devolve quantos enfileirou."""
    enfileirados = 0
    for ag in Agendamento.query.filter_by(ativo=True).order_by(Agendamento.id).all():
        antes = ag.ultima_execucao
        if antes is None:  # recém-criado: conta a partir de agora
            _marcar(ag, None)
            continue
        prevista = proxima_execucao(ag.cron, tempo.para_belem(antes))
        if prevista > tempo.agora() or ag.tipo not in TAREFAS or not _marcar(ag, antes):
            continue
        enfileirar(ag.tipo, chave_unica=f"agenda:{ag.nome}:{prevista.isoformat()}", **json.loads(ag.args))
        enfileirados += 1
    return enfileirados


def ciclo(limite=50, executor=None):
    """Um ciclo do executor; devolve a contagem que `jobs rodar` imprime (JOB-04, JOB-08)."""
    enfileirar_agendados()
    liberar_abandonados()
    ids = [i for (i,) in db.session.query(Job.id)
           .filter(Job.status == "pendente", Job.executar_em <= tempo.agora_utc())
           .order_by(Job.executar_em, Job.id).limit(limite)]
    conta = {"executados": 0, "ok": 0, "falhas": 0, "mortos": 0}
    for job_id in ids:
        resultado = executar(job_id, executor)
        if resultado is None:
            continue
        conta["executados"] += 1
        conta[{"ok": "ok", "pendente": "falhas", "morto": "mortos"}[resultado]] += 1
    return conta


# ---------- comandos (JOB-04, JOB-09) ----------
def agendar_padrao():
    """Cria os agendamentos padrão e atualiza cron, tipo e args dos que mudaram; `ativo` fica com o admin."""
    criados = atualizados = 0
    existentes = {a.nome: a for a in Agendamento.query}
    for nome, (cron, tipo, args, ativo) in sorted(AGENDAS_PADRAO.items()):
        ag = existentes.get(nome)
        if ag is None:
            db.session.add(Agendamento(nome=nome, cron=cron, tipo=tipo, args=args, ativo=ativo))
            criados += 1
        elif (ag.cron, ag.tipo, ag.args) != (cron, tipo, args):
            ag.cron, ag.tipo, ag.args = cron, tipo, args
            atualizados += 1
    db.session.commit()
    return criados, atualizados


@bp.cli.command("agendar")
def agendar_cmd():
    """Cria ou atualiza os agendamentos padrão, sem duplicar."""
    criados, atualizados = agendar_padrao()
    click.echo(f"criados={criados} atualizados={atualizados}")


def _linha(conta):
    return " ".join(f"{k}={conta[k]}" for k in ("executados", "ok", "falhas", "mortos"))


@bp.cli.command("rodar")
@click.option("--uma-vez", is_flag=True, help="um ciclo só, para o cron do host")
@click.option("--limite", default=50, show_default=True)
@click.option("--intervalo", default=30, show_default=True, help="segundos entre ciclos")
def rodar_cmd(uma_vez, limite, intervalo):
    """Executa os jobs vencidos; sem --uma-vez, repete até SIGINT ou SIGTERM."""
    if uma_vez:
        click.echo(_linha(ciclo(limite)))
        return
    parar = []
    for sinal in (signal.SIGINT, signal.SIGTERM):  # o job em andamento termina antes de sair
        signal.signal(sinal, lambda *_: parar.append(True))
    while not parar:
        click.echo(_linha(ciclo(limite)))
        fim = time.monotonic() + intervalo
        while not parar and time.monotonic() < fim:
            time.sleep(0.2)


# ---------- disparo externo (JOB-11) ----------
@bp.route("/jobs/ciclo", methods=["POST"])
def ciclo_http():
    """Um ciclo curto para o cron HTTP externo; no plano gratuito do Render o cron não vê o disco do web."""
    esperado = current_app.config.get("JOBS_TOKEN") or os.environ.get("JOBS_TOKEN")
    if not esperado:
        abort(404)
    recebido = request.headers.get("Authorization", "")
    if not hmac.compare_digest(recebido.encode(), f"Bearer {esperado}".encode()):
        return jsonify(erro="token inválido"), 401
    return jsonify(ciclo(limite=10))


# ---------- painel do admin (JOB-10) ----------
@bp.route("/admin/jobs")
@admin_required
def painel():
    status, tipo = request.args.get("status", ""), request.args.get("tipo", "")
    q = Job.query
    if status in JOB_STATUS:
        q = q.filter(Job.status == status)
    if tipo:
        q = q.filter(Job.tipo == tipo)
    page = paginate(q.order_by(Job.criado_em.desc(), Job.id.desc()), request.args.get("page"))
    agendamentos = Agendamento.query.order_by(Agendamento.nome).all()
    proximas = {a.id: proxima_execucao(a.cron, tempo.para_belem(a.ultima_execucao or tempo.agora_utc()))
                for a in agendamentos}
    return render_template("admin/jobs.html", page=page, status=status, tipo=tipo, todos_status=JOB_STATUS,
                           tipos=sorted(TAREFAS), agendamentos=agendamentos, proximas=proximas,
                           belem=tempo.para_belem)


def _auditar(acao, detalhe):
    db.session.add(AuditLog(admin_id=current_user().id, action=acao, detail=detalhe[:300]))  # ADM-09
    db.session.commit()


@bp.route("/admin/jobs/<int:jid>/retentar", methods=["POST"])
@admin_required
def retentar(jid):
    job = db.get_or_404(Job, jid)
    if job.status not in ("morto", "falhou"):
        flash("Só um job morto ou que falhou pode ser tentado de novo.", "erro")
    else:
        job.status, job.tentativas, job.executar_em, job.concluido_em = "pendente", 0, tempo.agora_utc(), None
        _auditar("jobs.retentar", f"job {job.id} ({job.tipo})")
        flash(f"Job {job.id} volta para a fila.", "ok")
    return redirect("/admin/jobs")


@bp.route("/admin/jobs/<int:jid>/cancelar", methods=["POST"])
@admin_required
def cancelar(jid):
    job = db.get_or_404(Job, jid)
    if job.status != "pendente":
        flash("Só um job pendente pode ser cancelado.", "erro")
    else:  # cancelado fica como `falhou`, para poder ser tentado de novo
        job.status, job.concluido_em = "falhou", tempo.agora_utc()
        job.ultimo_erro = f"cancelado por @{current_user().username}"
        _auditar("jobs.cancelar", f"job {job.id} ({job.tipo})")
        flash(f"Job {job.id} cancelado.", "ok")
    return redirect("/admin/jobs")


@bp.route("/admin/jobs/agendamentos/<int:aid>", methods=["POST"])
@admin_required
def alternar_agendamento(aid):
    ag = db.get_or_404(Agendamento, aid)
    ag.ativo = not ag.ativo
    if ag.ativo:
        ag.ultima_execucao = None  # religado: conta a partir de agora, sem cobrar o tempo desligado
    _auditar("jobs.agendamento", f"{ag.nome} {'ligado' if ag.ativo else 'desligado'}")
    flash(f"Agendamento {ag.nome} {'ligado' if ag.ativo else 'desligado'}.", "ok")
    return redirect("/admin/jobs")


# ---------- tarefas do catálogo (JOB-09) ----------
@tarefa("sync_games")
def sync_games(fonte="steam", limite=40):
    from .sync import sincronizar

    sincronizar(fonte, limite)


@tarefa("enriquecer_snapshot")
def enriquecer_snapshot():
    if not os.environ.get("RAWG_API_KEY"):
        raise ErroPermanente("RAWG_API_KEY não configurada: o enriquecimento do snapshot usa a RAWG")
    try:
        from .snapshot import enriquecer
    except ModuleNotFoundError:
        raise ErroPermanente("o snapshot multi-loja ainda não existe nesta versão") from None
    from .fontes import arquivo

    enriquecer(arquivo.CAMINHO)


agenda_padrao("sync_games", "0 4 * * *", "sync_games")
agenda_padrao("enriquecer_snapshot", "0 5 * * 1", "enriquecer_snapshot", ativo=False)
