"""Clubes de jogos (CLB-01..14): papéis, jogo do mês, metas coletivas pelo diário, mural moderado e ranking."""
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from . import gamificacao, rankings
from .diario import consulta_minutos, pode_ver_perfil
from .menores.politica import pode
from .models import (
    REPORT_REASONS, Activity, AuditLog, Clube, ContribuinteMeta, Genre, JogoDoMes, MembroClube, MetaClube,
    PostClube, Report, SessaoJogo, Status, User, db, now, visible_games,
)
from .notificacoes import notificar
from .tempo import hoje
from .util import admin_required, current_user, login_required, paginate, slugify

bp = Blueprint("clubes", __name__)
MAX_DONO = 5
TIPOS_META = {"horas_jogo": "Horas no jogo", "horas_total": "Horas em qualquer jogo",
              "zerados_jogo": "Membros que zeraram o jogo"}
COM_DATA = ("manual", "csv")  # a sessão de saldo da Steam traria anos de horas de uma vez (HRS-04)
OCULTAR_COM = 3
INDISPONIVEL = "Este clube não está disponível para a sua conta"


# ---------- acesso ----------
def membro(clube, user):
    if user is None:
        return None
    return MembroClube.query.filter_by(clube_id=clube.id, user_id=user.id).first()


def ativo(m):
    return m is not None and m.situacao == "ativo"


def modera(m):
    return ativo(m) and m.papel in ("dono", "moderador")


def clubes_como_dono(user_id):
    """Clubes ativos de que `user_id` é dono; removidos pelo admin não contam no limite (CLB-01)."""
    return (MembroClube.query.join(Clube, Clube.id == MembroClube.clube_id)
            .filter(MembroClube.user_id == user_id, MembroClube.papel == "dono", MembroClube.situacao == "ativo",
                    Clube.removido_em.is_(None)).count())


def clube_ou_404(slug):
    """Removido responde 404 a todos; privado, a quem não é membro ativo (CLB-02)."""
    clube = Clube.query.filter_by(slug=slug).first()
    if clube is None or clube.removido_em is not None:
        abort(404)
    me = current_user()
    m = membro(clube, me)
    if not clube.publico and not ativo(m):
        abort(404)
    return clube, me, m


def exigir(condicao):
    if not condicao:
        abort(403)


# ---------- criação e listagem (CLB-01, CLB-02) ----------
def validar_clube(form):
    erros, dados = {}, {}
    dados["nome"] = form.get("nome", "").strip()
    if not 3 <= len(dados["nome"]) <= 60:
        erros["nome"] = "O nome deve ter de 3 a 60 caracteres."
    elif Clube.query.filter_by(nome_lower=dados["nome"].lower()).first():
        erros["nome"] = "Já existe um clube com esse nome."
    dados["descricao"] = form.get("descricao", "").strip()
    if len(dados["descricao"]) > 500:
        erros["descricao"] = "A descrição deve ter até 500 caracteres."
    dados["genero_id"] = None
    if form.get("genero"):
        genero = Genre.query.filter_by(slug=form["genero"]).first()
        if genero is None:
            erros["genero"] = "Escolha um gênero existente."
        else:
            dados["genero_id"] = genero.id
    dados["publico"] = form.get("visibilidade") != "privado"
    return dados, erros


def slug_livre(nome):
    base = slugify(nome) or "clube"
    slug, n = base, 2
    while Clube.query.filter_by(slug=slug).first():
        slug, n = f"{base}-{n}", n + 1
    return slug


@bp.route("/clubes", methods=["GET"])
def lista():
    me, termo = current_user(), request.args.get("q", "").strip()
    meus = db.session.query(MembroClube.clube_id).filter(MembroClube.user_id == (me.id if me else -1),
                                                         MembroClube.situacao == "ativo")
    q = Clube.query.filter(Clube.removido_em.is_(None), Clube.publico.is_(True) | Clube.id.in_(meus))
    if termo:
        q = q.filter(Clube.nome_lower.contains(termo.lower()))
    page = paginate(q.order_by(Clube.nome_lower), request.args.get("page"))
    return render_template("clubes/lista.html", page=page, q=termo)


@bp.route("/clubes/novo", methods=["GET", "POST"])
@login_required
def novo():
    me = current_user()
    generos = Genre.query.order_by(Genre.name).all()
    if request.method == "GET":
        return render_template("clubes/novo.html", erros={}, form={}, generos=generos)
    dados, erros = validar_clube(request.form)
    if clubes_como_dono(me.id) >= MAX_DONO:
        erros["nome"] = f"Você já é dono de {MAX_DONO} clubes, o máximo permitido."
    if erros:
        return render_template("clubes/novo.html", erros=erros, form=request.form, generos=generos), 400
    clube = Clube(nome_lower=dados["nome"].lower(), slug=slug_livre(dados["nome"]), **dados)
    db.session.add(clube)
    db.session.flush()
    db.session.add(MembroClube(clube_id=clube.id, user_id=me.id, papel="dono", entrou_em=hoje()))
    db.session.commit()
    return redirect(f"/clubes/{clube.slug}")


# ---------- página do clube ----------
def jogo_do_mes(clube, membros_ids):
    """Jogo do mês atual e as horas dos membros nele no mês (CLB-05)."""
    mes = hoje().strftime("%Y-%m")
    jdm = JogoDoMes.query.filter_by(clube_id=clube.id, mes=mes).first()
    if jdm is None:
        return None, 0
    inicio = hoje().replace(day=1)
    sub = consulta_minutos(inicio, hoje(), COM_DATA).filter(SessaoJogo.game_id == jdm.game_id,
                                                           SessaoJogo.user_id.in_(membros_ids)).subquery()
    return jdm, db.session.query(func.coalesce(func.sum(sub.c.duracao_min), 0)).scalar()


def contribuicoes(meta):
    """{user_id: valor} de cada membro ativo, contando só o período e desde a entrada dele (CLB-07)."""
    membros = (db.session.query(MembroClube.user_id, MembroClube.entrou_em)
               .filter_by(clube_id=meta.clube_id, situacao="ativo").subquery())
    if meta.tipo == "zerados_jogo":
        q = (db.session.query(Status.user_id, func.count())
             .join(membros, membros.c.user_id == Status.user_id)
             .filter(Status.game_id == meta.game_id, Status.zerado_em.between(meta.inicio, meta.fim),
                     Status.zerado_em >= membros.c.entrou_em)
             .group_by(Status.user_id))
    else:
        sub = consulta_minutos(meta.inicio, meta.fim, COM_DATA).subquery()
        q = (db.session.query(sub.c.user_id, func.sum(sub.c.duracao_min))
             .join(membros, membros.c.user_id == sub.c.user_id)
             .filter(sub.c.jogada_em >= membros.c.entrou_em)
             .group_by(sub.c.user_id))
        if meta.tipo == "horas_jogo":
            q = q.filter(sub.c.game_id == meta.game_id)
    return {u: v for u, v in q if v}


def progresso(meta):
    """(valor no mesmo formato do alvo, top 5 [(user_id, valor)])."""
    contrib = contribuicoes(meta)
    if meta.tipo == "zerados_jogo":
        total = len(contrib)
    else:
        total = sum(contrib.values()) // 60
    top = sorted(contrib.items(), key=lambda x: (-x[1], x[0]))[:5]
    return total, top


def avaliar_meta(meta, clube):
    """Marca a meta como atingida uma única vez e avisa (CLB-08); quem chama faz o commit."""
    if meta.atingida_em is not None or not meta.inicio <= hoje() or meta.fim < meta.inicio:
        return False
    total, _ = progresso(meta)
    if total < meta.alvo:
        return False
    marcada = (MetaClube.query.filter(MetaClube.id == meta.id, MetaClube.atingida_em.is_(None))
               .update({"atingida_em": now()}, synchronize_session=False))
    if not marcada:  # outro evento chegou antes
        return False
    contribuintes = list(contribuicoes(meta))
    for user_id in contribuintes:
        db.session.add(ContribuinteMeta(meta_id=meta.id, user_id=user_id))
    for (user_id,) in db.session.query(MembroClube.user_id).filter_by(clube_id=clube.id, situacao="ativo"):
        notificar(user_id, "clube", texto=f"O clube {clube.nome} atingiu a meta: {TIPOS_META[meta.tipo]} ({meta.alvo})",
                  alvo_url=f"/clubes/{clube.slug}")
    if clube.publico:
        dono = MembroClube.query.filter_by(clube_id=clube.id, papel="dono").first()
        db.session.add(Activity(user_id=dono.user_id, kind="clube", texto=f"comemora: o clube {clube.nome} atingiu uma meta"))
    for user_id in contribuintes:
        gamificacao.adiar_evento(user_id, "meta_clube", meta_id=meta.id)
    return True


@gamificacao.ouvir
def ao_evento(user_id, tipo, ctx):
    """Reavalia as metas abertas dos clubes do usuário a cada sessão ou jogo zerado (CLB-08)."""
    if tipo not in ("sessao", "importacao"):
        return
    abertas = (MetaClube.query.join(MembroClube, (MembroClube.clube_id == MetaClube.clube_id)
                                    & (MembroClube.user_id == user_id) & (MembroClube.situacao == "ativo"))
               .join(Clube, Clube.id == MetaClube.clube_id)
               .filter(MetaClube.atingida_em.is_(None), Clube.removido_em.is_(None)).all())
    for meta in abertas:
        avaliar_meta(meta, db.session.get(Clube, meta.clube_id))


gamificacao.registrar_metrica(
    "metas_clube", ("meta_clube",),
    lambda user_id: db.session.query(func.count(ContribuinteMeta.id)).filter_by(user_id=user_id).scalar() or 0)


def top_horas(clube):
    """Top 5 de horas da temporada atual entre os membros (CLB-09)."""
    temporadas = rankings.Temporada.query.all()
    temporada, de, ate = rankings.janela("atual", temporadas)
    ids = db.session.query(MembroClube.user_id).filter_by(clube_id=clube.id, situacao="ativo")
    return rankings.apurar("horas", de, ate, temporada, ids=ids, visitante=current_user())[:5]


def escopo_clube(me, args):
    clube = Clube.query.filter_by(slug=args.get("clube", "")).first()
    if clube is None or clube.removido_em is not None or (not clube.publico and not ativo(membro(clube, me))):
        abort(404)  # RNK-04: clube privado responde 404 a quem não é membro
    ids = db.session.query(MembroClube.user_id).filter_by(clube_id=clube.id, situacao="ativo")
    return ids, {"clube": clube}


rankings.registrar_escopo("clube", escopo_clube)


@bp.route("/clubes/<slug>")
def pagina(slug):
    clube, me, m = clube_ou_404(slug)
    membros = (db.session.query(MembroClube, User).join(User, User.id == MembroClube.user_id)
               .filter(MembroClube.clube_id == clube.id, MembroClube.situacao == "ativo")
               .order_by(MembroClube.papel != "dono", MembroClube.papel != "moderador", User.username_lower).all())
    ids = [u.id for _, u in membros]
    usuarios = {u.id: u for _, u in membros}
    jdm, minutos_jdm = jogo_do_mes(clube, ids)
    metas = [(meta, *progresso(meta)) for meta in
             MetaClube.query.filter_by(clube_id=clube.id).order_by(MetaClube.fim.desc(), MetaClube.id.desc())]
    mural = PostClube.query.filter_by(clube_id=clube.id)
    if not modera(m):
        mural = mural.filter(PostClube.oculto.is_(False))
    mural = paginate(mural.order_by(PostClube.created_at.desc(), PostClube.id.desc()), request.args.get("page"))
    pendentes = (MembroClube.query.filter_by(clube_id=clube.id, situacao="pendente").all() if modera(m) else [])

    def nome_visivel(user_id):
        u = usuarios.get(user_id)
        return u.display_name if u and pode_ver_perfil(me, u) else "membro do clube"

    def autor(user_id):
        """(usuário para o avatar, ou None, e o nome); perfil privado e ex-membro ficam sem iniciais."""
        u = usuarios.get(user_id)
        if u is None:
            return None, "ex-membro"
        return (u, u.display_name) if pode_ver_perfil(me, u) else (None, "membro do clube")
    return render_template(
        "clubes/clube.html", clube=clube, m=m, membros=membros, jdm=jdm, minutos_jdm=minutos_jdm, metas=metas,
        mural=mural, pendentes=pendentes, tipos=TIPOS_META, nome_visivel=nome_visivel, autor=autor,
        top=top_horas(clube),
        reasons=REPORT_REASONS, modera=modera(m), ativo=ativo(m),
    )


# ---------- entrada, saída e papéis (CLB-03, CLB-04, CLB-12) ----------
@bp.route("/clubes/<slug>/entrar", methods=["POST"])
@login_required
def entrar(slug):
    clube = Clube.query.filter_by(slug=slug, removido_em=None).first_or_404()
    me = current_user()
    if not pode(me, "entrar_clube", clube):
        flash(INDISPONIVEL, "erro")
        return redirect("/clubes")
    m = membro(clube, me)
    if m is not None and m.situacao in ("ativo", "pendente"):
        return redirect(f"/clubes/{slug}" if m.situacao == "ativo" else "/clubes")
    if m is not None and m.situacao == "removido":
        abort(403)
    try:
        db.session.add(MembroClube(clube_id=clube.id, user_id=me.id, situacao="ativo" if clube.publico else "pendente",
                                   entrou_em=hoje() if clube.publico else None))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
    if not clube.publico:
        flash("Pedido enviado aos moderadores do clube.", "ok")
        return redirect("/clubes")
    return redirect(f"/clubes/{slug}")


@bp.route("/clubes/<slug>/sair", methods=["POST"])
@login_required
def sair(slug):
    clube, me, m = clube_ou_404(slug)
    exigir(ativo(m))
    if m.papel == "dono":
        flash("Transfira o papel de dono a outro membro antes de sair.", "erro")
        return redirect(f"/clubes/{slug}")
    db.session.delete(m)
    db.session.commit()
    return redirect("/clubes")


def alvo_do_clube(clube, user_id):
    alvo = MembroClube.query.filter_by(clube_id=clube.id, user_id=user_id).first()
    if alvo is None:
        abort(404)
    return alvo


@bp.route("/clubes/<slug>/pedidos/<int:user_id>/<decisao>", methods=["POST"])
@login_required
def decidir_pedido(slug, user_id, decisao):
    clube, me, m = clube_ou_404(slug)
    exigir(modera(m))
    alvo = alvo_do_clube(clube, user_id)
    if alvo.situacao != "pendente" or decisao not in ("aprovar", "recusar"):
        abort(400)
    if decisao == "aprovar":
        alvo.situacao, alvo.entrou_em = "ativo", hoje()
        notificar(user_id, "clube", texto=f"Seu pedido para entrar em {clube.nome} foi aprovado",
                  alvo_url=f"/clubes/{slug}")
    else:
        db.session.delete(alvo)
    db.session.commit()
    return redirect(f"/clubes/{slug}")


@bp.route("/clubes/<slug>/membros/<int:user_id>/<acao>", methods=["POST"])
@login_required
def papel(slug, user_id, acao):
    """Dono promove, rebaixa e transfere; moderadores e dono removem membros comuns (CLB-04)."""
    clube, me, m = clube_ou_404(slug)
    alvo = alvo_do_clube(clube, user_id)
    exigir(ativo(alvo) and alvo.user_id != me.id)
    if acao == "remover":
        exigir(modera(m) and (alvo.papel == "membro" or (m.papel == "dono" and alvo.papel == "moderador")))
        alvo.situacao, alvo.papel = "removido", "membro"
    elif acao in ("promover", "rebaixar"):
        exigir(ativo(m) and m.papel == "dono" and alvo.papel != "dono")
        alvo.papel = "moderador" if acao == "promover" else "membro"
    elif acao == "transferir":
        exigir(ativo(m) and m.papel == "dono")
        if clubes_como_dono(alvo.user_id) >= MAX_DONO:
            flash(f"Esse membro já é dono de {MAX_DONO} clubes, o máximo permitido.", "erro")
            return redirect(f"/clubes/{slug}")
        alvo.papel, m.papel = "dono", "moderador"
    else:
        abort(404)
    db.session.commit()
    return redirect(f"/clubes/{slug}")


# ---------- jogo do mês e metas (CLB-05, CLB-06) ----------
@bp.route("/clubes/<slug>/jogo-do-mes", methods=["POST"])
@login_required
def definir_jogo_do_mes(slug):
    clube, me, m = clube_ou_404(slug)
    exigir(modera(m))
    mes = request.form.get("mes", "")
    game = visible_games().filter_by(slug=request.form.get("jogo", "")).first()
    try:
        date.fromisoformat(mes + "-01")
    except ValueError:
        game = None
    if game is None:
        flash("Escolha um jogo do catálogo e um mês no formato AAAA-MM.", "erro")
        return redirect(f"/clubes/{slug}")
    jdm = JogoDoMes.query.filter_by(clube_id=clube.id, mes=mes).first() or JogoDoMes(clube_id=clube.id, mes=mes)
    jdm.game_id = game.id
    db.session.add(jdm)
    db.session.commit()
    return redirect(f"/clubes/{slug}")


@bp.route("/clubes/<slug>/metas", methods=["POST"])
@login_required
def nova_meta(slug):
    clube, me, m = clube_ou_404(slug)
    exigir(modera(m))
    f, erros = request.form, []
    tipo = f.get("tipo")
    if tipo not in TIPOS_META:
        erros.append("Escolha o tipo da meta.")
    try:
        alvo = int(f.get("alvo", ""))
        if alvo < 1:
            raise ValueError
    except ValueError:
        erros.append("O alvo deve ser um inteiro maior ou igual a 1.")
    try:
        inicio, fim = date.fromisoformat(f.get("inicio", "")), date.fromisoformat(f.get("fim", ""))
        if fim < inicio:
            erros.append("O fim deve ser igual ou depois do início.")
    except ValueError:
        erros.append("Informe as datas de início e fim.")
    game = None
    if f.get("jogo"):
        game = visible_games().filter_by(slug=f["jogo"]).first()
        if game is None:
            erros.append("Jogo não encontrado no catálogo.")
    if tipo in ("horas_jogo", "zerados_jogo") and game is None and not erros:
        erros.append("Esse tipo de meta precisa de um jogo.")
    if erros:
        for e in erros:
            flash(e, "erro")
        return redirect(f"/clubes/{slug}")
    meta = MetaClube(clube_id=clube.id, tipo=tipo, alvo=alvo, inicio=inicio, fim=fim, game_id=game.id if game else None)
    db.session.add(meta)
    db.session.flush()
    avaliar_meta(meta, clube)
    db.session.commit()
    gamificacao.despachar_adiados()
    return redirect(f"/clubes/{slug}")


# ---------- mural e denúncias (CLB-10, CLB-11, CLB-12) ----------
def post_do_clube(clube, pid):
    post = db.session.get(PostClube, pid)
    if post is None or post.clube_id != clube.id:
        abort(404)
    return post


@bp.route("/clubes/<slug>/mural", methods=["POST"])
@login_required
def publicar(slug):
    clube, me, m = clube_ou_404(slug)
    exigir(ativo(m))
    if not pode(me, "publicar_clube", clube):
        flash(INDISPONIVEL, "erro")
        return redirect(f"/clubes/{slug}")
    texto = request.form.get("texto", "").strip()
    if not 1 <= len(texto) <= 1000:
        flash("O post deve ter de 1 a 1.000 caracteres.", "erro")
        return redirect(f"/clubes/{slug}")
    db.session.add(PostClube(clube_id=clube.id, user_id=me.id, texto=texto, spoiler=bool(request.form.get("spoiler"))))
    db.session.commit()
    return redirect(f"/clubes/{slug}#mural")


@bp.route("/clubes/<slug>/mural/<int:pid>/apagar", methods=["POST"])
@login_required
def apagar_post(slug, pid):
    clube, me, m = clube_ou_404(slug)
    post = post_do_clube(clube, pid)
    exigir(post.user_id == me.id)
    Report.query.filter_by(alvo_tipo="post_clube", alvo_id=post.id).delete()
    db.session.delete(post)
    db.session.commit()
    return redirect(f"/clubes/{slug}#mural")


@bp.route("/clubes/<slug>/mural/<int:pid>/ocultar", methods=["POST"])
@login_required
def ocultar_post(slug, pid):
    clube, me, m = clube_ou_404(slug)
    exigir(modera(m))
    post = post_do_clube(clube, pid)
    motivo = request.form.get("motivo", "").strip()
    if not 1 <= len(motivo) <= 200:
        flash("Informe o motivo da ocultação.", "erro")
        return redirect(f"/clubes/{slug}#mural")
    post.oculto, post.motivo_ocultacao, post.oculto_por = True, motivo, me.id
    _decidir_denuncias(post, "ocultada", me)
    db.session.commit()
    return redirect(f"/clubes/{slug}#mural")


def _decidir_denuncias(post, status, me):
    for r in Report.query.filter_by(alvo_tipo="post_clube", alvo_id=post.id, status="pendente"):
        r.status, r.decided_by, r.decided_at = status, me.id, now()


@bp.route("/clubes/<slug>/mural/<int:pid>/denunciar", methods=["POST"])
@login_required
def denunciar(slug, pid):
    clube, me, m = clube_ou_404(slug)
    exigir(ativo(m))
    post = post_do_clube(clube, pid)
    reason = request.form.get("reason", "")
    if post.user_id == me.id:
        flash("Você não pode denunciar o próprio post.", "erro")
    elif reason not in REPORT_REASONS:
        flash("Escolha o motivo da denúncia.", "erro")
    elif Report.query.filter_by(alvo_tipo="post_clube", alvo_id=post.id, reporter_id=me.id).first():
        flash("Você já denunciou este post.", "erro")
    else:
        db.session.add(Report(alvo_tipo="post_clube", alvo_id=post.id, reporter_id=me.id, reason=reason,
                              comment=request.form.get("comment", "").strip()[:500]))
        db.session.flush()
        pendentes = Report.query.filter_by(alvo_tipo="post_clube", alvo_id=post.id, status="pendente").count()
        if pendentes >= OCULTAR_COM:
            post.oculto, post.motivo_ocultacao = True, "ocultado automaticamente por denúncias"
        db.session.commit()
        flash("Denúncia enviada aos moderadores do clube.", "ok")
    return redirect(f"/clubes/{slug}#mural")


@bp.route("/clubes/<slug>/moderacao", methods=["GET", "POST"])
@login_required
def moderacao(slug):
    clube, me, m = clube_ou_404(slug)
    exigir(modera(m))
    if request.method == "POST":
        post = post_do_clube(clube, request.form.get("post", 0, type=int))
        if request.form.get("decisao") == "manter":
            post.oculto, post.motivo_ocultacao = False, ""
            _decidir_denuncias(post, "rejeitada", me)
        else:
            post.oculto, post.oculto_por = True, me.id
            post.motivo_ocultacao = post.motivo_ocultacao or "denúncia aceita"
            _decidir_denuncias(post, "ocultada", me)
        db.session.commit()
        return redirect(f"/clubes/{slug}/moderacao")
    denuncias = (db.session.query(Report, PostClube).join(PostClube, PostClube.id == Report.alvo_id)
                 .filter(Report.alvo_tipo == "post_clube", PostClube.clube_id == clube.id)
                 .order_by(Report.status != "pendente", Report.created_at.desc()).all())
    return render_template("clubes/moderacao.html", clube=clube, denuncias=denuncias, reasons=REPORT_REASONS)


# ---------- admin (CLB-13) ----------
@bp.route("/admin/clubes")
@admin_required
def admin_lista():
    page = paginate(Clube.query.order_by(Clube.nome_lower), request.args.get("page"))
    return render_template("clubes/admin.html", page=page)


@bp.route("/admin/clubes/<int:cid>/<acao>", methods=["POST"])
@admin_required
def admin_acao(cid, acao):
    clube = db.get_or_404(Clube, cid)
    if acao == "remover":
        clube.removido_em = clube.removido_em or now()
    elif acao == "restaurar":
        clube.removido_em = None
    else:
        abort(404)
    db.session.add(AuditLog(admin_id=current_user().id, action=f"clube.{acao}", detail=clube.slug))
    db.session.commit()
    return redirect("/admin/clubes")

