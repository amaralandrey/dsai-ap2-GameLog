"""Retrospectiva anual e horas recentes do perfil (RET-05..11), sobre a consulta base do diário (DIA-08)."""
from datetime import date, timedelta

from flask import Blueprint, abort, redirect, render_template
from sqlalchemy import func

from . import graficos_svg
from .diario import consulta_minutos, ocultos_para, perfil_ou_404
from .models import Follow, Game, Genre, SessaoJogo, Status, db, game_genres
from .tempo import hoje, semana_iso
from .util import current_user

bp = Blueprint("retrospectiva", __name__)
ANO_MINIMO = 2000
MESES_CURTOS = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
COM_DATA = ("manual", "csv")  # a sessão de saldo da Steam não tem data real (RET-07)
SEMANAS_SPARKLINE = 12


def horas(minutos):
    return round((minutos or 0) / 60, 1)


def minutos_por_mes(user_ids, ano):
    """{(user_id, mês): minutos} das sessões com data, numa única consulta agrupada (RET-06)."""
    sub = consulta_minutos(date(ano, 1, 1), date(ano, 12, 31), COM_DATA).filter(
        SessaoJogo.user_id.in_(user_ids)).subquery()
    mes = func.cast(func.strftime("%m", sub.c.jogada_em), db.Integer)
    linhas = db.session.query(sub.c.user_id, mes, func.sum(sub.c.duracao_min)).group_by(sub.c.user_id, mes)
    return {(u, m): total for u, m, total in linhas}


def minutos_por_genero(user_ids, ano, limite=5):
    """[(gênero, minutos)] do ano: as horas de uma sessão contam para cada gênero do jogo (RET-06)."""
    sub = consulta_minutos(date(ano, 1, 1), date(ano, 12, 31)).filter(SessaoJogo.user_id.in_(user_ids)).subquery()
    total = func.sum(sub.c.duracao_min)
    return (
        db.session.query(Genre.name, total)
        .join(game_genres, game_genres.c.genre_id == Genre.id)
        .join(sub, sub.c.game_id == game_genres.c.game_id)
        .group_by(Genre.id)
        .order_by(total.desc(), Genre.name)
        .limit(limite)
        .all()
    )


def totais(user_id, ano):
    """(minutos, jogos diferentes, minutos da Steam, sessões com data) do ano, numa consulta (RET-05, RET-07)."""
    steam = func.sum(func.iif(SessaoJogo.origem == "steam", SessaoJogo.duracao_min, 0))
    com_data = func.sum(func.iif(SessaoJogo.origem == "steam", 0, 1))
    return (
        db.session.query(func.coalesce(func.sum(SessaoJogo.duracao_min), 0),
                         func.count(func.distinct(SessaoJogo.game_id)), func.coalesce(steam, 0),
                         func.coalesce(com_data, 0))
        .filter(SessaoJogo.user_id == user_id, SessaoJogo.jogada_em.between(date(ano, 1, 1), date(ano, 12, 31)))
        .one()
    )


def jogo_mais_jogado(user_id, ano):
    """Jogo visível com mais minutos no ano, de qualquer origem (RET-07, RET-09)."""
    total = func.sum(SessaoJogo.duracao_min)
    return (
        db.session.query(Game, total)
        .join(SessaoJogo, SessaoJogo.game_id == Game.id)
        .filter(SessaoJogo.user_id == user_id, SessaoJogo.jogada_em.between(date(ano, 1, 1), date(ano, 12, 31)),
                Game.removed_at.is_(None))
        .group_by(Game.id)
        .order_by(total.desc(), Game.title)
        .first()
    )


def sessao_mais_longa(user_id, ano):
    return (
        SessaoJogo.query.join(Game, Game.id == SessaoJogo.game_id)
        .filter(SessaoJogo.user_id == user_id, SessaoJogo.origem.in_(COM_DATA),
                SessaoJogo.jogada_em.between(date(ano, 1, 1), date(ano, 12, 31)), Game.removed_at.is_(None))
        .order_by(SessaoJogo.duracao_min.desc(), SessaoJogo.jogada_em, SessaoJogo.id)
        .first()
    )


def zerados_no_ano(user_id, ano):
    return (
        db.session.query(Game, Status.zerado_em)
        .join(Status, Status.game_id == Game.id)
        .filter(Status.user_id == user_id, Status.zerado_em.between(date(ano, 1, 1), date(ano, 12, 31)),
                Game.removed_at.is_(None))
        .order_by(Status.zerado_em, Game.title)
        .all()
    )


def comparacao(dono, ano, meus_meses):
    """Horas por mês do dono contra a média de quem ele segue e pode ver, e 3 gêneros em comum (RET-10)."""
    seguidos = (
        db.session.query(Follow.followed_id)
        .join(SessaoJogo, SessaoJogo.user_id == Follow.followed_id)
        .filter(Follow.follower_id == dono.id, Follow.aprovado, Follow.followed_id.not_in(ocultos_para(dono)),
                SessaoJogo.origem.in_(COM_DATA), SessaoJogo.jogada_em.between(date(ano, 1, 1), date(ano, 12, 31)))
        .distinct()
        .all()
    )
    ids = [i for (i,) in seguidos]
    if not ids:
        return None
    por_mes = minutos_por_mes(ids, ano)
    media = [horas(sum(por_mes.get((u, m), 0) for u in ids) / len(ids)) for m in range(1, 13)]
    deles = dict(minutos_por_genero(ids, ano, limite=None))
    meus = dict(minutos_por_genero([dono.id], ano, limite=None))
    comuns = sorted(set(deles) & set(meus), key=lambda g: (-(deles[g] + meus[g]), g))[:3]
    grafico = graficos_svg.linha([("Você", meus_meses), ("Média de quem você segue", media)], MESES_CURTOS,
                                 f"Suas horas por mês e a média de quem você segue em {ano}")
    return {"grafico": grafico, "media": media, "generos": comuns}


@bp.route("/u/<username>/retrospectiva")
def atual(username):
    return redirect(f"/u/{username}/retrospectiva/{hoje().year}")


@bp.route("/u/<username>/retrospectiva/<int:ano>")
def pagina(username, ano):
    if not ANO_MINIMO <= ano <= hoje().year:
        abort(404)
    dono = perfil_ou_404(username)  # RET-09
    total, jogos, steam, sessoes = totais(dono.id, ano)
    zerados = zerados_no_ano(dono.id, ano)
    if not total and not zerados:
        return render_template("retrospectiva/pagina.html", dono=dono, ano=ano, vazio=True)
    por_mes = minutos_por_mes([dono.id], ano)
    meses = [horas(por_mes.get((dono.id, m), 0)) for m in range(1, 13)]
    generos = [(nome, horas(m)) for nome, m in minutos_por_genero([dono.id], ano)]
    me = current_user()
    return render_template(
        "retrospectiva/pagina.html", dono=dono, ano=ano, vazio=False, total=total, jogos=jogos, steam=steam,
        sessoes=sessoes, zerados=zerados, mais_jogado=jogo_mais_jogado(dono.id, ano),
        mais_longa=sessao_mais_longa(dono.id, ano), meses=meses, generos=generos, rotulos_meses=MESES_CURTOS,
        grafico_meses=graficos_svg.barras([("Horas", meses)], MESES_CURTOS, f"Horas por mês em {ano}"),
        grafico_generos=graficos_svg.barras([("Horas", [h for _, h in generos])], [n for n, _ in generos],
                                            f"Gêneros com mais horas em {ano}") if generos else None,
        comparar=comparacao(dono, ano, meses) if me and me.id == dono.id else None,
    )


def horas_por_semana(user_id):
    """Horas das últimas 12 semanas ISO, da mais antiga para a atual, numa consulta (RET-11)."""
    fim = hoje()
    inicio = fim - timedelta(days=fim.weekday() + 7 * (SEMANAS_SPARKLINE - 1))
    linhas = (
        db.session.query(SessaoJogo.jogada_em, func.sum(SessaoJogo.duracao_min))
        .filter(SessaoJogo.user_id == user_id, SessaoJogo.origem.in_(COM_DATA),
                SessaoJogo.jogada_em.between(inicio, fim))
        .group_by(SessaoJogo.jogada_em)
        .all()
    )
    semanas = [semana_iso(inicio + timedelta(weeks=i)) for i in range(SEMANAS_SPARKLINE)]
    soma = dict.fromkeys(semanas, 0)
    for dia, minutos in linhas:
        soma[semana_iso(dia)] += minutos
    return [horas(soma[s]) for s in semanas]


def sparkline_do_perfil(user_id):
    valores = horas_por_semana(user_id)
    return graficos_svg.sparkline(valores, "Horas nas últimas 12 semanas"), valores
