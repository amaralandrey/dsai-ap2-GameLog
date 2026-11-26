import json
import logging

from ..models import db
from ..tokens import autenticar

VERSOES = ("2025-06-18", "2025-03-26", "2024-11-05")
ERRO_JSON, ERRO_REQUISICAO, ERRO_METODO, ERRO_PARAMS, ERRO_INTERNO = -32700, -32600, -32601, -32602, -32603
NAO_INICIADO = -32002
LIVRES_ANTES_DE_INICIAR = ("initialize", "ping")
METODOS = {
    "initialize": "_initialize",
    "ping": "_ping",
    "notifications/initialized": "_ping",
    "tools/list": "_tools_list",
    "tools/call": "_tools_call",
}
log = logging.getLogger(__name__)


class ErroProtocolo(Exception):
    def __init__(self, codigo, mensagem):
        super().__init__(mensagem)
        self.codigo, self.mensagem = codigo, mensagem


class Sessao:
    def __init__(self, token_id):
        self.token_id, self.iniciado = token_id, False

    def tratar(self, msg):
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            raise ErroProtocolo(ERRO_REQUISICAO, "requisição inválida")
        nome = METODOS.get(msg["method"])
        if nome is None:
            raise ErroProtocolo(ERRO_METODO, f"método desconhecido: {msg['method']}")
        if not self.iniciado and msg["method"] not in LIVRES_ANTES_DE_INICIAR:
            raise ErroProtocolo(NAO_INICIADO, "servidor ainda não inicializado")
        params = msg.get("params", {})
        if not isinstance(params, dict):
            raise ErroProtocolo(ERRO_PARAMS, "params precisa ser um objeto")
        return getattr(self, nome)(params)

    def _initialize(self, params):
        pedida = params.get("protocolVersion")
        self.iniciado = True
        return {
            "protocolVersion": pedida if pedida in VERSOES else VERSOES[0],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "gamelog", "version": "1"},
        }

    def _ping(self, params):
        return {}

    def _tools_list(self, params):
        from .ferramentas import listar

        return {"tools": listar(self.token_id)}

    def _tools_call(self, params):
        from .ferramentas import chamar

        if not isinstance(params.get("name"), str):
            raise ErroProtocolo(ERRO_PARAMS, "informe o nome da ferramenta")
        return chamar(self.token_id, params["name"], params.get("arguments", {}))


def _erro(id_, codigo, mensagem):
    return json.dumps({"jsonrpc": "2.0", "id": id_, "error": {"code": codigo, "message": mensagem}},
                      ensure_ascii=False)


def processar(sessao, linha):
    try:
        msg = json.loads(linha)
    except ValueError:
        return _erro(None, ERRO_JSON, "JSON inválido")
    notificacao = isinstance(msg, dict) and "id" not in msg
    id_ = msg.get("id") if isinstance(msg, dict) else None
    try:
        resultado = sessao.tratar(msg)
    except ErroProtocolo as e:
        return None if notificacao else _erro(id_, e.codigo, e.mensagem)
    except Exception:
        log.exception("falha ao tratar a mensagem MCP")
        db.session.rollback()
        return None if notificacao else _erro(id_, ERRO_INTERNO, "erro interno")
    if notificacao:
        return None
    return json.dumps({"jsonrpc": "2.0", "id": id_, "result": resultado}, ensure_ascii=False)


def servir(sessao, entrada, saida):
    for linha in entrada:
        if not linha.strip():
            continue
        resposta = processar(sessao, linha)
        if resposta is not None:
            saida.write(resposta + "\n")
            saida.flush()
        db.session.remove()


def iniciar(texto_token, entrada, saida, erros):
    token = autenticar(texto_token)
    if token is None:
        erros.write("erro=token inválido\n")
        erros.flush()
        return 1
    sessao = Sessao(token.id)
    db.session.remove()
    servir(sessao, entrada, saida)
    return 0
