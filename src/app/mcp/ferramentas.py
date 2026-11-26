import re
from dataclasses import dataclass
from typing import Callable

from werkzeug.datastructures import MultiDict

from .. import serializadores
from ..models import STATUSES, GameList, Status, TokenApi, User, db, visible_games
from ..tempo import agora_utc
from ..tokens import auditar, consumir, tem_escopo, valido
from .protocolo import ERRO_PARAMS, ErroProtocolo

FERRAMENTAS = {}
TIPOS = {"string": lambda v: isinstance(v, str), "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
         "boolean": lambda v: isinstance(v, bool)}


@dataclass(frozen=True)
class Ferramenta:
    nome: str
    descricao: str
    schema: dict
    escopo: str
    executar: Callable


def esquema(obrigatorios, **propriedades):
    return {"type": "object", "properties": propriedades, "required": list(obrigatorios),
            "additionalProperties": False}


def ferramenta(nome, descricao, schema, escopo="ler"):
    def registrar(funcao):
        FERRAMENTAS[nome] = Ferramenta(nome, descricao, schema, escopo, funcao)
        return funcao
    return registrar


def resultado(texto, dados):
    return {"content": [{"type": "text", "text": texto}], "structuredContent": dados, "isError": False}


def falha(texto):
    return {"content": [{"type": "text", "text": texto}], "isError": True}


def _problema(regra, valor):
    if not TIPOS[regra["type"]](valor):
        return f"deve ser do tipo {regra['type']}"
    if "enum" in regra and valor not in regra["enum"]:
        return "use um de: " + ", ".join(str(v) for v in regra["enum"])
    if "minimum" in regra and valor < regra["minimum"]:
        return f"mínimo {regra['minimum']}"
    if "maximum" in regra and valor > regra["maximum"]:
        return f"máximo {regra['maximum']}"
    if "minLength" in regra and len(valor) < regra["minLength"]:
        return f"pelo menos {regra['minLength']} caractere(s)"
    if "maxLength" in regra and len(valor) > regra["maxLength"]:
        return f"até {regra['maxLength']} caracteres"
    if "pattern" in regra and not re.search(regra["pattern"], valor):
        return "formato inválido"
    return None


def validar(schema, argumentos):
    for chave in argumentos:
        if chave not in schema["properties"]:
            return f"{chave}: propriedade não permitida"
    for chave in schema["required"]:
        if chave not in argumentos:
            return f"{chave}: obrigatório"
    for chave, valor in argumentos.items():
        if problema := _problema(schema["properties"][chave], valor):
            return f"{chave}: {problema}"
    return None


def listar(token_id):
    token = db.session.get(TokenApi, token_id)
    return [{"name": f.nome, "description": f.descricao, "inputSchema": f.schema}
            for f in FERRAMENTAS.values() if token is not None and tem_escopo(token, f.escopo)]


def chamar(token_id, nome, argumentos):
    alvo = FERRAMENTAS.get(nome)
    if alvo is None:
        raise ErroProtocolo(ERRO_PARAMS, f"ferramenta desconhecida: {nome}")
    if not isinstance(argumentos, dict):
        raise ErroProtocolo(ERRO_PARAMS, "arguments precisa ser um objeto")
    token = db.session.get(TokenApi, token_id)
    if not valido(token):
        return falha("token inválido")
    token.ultimo_uso_em = agora_utc()
    permitido, _, espera = consumir(token)
    if not permitido:
        return falha(f"limite de requisições; tente em {espera} s")
    if not tem_escopo(token, alvo.escopo):
        return falha("escopo insuficiente")
    if problema := validar(alvo.schema, argumentos):
        return falha(problema)
    saida = alvo.executar(token, argumentos)
    if alvo.escopo == "escrever":
        db.session.rollback()
        auditar(db.session.get(TokenApi, token_id), "escrita", "MCP", f"mcp:{nome}")
        db.session.commit()
    return saida


SLUG = {"type": "string", "minLength": 1, "maxLength": 170, "description": "slug do jogo, como em /jogos/<slug>"}
JOGO_NAO_ENCONTRADO, USUARIO_NAO_ENCONTRADO = "jogo não encontrado", "usuário não encontrado"


def jogo_visivel(slug):
    from ..catalogo_merge import principal_do_mesclado

    return visible_games().filter_by(slug=slug).first() or principal_do_mesclado(slug)


def _titulos(games):
    return "; ".join(f"{g.title} ({g.release_date.year})" for g in games)


@ferramenta("buscar_jogos", "Busca jogos no catálogo do GameLog por nome, desenvolvedora ou gênero, com filtro de "
            "gênero e de loja.",
            esquema(["termo"], termo={"type": "string", "minLength": 1, "maxLength": 100},
                    genero={"type": "string", "maxLength": 60, "description": "slug do gênero, ex.: rpg"},
                    loja={"type": "string", "enum": ["steam", "gog", "epic"]},
                    limite={"type": "integer", "minimum": 1, "maximum": 20}))
def buscar_jogos(token, argumentos):
    from ..busca import search_games

    filtros = MultiDict({"q": argumentos["termo"]})
    for chave in ("genero", "loja"):
        if chave in argumentos:
            filtros[chave] = argumentos[chave]
    q, _ = search_games(filtros)
    games = [g for g, _ in q.limit(argumentos.get("limite", 10)).all()]
    texto = f"{len(games)} jogo(s): {_titulos(games)}" if games else "Nenhum jogo encontrado."
    return resultado(texto, {"jogos": [serializadores.jogo(g) for g in games]})


@ferramenta("detalhes_jogo", "Detalhes de um jogo: sinopse, gêneros, plataformas, nota média, preços e lojas.",
            esquema(["slug"], slug=SLUG))
def detalhes_jogo(token, argumentos):
    from ..api import detalhe_de_jogo

    game = jogo_visivel(argumentos["slug"])
    if game is None:
        return falha(JOGO_NAO_ENCONTRADO)
    dados = detalhe_de_jogo(game)
    media = f"nota média {dados['nota_media']}" if dados["nota_media"] is not None else "sem avaliações"
    return resultado(f"{game.title} ({game.release_date.year}), de {game.developer.name}; {media}. {game.synopsis}",
                     dados)


@ferramenta("disponivel_em", "Lojas em que o jogo está (Steam, GOG, Epic) com os links.",
            esquema(["slug"], slug=SLUG))
def disponivel_em(token, argumentos):
    game = jogo_visivel(argumentos["slug"])
    if game is None:
        return falha(JOGO_NAO_ENCONTRADO)
    lojas = serializadores.fontes(game)
    texto = f"{game.title} está em: " + ", ".join(f"{v['fonte']} ({v['url'] or 'sem link'})" for v in lojas) \
        if lojas else f"{game.title} não tem loja vinculada."
    return resultado(texto, {"slug": game.slug, "lojas": lojas})


@ferramenta("minhas_listas", "Status dos seus jogos e suas listas, inclusive as privadas.",
            esquema([], status={"type": "string", "enum": list(STATUSES)}))
def minhas_listas(token, argumentos):
    from ..api_v2 import lista_completa

    q = Status.query.filter_by(user_id=token.user_id)
    if "status" in argumentos:
        q = q.filter(Status.value == argumentos["status"])
    status = [serializadores.status(st) for st in q.order_by(Status.updated_at.desc(), Status.id.desc())]
    listas = [lista_completa(gl) for gl in GameList.query.filter_by(user_id=token.user_id)
              .order_by(GameList.created_at.desc(), GameList.id.desc())]
    texto = f"{len(status)} jogo(s) com status e {len(listas)} lista(s): " + ", ".join(lista["nome"] for lista in listas)
    return resultado(texto, {"status": status, "listas": listas})


def _erros(erros):
    return falha("; ".join(f"{campo}: {mensagem}" for campo, mensagem in erros.items()))


@ferramenta("marcar_status", "Marca o status de um jogo para você; \"nenhum\" remove o status.",
            esquema(["slug", "status"], slug=SLUG, status={"type": "string", "enum": [*STATUSES, "nenhum"]}),
            escopo="escrever")
def marcar_status(token, argumentos):
    from ..listas import definir_status

    game = jogo_visivel(argumentos["slug"])
    if game is None:
        return falha(JOGO_NAO_ENCONTRADO)
    valor = None if argumentos["status"] == "nenhum" else argumentos["status"]
    if problema := definir_status(token.user, game, valor):
        return _erros({"status": problema})
    return resultado(f"{game.title}: status {valor or 'removido'}.", {"jogo": game.slug, "status": valor})


@ferramenta("avaliar", "Avalia um jogo com nota inteira de 0 a 10 e resenha opcional; avaliar de novo edita.",
            esquema(["slug", "nota"], slug=SLUG, nota={"type": "integer", "minimum": 0, "maximum": 10},
                    texto={"type": "string", "maxLength": 5000}, spoiler={"type": "boolean"}),
            escopo="escrever")
def avaliar_jogo(token, argumentos):
    from ..avaliacoes import avaliar

    game = jogo_visivel(argumentos["slug"])
    if game is None:
        return falha(JOGO_NAO_ENCONTRADO)
    review, nova, erros = avaliar(token.user, game, argumentos["nota"], argumentos.get("texto", ""),
                                  argumentos.get("spoiler", False))
    if erros:
        return _erros(erros)
    acao = "registrada" if nova else "atualizada"
    return resultado(f"Avaliação de {game.title} {acao}: nota {review.score}.", serializadores.avaliacao(review))


@ferramenta("registrar_sessao", "Registra uma sessão de jogo no seu diário; sem data, vale hoje (fuso de Belém).",
            esquema(["slug", "duracao_min"], slug=SLUG, duracao_min={"type": "integer", "minimum": 1, "maximum": 1440},
                    data={"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}, zerou={"type": "boolean"},
                    anotacao={"type": "string", "maxLength": 1000}),
            escopo="escrever")
def registrar_sessao_jogo(token, argumentos):
    from ..diario import formatar_minutos, registrar_sessao
    from ..tempo import hoje

    game = jogo_visivel(argumentos["slug"])
    if game is None:
        return falha(JOGO_NAO_ENCONTRADO)
    form = {"jogada_em": argumentos.get("data") or hoje().isoformat(), "duracao": str(argumentos["duracao_min"]),
            "zerou": "1" if argumentos.get("zerou") else "", "anotacao": argumentos.get("anotacao", "")}
    sessao, erros = registrar_sessao(token.user, game, form)
    if erros:
        return _erros(erros)
    return resultado(f"Sessão de {formatar_minutos(sessao.duracao_min)} em {game.title} registrada em "
                     f"{sessao.jogada_em.strftime('%d/%m/%Y')}.", serializadores.sessao(sessao))


@ferramenta("recomendacoes", "Jogos recomendados para você pelas suas notas e por quem tem gosto parecido.",
            esquema([], limite={"type": "integer", "minimum": 1, "maximum": 10}))
def recomendacoes(token, argumentos):
    from ..recomendacoes import recommend

    itens, aviso = recommend(token.user_id, argumentos.get("limite", 5))
    dados = [{"jogo": serializadores.jogo(g), "nota_media": round(media, 1) if media is not None else None,
              "motivo": motivo} for g, media, motivo in itens]
    texto = "; ".join(f"{g.title}: {motivo}" for g, _, motivo in itens) or "Nenhuma recomendação agora."
    return resultado(texto, {"recomendacoes": dados, "aviso": aviso})


@ferramenta("comparar_gosto", "Afinidade de gosto entre você e outro usuário, pelas notas dos jogos em comum.",
            esquema(["username"], username={"type": "string", "minLength": 1, "maxLength": 20}))
def comparar_gosto(token, argumentos):
    from ..afinidade import resumo_com
    from ..diario import pode_ver_perfil
    from ..menores.politica import pode

    outro = User.query.filter_by(username_lower=argumentos["username"].lower()).first()
    if (outro is None or outro.id == token.user_id or not pode_ver_perfil(token.user, outro)
            or not pode(outro, "historico_para_terceiros")):
        return falha(USUARIO_NAO_ENCONTRADO)
    afinidade, comuns = resumo_com(token.user_id, outro.id)
    texto = (f"Afinidade com {outro.username}: {afinidade}% em {comuns} jogo(s) em comum." if afinidade is not None
             else f"Poucos jogos em comum com {outro.username} ({comuns}) para calcular a afinidade.")
    return resultado(texto, {"username": outro.username, "afinidade": afinidade, "jogos_em_comum": comuns})
