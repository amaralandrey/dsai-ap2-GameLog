import hashlib
import secrets
import string
from datetime import timedelta

from flask import Blueprint, abort, make_response, redirect, render_template, request
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import joinedload

from . import tempo
from .models import AuditoriaToken, LimiteTaxaToken, TokenApi, db
from .util import current_user, login_required, paginate

bp = Blueprint("tokens", __name__)

PREFIXO = "glp_"
TAMANHO = 40
ESCOPOS = ("ler", "escrever")
VALIDADES = (30, 90, 365)
MAX_ATIVOS = 10
LIMITE_POR_MINUTO = 60
NOME_MAX = 60
ALFABETO = string.ascii_letters + string.digits


def hash_token(texto):
    return hashlib.sha256(texto.encode()).hexdigest()


def gerar():
    return PREFIXO + "".join(secrets.choice(ALFABETO) for _ in range(TAMANHO))


def ativos(user_id):
    agora = tempo.agora_utc()
    return TokenApi.query.filter(TokenApi.user_id == user_id, TokenApi.revogado_em.is_(None),
                                 TokenApi.expira_em > agora)


def validar(user, form):
    erros = {}
    nome = (form.get("nome") or "").strip()
    if not 1 <= len(nome) <= NOME_MAX:
        erros["nome"] = f"O nome é obrigatório e deve ter até {NOME_MAX} caracteres."
    elif ativos(user.id).count() >= MAX_ATIVOS:
        erros["nome"] = f"Limite de {MAX_ATIVOS} tokens ativos atingido: revogue um antes de criar outro."
    escopos = [e for e in ESCOPOS if e in form.getlist("escopos")]
    if not escopos or len(escopos) != len(set(form.getlist("escopos"))):
        erros["escopos"] = "Escolha ao menos um escopo: ler e/ou escrever."
    try:
        dias = int(form.get("dias", ""))
    except ValueError:
        dias = None
    if dias not in VALIDADES:
        erros["dias"] = "Escolha a validade: 30, 90 ou 365 dias."
    return {"nome": nome, "escopos": escopos, "dias": dias}, erros


def criar(user, nome, escopos, dias):
    texto, agora = gerar(), tempo.agora_utc()
    token = TokenApi(user_id=user.id, nome=nome, prefixo=texto[:len(PREFIXO) + 6], token_hash=hash_token(texto),
                     escopos=" ".join(e for e in ESCOPOS if e in escopos), criado_em=agora,
                     expira_em=agora + timedelta(days=dias))
    db.session.add(token)
    db.session.flush()
    auditar(token, "criar")
    return token, texto


def autenticar(texto):
    if not texto or not texto.startswith(PREFIXO):
        return None
    token = TokenApi.query.options(joinedload(TokenApi.user)).filter_by(token_hash=hash_token(texto)).first()
    return token if valido(token) else None


def valido(token):
    if token is None or token.revogado_em is not None or token.expira_em <= tempo.agora_utc():
        return False
    return conta_ativa(token.user)


def conta_ativa(user):
    from .menores.idade import aguarda_responsavel

    return user.suspensa_em is None and user.nascimento is not None and not aguarda_responsavel(user)


def tem_escopo(token, escopo):
    return escopo in token.escopos.split()


def revogar(token):
    if token.revogado_em is None:
        token.revogado_em = tempo.agora_utc()
        auditar(token, "revogar")


def revogar_todos(user_id):
    for token in ativos(user_id):
        revogar(token)


def consumir(token):
    agora = tempo.agora_utc()
    janela = agora.replace(second=0, microsecond=0)
    contagem = db.session.execute(
        insert(LimiteTaxaToken).values(token_id=token.id, janela=janela, contagem=1)
        .on_conflict_do_update(index_elements=["token_id", "janela"],
                               set_={"contagem": LimiteTaxaToken.contagem + 1})
        .returning(LimiteTaxaToken.contagem)
    ).scalar_one()
    if contagem == 1:
        LimiteTaxaToken.query.filter(LimiteTaxaToken.token_id == token.id,
                                     LimiteTaxaToken.janela < janela).delete(synchronize_session=False)
    db.session.commit()
    restantes = max(0, LIMITE_POR_MINUTO - contagem)
    return contagem <= LIMITE_POR_MINUTO, restantes, max(1, 60 - agora.second)


def auditar(token, acao, metodo="", rota="", status_http=None):
    db.session.add(AuditoriaToken(token_id=token.id, user_id=token.user_id, acao=acao, metodo=metodo,
                                  rota=rota[:200], status_http=status_http, criado_em=tempo.agora_utc()))


def _do_dono(username):
    me = current_user()
    if me.username_lower != username.lower():
        abort(403)
    return me


def _token_do_dono(me, tid):
    token = db.session.get(TokenApi, tid)
    if token is None or token.user_id != me.id:
        abort(404)
    return token


def _lista(me, erros=None, form=None, novo=None, status=200):
    tokens = TokenApi.query.filter_by(user_id=me.id).order_by(TokenApi.criado_em.desc(), TokenApi.id.desc()).all()
    return make_response(render_template("tokens/lista.html", tokens=tokens, erros=erros or {}, form=form or {},
                                         novo=novo, agora=tempo.agora_utc(), validades=VALIDADES,
                                         escopos=ESCOPOS), status)


@bp.route("/u/<username>/tokens", methods=["GET", "POST"])
@login_required
def lista(username):
    me = _do_dono(username)
    if request.method == "GET":
        return _lista(me)
    dados, erros = validar(me, request.form)
    if erros:
        return _lista(me, erros, request.form, status=400)
    _, texto = criar(me, dados["nome"], dados["escopos"], dados["dias"])
    db.session.commit()
    resposta = _lista(me, novo=texto, status=201)
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


@bp.route("/u/<username>/tokens/<int:tid>")
@login_required
def detalhe(username, tid):
    me = _do_dono(username)
    token = _token_do_dono(me, tid)
    q = AuditoriaToken.query.filter_by(token_id=token.id).order_by(AuditoriaToken.criado_em.desc(),
                                                                   AuditoriaToken.id.desc())
    return render_template("tokens/detalhe.html", token=token, page=paginate(q, request.args.get("page")))


@bp.route("/u/<username>/tokens/<int:tid>/revogar", methods=["POST"])
@login_required
def revogar_rota(username, tid):
    me = _do_dono(username)
    revogar(_token_do_dono(me, tid))
    db.session.commit()
    return redirect(f"/u/{me.username}/tokens")
