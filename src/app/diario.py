"""Diário de jogatina e visibilidade do perfil (DIA-01..14).

`pode_ver_perfil` é a única regra de visibilidade de perfil (DIA-12, GLOBAL-13); `ocultos_para` é a mesma
regra em SQL, para as listagens filtrarem no banco sem uma consulta por linha.
"""
import re
from datetime import datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request
from sqlalchemy import func, select

from .models import Activity, Follow, Game, SessaoJogo, Status, User, db, visible_games
from .tempo import hoje
from .util import current_user, login_required, paginate

bp = Blueprint("diario", __name__)
MAX_MIN = 1440
MAX_ANOTACAO = 1000
DURACAO_RE = re.compile(r"^(?:(\d+)\s*h\s*(?:(\d+)\s*(?:min|m)?)?|(\d+)\s*(?:min|m)?|(\d+):([0-5]\d))$")


# ---------- visibilidade do perfil (DIA-12, DIA-13) ----------
def pode_ver_perfil(visitante, dono):
    """Perfil público, o dono, um admin ou alguém que o dono segue (seguir o dono não basta)."""
    if not dono.perfil_privado:
        return True
    if visitante is None:
        return False
    if visitante.id == dono.id or visitante.is_admin:
        return True
    return Follow.query.filter_by(follower_id=dono.id, followed_id=visitante.id).first() is not None


def ocultos_para(visitante):
    """Ids dos donos de perfil privado que `visitante` não pode ver (a regra de `pode_ver_perfil` em SQL)."""
    q = select(User.id).where(User.perfil_privado.is_(True))
    if visitante is None:
        return q
    if visitante.is_admin:
        return q.where(False)
    me_segue = select(Follow.follower_id).where(Follow.followed_id == visitante.id)
    return q.where(User.id != visitante.id, User.id.not_in(me_segue))


def perfil_ou_404(username):
    """Dono do perfil, ou 404 quando o visitante não pode ver o perfil (DIA-12, GLOBAL-18)."""
    dono = User.query.filter_by(username_lower=username.lower()).first_or_404()
    if not pode_ver_perfil(current_user(), dono):
        abort(404)
    return dono


# ---------- horas (DIA-08) ----------
def consulta_minutos(de=None, ate=None, origens=None):
    """Consulta base das horas: (user_id, game_id, jogada_em, duracao_min) das sessões, num intervalo inclusivo.

    Retrospectiva, rankings e metas de clube agregam a partir daqui, com GROUP BY no banco.
    """
    q = db.session.query(SessaoJogo.user_id, SessaoJogo.game_id, SessaoJogo.jogada_em, SessaoJogo.duracao_min)
    if de is not None:
        q = q.filter(SessaoJogo.jogada_em >= de)
    if ate is not None:
        q = q.filter(SessaoJogo.jogada_em <= ate)
    if origens is not None:
        q = q.filter(SessaoJogo.origem.in_(origens))
    return q


def minutos_registrados(user_id, game_id=None, de=None, ate=None, origens=None):
    base = consulta_minutos(de, ate, origens).filter(SessaoJogo.user_id == user_id)
    if game_id is not None:
        base = base.filter(SessaoJogo.game_id == game_id)
    sub = base.subquery()
    return db.session.query(func.coalesce(func.sum(sub.c.duracao_min), 0)).scalar()


def minutos_por_usuario(game_id, user_ids):
    """Minutos de cada autor num jogo, numa consulta só (selo da página do jogo, DIA-09)."""
    if not user_ids:
        return {}
    sub = consulta_minutos().filter(SessaoJogo.game_id == game_id, SessaoJogo.user_id.in_(user_ids)).subquery()
    return dict(db.session.query(sub.c.user_id, func.sum(sub.c.duracao_min)).group_by(sub.c.user_id).all())


def formatar_minutos(minutos):
    """"12h", "12h 30min" ou "45min"."""
    minutos = int(minutos or 0)
    h, m = divmod(minutos, 60)
    if not h:
        return f"{m}min"
    return f"{h}h {m}min" if m else f"{h}h"


def selo_horas(minutos):
    """Horas inteiras para baixo; menos de 1 hora vira "<1h" (DIA-09)."""
    return "<1h" if minutos < 60 else f"{minutos // 60}h"


# ---------- validação (DIA-01, DIA-02) ----------
def ler_duracao(texto):
    """Aceita "90", "1h30", "1h 30min", "2h", "45min" ou "1:30"; devolve minutos ou None."""
    m = DURACAO_RE.match((texto or "").strip().lower())
    if not m:
        return None
    h, hm, so_min, ch, cm = m.groups()
    if h is not None:
        return int(h) * 60 + int(hm or 0)
    if so_min is not None:
        return int(so_min)
    return int(ch) * 60 + int(cm)


def ler_data(texto):
    texto = (texto or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, fmt).date()
        except ValueError:
            pass
    return None


def ler_hora(texto):
    try:
        return datetime.strptime((texto or "").strip(), "%H:%M").time()
    except ValueError:
        return None


# ---------- consistência de toda sessão manual ou de CSV (HRS-09) ----------
def _intervalo(jogada_em, inicio, duracao_min):
    comeco = datetime.combine(jogada_em, inicio)
    return comeco, comeco + timedelta(minutes=duracao_min)


def consistencia(user_id, jogada_em, duracao_min, inicio=None, ignorar_id=None, pendentes=()):
    """Mensagem do problema, ou None: duração, data, total do dia e sobreposição de horários.

    `pendentes` são sessões ainda não gravadas (linhas anteriores do mesmo CSV), como dicionários com
    `jogada_em`, `duracao_min` e `inicio`. A sessão de saldo da Steam não entra em nenhuma das somas.
    """
    if not 1 <= duracao_min <= MAX_MIN:
        return "duração fora do intervalo de 1 minuto a 24 horas"
    if jogada_em > hoje():
        return "A data não pode estar no futuro"
    vizinhas = (
        SessaoJogo.query.filter(SessaoJogo.user_id == user_id, SessaoJogo.origem.in_(("manual", "csv")),
                                SessaoJogo.jogada_em.between(jogada_em - timedelta(days=1), jogada_em))
        .filter(SessaoJogo.id != (ignorar_id or 0))
        .all()
    )
    outras = [(s.jogada_em, s.duracao_min, s.inicio) for s in vizinhas]
    outras += [(p["jogada_em"], p["duracao_min"], p["inicio"]) for p in pendentes]
    if sum(d for dia, d, _ in outras if dia == jogada_em) + duracao_min > MAX_MIN:
        return "a soma das sessões do dia passaria de 24 horas"
    if inicio is not None:
        a, b = _intervalo(jogada_em, inicio, duracao_min)
        for dia, dur, ini in outras:
            if ini is None:
                continue
            c, d = _intervalo(dia, ini, dur)
            if a < d and c < b:
                return f"sobrepõe a sessão de {dia.strftime('%d/%m')} às {ini.strftime('%H:%M')}"
    return None


def validar_campos(game, form, user_id=None, ignorar_id=None):
    """Campos do formulário de sessão: devolve (dados, erros por campo)."""
    erros, dados = {}, {}
    dados["jogada_em"] = ler_data(form.get("jogada_em"))
    if dados["jogada_em"] is None:
        erros["jogada_em"] = "Informe uma data válida."
    elif dados["jogada_em"] > hoje():
        erros["jogada_em"] = "A data não pode estar no futuro"
    dur = ler_duracao(form.get("duracao"))
    if dur is None or not 1 <= dur <= MAX_MIN:
        erros["duracao"] = "A duração deve ficar entre 1 minuto e 24 horas (ex.: 90, 1h30 ou 1:30)."
    dados["duracao_min"] = dur
    dados["inicio"] = None
    if (form.get("inicio") or "").strip():
        dados["inicio"] = ler_hora(form.get("inicio"))
        if dados["inicio"] is None:
            erros["inicio"] = "Use o formato HH:MM."
    dados["plataforma_id"] = None
    if (form.get("plataforma_id") or "").strip():
        validas = {p.id for p in game.platforms}
        try:
            dados["plataforma_id"] = int(form.get("plataforma_id"))
        except ValueError:
            dados["plataforma_id"] = -1
        if dados["plataforma_id"] not in validas:
            erros["plataforma_id"] = "Escolha uma das plataformas do jogo."
    anotacao = (form.get("anotacao") or "").strip()
    if len(anotacao) > MAX_ANOTACAO:
        erros["anotacao"] = f"A anotação deve ter até {MAX_ANOTACAO} caracteres."
    dados.update(anotacao=anotacao, zerou=bool(form.get("zerou")), spoiler=bool(form.get("spoiler")))
    if not erros and user_id is not None:
        problema = consistencia(user_id, dados["jogada_em"], dur, dados["inicio"], ignorar_id)
        if problema:
            erros["inicio" if problema.startswith("sobrepõe") else "duracao"] = problema[0].upper() + problema[1:] + "."
    return dados, erros


# ---------- efeitos de uma sessão (DIA-03, DIA-04, DIA-10) ----------
def recalcular_zerado(user_id, game_id):
    """`zerado_em` é o dia mais antigo entre as sessões com "zerei"; sem nenhuma, fica vazio (DIA-04)."""
    st = Status.query.filter_by(user_id=user_id, game_id=game_id).first()
    if st is None:
        return
    st.zerado_em = (
        db.session.query(func.min(SessaoJogo.jogada_em))
        .filter_by(user_id=user_id, game_id=game_id, zerou=True)
        .scalar()
    )


def aplicar_status(sessao):
    """Sem status ou "planejado" vira "jogando"; "zerei" grava `zerado_em` e muda para "jogado"."""
    st = Status.query.filter_by(user_id=sessao.user_id, game_id=sessao.game_id).first()
    if st is None:
        st = Status(user_id=sessao.user_id, game_id=sessao.game_id, value="jogando")
        db.session.add(st)
    elif st.value == "planejado":
        st.value = "jogando"
    if sessao.zerou:
        if st.value != "jogado":
            st.value = "jogado"
            ja = Activity.query.filter_by(user_id=sessao.user_id, kind="jogado", game_id=sessao.game_id).first()
            if ja is None:
                db.session.add(Activity(user_id=sessao.user_id, kind="jogado", game_id=sessao.game_id))
        if st.zerado_em is None or sessao.jogada_em < st.zerado_em:
            st.zerado_em = sessao.jogada_em
    return st


def atualizar_atividade(user_id, game_id, dia):
    """Uma atividade `sessao` por jogo e dia, com a soma das sessões manuais do dia (DIA-10)."""
    total = (
        db.session.query(func.sum(SessaoJogo.duracao_min))
        .filter_by(user_id=user_id, game_id=game_id, jogada_em=dia, origem="manual")
        .scalar()
    )
    atv = Activity.query.filter_by(user_id=user_id, kind="sessao", game_id=game_id, dia=dia).first()
    if not total:
        if atv:
            db.session.delete(atv)
    elif atv:
        atv.valor = total
    else:
        db.session.add(Activity(user_id=user_id, kind="sessao", game_id=game_id, dia=dia, valor=total))


def gravar_sessao(user, game, dados, origem="manual"):
    """Cria a sessão e aplica status e atividade; quem chama faz o commit."""
    sessao = SessaoJogo(user_id=user.id, game_id=game.id, origem=origem, **dados)
    db.session.add(sessao)
    db.session.flush()
    if origem != "steam":  # HRS-04: o saldo da Steam não muda status
        aplicar_status(sessao)
    if origem == "manual":
        atualizar_atividade(user.id, game.id, sessao.jogada_em)
    return sessao


# ---------- rotas ----------
def sessao_do_dono(sid):
    sessao = db.get_or_404(SessaoJogo, sid)
    if sessao.user_id != current_user().id:
        abort(403)  # DIA-05
    return sessao


def tela_jogo_com_erros(game, erros, form):
    return render_template("diario/sessao.html", game=game, sessao=None, erros=erros, form=form), 400


@bp.route("/jogos/<slug>/sessoes", methods=["POST"])
@login_required
def registrar(slug):
    game = visible_games().filter_by(slug=slug).first_or_404()  # DIA-03: removido não recebe sessão
    me = current_user()
    dados, erros = validar_campos(game, request.form, me.id)
    if erros:
        return tela_jogo_com_erros(game, erros, request.form)
    gravar_sessao(me, game, dados)
    db.session.commit()
    flash("Sessão registrada no diário.", "ok")
    return redirect(f"/jogos/{game.slug}")


@bp.route("/sessoes/<int:sid>/editar", methods=["GET", "POST"])
@login_required
def editar(sid):
    sessao = sessao_do_dono(sid)
    game = sessao.game
    if request.method == "GET":
        form = {"jogada_em": sessao.jogada_em.isoformat(), "duracao": str(sessao.duracao_min),
                "inicio": sessao.inicio.strftime("%H:%M") if sessao.inicio else "",
                "plataforma_id": sessao.plataforma_id or "", "anotacao": sessao.anotacao,
                "zerou": sessao.zerou, "spoiler": sessao.spoiler}
        return render_template("diario/sessao.html", game=game, sessao=sessao, erros={}, form=form)
    dados, erros = validar_campos(game, request.form, sessao.user_id, sessao.id)
    if erros:
        return render_template("diario/sessao.html", game=game, sessao=sessao, erros=erros, form=request.form), 400
    dia_antigo = sessao.jogada_em
    for campo, valor in dados.items():
        setattr(sessao, campo, valor)
    db.session.flush()
    aplicar_status(sessao)
    recalcular_zerado(sessao.user_id, sessao.game_id)
    if sessao.origem == "manual":
        atualizar_atividade(sessao.user_id, sessao.game_id, dia_antigo)
        atualizar_atividade(sessao.user_id, sessao.game_id, sessao.jogada_em)
    db.session.commit()
    flash("Sessão atualizada.", "ok")
    return redirect("/diario")


@bp.route("/sessoes/<int:sid>/apagar", methods=["POST"])
@login_required
def apagar(sid):
    sessao = sessao_do_dono(sid)
    user_id, game_id, dia, origem = sessao.user_id, sessao.game_id, sessao.jogada_em, sessao.origem
    db.session.delete(sessao)
    db.session.flush()
    recalcular_zerado(user_id, game_id)  # DIA-04: o status não muda
    if origem == "manual":
        atualizar_atividade(user_id, game_id, dia)
    db.session.commit()
    flash("Sessão apagada.", "ok")
    return redirect(request.form.get("next") if (request.form.get("next") or "").startswith("/diario") else "/diario")


def pagina_do_diario(dono, own):
    q = SessaoJogo.query.filter_by(user_id=dono.id).join(Game, Game.id == SessaoJogo.game_id)
    jogo = None
    if request.args.get("jogo"):
        jogo = Game.query.filter_by(slug=request.args["jogo"]).first()
        q = q.filter(SessaoJogo.game_id == (jogo.id if jogo else -1))
    page = paginate(q.order_by(SessaoJogo.jogada_em.desc(), SessaoJogo.id.desc()), request.args.get("page"))
    meses = []
    for s in page.items:  # DIA-06: agrupadas por mês, na ordem da página
        chave = (s.jogada_em.year, s.jogada_em.month)
        if not meses or meses[-1][0] != chave:
            meses.append((chave, []))
        meses[-1][1].append(s)
    return render_template("diario/diario.html", dono=dono, own=own, page=page, meses=meses, jogo=jogo,
                           nome_do_mes=nome_do_mes)


@bp.route("/diario")
@login_required
def meu_diario():
    return pagina_do_diario(current_user(), True)


@bp.route("/u/<username>/diario")
def diario_de(username):
    dono = perfil_ou_404(username)
    me = current_user()
    return pagina_do_diario(dono, me is not None and me.id == dono.id)


def zerados(user_id):
    return Status.query.filter(Status.user_id == user_id, Status.zerado_em.isnot(None)).count()


MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro")


def nome_do_mes(chave):
    ano, mes = chave
    return f"{MESES[mes - 1]} de {ano}"
