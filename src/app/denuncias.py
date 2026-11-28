"""Denúncias e risco a menores (DEN-01..12): alvos, categoria de risco, decisão fundamentada, recurso e trilha.

ECA Digital (Lei 15.211/2025): notificação identificada (art. 29), decisão fundamentada com recurso (art. 30) e,
nos casos graves, ocultação, preservação e comunicação às autoridades registrada pelo admin (art. 27).
"""
import os
import re
from datetime import timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request
from sqlalchemy.exc import IntegrityError

from .admin import audit
from .models import (REPORT_REASONS, Clube, Comment, ComunicacaoAutoridade, EventoDenuncia, PostClube, Report,
                     Review, User, db, now)
from .notificacoes import notificar
from .util import admin_required, current_user, login_required, paginate

bp = Blueprint("denuncias", __name__)

RISCO = "risco_menor"
SUBCATEGORIAS = {
    "aliciamento": "Aliciamento",
    "exploracao_sexual": "Exploração sexual",
    "conteudo_sexual_menor": "Conteúdo sexual envolvendo menor",
    "contato_inadequado": "Contato inadequado com menor",
    "usuario_menor_de_12": "Usuário com menos de 12 anos",
    "outro_risco": "Outro risco a menor",
}
CAUTELAR = ("exploracao_sexual", "conteudo_sexual_menor")
GRAVES = ("aliciamento", *CAUTELAR)
QUALIDADES = {"vitima": "Vítima", "responsavel": "Responsável pela vítima", "ministerio_publico": "Ministério Público",
              "entidade": "Entidade de proteção"}
ALVOS = {"avaliacao": "esta avaliação", "comentario": "este comentário", "perfil": "este perfil",
         "post_clube": "este post"}
PROPRIO = {"avaliacao": "a própria avaliação", "comentario": "o próprio comentário", "perfil": "o próprio perfil",
           "post_clube": "o próprio post"}
DECISOES = {"ocultar": "ocultada", "suspender": "suspensa", "restaurar": "restaurada", "rejeitar": "rejeitada"}
RECORRIVEIS = ("ocultar", "suspender")
MIN_FUNDAMENTACAO = 20
MIN_RISCO, MAX_RISCO = 10, 1000
RECURSO_DIAS = 15
LINKS = (("comentario", re.compile(r"#com-(\d+)")), ("avaliacao", re.compile(r"#av-(\d+)")),
         ("post_clube", re.compile(r"#post-(\d+)")), ("perfil", re.compile(r"/u/([A-Za-z0-9_]+)/?$")))


def _horas(variavel, padrao):
    try:
        return max(1, int(os.environ.get(variavel) or padrao))
    except ValueError:
        return padrao


def prazo_horas():
    return _horas("PRAZO_DENUNCIA_MENOR_HORAS", 24)


def preservacao_dias():
    return _horas("PRESERVACAO_DIAS", 180)


# ---------- alvos (DEN-01) ----------
def alvo(tipo, alvo_id):
    modelo = {"avaliacao": Review, "comentario": Comment, "perfil": User, "post_clube": PostClube}.get(tipo)
    return db.session.get(modelo, alvo_id) if modelo and alvo_id is not None else None


def alvo_da_denuncia(rep):
    return alvo(rep.alvo_tipo, rep.review_id if rep.alvo_tipo == "avaliacao" and rep.review_id else rep.alvo_id)


def autor(tipo, obj):
    if obj is None:
        return None
    return obj if tipo == "perfil" else db.session.get(User, obj.user_id)


def ocultar(tipo, obj, oculto):
    if tipo == "avaliacao":
        obj.hidden = oculto
    elif tipo in ("comentario", "post_clube"):
        obj.oculto = oculto


def caminho(tipo, obj):
    if obj is None:
        return "/"
    if tipo == "avaliacao":
        return f"/jogos/{obj.game.slug}#av-{obj.id}"
    if tipo == "comentario":
        return f"/jogos/{obj.review.game.slug}#com-{obj.id}"
    if tipo == "perfil":
        return f"/u/{obj.username}"
    return f"/clubes/{db.session.get(Clube, obj.clube_id).slug}#post-{obj.id}"


def preservado(obj):
    return obj is not None and obj.preservar_ate is not None and obj.preservar_ate > now()


def preservar(tipo, obj, base):
    """Conteúdo e conta da denúncia grave ficam guardados até `base` + PRESERVACAO_DIAS (DEN-09)."""
    ate = base + timedelta(days=preservacao_dias())
    for linha in {obj, autor(tipo, obj)}:
        linha.preservar_ate = max(linha.preservar_ate or ate, ate)
    return ate


def evento(rep, tipo, detalhe="", autor_id=None):
    db.session.add(EventoDenuncia(report_id=rep.id, tipo=tipo, detalhe=detalhe[:1000], autor_id=autor_id))


# ---------- criar (DEN-01..03, DEN-05) ----------
def validar(form):
    """(motivo, subcategoria, comentário) ou a mensagem de erro."""
    motivo, comentario = form.get("reason", ""), (form.get("comment") or "").strip()
    if motivo == RISCO:
        sub = form.get("subcategoria")
        if sub not in SUBCATEGORIAS:
            return "Escolha o tipo de risco a menor."
        if not MIN_RISCO <= len(comentario) <= MAX_RISCO:
            return f"Descreva o risco em {MIN_RISCO} a {MAX_RISCO} caracteres."
        return motivo, sub, comentario
    if motivo not in REPORT_REASONS:
        return "Escolha o motivo da denúncia."
    if len(comentario) > 500:
        return "O comentário deve ter até 500 caracteres."
    return motivo, None, comentario


def criar(tipo, obj, motivo, sub, comentario, reporter_id=None, **notificante):
    """Grava a denúncia e a trilha; nos casos graves, oculta (cautelar) e preserva. Quem chama faz o commit."""
    rep = Report(alvo_tipo=tipo, alvo_id=obj.id if obj else None, reporter_id=reporter_id, reason=motivo,
                 review_id=obj.id if obj is not None and tipo == "avaliacao" else None, subcategoria=sub,
                 comment=comentario, created_at=now(), **notificante)
    if motivo == RISCO:
        rep.prazo_em = rep.created_at + timedelta(hours=prazo_horas())
    db.session.add(rep)
    db.session.flush()
    evento(rep, "criada", SUBCATEGORIAS.get(sub, REPORT_REASONS.get(motivo, "")), reporter_id)
    if sub in CAUTELAR and obj is not None and tipo != "perfil":
        ocultar(tipo, obj, True)
        rep.cautelar = True
        evento(rep, "ocultada_cautelar")
    if sub in GRAVES and obj is not None:
        evento(rep, "preservacao", f"até {preservar(tipo, obj, rep.created_at):%d/%m/%Y}")
    return rep


def denunciar(me, tipo, obj, form):
    """Mensagem de erro ou None; regra de AVA-08 para os motivos antigos e o próprio conteúdo recusado (DEN-01).

    Grava e faz o commit; o duplo envio simultâneo vira a mesma mensagem de denúncia repetida.
    """
    if autor(tipo, obj).id == me.id:
        return f"Você não pode denunciar {PROPRIO[tipo]}."
    validado = validar(form)
    if isinstance(validado, str):
        return validado
    ja = Report.query.filter_by(reporter_id=me.id, alvo_tipo=tipo, alvo_id=obj.id)
    if tipo == "avaliacao":
        ja = Report.query.filter(Report.reporter_id == me.id, Report.review_id == obj.id)
    if ja.first():
        return f"Você já denunciou {ALVOS[tipo]}."
    try:
        criar(tipo, obj, *validado, reporter_id=me.id)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return f"Você já denunciou {ALVOS[tipo]}."
    return None


@bp.route("/denunciar/<tipo>/<int:alvo_id>", methods=["POST"])
@login_required
def denunciar_rota(tipo, alvo_id):
    obj = alvo(tipo, alvo_id)
    if obj is None or tipo not in ALVOS:
        abort(404)
    erro = denunciar(current_user(), tipo, obj, request.form)
    flash(erro or "Denúncia enviada. Obrigado!", "erro" if erro else "ok")
    return redirect(caminho(tipo, obj))


# ---------- notificação pública identificada (DEN-03) ----------
def alvo_do_link(link):
    for tipo, padrao in LINKS:
        if m := padrao.search(link):
            if tipo == "perfil":
                obj = User.query.filter_by(username_lower=m.group(1).lower()).first()
            else:
                obj = alvo(tipo, int(m.group(1)))
            if obj is not None:
                return tipo, obj
    return "link", None


@bp.route("/denunciar-risco-a-menor", methods=["GET", "POST"])
def publica():
    from .auth import EMAIL_RE

    form = request.form
    if request.method == "GET":
        return render_template("denuncias/publica.html", erro=None, form={}, qualidades=QUALIDADES,
                               subcategorias=SUBCATEGORIAS)
    nome, email = (form.get("nome") or "").strip(), (form.get("email") or "").strip().lower()
    qualidade, link = form.get("qualidade"), (form.get("link") or "").strip()
    descricao, sub = (form.get("descricao") or "").strip(), form.get("subcategoria")
    erro = None
    if not nome or not EMAIL_RE.match(email) or qualidade not in QUALIDADES:
        erro = "Identifique-se com nome, e-mail e qualidade: não recebemos notificação anônima."
    elif not link or sub not in SUBCATEGORIAS or not MIN_RISCO <= len(descricao) <= MAX_RISCO:
        erro = f"Informe o link, o tipo de risco e a descrição ({MIN_RISCO} a {MAX_RISCO} caracteres)."
    if erro:
        return render_template("denuncias/publica.html", erro=erro, form=form, qualidades=QUALIDADES,
                               subcategorias=SUBCATEGORIAS), 400
    tipo, obj = alvo_do_link(link)
    criar(tipo, obj, RISCO, sub, descricao, notificante_nome=nome[:120], notificante_email=email,
          qualidade=qualidade, link=link[:500])
    db.session.commit()
    flash("Notificação recebida. A moderação analisa os casos de risco a menores com prioridade.", "ok")
    return redirect("/denunciar-risco-a-menor")


# ---------- decisão e recurso (DEN-06, DEN-07, DEN-11) ----------
def decidir(rep, decisao, fundamentacao, admin):
    """None ou (mensagem, status). Encerra as denúncias pendentes do mesmo alvo; quem chama faz o commit."""
    fundamentacao = (fundamentacao or "").strip()
    if rep.status != "pendente":
        return "Esta denúncia já foi decidida.", 400
    if decisao not in DECISOES or len(fundamentacao) < MIN_FUNDAMENTACAO:
        return f"Escolha a decisão e escreva a fundamentação (ao menos {MIN_FUNDAMENTACAO} caracteres).", 400
    obj = alvo_da_denuncia(rep)
    if decisao == "ocultar" and rep.alvo_tipo == "perfil":
        return "Para um perfil, a decisão é suspender a conta ou rejeitar a denúncia.", 400
    outros_admins = User.query.filter(User.role == "admin", User.id != admin.id).count()
    if rep.recurso_em is not None and rep.primeiro_decisor == admin.id and outros_admins:
        return "O recurso é decidido por outro administrador.", 403
    dono = autor(rep.alvo_tipo, obj)
    if obj is not None and rep.alvo_tipo != "perfil":
        if decisao in RECORRIVEIS:
            ocultar(rep.alvo_tipo, obj, True)
        elif rep.cautelar or rep.recurso_em is not None:
            ocultar(rep.alvo_tipo, obj, False)
    if decisao == "suspender" and dono is not None:
        dono.suspensa_em, dono.session_version = dono.suspensa_em or now(), dono.session_version + 1
    mesmo_alvo = Report.query.filter(Report.alvo_tipo == rep.alvo_tipo, Report.alvo_id == rep.alvo_id,
                                     Report.status == "pendente", Report.alvo_id.isnot(None)).all()
    for r in {rep, *mesmo_alvo}:
        r.status, r.decisao, r.fundamentacao = DECISOES[decisao], decisao, fundamentacao
        r.decided_by, r.decided_at = admin.id, now()
        r.primeiro_decisor = r.primeiro_decisor or admin.id
        evento(r, "decidida", f"{decisao}: {fundamentacao}", admin.id)
        if r.reporter_id and r.reason == RISCO:
            notificar(r.reporter_id, "sistema", texto="Sua denúncia de risco a menor foi analisada pela moderação.")
    if rep.subcategoria in GRAVES and obj is not None:
        evento(rep, "preservacao", f"até {preservar(rep.alvo_tipo, obj, rep.decided_at):%d/%m/%Y}", admin.id)
    if rep.subcategoria == "usuario_menor_de_12" and decisao in RECORRIVEIS and dono is not None:
        dono.idade_contestada_em = dono.idade_contestada_em or now()
        dono.aguardando_responsavel, dono.perfil_privado = True, True
    if dono is not None:
        notificar(dono.id, "sistema", alvo_url=f"/denuncias/{rep.id}/recurso",
                  texto=f"Decisão da moderação ({DECISOES[decisao]}): {fundamentacao}")
    audit(f"denuncia.{DECISOES[decisao]}", f"denúncia #{rep.id} ({rep.alvo_tipo} #{rep.alvo_id})")
    return None


def pode_recorrer(rep, user):
    obj = alvo_da_denuncia(rep)
    dono = autor(rep.alvo_tipo, obj)
    return (dono is not None and user is not None and dono.id == user.id and rep.decisao in RECORRIVEIS
            and rep.recurso_em is None and rep.decided_at is not None
            and now() - rep.decided_at <= timedelta(days=RECURSO_DIAS))


@bp.route("/denuncias/<int:rid>/recurso", methods=["GET", "POST"])
@login_required
def recurso(rid):
    """Uma vez, em até 15 dias; volta para a fila marcado como recurso (DEN-07)."""
    rep, me = db.get_or_404(Report, rid), current_user()
    dono = autor(rep.alvo_tipo, alvo_da_denuncia(rep))
    if dono is None or dono.id != me.id:
        abort(404)
    if request.method == "POST":
        texto = (request.form.get("texto") or "").strip()
        if not pode_recorrer(rep, me):
            flash("O recurso já foi usado ou o prazo de 15 dias passou.", "erro")
        elif not MIN_RISCO <= len(texto) <= MAX_RISCO:
            flash(f"Explique o recurso em {MIN_RISCO} a {MAX_RISCO} caracteres.", "erro")
        else:
            rep.recurso_em, rep.recurso_texto, rep.status = now(), texto, "pendente"
            evento(rep, "recurso", texto, me.id)
            db.session.commit()
            flash("Recurso enviado. Outra pessoa da moderação vai rever a decisão.", "ok")
        return redirect(f"/denuncias/{rep.id}/recurso")
    return render_template("denuncias/recurso.html", rep=rep, pode=pode_recorrer(rep, me), decisoes=DECISOES)


# ---------- fila do admin (DEN-04, DEN-08, DEN-10) ----------
def comunicacao_pendente(rep):
    return rep.subcategoria in GRAVES and not ComunicacaoAutoridade.query.filter_by(report_id=rep.id).first()


def fila_risco():
    return (Report.query.filter(Report.reason == RISCO, Report.status == "pendente")
            .order_by(Report.created_at, Report.id))


def decidir_pela_fila(rid, decisao):
    """As rotas antigas de ocultar e rejeitar passam pela mesma decisão fundamentada (ADM-06, DEN-06)."""
    rep = db.get_or_404(Report, rid)
    if rep.alvo_tipo == "post_clube" and rep.reason != RISCO:
        abort(404)
    erro = decidir(rep, decisao, request.form.get("fundamentacao"), current_user())
    if erro:
        flash(erro[0], "erro")
        return redirect(f"/admin/denuncias/{rep.id}"), erro[1]
    db.session.commit()
    flash("Decisão registrada.", "ok")
    return redirect("/admin/denuncias")


@bp.route("/admin/denuncias/<int:rid>")
@admin_required
def detalhe(rid):
    rep = db.get_or_404(Report, rid)
    obj = alvo_da_denuncia(rep)
    eventos = EventoDenuncia.query.filter_by(report_id=rep.id).order_by(EventoDenuncia.id).all()
    comunicacoes = ComunicacaoAutoridade.query.filter_by(report_id=rep.id).order_by(ComunicacaoAutoridade.id).all()
    return render_template("denuncias/detalhe.html", rep=rep, obj=obj, dono=autor(rep.alvo_tipo, obj),
                           caminho=caminho(rep.alvo_tipo, obj) if obj else rep.link, eventos=eventos,
                           comunicacoes=comunicacoes, subcategorias=SUBCATEGORIAS, qualidades=QUALIDADES,
                           decisoes=DECISOES, graves=GRAVES, reasons=REPORT_REASONS, agora=now())


@bp.route("/admin/denuncias/<int:rid>/decidir", methods=["POST"])
@admin_required
def decidir_rota(rid):
    return decidir_pela_fila(rid, request.form.get("decisao"))


@bp.route("/admin/denuncias/<int:rid>/comunicacao", methods=["POST"])
@admin_required
def comunicar(rid):
    """Registra a comunicação às autoridades feita pelo admin (DEN-08)."""
    from datetime import date

    rep = db.get_or_404(Report, rid)
    orgao, protocolo = (request.form.get("orgao") or "").strip(), (request.form.get("protocolo") or "").strip()
    try:
        data = date.fromisoformat(request.form.get("data") or "")
    except ValueError:
        data = None
    if rep.subcategoria not in GRAVES or not orgao or not protocolo or data is None:
        flash("Informe o órgão, o protocolo e a data da comunicação.", "erro")
        return redirect(f"/admin/denuncias/{rep.id}"), 400
    me = current_user()
    db.session.add(ComunicacaoAutoridade(report_id=rep.id, orgao=orgao[:120], protocolo=protocolo[:80], data=data,
                                         registrado_por=me.id))
    evento(rep, "comunicada", f"{orgao}, protocolo {protocolo}, {data:%d/%m/%Y}", me.id)
    audit("denuncia.comunicada", f"denúncia #{rep.id}: {orgao}")
    db.session.commit()
    flash("Comunicação registrada.", "ok")
    return redirect(f"/admin/denuncias/{rep.id}")


def paginas_da_fila(status, pagina):
    """(risco a menores pendentes, página das outras denúncias) para `/admin/denuncias` (DEN-04, ADM-05)."""
    q = Report.query.filter(Report.reason != RISCO, Report.alvo_tipo.in_(("avaliacao", "comentario", "perfil")),
                            Report.status == status)
    q = q.order_by(Report.created_at, Report.id) if status == "pendente" else q.order_by(Report.decided_at.desc())
    return fila_risco().all(), paginate(q, pagina)
