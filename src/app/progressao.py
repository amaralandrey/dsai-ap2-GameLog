"""Níveis, XP e sequências semanais (PRG-01..12).

O XP sai dos eventos do motor de conquistas: `ao_evento` é registrado com `gamificacao.ouvir` e roda antes
das conquistas. A sequência é semanal e não há multiplicador por sequência, para não premiar o uso
compulsivo (ECA Digital, art. 8, IV).
"""
import secrets
from datetime import timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from . import gamificacao
from .menores.politica import pode
from .models import (
    AuditLog, ExtratoXP, Notificacao, PerfilProgressao, Review, SessaoJogo, Status, User, db,
)
from .tempo import agora_utc, hoje, inicio_do_dia_utc, semana_iso, semanas_entre
from .util import admin_required, current_user, paginate

bp = Blueprint("progressao", __name__)
XP_AVALIACAO, XP_RESENHA, RESENHA_MIN = 10, 25, 140
XP_SESSAO, XP_ZERADO = 10, 50
XP_CONQUISTA = {"comum": 20, "rara": 50, "epica": 100, "lendaria": 200}
TETO_DIARIO = 150
REPETIVEIS = ("avaliacao", "sessao")
MAX_CONGELAMENTOS, SEMANAS_POR_CONGELAMENTO = 2, 4
MIN_SEQUENCIA_AVISO = 2
MAX_JUSTIFICATIVA = 200


# ---------- curva de níveis (PRG-04) ----------
def xp_do_nivel(n):
    return 50 * n * (n - 1)


def nivel_de(xp):
    """O maior n com 50·n·(n−1) ≤ xp; nunca menor que 1."""
    n = 1
    while xp_do_nivel(n + 1) <= xp:
        n += 1
    return n


def porcentagem(perfil):
    """Progresso entre o XP do nível atual e o do próximo; estorno abaixo do nível mostra 0%."""
    base, proximo = xp_do_nivel(perfil.nivel), xp_do_nivel(perfil.nivel + 1)
    return max(0, min(100, int(100 * (perfil.xp_total - base) / (proximo - base))))


def perfil_de(user_id, criar=True):
    p = PerfilProgressao.query.filter_by(user_id=user_id).first()
    if p is None and criar:
        p = PerfilProgressao(user_id=user_id, xp_total=0, nivel=1, sequencia_semanas=0, maior_sequencia=0,
                             congelamentos=0)
        db.session.add(p)
        db.session.flush()
    return p


# ---------- lançamentos (PRG-02, PRG-03, PRG-05) ----------
def _ja_lancado(user_id, origem_tipo, origem_id):
    return ExtratoXP.query.filter_by(user_id=user_id, origem_tipo=origem_tipo, origem_id=str(origem_id)).first()


def _xp_repetivel_hoje(user_id):
    comeco = inicio_do_dia_utc(hoje())
    return (db.session.query(func.coalesce(func.sum(ExtratoXP.xp), 0))
            .filter(ExtratoXP.user_id == user_id, ExtratoXP.origem_tipo.in_(REPETIVEIS),
                    ExtratoXP.created_at >= comeco, ExtratoXP.created_at < comeco + timedelta(days=1)).scalar())


def lancar(user_id, origem_tipo, origem_id, xp, motivo):
    """Grava um lançamento uma única vez por (usuário, origem) e atualiza total e nível."""
    if _ja_lancado(user_id, origem_tipo, origem_id):
        return None
    if origem_tipo in REPETIVEIS and xp > 0 and _xp_repetivel_hoje(user_id) + xp > TETO_DIARIO:
        xp, motivo = 0, "teto diário"
    item = ExtratoXP(user_id=user_id, origem_tipo=origem_tipo, origem_id=str(origem_id), xp=xp, motivo=motivo,
                     created_at=agora_utc())
    try:
        with db.session.begin_nested():
            db.session.add(item)
            db.session.flush()
    except IntegrityError:
        return None
    _somar(user_id, xp)
    return item


def _somar(user_id, xp):
    p = perfil_de(user_id)
    p.xp_total += xp
    novo = nivel_de(p.xp_total)
    if novo > p.nivel:  # vários níveis de uma vez: um aviso só, com o nível final
        p.nivel = novo
        from .notificacoes import notificar

        user = db.session.get(User, user_id)
        notificar(user_id, "nivel", texto=f"nível {novo}", alvo_url=f"/u/{user.username}/xp")
        db.session.flush()
        gamificacao.emitir_evento(user_id, "nivel", nivel=novo)


def estornar(user_id, origem_tipo, origem_id):
    """Lançamento negativo com o mesmo valor; o nível alcançado não cai (PRG-03, PRG-04)."""
    original = _ja_lancado(user_id, origem_tipo, origem_id)
    if original is None or not original.xp:
        return None
    return lancar(user_id, f"estorno_{origem_tipo}", origem_id, -original.xp, f"estorno: {original.motivo}")


def _creditar_zerados(user_id):
    """+50 por jogo zerado, uma vez por jogo, mesmo desmarcando e marcando de novo."""
    creditados = db.session.query(ExtratoXP.origem_id).filter_by(user_id=user_id, origem_tipo="zerado")
    for (game_id,) in (db.session.query(Status.game_id)
                       .filter(Status.user_id == user_id, Status.zerado_em.isnot(None))
                       .filter(func.cast(Status.game_id, db.String).not_in(creditados))):
        lancar(user_id, "zerado", game_id, XP_ZERADO, "zerou um jogo")


@gamificacao.ouvir
def ao_evento(user_id, tipo, ctx):
    if tipo == "avaliacao" and ctx.get("review_id"):
        review = db.session.get(Review, ctx["review_id"])
        if review is not None:
            longa = len(review.text or "") >= RESENHA_MIN
            lancar(user_id, "avaliacao", review.id, XP_RESENHA if longa else XP_AVALIACAO,
                   "avaliação com resenha" if longa else "avaliação")
            registrar_semana(user_id)
    elif tipo == "avaliacao_apagada" and ctx.get("review_id"):
        estornar(user_id, "avaliacao", ctx["review_id"])
    elif tipo == "sessao" and ctx.get("sessao_id"):
        sessao = db.session.get(SessaoJogo, ctx["sessao_id"])
        if sessao is not None and sessao.origem == "manual":  # importadas não dão XP
            lancar(user_id, "sessao", sessao.id, XP_SESSAO, "sessão no diário")
            registrar_semana(user_id)
        _creditar_zerados(user_id)
    elif tipo == "sessao_apagada" and ctx.get("sessao_id"):
        estornar(user_id, "sessao", ctx["sessao_id"])
    elif tipo == "importacao":
        _creditar_zerados(user_id)
    elif tipo == "conquista" and ctx.get("conquista_id"):
        lancar(user_id, "conquista", ctx["conquista_id"], XP_CONQUISTA.get(ctx.get("raridade"), 0),
               "conquista desbloqueada")


# ---------- sequência semanal (PRG-06..08) ----------
def registrar_semana(user_id, dia=None):
    """Primeira ação válida da semana: soma, usa congelamentos ou recomeça."""
    semana = semana_iso(dia or hoje())
    p = perfil_de(user_id)
    if p.ultima_semana_ativa == semana:
        return p
    if p.ultima_semana_ativa is None:
        p.sequencia_semanas = 1
    else:
        vazias = semanas_entre(p.ultima_semana_ativa, semana) - 1
        if vazias <= 0:
            p.sequencia_semanas += 1
        elif p.congelamentos >= vazias:
            _movimentar_congelamentos(p, -vazias, semana)
            p.sequencia_semanas += 1
        else:
            if p.congelamentos:
                _movimentar_congelamentos(p, -p.congelamentos, semana)
            p.sequencia_semanas = 1
    p.ultima_semana_ativa = semana
    p.maior_sequencia = max(p.maior_sequencia, p.sequencia_semanas)
    if p.sequencia_semanas % SEMANAS_POR_CONGELAMENTO == 0 and p.congelamentos < MAX_CONGELAMENTOS:
        _movimentar_congelamentos(p, 1, semana)
    return p


def _movimentar_congelamentos(p, quantidade, semana):
    p.congelamentos += quantidade
    motivo = "congelamento ganho" if quantidade > 0 else f"{-quantidade} congelamento(s) usado(s)"
    db.session.add(ExtratoXP(user_id=p.user_id, origem_tipo="congelamento",
                             origem_id=f"{semana}:{'ganho' if quantidade > 0 else 'uso'}", xp=0, motivo=motivo,
                             created_at=agora_utc()))


def sequencia_exibida(p, semana=None):
    """0 enquanto as semanas vazias já passadas não puderem ser cobertas pelos congelamentos (PRG-07)."""
    if p is None or p.ultima_semana_ativa is None:
        return 0
    vazias = semanas_entre(p.ultima_semana_ativa, semana or semana_iso(hoje())) - 1
    return p.sequencia_semanas if vazias <= 0 or p.congelamentos >= vazias else 0


def resumo(user_id):
    """Nível, XP, barra e sequência para o perfil, numa consulta (PRG-09)."""
    p = perfil_de(user_id, criar=False)
    if p is None:
        return {"nivel": 1, "xp": 0, "pct": 0, "sequencia": 0, "congelamentos": 0, "proximo": xp_do_nivel(2)}
    return {"nivel": p.nivel, "xp": p.xp_total, "pct": porcentagem(p), "sequencia": sequencia_exibida(p),
            "congelamentos": p.congelamentos, "proximo": xp_do_nivel(p.nivel + 1)}


# ---------- aviso semanal (PRG-10) ----------
def avisar_sequencias():
    """Avisa quem tem sequência de 2+ semanas e ainda não agiu nesta; no máximo uma vez por semana."""
    from .notificacoes import notificar

    anterior = semana_iso(hoje() - timedelta(weeks=1))
    desde = inicio_do_dia_utc(hoje() - timedelta(days=hoje().weekday()))
    avisados = db.session.query(Notificacao.user_id).filter(Notificacao.tipo == "sequencia",
                                                            Notificacao.created_at >= desde)
    candidatos = (User.query.join(PerfilProgressao, PerfilProgressao.user_id == User.id)
                  .filter(PerfilProgressao.sequencia_semanas >= MIN_SEQUENCIA_AVISO,
                          PerfilProgressao.ultima_semana_ativa == anterior, User.id.not_in(avisados)))
    enviados = 0
    for user in candidatos:
        if not pode(user, "aviso_engajamento"):  # PMN
            continue
        if notificar(user.id, "sequencia", texto="Registre algo esta semana para manter sua sequência",
                     alvo_url="/diario"):
            enviados += 1
    db.session.commit()
    return enviados


registrar_metrica = gamificacao.registrar_metrica
registrar_metrica("semanas_seguidas", ("sessao", "avaliacao"),
                  lambda user_id: db.session.query(PerfilProgressao.maior_sequencia).filter_by(user_id=user_id).scalar() or 0)
registrar_metrica("nivel", ("nivel",),
                  lambda user_id: db.session.query(PerfilProgressao.nivel).filter_by(user_id=user_id).scalar() or 1)


# ---------- telas (PRG-09, PRG-11) ----------
@bp.route("/u/<username>/xp")
def extrato(username):
    me = current_user()
    if me is None or me.username_lower != username.lower():
        abort(404)  # o extrato é pessoal
    page = paginate(ExtratoXP.query.filter_by(user_id=me.id).order_by(ExtratoXP.created_at.desc(), ExtratoXP.id.desc()),
                    request.args.get("page"))
    return render_template("progressao/extrato.html", page=page, r=resumo(me.id))


@bp.route("/admin/xp", methods=["GET", "POST"])
@admin_required
def ajustar():
    erros, form = {}, request.form
    if request.method == "POST":
        alvo = User.query.filter_by(username_lower=form.get("usuario", "").strip().lower()).first()
        if alvo is None:
            erros["usuario"] = "Usuário não encontrado."
        try:
            xp = int(form.get("xp", ""))
            if xp == 0:
                raise ValueError
        except ValueError:
            erros["xp"] = "Informe um inteiro diferente de zero."
        justificativa = form.get("justificativa", "").strip()
        if not 1 <= len(justificativa) <= MAX_JUSTIFICATIVA:
            erros["justificativa"] = f"A justificativa é obrigatória e tem até {MAX_JUSTIFICATIVA} caracteres."
        if not erros:
            lancar(alvo.id, "ajuste", secrets.token_hex(8), xp, justificativa)
            db.session.add(AuditLog(admin_id=current_user().id, action="xp.ajustar",
                                    detail=f"@{alvo.username} {xp:+d}: {justificativa}"[:300]))
            db.session.commit()
            flash(f"Ajuste de {xp:+d} XP aplicado a @{alvo.username}.", "ok")
            return redirect("/admin/xp")
    return render_template("progressao/admin.html", erros=erros, form=form), (400 if erros else 200)

