"""Anonimização e remoção de conta pelo mapa de dados (DIR-06, DIR-07, RTN-06).

`anonimizar_conta` é a rotina da exclusão de conta: a DIR a chama em `/conta/excluir`, e o expurgo (RTN-06) a chama
para as contas inativas. Ela decide cada coluna e cada tabela pela regra do mapa (`regra_anonimizacao`), sem lista
própria: as avaliações ficam (`manter`) e passam a mostrar "Usuário removido", e o que é `apagar_linha` sai.
`apagar_conta` remove a conta e tudo o que depende dela, para a conta pendente que nunca teve conteúdo (RSP-04).
Nenhuma das duas faz commit: quem chama decide a transação.
"""
from sqlalchemy import delete, select, update

from ..models import LoginAttempt, User, db
from .mapa import regra_anonimizacao

DOMINIO = "removido.invalid"  # domínio reservado (RFC 2606)
NOME = "Usuário removido"
SENHA_INUTILIZAVEL = "!"  # não é um hash do werkzeug: nenhuma senha confere


def anonimizada(user):
    return user is not None and user.email.endswith(f"@{DOMINIO}")


def _referencias(tabela):
    """(tabela filha, coluna, coluna referida) de cada chave estrangeira que aponta para `tabela`."""
    return [(filha, coluna, fk.column) for filha in db.metadata.sorted_tables for coluna in filha.columns
            for fk in coluna.foreign_keys if fk.column.table is tabela]


def apagar_linhas(tabela, condicao):
    """Apaga as linhas e, antes, as que dependem delas, em qualquer tabela (as chaves estrangeiras estão ligadas)."""
    for filha, coluna, referida in _referencias(tabela):
        if filha is not tabela:
            apagar_linhas(filha, coluna.in_(select(referida).where(condicao)))
    db.session.execute(delete(tabela).where(condicao))


def _substituto(user, coluna):
    valores = {
        "username": f"removido-{user.id}", "username_lower": f"removido-{user.id}",
        "email": f"removido-{user.id}@{DOMINIO}", "display_name": NOME,
        "password_hash": SENHA_INUTILIZAVEL, "session_version": user.session_version + 1,
    }
    return valores[coluna.name]  # KeyError: coluna nova com `substituir` precisa de valor aqui


def _apagado(coluna):
    padrao = coluna.default.arg if coluna.default is not None and not callable(coluna.default.arg) else None
    return None if coluna.nullable else padrao


def anonimizar_conta(user):
    """Aplica o mapa à conta e às tabelas que apontam para ela; a linha do usuário fica com o id."""
    db.session.flush()
    usuario = User.__table__
    for filha, coluna, _ in _referencias(usuario):
        regra = regra_anonimizacao(filha.name, coluna.name)
        if regra == "apagar_linha":
            apagar_linhas(filha, coluna == user.id)
        elif regra == "apagar":
            db.session.execute(update(filha).where(coluna == user.id).values({coluna.name: None}))
    db.session.execute(delete(LoginAttempt).where(LoginAttempt.email == user.email))
    valores = {}
    for coluna in usuario.columns:
        regra = regra_anonimizacao("user", coluna.name)
        if regra == "substituir":
            valores[coluna.name] = _substituto(user, coluna)
        elif regra == "apagar":
            valores[coluna.name] = _apagado(coluna)
    db.session.execute(update(usuario).where(usuario.c.id == user.id).values(valores))
    db.session.expire_all()


def apagar_conta(user):
    """Remove a linha do usuário e tudo o que depende dela, sem anonimizar nada (RSP-04, RTN-06)."""
    db.session.flush()
    db.session.execute(delete(LoginAttempt).where(LoginAttempt.email == user.email))
    apagar_linhas(User.__table__, User.__table__.c.id == user.id)
    db.session.expire_all()
