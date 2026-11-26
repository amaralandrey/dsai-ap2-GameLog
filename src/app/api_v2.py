from functools import wraps
from urllib.parse import urlencode

from flask import Blueprint, g, render_template, request
from sqlalchemy import func
from sqlalchemy.orm import contains_eager, joinedload, selectinload

from . import serializadores, tempo
from .api import (detalhe_de_jogo, erro, jogo_ou_404, mesclado_ou_404, pagina_de_avaliacoes, pagina_de_jogos,
                  paginado as paginado_v1, per_page, resposta)
from .avaliacoes import avaliar
from .diario import ler_data, pode_ver_perfil, registrar_sessao
from .listas import adicionar_jogo, definir_status
from .listas_inteligentes import dados_da_lista
from .menores.politica import pode
from .models import (STATUSES, Conquista, Game, GameList, ListItem, SessaoJogo, Status, User, UsuarioConquista, db,
                     visible_games)
from .rankings import CATEGORIAS, ESCOPOS, linhas_do_ranking
from .tokens import LIMITE_POR_MINUTO, auditar, autenticar, consumir, tem_escopo
from .util import Page, paginate

bp = Blueprint("api_v2", __name__, url_prefix="/api/v2")
LEITURA = ("GET", "HEAD", "OPTIONS")
ENDPOINTS = (
    ("GET", "/api/v2", None),
    ("GET", "/api/v2/docs", None),
    ("GET", "/api/v2/jogos", None),
    ("GET", "/api/v2/jogos/<slug>", None),
    ("GET", "/api/v2/jogos/<slug>/avaliacoes", None),
    ("GET", "/api/v2/usuarios/<username>", None),
    ("GET", "/api/v2/usuarios/<username>/diario", None),
    ("GET", "/api/v2/rankings/<categoria>", None),
    ("GET", "/api/v2/eu", "ler"),
    ("GET", "/api/v2/eu/status", "ler"),
    ("GET", "/api/v2/eu/listas", "ler"),
    ("GET", "/api/v2/eu/listas/<id>", "ler"),
    ("GET", "/api/v2/eu/diario", "ler"),
    ("GET", "/api/v2/eu/conquistas", "ler"),
    ("PUT", "/api/v2/eu/status/<slug>", "escrever"),
    ("PUT", "/api/v2/eu/avaliacoes/<slug>", "escrever"),
    ("POST", "/api/v2/eu/diario", "escrever"),
    ("POST", "/api/v2/eu/listas/<id>/jogos", "escrever"),
)


def nao_autorizado():
    r = erro(401, "token inválido")
    r.headers["WWW-Authenticate"] = "Bearer"
    return r


def escopo_insuficiente(escopo):
    return resposta({"erro": "escopo insuficiente", "requer": escopo}, 403)


def invalido(campos, mensagem="entrada inválida"):
    return resposta({"erro": mensagem, "campos": campos}, 422)


def corpo_json():
    corpo = request.get_json(silent=True)
    return corpo if isinstance(corpo, dict) else None


def visitante():
    token = g.get("token")
    return token.user if token is not None and tem_escopo(token, "ler") else None


@bp.before_request
def autenticar_requisicao():
    g.token = None
    cabecalho = request.headers.get("Authorization")
    if cabecalho is None:
        return None
    tipo, _, texto = cabecalho.partition(" ")
    token = autenticar(texto.strip()) if tipo == "Bearer" else None
    if token is None:
        return nao_autorizado()
    token.ultimo_uso_em = tempo.agora_utc()
    permitido, g.restantes, espera = consumir(token)
    g.token = token
    if not permitido:
        r = erro(429, "limite de requisições")
        r.headers["Retry-After"] = str(espera)
        return r
    return None


@bp.after_request
def cabecalhos(resp):
    resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, OPTIONS"
    token = g.get("token")
    if token is None:
        return resp
    resp.headers["X-RateLimit-Limit"] = str(LIMITE_POR_MINUTO)
    resp.headers["X-RateLimit-Remaining"] = str(g.restantes)
    resp.headers["Cache-Control"] = "no-store"
    if request.method not in LEITURA:
        db.session.rollback()
        auditar(token, "escrita", request.method, request.path, resp.status_code)
        db.session.commit()
    return resp


def do_dono(f):
    @wraps(f)
    def wrapper(*a, **kw):
        token = g.token
        if token is None:
            return nao_autorizado()
        escopo = "ler" if request.method in LEITURA else "escrever"
        if not tem_escopo(token, escopo):
            return escopo_insuficiente(escopo)
        return f(token.user, *a, **kw)

    return wrapper


def proxima(page):
    if page.page >= page.pages:
        return None
    args = [(k, v) for k, v in request.args.items(multi=True) if k != "page"] + [("page", page.page + 1)]
    return serializadores.absoluta(f"{request.path}?{urlencode(args)}")


def paginado(page, itens):
    return {**paginado_v1(page, itens), "proxima": proxima(page)}


def paginar_lista(linhas, page_arg):
    tamanho = per_page()
    pages = max(1, -(-len(linhas) // tamanho))
    try:
        n = int(page_arg)
    except (TypeError, ValueError):
        n = 1
    n = min(max(n, 1), pages)
    return Page(linhas[(n - 1) * tamanho:n * tamanho], n, pages, len(linhas))


@bp.route("")
@bp.route("/")
def indice():
    return resposta({
        "versao": "2",
        "endpoints": [{"metodo": m, "rota": r, "escopo": e} for m, r, e in ENDPOINTS],
        "docs": serializadores.absoluta("/api/v2/docs"),
    })


@bp.route("/docs")
def docs():
    return render_template("api_v2/docs.html", endpoints=ENDPOINTS, base=serializadores.absoluta("/api/v2"))


@bp.route("/jogos")
def jogos():
    return resposta(paginado(*pagina_de_jogos()))


@bp.route("/eu")
@do_dono
def eu(dono):
    return resposta(serializadores.usuario_publico(dono))


def pagina(q):
    return paginate(q, request.args.get("page"), per_page())


@bp.route("/jogos/<slug>")
def jogo(slug):
    game = jogo_ou_404(slug)
    if game is None:
        return mesclado_ou_404(slug, prefixo="/api/v2/jogos")
    return resposta(detalhe_de_jogo(game))


@bp.route("/jogos/<slug>/avaliacoes")
def avaliacoes(slug):
    game = jogo_ou_404(slug)
    if game is None:
        return mesclado_ou_404(slug, "/avaliacoes", prefixo="/api/v2/jogos")
    page, curtidas = pagina_de_avaliacoes(game, visitante())
    return resposta(paginado(page, [serializadores.avaliacao(r, curtidas.get(r.id, 0)) for r in page.items]))


def usuario_visivel(username):
    dono = User.query.filter_by(username_lower=username.lower()).first()
    if dono is None or not pode_ver_perfil(visitante(), dono):
        return None
    return dono


def usuario_nao_encontrado():
    return erro(404, "Usuário não encontrado")


def sessoes_de(dono):
    campos, filtros = {}, []
    for nome, operador in (("de", SessaoJogo.jogada_em.__ge__), ("ate", SessaoJogo.jogada_em.__le__)):
        if request.args.get(nome):
            dia = ler_data(request.args[nome])
            if dia is None:
                campos[nome] = "Use a data no formato AAAA-MM-DD."
            else:
                filtros.append(operador(dia))
    if campos:
        return invalido(campos)
    q = (SessaoJogo.query.filter(SessaoJogo.user_id == dono.id, *filtros)
         .join(Game, Game.id == SessaoJogo.game_id)
         .options(contains_eager(SessaoJogo.game), joinedload(SessaoJogo.plataforma)))
    if request.args.get("jogo"):
        q = q.filter(Game.slug == request.args["jogo"])
    page = pagina(q.order_by(SessaoJogo.jogada_em.desc(), SessaoJogo.id.desc()))
    return resposta(paginado(page, [serializadores.sessao(s) for s in page.items]))


@bp.route("/usuarios/<username>")
def usuario(username):
    dono = usuario_visivel(username)
    return resposta(serializadores.usuario_publico(dono)) if dono else usuario_nao_encontrado()


@bp.route("/usuarios/<username>/diario")
def diario_de_usuario(username):
    dono = usuario_visivel(username)
    return sessoes_de(dono) if dono else usuario_nao_encontrado()


@bp.route("/rankings/<categoria>")
def ranking(categoria):
    if categoria not in CATEGORIAS:
        return erro(404, "Ranking não encontrado")
    escopo = request.args.get("escopo") if request.args.get("escopo") in ESCOPOS else "global"
    me = visitante()
    if escopo != "global" and me is None:
        return nao_autorizado() if g.token is None else escopo_insuficiente("ler")
    linhas, *_ = linhas_do_ranking(categoria, escopo, request.args.get("janela"), me, request.args)
    page = paginar_lista(list(enumerate(linhas, start=1)), request.args.get("page"))
    return resposta(paginado(page, [{"posicao": p, "usuario": serializadores.usuario_publico(u), "valor": v}
                                    for p, (u, v) in page.items]))


@bp.route("/eu/status")
@do_dono
def meus_status(dono):
    q = (Status.query.filter_by(user_id=dono.id)
         .options(selectinload(Status.game).options(joinedload(Game.developer), selectinload(Game.genres),
                                                    selectinload(Game.platforms))))
    if request.args.get("status") in STATUSES:
        q = q.filter(Status.value == request.args["status"])
    page = pagina(q.order_by(Status.updated_at.desc(), Status.id.desc()))
    return resposta(paginado(page, [serializadores.status(st) for st in page.items]))


@bp.route("/eu/listas")
@do_dono
def minhas_listas(dono):
    page = pagina(GameList.query.filter_by(user_id=dono.id).order_by(GameList.created_at.desc(), GameList.id.desc()))
    ids = [gl.id for gl in page.items]
    jogos = dict(db.session.query(ListItem.list_id, func.count()).filter(ListItem.list_id.in_(ids))
                 .group_by(ListItem.list_id)) if ids else {}
    return resposta(paginado(page, [{"id": gl.id, "nome": gl.name, "url": serializadores.absoluta(f"/listas/{gl.id}"),
                                     "publica": gl.public, "jogos": jogos.get(gl.id, 0)} for gl in page.items]))


def lista_do_dono(dono, lid):
    gl = db.session.get(GameList, lid)
    return gl if gl is not None and gl.user_id == dono.id else None


def lista_completa(gl):
    itens = ListItem.query.filter_by(list_id=gl.id).options(joinedload(ListItem.game)).order_by(ListItem.position)
    return {"id": gl.id, **dados_da_lista(gl.name, f"/listas/{gl.id}", gl.public, gl.user_id, [i.game for i in itens])}


@bp.route("/eu/listas/<int:lid>")
@do_dono
def minha_lista(dono, lid):
    gl = lista_do_dono(dono, lid)
    return resposta(lista_completa(gl)) if gl else erro(404, "Lista não encontrada")


@bp.route("/eu/diario")
@do_dono
def meu_diario(dono):
    return sessoes_de(dono)


@bp.route("/eu/conquistas")
@do_dono
def minhas_conquistas(dono):
    q = (UsuarioConquista.query.filter_by(user_id=dono.id).join(Conquista)
         .options(contains_eager(UsuarioConquista.conquista)))
    if not pode(dono, "conquista_por_tempo"):
        q = q.filter(Conquista.por_tempo.is_(False))
    q = (q.order_by(UsuarioConquista.desbloqueada_em.is_(None), UsuarioConquista.desbloqueada_em.desc(),
                   UsuarioConquista.id))
    page = pagina(q)
    return resposta(paginado(page, [serializadores.conquista(uc) for uc in page.items]))


def corpo_invalido():
    return invalido({}, "corpo JSON inválido")


def como_formulario(corpo, booleanos=()):
    form, erros = {}, {}
    for chave, valor in corpo.items():
        if chave in booleanos:
            if isinstance(valor, bool):
                form[chave] = "1" if valor else ""
            else:
                erros[chave] = "Use true ou false."
        elif isinstance(valor, (dict, list)):
            erros[chave] = "Valor inválido."
        else:
            form[chave] = "" if valor is None else str(valor)
    return form, erros


def jogo_do_corpo(corpo):
    slug = corpo.get("jogo")
    return visible_games().filter_by(slug=slug).first() if isinstance(slug, str) else None


def jogo_nao_encontrado():
    return erro(404, "Jogo não encontrado")


@bp.route("/eu/status/<slug>", methods=["PUT"])
@do_dono
def marcar_status(dono, slug):
    game = visible_games().filter_by(slug=slug).first()
    if game is None:
        return jogo_nao_encontrado()
    corpo = corpo_json()
    if corpo is None:
        return corpo_invalido()
    valor = corpo.get("status")
    if "status" not in corpo or not (valor is None or isinstance(valor, str)):
        return invalido({"status": "Informe um status ou null para remover."})
    problema = definir_status(dono, game, valor)
    if problema:
        return invalido({"status": problema})
    return resposta({"jogo": game.slug, "status": valor or None})


@bp.route("/eu/avaliacoes/<slug>", methods=["PUT"])
@do_dono
def avaliar_jogo(dono, slug):
    game = visible_games().filter_by(slug=slug).first()
    if game is None:
        return jogo_nao_encontrado()
    corpo = corpo_json()
    if corpo is None:
        return corpo_invalido()
    spoiler = corpo.get("spoiler", False)
    if not isinstance(spoiler, bool):
        return invalido({"spoiler": "Use true ou false."})
    review, nova, erros = avaliar(dono, game, corpo.get("nota"), corpo.get("texto", ""), spoiler)
    if erros:
        return invalido(erros)
    return resposta(serializadores.avaliacao(review), 201 if nova else 200)


@bp.route("/eu/diario", methods=["POST"])
@do_dono
def registrar_no_diario(dono):
    corpo = corpo_json()
    if corpo is None:
        return corpo_invalido()
    form, erros = como_formulario(corpo, ("zerou", "spoiler"))
    game = jogo_do_corpo(corpo)
    if game is None:
        erros["jogo"] = "Jogo não encontrado."
    if erros:
        return invalido(erros)
    sessao, erros = registrar_sessao(dono, game, form)
    if erros:
        return invalido(erros)
    return resposta(serializadores.sessao(sessao), 201)


@bp.route("/eu/listas/<int:lid>/jogos", methods=["POST"])
@do_dono
def adicionar_a_lista(dono, lid):
    gl = lista_do_dono(dono, lid)
    if gl is None:
        return erro(404, "Lista não encontrada")
    corpo = corpo_json()
    if corpo is None:
        return corpo_invalido()
    game = jogo_do_corpo(corpo)
    problema = "Jogo não encontrado." if game is None else adicionar_jogo(gl, game)
    if problema:
        return invalido({"jogo": problema})
    return resposta(lista_completa(gl), 201)
