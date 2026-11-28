"""Responsável e supervisão parental (RSP-01..12): convite, aceite, painel `/responsavel` e controles.

O vínculo aceito confirma a idade pelo adaptador `responsavel` (IDA-07) e tira a conta de 12 a 15 anos da espera.
Os controles valem só nessa faixa: aos 16 anos o vínculo é encerrado por idade e a política de PMN decide sozinha.
"""
from datetime import timedelta

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from .. import tempo
from ..bloqueios import bloquear
from ..emails import enviar_email, frase, url_publica
from ..jobs import agenda_padrao, tarefa
from ..models import (Activity, Clube, Comment, ControleParental, Follow, InscricaoTorneio, MembroClube, Review,
                      SessaoJogo, Torneio, User, VinculoResponsavel, db, now)
from ..notificacoes import notificar
from ..privacidade import consentimentos
from ..util import PER_PAGE, Page, current_user, login_required
from .idade import ADAPTADORES, ADULTO, VINCULADO, IdadeInvalida, aguarda_responsavel, faixa_etaria, registrar, sql_menor

bp = Blueprint("responsavel", __name__)

VALIDADE_CONVITE = timedelta(days=7)
MAX_CONVITES_POR_DIA = 3
MAX_RESPONSAVEIS = 2
VERSAO_DECLARACAO = 1
DECLARACAO = ("Declaro que sou mãe, pai ou responsável legal por esta criança ou adolescente e que a data de "
              "nascimento informada é a verdadeira.")
CONTROLES = {"comentarios": "Comentar avaliações", "clubes": "Entrar e postar em clubes",
             "torneios": "Se inscrever em torneios", "perfil_publico": "Perfil público"}
JANELAS = (7, 30)


# ---------- consultas usadas por outras specs ----------
def ativos(menor_id=None, responsavel_id=None):
    q = VinculoResponsavel.query.filter(VinculoResponsavel.aceito_em.isnot(None),
                                        VinculoResponsavel.revogado_em.is_(None),
                                        VinculoResponsavel.encerrado_idade_em.is_(None))
    if menor_id is not None:
        q = q.filter(VinculoResponsavel.menor_id == menor_id)
    if responsavel_id is not None:
        q = q.filter(VinculoResponsavel.responsavel_id == responsavel_id)
    return q


def e_responsavel(responsavel, menor):
    """Vínculo ativo entre os dois (CNS-09, RSP-05)."""
    return ativos(menor.id, responsavel.id).first() is not None


def responsaveis_ativos(menor):
    return [v.responsavel_id for v in ativos(menor.id).order_by(VinculoResponsavel.id)]


def controles_de(menor):
    """A linha de controles, criada desligada na primeira consulta (RSP-07)."""
    c = ControleParental.query.filter_by(menor_id=menor.id).first()
    if c is None:
        c = ControleParental(menor_id=menor.id)
        db.session.add(c)
    return c


# ---------- convite (RSP-01) ----------
def _serializador():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="convite-responsavel")


def link_do_convite(vinculo):
    return url_publica(f"/responsavel/convite/{_serializador().dumps(vinculo.id)}")


def convites_hoje(menor):
    return VinculoResponsavel.query.filter(VinculoResponsavel.menor_id == menor.id,
                                           VinculoResponsavel.convidado_em >= tempo.inicio_do_dia_utc(tempo.hoje())
                                           ).count()


@bp.route("/conta/pendente/convite", methods=["POST"])
@login_required
def convidar():
    """Até 3 convites por dia; o e-mail do responsável não volta a aparecer para o menor (RSP-01)."""
    from ..auth import EMAIL_RE

    me = current_user()
    if faixa_etaria(me) != VINCULADO:
        return redirect("/")
    volta = "/conta/pendente" if aguarda_responsavel(me) else "/conta/responsaveis"
    if ativos(me.id).count() >= MAX_RESPONSAVEIS:
        flash(f"Você já tem {MAX_RESPONSAVEIS} responsáveis.", "erro")
        return redirect(volta)
    if convites_hoje(me) >= MAX_CONVITES_POR_DIA:
        msg = f"Você já enviou {MAX_CONVITES_POR_DIA} convites hoje. Tente de novo amanhã."
        return render_template("erro.html", code=429, msg=msg), 429
    email = (request.form.get("email") or "").strip().lower()
    if not EMAIL_RE.match(email) or email == me.email.lower():
        flash("Informe o e-mail do seu responsável, diferente do seu.", "erro")
        return redirect(volta)
    vinculo = VinculoResponsavel(menor_id=me.id, email_convite=email, convidado_em=now())
    db.session.add(vinculo)
    db.session.flush()
    enviar_email(None, "convite_responsavel", para=email, menor=me, link=link_do_convite(vinculo),
                 dias=VALIDADE_CONVITE.days)
    db.session.commit()
    flash("Convite enviado. Peça ao seu responsável para abrir o link do e-mail.", "ok")
    return redirect(volta)


@bp.route("/conta/responsaveis")
@login_required
def meus_responsaveis():
    """Quem supervisiona a conta de 12 a 15 anos, sem o e-mail, e o convite a um segundo responsável (RSP-05)."""
    me = current_user()
    if faixa_etaria(me) != VINCULADO:
        return redirect("/")
    vinculos = ativos(me.id).order_by(VinculoResponsavel.aceito_em).all()
    return render_template("responsavel/meus.html", vinculos=vinculos, maximo=MAX_RESPONSAVEIS,
                           convites=convites_hoje(me), max_convites=MAX_CONVITES_POR_DIA)


# ---------- aceite e recusa (RSP-02, RSP-03) ----------
def convite_ou_400(token):
    try:
        vid = _serializador().loads(token, max_age=int(VALIDADE_CONVITE.total_seconds()))
    except BadSignature:
        abort(400)
    vinculo = db.session.get(VinculoResponsavel, vid) if isinstance(vid, int) else None
    if vinculo is None or not vinculo.aberto:
        abort(400)
    return vinculo


def _quem_aceita(vinculo):
    """Só uma conta adulta, que não é o próprio menor (RSP-03)."""
    me = current_user()
    if me.id == vinculo.menor_id or faixa_etaria(me) != ADULTO or aguarda_responsavel(me):
        abort(403)
    return me


@bp.route("/responsavel/convite/<token>", methods=["GET", "POST"])
def convite(token):
    vinculo = convite_ou_400(token)
    if current_user() is None:
        return redirect(url_for("auth.signup", next=request.path))
    _quem_aceita(vinculo)
    return _aceitar(vinculo)


@login_required
def _aceitar(vinculo):
    me, menor = _quem_aceita(vinculo), vinculo.menor
    if request.method == "GET":
        return render_template("responsavel/convite.html", menor=menor, declaracao=DECLARACAO, erro=None)
    erro = None
    if not request.form.get("declaro"):
        erro = "Confirme a declaração de responsável legal."
    elif ativos(menor.id).count() >= MAX_RESPONSAVEIS:
        erro = f"Esta conta já tem {MAX_RESPONSAVEIS} responsáveis."
    else:
        try:
            resultado = ADAPTADORES["responsavel"].verificar(menor, request.form)
        except IdadeInvalida as exc:
            erro = str(exc)
    if erro:
        return render_template("responsavel/convite.html", menor=menor, declaracao=DECLARACAO, erro=erro), 400
    faixa = registrar(menor, resultado)
    vinculo.responsavel_id, vinculo.aceito_em, vinculo.versao_declaracao = me.id, now(), VERSAO_DECLARACAO
    menor.aguardando_responsavel = False
    if faixa == VINCULADO:
        controles_de(menor)
    notificar(menor.id, "responsavel", texto=f"{me.display_name} aceitou ser seu responsável no GameLog.",
              alvo_url="/")
    db.session.commit()
    flash(f"Vínculo com {menor.display_name} confirmado.", "ok")
    return redirect(f"/responsavel/{menor.id}")


@bp.route("/responsavel/convite/<token>/recusar", methods=["POST"])
@login_required
def recusar_convite(token):
    vinculo = convite_ou_400(token)
    _quem_aceita(vinculo)
    vinculo.recusado_em = now()
    db.session.commit()
    flash("Convite recusado.", "ok")
    return redirect("/")


# ---------- painel (RSP-05..10) ----------
def vinculo_do_painel(menor_id):
    """O menor no painel recebe 403 (RSP-10); quem não é responsável dele, 404 (RSP-05)."""
    me = current_user()
    if me.id == menor_id:
        abort(403)
    vinculo = ativos(menor_id, me.id).first()
    if vinculo is None:
        abort(404)
    return me, vinculo.menor


def atividade(menor):
    """(quando, texto, caminho) do diário, avaliações, listas, comentários, clubes e torneios, do mais novo."""
    itens = []
    for a in Activity.query.filter_by(user_id=menor.id):
        texto, caminho = frase(a)
        itens.append((a.created_at, texto.removeprefix(f"{menor.display_name} "), caminho))
    for c in Comment.query.filter_by(user_id=menor.id).options(joinedload(Comment.review).joinedload(Review.game)):
        game = c.review.game
        itens.append((c.created_at, f"comentou uma avaliação de {game.title}", f"/jogos/{game.slug}#com-{c.id}"))
    for m, clube in db.session.query(MembroClube, Clube).join(Clube, Clube.id == MembroClube.clube_id).filter(
            MembroClube.user_id == menor.id):
        itens.append((m.created_at, f"entrou no clube {clube.nome}", f"/clubes/{clube.slug}"))
    for i, t in db.session.query(InscricaoTorneio, Torneio).join(Torneio, Torneio.id == InscricaoTorneio.torneio_id
                                                                 ).filter(InscricaoTorneio.user_id == menor.id):
        itens.append((i.created_at, f"se inscreveu no torneio {t.nome}", f"/torneios/{t.slug}"))
    return sorted(itens, key=lambda item: item[0], reverse=True)


def pagina(itens, numero):
    total = len(itens)
    paginas = max(1, -(-total // PER_PAGE))
    try:
        numero = min(max(int(numero), 1), paginas)
    except (TypeError, ValueError):
        numero = 1
    return Page(itens[(numero - 1) * PER_PAGE:numero * PER_PAGE], numero, paginas, total)


def metricas(menor, itens):
    """Horas no diário e número de ações nos últimos 7 e 30 dias (RSP-06)."""
    resultado = {}
    for dias in JANELAS:
        desde = tempo.hoje() - timedelta(days=dias - 1)
        minutos = db.session.query(func.coalesce(func.sum(SessaoJogo.duracao_min), 0)).filter(
            SessaoJogo.user_id == menor.id, SessaoJogo.jogada_em >= desde).scalar()
        limite = tempo.agora_utc() - timedelta(days=dias)
        resultado[dias] = {"minutos": minutos, "acoes": sum(1 for quando, _, _ in itens if quando >= limite)}
    return resultado


def interacoes(menor):
    """Contas adultas com quem o menor interage e como (RSP-09)."""
    relacoes = {}

    def juntar(ids, rotulo):
        for uid in ids:
            relacoes.setdefault(uid, []).append(rotulo)

    juntar((f.follower_id for f in Follow.query.filter(Follow.followed_id == menor.id, Follow.aprovado)), "segue")
    juntar((f.followed_id for f in Follow.query.filter(Follow.follower_id == menor.id)), "é seguido")
    juntar(db.session.scalars(db.select(Comment.user_id).join(Review).where(Review.user_id == menor.id)),
           "comentou")
    clubes = db.select(MembroClube.clube_id).where(MembroClube.user_id == menor.id, MembroClube.situacao == "ativo")
    juntar(db.session.scalars(db.select(MembroClube.user_id).where(MembroClube.clube_id.in_(clubes),
                                                                   MembroClube.situacao == "ativo")), "clube")
    relacoes.pop(menor.id, None)
    if not relacoes:
        return []
    adultos = User.query.filter(User.id.in_(relacoes), ~sql_menor()).order_by(User.username_lower)
    return [(u, sorted(set(relacoes[u.id]))) for u in adultos]


@bp.route("/responsavel")
@login_required
def lista():
    me = current_user()
    vinculos = ativos(responsavel_id=me.id).order_by(VinculoResponsavel.aceito_em).all()
    return render_template("responsavel/lista.html", vinculos=vinculos)


@bp.route("/responsavel/<int:menor_id>")
@login_required
def painel(menor_id):
    _, menor = vinculo_do_painel(menor_id)
    itens = atividade(menor)
    pedidos = (Follow.query.filter(Follow.followed_id == menor.id, ~Follow.aprovado)
               .order_by(Follow.id.desc()).all())
    inscricoes = (db.session.query(InscricaoTorneio, Torneio).join(Torneio, Torneio.id == InscricaoTorneio.torneio_id)
                  .filter(InscricaoTorneio.user_id == menor.id, InscricaoTorneio.situacao == "pendente_responsavel")
                  .all())
    vinculado = faixa_etaria(menor) == VINCULADO
    controles = controles_de(menor) if vinculado else None
    db.session.commit()
    return render_template("responsavel/painel.html", menor=menor, page=pagina(itens, request.args.get("page")),
                           metricas=metricas(menor, itens), pedidos=pedidos, inscricoes=inscricoes,
                           interacoes=interacoes(menor), controles=controles, rotulos=CONTROLES,
                           resumo=consentimentos.tem_consentimento(menor, "email_resumo"), vinculado=vinculado)


def _voltar(menor):
    return redirect(f"/responsavel/{menor.id}")


@bp.route("/responsavel/<int:menor_id>/controles", methods=["POST"])
@login_required
def mudar_controles(menor_id):
    """Cada mudança grava quem e quando, e avisa o menor (RSP-07)."""
    me, menor = vinculo_do_painel(menor_id)
    if faixa_etaria(menor) != VINCULADO:
        abort(404)
    c = controles_de(menor)
    mudou = []
    for campo, rotulo in CONTROLES.items():
        ligado = bool(request.form.get(campo))
        if getattr(c, campo) != ligado:
            setattr(c, campo, ligado)
            mudou.append(f"{rotulo.lower()} ({'ligado' if ligado else 'desligado'})")
    if mudou:
        c.alterado_por, c.alterado_em = me.id, now()
        menor.perfil_privado = not c.perfil_publico
        notificar(menor.id, "responsavel", texto=f"Seu responsável mudou: {', '.join(mudou)}.")
    db.session.commit()
    flash("Controles salvos.", "ok")
    return _voltar(menor)


@bp.route("/responsavel/<int:menor_id>/resumo", methods=["POST"])
@login_required
def mudar_resumo(menor_id):
    """O resumo por e-mail é o consentimento `email_resumo`, concedido pelo responsável (CNS-09)."""
    me, menor = vinculo_do_painel(menor_id)
    if request.form.get("ligado"):
        consentimentos.conceder_pelo_responsavel(me, menor, "email_resumo")
    else:
        consentimentos.revogar_pelo_responsavel(me, menor, "email_resumo")
    db.session.commit()
    flash("Resumo por e-mail atualizado.", "ok")
    return _voltar(menor)


def _pedido(menor, fid):
    pedido = db.session.get(Follow, fid)
    if pedido is None or pedido.followed_id != menor.id or pedido.aprovado_em is not None:
        abort(404)
    return pedido


@bp.route("/responsavel/<int:menor_id>/pedidos/<int:fid>/<decisao>", methods=["POST"])
@login_required
def decidir_pedido(menor_id, fid, decisao):
    """O responsável aprova ou recusa quem pediu para seguir o menor (RSP-06, PMN-03)."""
    from ..social import eventos_de_seguir

    _, menor = vinculo_do_painel(menor_id)
    pedido = _pedido(menor, fid)
    if decisao == "aprovar":
        pedido.aprovado_em = now()
        db.session.commit()
        eventos_de_seguir(pedido.follower_id, pedido.followed_id)
    elif decisao == "recusar":
        db.session.delete(pedido)
        db.session.commit()
    else:
        abort(404)
    flash("Pedido " + ("aprovado." if decisao == "aprovar" else "recusado."), "ok")
    return _voltar(menor)


@bp.route("/responsavel/<int:menor_id>/torneios/<int:iid>/<decisao>", methods=["POST"])
@login_required
def decidir_torneio(menor_id, iid, decisao):
    """Aprovar confirma a inscrição, se ainda houver vaga e prazo; recusar a cancela e avisa o menor (RSP-08)."""
    from ..torneios_servico import autorizar_inscricao

    _, menor = vinculo_do_painel(menor_id)
    insc = db.session.get(InscricaoTorneio, iid)
    if insc is None or insc.user_id != menor.id or insc.situacao != "pendente_responsavel" or decisao not in (
            "aprovar", "recusar"):
        abort(404)
    torneio = db.session.get(Torneio, insc.torneio_id)
    if decisao == "aprovar":
        if not autorizar_inscricao(insc):
            flash("Não deu para confirmar: o torneio não tem mais vaga ou já começou.", "erro")
            return _voltar(menor)
        texto = f"Seu responsável confirmou sua inscrição no torneio {torneio.nome}."
    else:
        insc.situacao = "descartada"
        texto = f"Seu responsável recusou sua inscrição no torneio {torneio.nome}."
    notificar(menor.id, "responsavel", texto=texto, alvo_url=f"/torneios/{torneio.slug}")
    db.session.commit()
    flash("Inscrição " + ("confirmada." if decisao == "aprovar" else "recusada."), "ok")
    return _voltar(menor)


def _conta(uid, menor):
    alvo = db.session.get(User, uid)
    if alvo is None or alvo.id == menor.id:
        abort(404)
    return alvo


@bp.route("/responsavel/<int:menor_id>/seguidores/<int:uid>/remover", methods=["POST"])
@login_required
def remover_seguidor(menor_id, uid):
    _, menor = vinculo_do_painel(menor_id)
    alvo = _conta(uid, menor)
    Follow.query.filter_by(follower_id=alvo.id, followed_id=menor.id).delete()
    db.session.commit()
    flash(f"@{alvo.username} não segue mais {menor.display_name}.", "ok")
    return _voltar(menor)


@bp.route("/responsavel/<int:menor_id>/bloquear/<int:uid>", methods=["POST"])
@login_required
def bloquear_conta(menor_id, uid):
    """Bloqueio em nome do menor, que só o responsável desfaz (RSP-09, BLQ-08)."""
    me, menor = vinculo_do_painel(menor_id)
    alvo = _conta(uid, menor)
    bloquear(menor, alvo, criado_por=me)
    db.session.commit()
    flash(f"@{alvo.username} foi bloqueado para {menor.display_name}.", "ok")
    return _voltar(menor)


@bp.route("/responsavel/<int:menor_id>/desvincular", methods=["POST"])
@login_required
def desvincular(menor_id):
    """Só o responsável desfaz o vínculo; sem outro ativo, a conta de 12 a 15 volta a esperar (RSP-10)."""
    me, menor = vinculo_do_painel(menor_id)
    ativos(menor.id, me.id).one().revogado_em = now()
    db.session.flush()
    if faixa_etaria(menor) == VINCULADO and ativos(menor.id).first() is None:
        menor.aguardando_responsavel = True
    notificar(menor.id, "responsavel", texto=f"{me.display_name} desfez o vínculo de responsável.")
    db.session.commit()
    flash(f"Vínculo com {menor.display_name} desfeito.", "ok")
    return redirect("/responsavel")


# ---------- 16 anos (RSP-11) ----------
@tarefa("responsavel_idade")
def encerrar_por_idade():
    """Encerra o vínculo de quem saiu da faixa de 12 a 15 anos e avisa o menor e o responsável."""
    for v in ativos().order_by(VinculoResponsavel.id).all():
        if faixa_etaria(v.menor) == VINCULADO:
            continue
        v.encerrado_idade_em = now()
        notificar(v.menor_id, "responsavel", texto="Você fez 16 anos: os controles do responsável foram encerrados.")
        notificar(v.responsavel_id, "responsavel", alvo_url="/responsavel",
                  texto=f"{v.menor.display_name} fez 16 anos: o vínculo de responsável foi encerrado.")


agenda_padrao("responsavel_idade", "10 0 * * *", "responsavel_idade")
