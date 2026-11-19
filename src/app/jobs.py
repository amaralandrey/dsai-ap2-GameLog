"""Fila persistente de jobs sobre o SQLite (JOB-01..12), sem broker e sem agendador dentro do Flask.

Quem executa é `flask jobs rodar` ou `POST /jobs/ciclo`, chamados de fora; a trava por UPDATE condicional
garante que dois executores (ou os 2 workers do gunicorn) nunca rodem o mesmo job ao mesmo tempo.
"""
import json
import os
import socket
from datetime import timedelta

from sqlalchemy.exc import IntegrityError

from . import tempo
from .models import Job, db

TAREFAS = {}
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
    except Exception as exc:
        db.session.rollback()  # desfaz o que a tarefa gravou (JOB-06)
        return _falhar(job_id, exc)
    job.status, job.concluido_em = "ok", tempo.agora_utc()
    job.travado_por = job.travado_em = None
    db.session.commit()  # o que a tarefa gravou e o "ok" entram juntos
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


def ciclo(limite=50, executor=None):
    """Um ciclo do executor; devolve a contagem que `jobs rodar` imprime (JOB-04)."""
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
