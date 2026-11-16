"""Notificações (NTF-01..12): `notificar()` é o único jeito de avisar um usuário (GLOBAL-14).

Esta spec não envia e-mail: `deve_enviar_email` só informa a preferência, e a spec de e-mail decide o envio.
"""
from datetime import timedelta

from flask import Blueprint, abort, redirect, render_template, request
from sqlalchemy import func

from .models import Follow, Game, Notificacao, PreferenciaNotificacao, Review, Status, User, db, now
from .util import current_user, login_required, paginate

bp = Blueprint("notificacoes", __name__)
TIPOS = {
    "curtida": "Curtidas nas suas avaliações",
    "comentario": "Comentários nas suas avaliações",
    "seguidor": "Novos seguidores",
    "conquista": "Conquistas desbloqueadas",
    "nivel": "Subida de nível",
    "sequencia": "Sequência semanal prestes a quebrar",
    "amigo_zerou": "Quem você segue zerou um jogo da sua wishlist",
    "oferta": "Ofertas da wishlist",
    "preco_alvo": "Preço-alvo alcançado",
    "clube": "Clubes",
    "torneio": "Torneios",
    "responsavel": "Responsável",
    "sistema": "Avisos do GameLog",
}
OBRIGATORIOS_NO_SITE = ("sistema",)
JANELA = timedelta(hours=24)
MAX_AMIGO_ZEROU = 200
_filtros = []


def registrar_filtro(funcao):
    """Filtro `funcao(user_id, tipo, ctx) -> bool`; falso impede a notificação (NTF-03). Serve de decorador."""
    if funcao not in _filtros:
        _filtros.append(funcao)
    return funcao


def url_interna(url):
    """Só caminho interno com uma única `/` no começo; o resto vira vazio (NTF-01)."""
    url = url or ""
    return url if url.startswith("/") and not url.startswith("//") and "\\" not in url else ""


def preferencia(user_id, tipo):
    """(no site, por e-mail); sem registro, o site vale ligado e o e-mail desligado (NTF-09)."""
    p = PreferenciaNotificacao.query.filter_by(user_id=user_id, tipo=tipo).first()
    site = True if p is None else p.site
    return (site or tipo in OBRIGATORIOS_NO_SITE), (False if p is None else p.email)


def _recente(user_id, tipo, **filtros):
    return Notificacao.query.filter_by(user_id=user_id, tipo=tipo, **filtros).filter(
        Notificacao.created_at >= now() - JANELA).first() is not None


def notificar(user_id, tipo, **ctx):
    """Cria e devolve a notificação, ou None; quem chama faz o commit (NTF-01..03)."""
    if tipo not in TIPOS:
        raise ValueError(f"tipo de notificação desconhecido: {tipo}")
    ator_id = ctx.get("ator_id")
    if ator_id is not None and ator_id == user_id:
        return None
    if not preferencia(user_id, tipo)[0]:
        return None
    if any(not filtro(user_id, tipo, ctx) for filtro in list(_filtros)):
        return None
    n = Notificacao(user_id=user_id, tipo=tipo, ator_id=ator_id, game_id=ctx.get("game_id"),
                    review_id=ctx.get("review_id"), texto=(ctx.get("texto") or "")[:200],
                    alvo_url=url_interna(ctx.get("alvo_url")))
    db.session.add(n)
    db.session.flush()
    return n


def deve_enviar_email(notificacao):
    """O destinatário quer este tipo por e-mail? Usada pela spec de e-mail (NTF-11)."""
    return notificacao is not None and preferencia(notificacao.user_id, notificacao.tipo)[1]


# ---------- avisos das ações sociais (NTF-04..06) ----------
def avisar_curtida(review, ator_id):
    """Uma notificação por curtidor e avaliação a cada 24 horas (NTF-04)."""
    if _recente(review.user_id, "curtida", ator_id=ator_id, review_id=review.id):
        return None
    return notificar(review.user_id, "curtida", ator_id=ator_id, review_id=review.id, game_id=review.game_id,
                     alvo_url=f"/jogos/{review.game.slug}#av-{review.id}")


def avisar_comentario(comment):
    review = comment.review
    return notificar(review.user_id, "comentario", ator_id=comment.user_id, review_id=review.id,
                     game_id=review.game_id, alvo_url=f"/jogos/{review.game.slug}#com-{comment.id}")


def avisar_seguidor(seguido_id, seguidor):
    """Uma vez por par a cada 24 horas, mesmo seguindo de novo (NTF-05)."""
    if _recente(seguido_id, "seguidor", ator_id=seguidor.id):
        return None
    return notificar(seguido_id, "seguidor", ator_id=seguidor.id, alvo_url=f"/u/{seguidor.username}")


def avisar_amigo_zerou(quem, game):
    """Quem segue `quem`, pode ver o perfil dele e tem o jogo planejado; até 200 (NTF-06)."""
    from .diario import pode_ver_perfil

    seguidores = (
        User.query.join(Follow, Follow.follower_id == User.id)
        .join(Status, (Status.user_id == User.id) & (Status.game_id == game.id) & (Status.value == "planejado"))
        .filter(Follow.followed_id == quem.id)
        .order_by(User.id)
    )
    criadas = []
    for destinatario in seguidores:
        if len(criadas) >= MAX_AMIGO_ZEROU:
            break
        if not pode_ver_perfil(destinatario, quem):  # DIA-12, a regra única
            continue
        n = notificar(destinatario.id, "amigo_zerou", ator_id=quem.id, game_id=game.id,
                      alvo_url=f"/jogos/{game.slug}")
        if n:
            criadas.append(n)
    return criadas


# ---------- telas (NTF-07..10) ----------
def visiveis(user_id):
    """Sem avaliação oculta pela moderação nem jogo removido (NTF-07)."""
    return (
        Notificacao.query.filter(Notificacao.user_id == user_id)
        .outerjoin(Review, Review.id == Notificacao.review_id)
        .outerjoin(Game, Game.id == Notificacao.game_id)
        .filter((Notificacao.review_id.is_(None)) | (Review.hidden.is_(False)),
                (Notificacao.game_id.is_(None)) | (Game.removed_at.is_(None)))
    )


def agrupar_curtidas(itens):
    """Curtidas não lidas da mesma avaliação viram uma linha só, sem apagar nada do banco (NTF-04)."""
    linhas, grupos = [], {}
    for n in itens:
        if n.tipo == "curtida" and n.lida_em is None and n.review_id:
            if n.review_id in grupos:
                grupos[n.review_id]["outros"] += 1
                continue
            grupos[n.review_id] = {"n": n, "outros": 0}
            linhas.append(grupos[n.review_id])
        else:
            linhas.append({"n": n, "outros": 0})
    return linhas


def rotulo_sino(contagem):
    return "9+" if contagem > 9 else (str(contagem) if contagem else "")


def cabecalho_do_usuario(user_id):
    """Sino (NTF-10) e nível com a barra de XP (PRG-10), numa única consulta."""
    from .models import PerfilProgressao
    from .progressao import porcentagem

    xp = db.session.query(PerfilProgressao.xp_total).filter_by(user_id=user_id).scalar_subquery()
    nivel = db.session.query(PerfilProgressao.nivel).filter_by(user_id=user_id).scalar_subquery()
    contagem, xp_total, nivel_atual = db.session.query(nao_lidas_subconsulta(user_id), xp, nivel).one()
    perfil = PerfilProgressao(xp_total=xp_total or 0, nivel=nivel_atual or 1)
    return {"sino": rotulo_sino(contagem or 0), "nivel": perfil.nivel, "pct": porcentagem(perfil)}


def nao_lidas_subconsulta(user_id):
    return (visiveis(user_id).filter(Notificacao.lida_em.is_(None)).with_entities(func.count())
            .scalar_subquery())


@bp.route("/notificacoes")
@login_required
def lista():
    me = current_user()
    page = paginate(visiveis(me.id).order_by(Notificacao.created_at.desc(), Notificacao.id.desc()),
                    request.args.get("page"))
    return render_template("notificacoes/lista.html", page=page, linhas=agrupar_curtidas(page.items))


def da_pessoa_ou_404(nid):
    n = db.session.get(Notificacao, nid)
    if n is None or n.user_id != current_user().id:
        abort(404)
    return n


@bp.route("/notificacoes/<int:nid>/ler", methods=["POST"])
@login_required
def ler(nid):
    n = da_pessoa_ou_404(nid)
    if n.tipo == "curtida" and n.review_id:  # a linha agrupada marca o grupo inteiro
        Notificacao.query.filter_by(user_id=n.user_id, tipo="curtida", review_id=n.review_id, lida_em=None).update(
            {"lida_em": now()})
    n.lida_em = n.lida_em or now()
    db.session.commit()
    return redirect(n.alvo_url or "/notificacoes")


@bp.route("/notificacoes/ler-todas", methods=["POST"])
@login_required
def ler_todas():
    Notificacao.query.filter_by(user_id=current_user().id, lida_em=None).update({"lida_em": now()})
    db.session.commit()
    return redirect("/notificacoes")


@bp.route("/notificacoes/preferencias", methods=["GET", "POST"])
@login_required
def preferencias():
    me = current_user()
    if request.method == "POST":
        atuais = {p.tipo: p for p in PreferenciaNotificacao.query.filter_by(user_id=me.id)}
        for tipo in TIPOS:  # tipo desconhecido no formulário é ignorado
            site = tipo in OBRIGATORIOS_NO_SITE or bool(request.form.get(f"site_{tipo}"))
            email = bool(request.form.get(f"email_{tipo}"))
            p = atuais.get(tipo) or PreferenciaNotificacao(user_id=me.id, tipo=tipo)
            p.site, p.email = site, email
            db.session.add(p)
        db.session.commit()
        return redirect("/notificacoes/preferencias")
    prefs = {tipo: preferencia(me.id, tipo) for tipo in TIPOS}
    return render_template("notificacoes/preferencias.html", tipos=TIPOS, prefs=prefs,
                           obrigatorios=OBRIGATORIOS_NO_SITE)
