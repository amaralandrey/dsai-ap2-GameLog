"""Torneios da comunidade (TRN-01..14): as rotas leem o formulário e chamam `torneios_servico` (GLOBAL-14)."""
from flask import Blueprint, abort, flash, redirect, render_template, request
from sqlalchemy import case, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from . import gamificacao, graficos_svg
from . import torneios_servico as svc
from .admin import audit
from .chaveamento import BYE
from .clubes import ativo, membro, modera
from .diario import ocultos_para
from .models import (
    Clube, Game, InscricaoTorneio, MembroClube, PartidaTorneio, RegistroTorneio, ReporteResultado, Torneio, User, db,
)
from .tempo import agora_utc, para_belem
from .util import admin_required, current_user, login_required, paginate

bp = Blueprint("torneios", __name__)
ORDEM_DAS_SITUACOES = {"aberto": 0, "andamento": 1, "encerrado": 2, "cancelado": 3}


# ---------- acesso ----------
def torneio_ou_404(slug):
    """Torneio de clube removido responde 404 a todos; de clube privado, a quem não é membro ativo (TRN-03)."""
    t = Torneio.query.options(joinedload(Torneio.game), joinedload(Torneio.clube)).filter_by(slug=slug).first()
    if t is None:
        abort(404)
    me = current_user()
    m = membro(t.clube, me) if t.clube_id else None
    if t.clube_id and (t.clube.removido_em is not None or (not t.clube.publico and not ativo(m))):
        abort(404)
    return t, me, m


def organiza(t, me, m):
    """Quem criou ou, no torneio de clube, a moderação do clube (TRN-02, TRN-12)."""
    return me is not None and (me.id == t.organizador_id or (t.clube_id is not None and modera(m)))


def organizador_ou_403(slug):
    t, me, m = torneio_ou_404(slug)
    if not organiza(t, me, m):
        abort(403)
    return t, me


def executar(acao, ok=None):
    """Roda a ação do serviço e faz o commit; o erro de regra vira mensagem, sem gravar nada."""
    try:
        resultado = acao()
        db.session.commit()
    except svc.ErroTorneio as e:
        db.session.rollback()
        flash(str(e), "erro")
        return None
    except IntegrityError:  # duplo envio: a outra requisição já gravou
        db.session.rollback()
        return None
    gamificacao.despachar_adiados()  # TRN-11: eventos do encerramento, depois do commit
    if ok:
        flash(ok, "ok")
    return resultado


def nomes_de(ids, me, revelar=()):
    """{id: nome} numa consulta; perfil que `me` não pode ver vira "perfil privado" (GLOBAL-13, DIA-13).

    `revelar` são ids mostrados mesmo assim: na partida, os dois lados e a organização precisam se achar.
    """
    linhas = db.session.query(User.id, User.display_name, User.id.in_(ocultos_para(me))).filter(User.id.in_(ids))
    return {uid: nome if not oculto or uid in revelar else "perfil privado" for uid, nome, oculto in linhas}


# ---------- lista e criação (TRN-01..03) ----------
@bp.route("/torneios")
def lista():
    me = current_user()
    situacao, jogo = request.args.get("situacao", ""), request.args.get("jogo", "").strip()
    meus = select(MembroClube.clube_id).where(MembroClube.user_id == (me.id if me else -1),
                                              MembroClube.situacao == "ativo")
    q = (Torneio.query.options(joinedload(Torneio.game)).outerjoin(Clube, Clube.id == Torneio.clube_id)
         .filter(Torneio.situacao != "cancelado",
                 Torneio.clube_id.is_(None) | (Clube.removido_em.is_(None) & (Clube.publico | Clube.id.in_(meus)))))
    if situacao in ("aberto", "andamento", "encerrado"):
        q = q.filter(Torneio.situacao == situacao)
    if jogo:
        q = q.join(Game, Game.id == Torneio.game_id).filter(Game.slug == jogo)
    ordem = case(ORDEM_DAS_SITUACOES, value=Torneio.situacao)
    page = paginate(q.order_by(ordem, Torneio.created_at.desc(), Torneio.id.desc()), request.args.get("page"))
    return render_template("torneios/lista.html", page=page, situacao=situacao, jogo=jogo, situacoes=svc.SITUACOES,
                           formatos=svc.FORMATOS)


def _formulario(template, status=200, **ctx):
    return render_template(template, formatos=svc.FORMATOS, sementes=svc.SEMENTES, **ctx), status


@bp.route("/torneios/novo", methods=["GET", "POST"])
@login_required
def novo():
    me = current_user()
    clubes = svc.clubes_que_modera(me)
    if request.method == "GET":
        return _formulario("torneios/novo.html", erros={}, form=request.args, clubes=clubes)
    dados, erros = svc.validar(request.form, me)
    if erros:
        return _formulario("torneios/novo.html", 400, erros=erros, form=request.form, clubes=clubes)
    t = svc.criar(me, dados)
    db.session.commit()
    return redirect(svc.url(t))


@bp.route("/torneios/<slug>/editar", methods=["GET", "POST"])
@login_required
def editar(slug):
    t, me = organizador_ou_403(slug)
    if request.method == "GET":
        form = {"nome": t.nome, "regras": t.regras, "prazo_rodada_dias": t.prazo_rodada_dias,
                "prazo_inscricao": para_belem(t.prazo_inscricao).strftime("%Y-%m-%dT%H:%M")}
        return _formulario("torneios/editar.html", t=t, erros={}, form=form)
    dados, erros = svc.validar(request.form)
    if erros:
        return _formulario("torneios/editar.html", 400, t=t, erros=erros, form=request.form)
    executar(lambda: svc.editar(t, dados), "Torneio atualizado.")
    return redirect(svc.url(t))


# ---------- página (TRN-03, TRN-13) ----------
def _linhas_da_chave(est, nome):
    """A chave em tabela, para leitores de tela (RET-04)."""
    def lado(x):
        return "bye" if x is BYE else "a definir" if x is None else nome(x)
    return [(p.rodada, p.id, lado(p.a), lado(p.b), lado(p.vencedor) if p.jogada else "")
            for p in est.partidas]


@bp.route("/torneios/<slug>")
def pagina(slug):
    """Número fixo de consultas, qualquer que seja o tamanho da chave (TRN-13)."""
    t, me, m = torneio_ou_404(slug)
    inscricoes = InscricaoTorneio.query.filter_by(torneio_id=t.id).order_by(
        InscricaoTorneio.semente.is_(None), InscricaoTorneio.semente, InscricaoTorneio.created_at).all()
    partidas = PartidaTorneio.query.filter_by(torneio_id=t.id).order_by(
        PartidaTorneio.rodada, PartidaTorneio.id).all()
    registros = RegistroTorneio.query.filter_by(torneio_id=t.id).order_by(RegistroTorneio.created_at.desc()).all()
    nomes = nomes_de({i.user_id for i in inscricoes} | {r.autor_id for r in registros} | {t.organizador_id}, me)

    def nome(uid):
        return nomes.get(uid, "?")
    est = svc.estado(t, inscricoes, partidas)
    svg = linhas = None
    if est is not None:
        svg = graficos_svg.chave(est.chave, titulo=f"Chave de {t.nome}", nomes={i: nome(i) for i in est.participantes})
        linhas = _linhas_da_chave(est, nome)
    posicao = {i.user_id: i.posicao_final for i in inscricoes}
    tabela = est.tabela if est else []
    if t.situacao == "encerrado":
        tabela = sorted(tabela, key=lambda linha: posicao[linha.participante])
    minha = next((i for i in inscricoes if me and i.user_id == me.id), None)
    return render_template(
        "torneios/torneio.html", t=t, m=m, organiza=organiza(t, me, m), inscricoes=inscricoes, partidas=partidas,
        registros=registros, nome=nome, svg=svg, linhas=linhas, tabela=tabela, posicao=posicao, minha=minha,
        ativo=ativo(m), vagas=sum(i.situacao == "inscrito" for i in inscricoes), formatos=svc.FORMATOS,
        situacoes=svc.SITUACOES, sementes=svc.SEMENTES, para_belem=para_belem,
    )


# ---------- inscrição (TRN-04) ----------
@bp.route("/torneios/<slug>/inscrever", methods=["POST"])
@login_required
def inscrever(slug):
    t, me, m = torneio_ou_404(slug)
    insc = executar(lambda: svc.inscrever(t, me, ativo(m)))
    if insc:
        flash("Inscrição aguardando a autorização do responsável." if insc.situacao == "pendente_responsavel"
              else "Inscrição feita.", "ok")
    return redirect(svc.url(t))


@bp.route("/torneios/<slug>/desistir", methods=["POST"])
@login_required
def desistir(slug):
    t, me, _ = torneio_ou_404(slug)
    executar(lambda: svc.desistir(t, me), "Você desistiu do torneio.")
    return redirect(svc.url(t))


@bp.route("/torneios/<slug>/inscritos/<int:user_id>/remover", methods=["POST"])
@login_required
def remover(slug, user_id):
    t, me = organizador_ou_403(slug)
    executar(lambda: svc.remover(t, me, user_id, request.form.get("motivo")), "Inscrição removida.")
    return redirect(svc.url(t))


# ---------- organização (TRN-05, TRN-12) ----------
@bp.route("/torneios/<slug>/iniciar", methods=["POST"])
@login_required
def iniciar(slug):
    t, me = organizador_ou_403(slug)
    executar(lambda: svc.iniciar(t, me), "Torneio iniciado.")
    return redirect(svc.url(t))


@bp.route("/torneios/<slug>/cancelar", methods=["POST"])
@login_required
def cancelar(slug):
    t, me = organizador_ou_403(slug)
    executar(lambda: svc.cancelar(t, me, request.form.get("motivo")), "Torneio cancelado.")
    return redirect(svc.url(t))


# ---------- partidas (TRN-06..09) ----------
def partida_ou_404(t, pid):
    linha = db.session.get(PartidaTorneio, pid)
    if linha is None or linha.torneio_id != t.id:
        abort(404)
    return linha


def _numero(texto, vazio=None):
    texto = (texto or "").strip()
    if not texto:
        return vazio
    try:
        return int(texto)
    except ValueError:
        raise svc.ErroTorneio("Use números inteiros no vencedor e no placar.") from None


def ler_resultado(form):
    """(vencedor, placar_a, placar_b); "empate" no vencedor é empate e vencedor vazio é inválido."""
    vencedor = None if form.get("vencedor") == "empate" else _numero(form.get("vencedor"), -1)
    return vencedor, _numero(form.get("placar_a")), _numero(form.get("placar_b"))


@bp.route("/torneios/<slug>/partidas/<int:pid>", methods=["POST"])
@login_required
def informar(slug, pid):
    """Só os dois lados informam o resultado (TRN-06)."""
    t, me, _ = torneio_ou_404(slug)
    linha = partida_ou_404(t, pid)
    if me.id not in (linha.a_id, linha.b_id):
        abort(403)
    executar(lambda: svc.informar(t, linha, me, *ler_resultado(request.form)), "Resultado informado.")
    return redirect(svc.url(t, linha))


@bp.route("/torneios/<slug>/partidas/<int:pid>")
def partida(slug, pid):
    t, me, m = torneio_ou_404(slug)
    linha = partida_ou_404(t, pid)
    lados, org = (linha.a_id, linha.b_id), organiza(t, me, m)
    joga = me is not None and me.id in lados
    reportes = ReporteResultado.query.filter_by(partida_id=linha.id).order_by(ReporteResultado.id).all()
    nomes = nomes_de(set(lados), me, revelar=lados if joga or org else ())
    registros = RegistroTorneio.query.filter_by(partida_id=linha.id).all()
    return render_template("torneios/partida.html", t=t, p=linha, nomes=nomes, joga=joga, organiza=org,
                           reportes=reportes if joga or org else [], registros=registros,
                           empate=t.formato in svc.EMPATE_PERMITIDO, prazo_vencido=linha.prazo < agora_utc(),
                           para_belem=para_belem)


@bp.route("/torneios/<slug>/partidas/<int:pid>/<acao>", methods=["POST"])
@login_required
def arbitrar(slug, pid, acao):
    """Decisão de contestação, W.O. e correção, só da organização e sempre com justificativa (TRN-07..09)."""
    t, me = organizador_ou_403(slug)
    linha, f = partida_ou_404(t, pid), request.form
    justificativa = f.get("justificativa")

    def decidir():
        opcao = f.get("opcao", "")
        if opcao.startswith("reporte-"):
            r = ReporteResultado.query.filter_by(id=_numero(opcao[8:], -1), partida_id=linha.id).first()
            if r is None:
                raise svc.ErroTorneio("Escolha um dos resultados informados.")
            return svc.decidir(t, linha, me, (r.vencedor_id, r.placar_a, r.placar_b), justificativa)
        if opcao == "wo":
            return svc.decidir(t, linha, me, (_numero(f.get("vencedor"), -1), None, None), justificativa, wo=True)
        return svc.decidir(t, linha, me, ler_resultado(f), justificativa)
    acoes = {
        "decidir": decidir,
        "wo": lambda: svc.dar_wo(t, linha, me, _numero(f.get("vencedor"), -1), justificativa),
        "corrigir": lambda: svc.corrigir(t, linha, me, ler_resultado(f), justificativa),
    }
    if acao not in acoes:
        abort(404)
    executar(acoes[acao], "Partida atualizada.")
    return redirect(svc.url(t, linha))


# ---------- admin (TRN-12) ----------
@bp.route("/admin/torneios")
@admin_required
def admin_lista():
    page = paginate(Torneio.query.options(joinedload(Torneio.game)).order_by(Torneio.created_at.desc(), Torneio.id.desc()),
                    request.args.get("page"))
    return render_template("torneios/admin.html", page=page, situacoes=svc.SITUACOES)


@bp.route("/admin/torneios/<int:tid>/cancelar", methods=["POST"])
@admin_required
def admin_cancelar(tid):
    t = db.get_or_404(Torneio, tid)

    def acao():
        svc.cancelar(t, current_user(), request.form.get("motivo"))
        audit("torneio.cancelar", t.slug)  # ADM-09, GLOBAL-17
    executar(acao, "Torneio cancelado.")
    return redirect("/admin/torneios")
