"""Retenção e minimização (RTN-01..06, RTN-11): o expurgo diário lê os prazos do mapa de dados.

Nenhum prazo mora aqui: `REGRAS` só diz qual coluna data cada linha e quais linhas contam (a notificação lida, o
e-mail enviado, o job terminado). Uma tabela sem prazo no mapa nunca é tocada, e mudar o prazo no mapa muda o
expurgo. O modelo `Incidente` entra em `REGRAS` com INC (5 anos depois de encerrado, INC-08), e os pedidos de
exclusão adiados pela preservação (DIR-07) entram em `contas` com DIR.
"""
from collections import namedtuple
from datetime import timedelta

from flask import Blueprint, has_request_context, render_template, request, session
from sqlalchemy import func, or_, select

from .. import tempo
from ..emails import enviar_email
from ..jobs import agenda_padrao, tarefa
from ..menores.idade import aguarda_responsavel
from ..models import AuditLog, EmailSaida, Job, LoginAttempt, Notificacao, User, db
from ..util import admin_required, current_user
from . import mapa
from .anonimizar import DOMINIO, anonimizar_conta, apagar_conta

bp = Blueprint("retencao", __name__)

Regra = namedtuple("Regra", "tabela coluna filtro", defaults=(None,))
REGRAS = {
    "login_attempt": Regra(LoginAttempt.__table__, "at"),
    "notificacao": Regra(Notificacao.__table__, "lida_em"),
    "email_saida": Regra(EmailSaida.__table__, "enviado_em", lambda t: t.c.status == "enviado"),
    "job": Regra(Job.__table__, "concluido_em", lambda t: t.c.status.in_(("ok", "morto"))),
    "audit_log": Regra(AuditLog.__table__, "created_at"),
}
LOTE = 500
AVISO_DIAS = 30  # RTN-06
PENDENTE_DIAS = 30  # RSP-04
ACAO = "privacidade.expurgo"


# ---------- linhas vencidas (RTN-01..03) ----------
def nao_preservada(tabela, agora):
    """DEN-09: a linha com `preservar_ate` no futuro fica; a coluna entra com DEN e o filtro vale sem outra mudança."""
    coluna = tabela.c.get("preservar_ate")
    return [] if coluna is None else [or_(coluna.is_(None), coluna <= agora)]


def condicao(regra, prazo, agora):
    t = regra.tabela
    return [t.c[regra.coluna] < agora - timedelta(days=prazo), *([regra.filtro(t)] if regra.filtro else []),
            *nao_preservada(t, agora)]


def vencidas(regra, prazo, agora):
    return db.session.scalar(select(func.count()).select_from(regra.tabela).where(*condicao(regra, prazo, agora)))


def apagar_vencidas(regra, prazo, agora):
    """Apaga em lotes de `LOTE`, um commit por lote: uma falha no meio mantém os lotes já gravados (RTN-03)."""
    t, apagadas = regra.tabela, 0
    while ids := db.session.scalars(select(t.c.id).where(*condicao(regra, prazo, agora)).order_by(t.c.id)
                                    .limit(LOTE)).all():
        db.session.execute(t.delete().where(t.c.id.in_(ids)))
        db.session.commit()
        apagadas += len(ids)
    return apagadas


def prazos():
    """{tabela: prazo em dias} das tabelas de `REGRAS` com prazo no mapa (RTN-02)."""
    return {nome: prazo for nome in REGRAS if (prazo := mapa.retencao(nome)) is not None}


# ---------- contas inativas e pendentes (RTN-05, RTN-06) ----------
def _usuarios(agora, *filtros):
    u = User.__table__
    return (User.query.filter(User.role != "admin", ~User.email.endswith(f"@{DOMINIO}"), *filtros,
                              *nao_preservada(u, agora)).order_by(User.id))


def a_avisar(prazo, agora):
    """Sem entrar há `prazo − 30` dias e ainda sem aviso; admin nunca é inativo."""
    acesso = func.coalesce(User.ultimo_acesso, User.created_at)
    return _usuarios(agora, User.aviso_inatividade_em.is_(None), User.aguardando_responsavel.is_(False),
                     acesso < agora - timedelta(days=prazo - AVISO_DIAS))


def a_anonimizar(agora):
    """Avisadas há 30 dias ou mais sem entrar: o login e a navegação zeram o aviso (RTN-06)."""
    return _usuarios(agora, User.aviso_inatividade_em <= agora - timedelta(days=AVISO_DIAS))


def pendentes_vencidas(agora):
    """Nasceram à espera do responsável há mais de 30 dias (RSP-04); a contestação de idade não conta.

    Com RSP, o vínculo aceito tira `aguardando_responsavel` da conta, e ela sai desta lista sem outra mudança.
    """
    return [u for u in _usuarios(agora, User.aguardando_responsavel.is_(True), User.idade_contestada_em.is_(None),
                                 User.created_at < agora - timedelta(days=PENDENTE_DIAS))
            if aguarda_responsavel(u)]


def avisar(user, prazo, agora):
    limite = tempo.para_belem(agora + timedelta(days=AVISO_DIAS)).date()
    enviar_email(user, "conta_inativa", limite=limite, anos=prazo // 365)
    user.aviso_inatividade_em = agora


def contas(agora, simular=False):
    """Avisa, anonimiza e apaga, com um commit por conta; `simular` só conta."""
    prazo = mapa.retencao("user")  # sem prazo no mapa, nenhuma conta é avisada nem anonimizada
    etapas = {
        "contas_avisadas": (a_avisar(prazo, agora).all() if prazo else [], lambda u: avisar(u, prazo, agora)),
        "contas_anonimizadas": (a_anonimizar(agora).all() if prazo else [], anonimizar_conta),
        "contas_pendentes_apagadas": (pendentes_vencidas(agora), apagar_conta),
    }
    if simular:
        return {nome: len(usuarios) for nome, (usuarios, _) in etapas.items()}
    for nome in ("contas_pendentes_apagadas", "contas_anonimizadas", "contas_avisadas"):
        for user in etapas[nome][0]:
            etapas[nome][1](user)
            db.session.commit()
    return {nome: len(usuarios) for nome, (usuarios, _) in etapas.items()}


def registrar_acesso(user, agora=None):
    """Login e primeira requisição autenticada do dia: marca o acesso e zera o aviso de inatividade (RTN-05)."""
    agora = agora or tempo.agora_utc()
    user.ultimo_acesso, user.aviso_inatividade_em = agora, None
    if has_request_context() and session.get("uid") == user.id:
        session["acesso"] = tempo.dia_de(agora).isoformat()


@bp.before_app_request
def acesso_do_dia():
    """A marca do dia fica na sessão: nas outras requisições do dia, nenhuma consulta a mais."""
    hoje = tempo.hoje().isoformat()
    if request.path.startswith("/static/") or "uid" not in session or session.get("acesso") == hoje:
        return
    user = current_user()
    if user is None:
        return
    if user.ultimo_acesso and tempo.dia_de(user.ultimo_acesso).isoformat() == hoje:
        session["acesso"] = hoje
        return
    registrar_acesso(user)
    db.session.commit()


# ---------- expurgo (RTN-01, RTN-04) ----------
def linha(conta):
    return " ".join(f"{k}={v}" for k, v in conta.items())


def expurgar(simular=False):
    """Contagem por tabela e por etapa das contas; sem `simular`, apaga e grava o AuditLog (RTN-01)."""
    agora = tempo.agora_utc()
    conta = {}
    for nome, prazo in prazos().items():
        regra = REGRAS[nome]
        conta[nome] = vencidas(regra, prazo, agora) if simular else apagar_vencidas(regra, prazo, agora)
    conta.update(contas(agora, simular))
    if not simular:
        db.session.add(AuditLog(admin_id=None, action=ACAO, detail=linha(conta)[:300], created_at=agora))
        db.session.commit()
    return conta


@tarefa("expurgo")
def expurgo():
    """Sem usuário em `args`; cada lote já gravado fica se o job falhar, e a nova tentativa continua (RTN-03)."""
    expurgar()


agenda_padrao("expurgo", "30 3 * * *", "expurgo")  # todo dia às 03:30 de Belém


# ---------- painel do admin (RTN-11) ----------
def ultimo_expurgo():
    """(quando, {tabela: contagem}) do último expurgo, ou None."""
    log = AuditLog.query.filter_by(action=ACAO).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).first()
    if log is None:
        return None
    return log.created_at, dict(p.split("=", 1) for p in log.detail.split() if "=" in p)


@bp.route("/admin/privacidade/retencao")
@admin_required
def painel():
    agora = tempo.agora_utc()
    linhas = [{"tabela": nome, "prazo": mapa.retencao(nome),
               "vencidas": vencidas(regra, mapa.retencao(nome), agora) if mapa.retencao(nome) else None}
              for nome, regra in REGRAS.items()]
    return render_template("admin/retencao.html", linhas=linhas, contas=contas(agora, simular=True),
                           prazo_contas=mapa.retencao("user"), ultimo=ultimo_expurgo(), belem=tempo.para_belem)
