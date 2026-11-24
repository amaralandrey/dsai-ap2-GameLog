import io
import json
from datetime import date, datetime
from pathlib import Path


from app.models import AuditoriaToken, Review, SessaoJogo, Status, User, db
from conftest import make_game, make_user
from test_api_v2_tokens import linha, relogio, token

INIT = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}


def rodar(app, mensagens, texto=None):
    from app.mcp.protocolo import Sessao, servir

    with app.app_context():
        tid = linha(texto).id if texto else 1
    entrada = "\n".join(m if isinstance(m, str) else json.dumps(m) for m in mensagens) + "\n"
    saida = io.StringIO()
    with app.app_context():
        servir(Sessao(tid), io.StringIO(entrada), saida)
    return [json.loads(x) for x in saida.getvalue().splitlines()]


def rpc(metodo, params=None, id_=1):
    m = {"jsonrpc": "2.0", "id": id_, "method": metodo}
    if params is not None:
        m["params"] = params
    return m


def test_mcp_02_initialize_e_ping(app):
    texto = token(app)
    r = rodar(app, [rpc("tools/list", id_=9), rpc("ping", id_=8), INIT,
                    {"jsonrpc": "2.0", "method": "notifications/initialized"}, rpc("ping", id_=2)], texto)
    assert [m["id"] for m in r] == [9, 8, 0, 2]
    assert r[0]["error"]["code"] == -32002
    assert r[1]["result"] == {}
    assert r[2]["result"]["protocolVersion"] == "2025-06-18"
    assert r[2]["result"]["capabilities"] == {"tools": {}}
    assert r[2]["result"]["serverInfo"]["name"] == "gamelog"
    assert r[3]["result"] == {}
    antiga = rodar(app, [{**INIT, "params": {"protocolVersion": "2025-03-26"}}], texto)[0]["result"]
    assert antiga["protocolVersion"] == "2025-03-26"
    from app.mcp.protocolo import VERSOES

    desconhecida = rodar(app, [{**INIT, "params": {"protocolVersion": "1999-01-01"}}], texto)[0]["result"]
    assert desconhecida["protocolVersion"] == VERSOES[0]


def test_mcp_03_codigos(app):
    texto = token(app)
    r = rodar(app, [INIT, "{x", rpc("ping", id_=2), {"id": 3, "method": "ping"}, rpc("ping", id_=4), "[]",
                    {"jsonrpc": "2.0", "id": 5}, rpc("nada/aqui", id_=6), {**INIT, "id": 7, "params": [1]},
                    rpc("ping", id_=8)], texto)
    codigos = [(m["id"], m.get("error", {}).get("code")) for m in r]
    assert codigos == [(0, None), (None, -32700), (2, None), (3, -32600), (4, None), (None, -32600), (5, -32600),
                       (6, -32601), (7, -32602), (8, None)]


def test_mcp_03_notificacao_sem_resposta(app):
    texto = token(app)
    r = rodar(app, [INIT, {"jsonrpc": "2.0", "method": "nada/aqui"}, {"jsonrpc": "2.0", "method": "tools/call",
                                                                     "params": {"name": "x"}}, ""], texto)
    assert len(r) == 1


def test_mcp_01_stdout_so_json_rpc(app):
    texto = token(app)
    r = rodar(app, [INIT, rpc("tools/list"), "{x", rpc("nada")], texto)
    assert all(m["jsonrpc"] == "2.0" for m in r) and len(r) == 4


def test_mcp_03_erro_interno_nao_derruba(app, monkeypatch):
    from app.mcp import protocolo

    texto = token(app)

    def quebra(*a):
        raise RuntimeError("segredo interno")

    monkeypatch.setattr(protocolo.Sessao, "_ping", quebra)
    r = rodar(app, [INIT, rpc("ping", id_=2), rpc("tools/list", id_=3)], texto)
    assert r[1]["error"] == {"code": -32603, "message": "erro interno"}
    assert "result" in r[2]


def test_mcp_04_sem_token_e_revogado(app):
    from app.mcp.protocolo import iniciar
    from app.tokens import revogar

    texto = token(app)
    with app.app_context():
        revogar(linha(texto))
        db.session.commit()
    for valor in (None, "", "glp_nada", texto):
        entrada, saida, erros = io.StringIO(json.dumps(INIT) + "\n"), io.StringIO(), io.StringIO()
        with app.app_context():
            assert iniciar(valor, entrada, saida, erros) == 1
        assert erros.getvalue() == "erro=token inválido\n"
        assert saida.getvalue() == "" and entrada.tell() == 0


def test_mcp_04_cli(app, monkeypatch):
    texto = token(app)
    runner = app.test_cli_runner()
    monkeypatch.setenv("GAMELOG_TOKEN", "")
    r = runner.invoke(args=["mcp"], input=json.dumps(INIT) + "\n")
    assert r.exit_code == 1 and r.stderr == "erro=token inválido\n" and r.stdout == ""
    monkeypatch.setenv("GAMELOG_TOKEN", texto)
    r = runner.invoke(args=["mcp"], input='{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
    assert r.exit_code == 0
    assert json.loads(r.stdout.strip().splitlines()[-1]) == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert texto not in r.stdout and texto not in r.stderr

LEITURA = {"buscar_jogos", "detalhes_jogo", "disponivel_em", "minhas_listas", "recomendacoes", "comparar_gosto"}
ESCRITA = {"marcar_status", "avaliar", "registrar_sessao"}


def chamar(app, texto, nome, argumentos=None, extra=()):
    r = rodar(app, [INIT, *extra, rpc("tools/call", {"name": nome, "arguments": argumentos or {}}, id_=99)], texto)
    return r[-1]


def test_mcp_05_lista_com_e_sem_escrita(app):
    so_ler, tudo = token(app, escopos=("ler",)), token(app, "bia")
    nomes = lambda t: {f["name"] for f in rodar(app, [INIT, rpc("tools/list")], t)[1]["result"]["tools"]}  # noqa: E731
    assert nomes(so_ler) == LEITURA
    assert nomes(tudo) == LEITURA | ESCRITA
    for f in rodar(app, [INIT, rpc("tools/list")], tudo)[1]["result"]["tools"]:
        assert f["description"] and f["inputSchema"]["type"] == "object"
        assert f["inputSchema"]["additionalProperties"] is False and "required" in f["inputSchema"]


def test_mcp_06_validacao_antes_do_servico(app, monkeypatch):
    from dataclasses import replace

    from app.mcp import ferramentas

    texto = token(app)
    chamadas = []
    original = ferramentas.FERRAMENTAS["avaliar"]
    monkeypatch.setitem(ferramentas.FERRAMENTAS, "avaliar",
                        replace(original, executar=lambda t, a: chamadas.append(a) or ferramentas.resultado("ok", {})))
    for args, campo in (({"slug": "x", "nota": "8"}, "nota"), ({"slug": "x", "nota": True}, "nota"),
                        ({"slug": "x", "nota": 11}, "nota"), ({"nota": 5}, "slug"),
                        ({"slug": "x", "nota": 5, "extra": 1}, "extra"), ({"slug": "x", "nota": 5, "spoiler": 1}, "spoiler"),
                        ({"slug": "x", "nota": 5, "texto": "a" * 5001}, "texto")):
        r = chamar(app, texto, "avaliar", args)["result"]
        assert r["isError"] is True and r["content"][0]["text"].startswith(campo), (args, r)
    assert chamadas == []
    assert chamar(app, texto, "avaliar", {"slug": "x", "nota": 5})["result"]["isError"] is False
    assert chamadas == [{"slug": "x", "nota": 5}]
    assert chamar(app, texto, "nada", {})["error"]["code"] == -32602
    sem_args = rodar(app, [INIT, rpc("tools/call", {"name": "avaliar", "arguments": [1]})], texto)[1]
    assert sem_args["error"]["code"] == -32602


def test_mcp_06_validar():
    from app.mcp.ferramentas import validar

    schema = {"type": "object", "additionalProperties": False, "required": ["a"],
              "properties": {"a": {"type": "string", "minLength": 1, "pattern": r"^\d{4}-\d{2}-\d{2}$"},
                             "n": {"type": "integer", "minimum": 1, "maximum": 3},
                             "e": {"type": "string", "enum": ["x", "y"]}, "b": {"type": "boolean"}}}
    assert validar(schema, {"a": "2026-11-20", "n": 2, "e": "x", "b": False}) is None
    assert validar(schema, {"a": "20/11/2026"}).startswith("a:")
    assert validar(schema, {"a": ""}).startswith("a:")
    assert validar(schema, {"a": "2026-11-20", "n": 4}).startswith("n:")
    assert validar(schema, {"a": "2026-11-20", "n": 0}).startswith("n:")
    assert validar(schema, {"a": "2026-11-20", "n": 2.0}).startswith("n:")
    assert validar(schema, {"a": "2026-11-20", "e": "z"}).startswith("e:")
    assert validar(schema, {"a": "2026-11-20", "b": "true"}).startswith("b:")


def estruturado(resposta):
    r = resposta["result"]
    assert r["isError"] is False, r
    return r["structuredContent"]


def erro_texto(resposta):
    r = resposta["result"]
    assert r["isError"] is True and "structuredContent" not in r
    return r["content"][0]["text"]


def test_mcp_07_buscar_detalhes_disponivel(app):
    from app.models import Game, JogoFonte
    from app.serializadores import jogo

    texto = token(app)
    make_game(app, "Alfa Quest", genre="RPG")
    make_game(app, "Alfa Zero")
    make_game(app, "Removido Alfa")
    with app.app_context():
        a = Game.query.filter_by(slug="alfa-quest").one()
        db.session.add(JogoFonte(game_id=a.id, fonte="gog", external_id="9", url_loja="https://gog.com/a"))
        Game.query.filter_by(slug="removido-alfa").one().removed_at = datetime(2026, 11, 1)
        db.session.commit()
        esperado = jogo(db.session.get(Game, a.id))
    busca = chamar(app, texto, "buscar_jogos", {"termo": "alfa"})
    dados = estruturado(busca)
    assert {j["slug"] for j in dados["jogos"]} == {"alfa-quest", "alfa-zero"}
    assert "Alfa Quest" in busca["result"]["content"][0]["text"]
    assert estruturado(chamar(app, texto, "buscar_jogos", {"termo": "alfa", "loja": "gog"}))["jogos"] == [esperado]
    assert len(estruturado(chamar(app, texto, "buscar_jogos", {"termo": "alfa", "limite": 1}))["jogos"]) == 1
    assert [j["slug"] for j in estruturado(chamar(app, texto, "buscar_jogos", {"termo": "alfa", "genero": "rpg"}))
            ["jogos"]] == ["alfa-quest"]
    detalhe = estruturado(chamar(app, texto, "detalhes_jogo", {"slug": "alfa-quest"}))
    assert detalhe["slug"] == "alfa-quest" and detalhe["sinopse"] and "precos" in detalhe
    lojas = estruturado(chamar(app, texto, "disponivel_em", {"slug": "alfa-quest"}))
    assert lojas == {"slug": "alfa-quest", "lojas": [{"fonte": "gog", "external_id": "9", "url": "https://gog.com/a"}]}
    for nome in ("detalhes_jogo", "disponivel_em"):
        for slug in ("nada", "removido-alfa"):
            assert erro_texto(chamar(app, texto, nome, {"slug": slug})) == "jogo não encontrado"


def test_mcp_07_minhas_listas_inclui_privadas(app):
    from app.models import GameList, ListItem

    texto = token(app)
    make_game(app, "Alfa")
    with app.app_context():
        ana = User.query.one()
        from app.models import Game

        g = Game.query.one()
        gl = GameList(user_id=ana.id, name="Secreta", public=False)
        db.session.add_all([gl, Status(user_id=ana.id, game_id=g.id, value="planejado")])
        db.session.flush()
        db.session.add(ListItem(list_id=gl.id, game_id=g.id, position=0))
        db.session.commit()
    dados = estruturado(chamar(app, texto, "minhas_listas"))
    assert [(s["jogo"]["slug"], s["status"]) for s in dados["status"]] == [("alfa", "planejado")]
    assert dados["listas"][0]["nome"] == "Secreta" and dados["listas"][0]["publica"] is False
    assert dados["listas"][0]["itens"][0]["jogo"]["slug"] == "alfa"
    assert estruturado(chamar(app, texto, "minhas_listas", {"status": "jogado"}))["status"] == []


def test_mcp_09_recomendacoes_e_comparar(app):
    from app.bloqueios import bloquear
    from test_perfil import avalia

    texto = token(app)
    for nome in ("bia", "carla", "davi", "eva"):
        make_user(app, nome)
    ids = []
    for i in range(6):
        make_game(app, f"Jogo {i}")
        with app.app_context():
            from app.models import Game

            ids.append(Game.query.filter_by(slug=f"jogo-{i}").one().id)
    for gid in ids[:3]:
        for nome in ("ana", "bia", "carla", "davi", "eva"):
            avalia(app, nome, gid, 8)
    with app.app_context():
        User.query.filter_by(username_lower="bia").one().perfil_privado = True
        User.query.filter_by(username_lower="davi").one().nascimento = date(2009, 6, 1)
        bloquear(User.query.filter_by(username_lower="eva").one(), User.query.filter_by(username_lower="ana").one())
        db.session.commit()
    recs = estruturado(chamar(app, texto, "recomendacoes", {"limite": 2}))
    assert len(recs["recomendacoes"]) == 2 and {"jogo", "nota_media", "motivo"} <= set(recs["recomendacoes"][0])
    gosto = estruturado(chamar(app, texto, "comparar_gosto", {"username": "carla"}))
    assert gosto == {"username": "carla", "afinidade": 100, "jogos_em_comum": 3}
    for nome in ("bia", "davi", "eva", "ninguem", "ana"):
        assert erro_texto(chamar(app, texto, "comparar_gosto", {"username": nome})) == "usuário não encontrado", nome


def test_mcp_08_status_avaliar_sessao_mesma_regra(app, monkeypatch):
    from app.models import Activity, Game

    relogio(monkeypatch, datetime(2026, 11, 20, 23, 30))
    texto = token(app)
    make_game(app, "Alfa")
    make_game(app, "Beta")
    r = chamar(app, texto, "avaliar", {"slug": "alfa", "nota": 9, "texto": "ótimo"})
    assert estruturado(r)["nota"] == 9
    with app.app_context():
        alfa, beta = (Game.query.filter_by(slug=s).one().id for s in ("alfa", "beta"))
        assert Status.query.filter_by(game_id=alfa).one().value == "jogado"
        assert Activity.query.filter_by(kind="avaliou").count() == 1
    assert estruturado(chamar(app, texto, "marcar_status", {"slug": "beta", "status": "planejado"}))["status"] == "planejado"
    sessao = estruturado(chamar(app, texto, "registrar_sessao", {"slug": "beta", "duracao_min": 90, "anotacao": "boa"}))
    assert sessao["jogada_em"] == "2026-11-20" and sessao["duracao_min"] == 90
    with app.app_context():
        assert Status.query.filter_by(game_id=beta).one().value == "jogando"
    estruturado(chamar(app, texto, "registrar_sessao", {"slug": "beta", "duracao_min": 1300, "data": "2026-11-19"}))
    demais = chamar(app, texto, "registrar_sessao", {"slug": "beta", "duracao_min": 200, "data": "2026-11-19"})
    assert "24 horas" in erro_texto(demais)
    assert erro_texto(chamar(app, texto, "registrar_sessao", {"slug": "beta", "duracao_min": 30,
                                                              "data": "2026-12-01"})).startswith("jogada_em")
    assert estruturado(chamar(app, texto, "marcar_status", {"slug": "beta", "status": "nenhum"}))["status"] is None
    with app.app_context():
        assert Status.query.filter_by(game_id=beta).count() == 0
    assert erro_texto(chamar(app, texto, "avaliar", {"slug": "nada", "nota": 5})) == "jogo não encontrado"


def test_mcp_08_sem_escopo(app):
    texto = token(app, escopos=("ler",))
    make_game(app, "Alfa")
    for nome, args in (("marcar_status", {"slug": "alfa", "status": "jogado"}), ("avaliar", {"slug": "alfa", "nota": 5}),
                       ("registrar_sessao", {"slug": "alfa", "duracao_min": 10})):
        assert erro_texto(chamar(app, texto, nome, args)) == "escopo insuficiente"
    with app.app_context():
        assert Status.query.count() == 0 and Review.query.count() == 0 and SessaoJogo.query.count() == 0


def test_mcp_10_limite_e_auditoria(app, monkeypatch):
    from app.tokens import revogar

    relogio(monkeypatch, datetime(2026, 11, 20, 10, 0, 15))
    texto = token(app)
    make_game(app, "Alfa")
    pedidos = [rpc("tools/call", {"name": "detalhes_jogo", "arguments": {"slug": "alfa"}}, id_=i) for i in range(61)]
    r = rodar(app, [INIT, *pedidos], texto)
    assert all(m["result"]["isError"] is False for m in r[1:61])
    assert r[61]["result"]["content"][0]["text"] == "limite de requisições; tente em 45 s"
    relogio(monkeypatch, datetime(2026, 11, 20, 10, 1, 0))
    chamar(app, texto, "avaliar", {"slug": "alfa", "nota": 7})
    chamar(app, texto, "detalhes_jogo", {"slug": "alfa"})
    with app.app_context():
        linhas = AuditoriaToken.query.filter_by(acao="escrita").all()
        assert [(a.metodo, a.rota) for a in linhas] == [("MCP", "mcp:avaliar")]
        revogar(linha(texto))
        db.session.commit()
    assert erro_texto(chamar(app, texto, "detalhes_jogo", {"slug": "alfa"})) == "token inválido"


def test_mcp_11_skills_citam_ferramentas_existentes(app):
    import re

    raiz = Path(__file__).resolve().parent.parent / "skills"
    assert sorted(p.name for p in raiz.iterdir() if p.is_dir()) == ["gamelog-catalogo", "gamelog-descoberta",
                                                                    "gamelog-diario"]
    existentes = {f["name"] for f in rodar(app, [INIT, rpc("tools/list")], token(app))[1]["result"]["tools"]}
    citadas = set()
    for pasta in raiz.iterdir():
        texto = (pasta / "SKILL.md").read_text(encoding="utf-8")
        frontmatter = re.match(r"^---\n(.*?)\n---\n", texto, re.S)
        assert frontmatter, pasta
        campos = dict(linha.split(": ", 1) for linha in frontmatter.group(1).splitlines())
        assert campos["name"] == pasta.name and len(campos["description"]) > 20
        linha = re.search(r"^Ferramentas: (.+)$", texto, re.M)
        assert linha, pasta
        nomes = set(re.findall(r"`([a-z_]+)`", linha.group(1)))
        assert nomes and nomes <= existentes, nomes - existentes
        citadas |= nomes
    assert citadas == existentes



def test_mcp_10_chamada_grava_ultimo_uso(app, monkeypatch):
    relogio(monkeypatch, datetime(2026, 11, 20, 10, 0))
    texto = token(app)
    make_game(app, "Alfa")
    chamar(app, texto, "detalhes_jogo", {"slug": "alfa"})
    with app.app_context():
        assert linha(texto).ultimo_uso_em == datetime(2026, 11, 20, 13, 0)


def test_mcp_04_conta_suspensa_nao_usa_o_token(app):
    texto = token(app)
    with app.app_context():
        User.query.one().suspensa_em = datetime(2026, 11, 20)
        db.session.commit()
    from app.mcp.protocolo import iniciar

    erros = io.StringIO()
    with app.app_context():
        assert iniciar(texto, io.StringIO(""), io.StringIO(), erros) == 1
