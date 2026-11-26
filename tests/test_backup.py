import copy
import hashlib
import hmac
import json
from datetime import date, datetime

import pytest

from app.models import (AuditLog, Game, GameList, Job, ListItem, LoginAttempt, Review, SessaoJogo, User, db)
from conftest import login, make_game, make_user
from test_api_v2_tokens import token

CHAVE = "k" * 32
TABELAS_NO_BACKUP = frozenset({
    "aceite_documento", "activity", "agendamento", "anotacao_incidente", "audit_log", "auditoria_token",
    "aviso_documento_fechado", "bloqueio", "clube", "codigo_recuperacao", "comment", "conquista", "consentimento",
    "contribuinte_meta", "desclassificacao", "developer", "dismissed", "documento_legal", "email_saida",
    "evento_seguranca", "extrato_xp", "follow", "game", "game_genres", "game_list", "game_platforms", "genre",
    "incidente", "inscricao_torneio", "job", "jogo_do_mes", "jogo_fonte", "like", "list_item", "lista_inteligente",
    "membro_clube", "meta_clube", "notificacao", "partida_torneio", "pedido_titular", "pendencia_merge",
    "perfil_progressao", "platform", "post_clube", "preco_loja", "preferencia_notificacao", "previa_importacao",
    "registro_torneio", "report", "reporte_resultado", "review", "sessao_jogo", "silencio", "snapshot_ranking",
    "status", "temporada", "token_api", "torneio", "user", "usuario_conquista", "verificacao_idade",
})


@pytest.fixture
def chave(monkeypatch):
    monkeypatch.setenv("BACKUP_KEY", CHAVE)
    return CHAVE


@pytest.fixture
def mundo(app, chave):
    texto = token(app, "ana")
    make_user(app, "bia")
    slug = make_game(app, "Alfa", genre="RPG")
    with app.app_context():
        ana = User.query.filter_by(username_lower="ana").one()
        g = Game.query.filter_by(slug=slug).one()
        db.session.add(Review(user_id=ana.id, game_id=g.id, score=8, text="bom", spoiler=True))
        db.session.add(SessaoJogo(user_id=ana.id, game_id=g.id, jogada_em=date(2026, 11, 20), duracao_min=90,
                                  origem="manual"))
        gl = GameList(user_id=ana.id, name="Minha", public=False)
        db.session.add(gl)
        db.session.flush()
        db.session.add(ListItem(list_id=gl.id, game_id=g.id, position=0))
        agora = datetime(2026, 11, 20, 12, 0)
        db.session.add(Job(tipo="sync_games", status="ok", executar_em=agora, criado_em=agora))
        db.session.add(Job(tipo="sync_games", status="pendente", executar_em=agora, criado_em=agora))
        db.session.add(LoginAttempt(email="x@ex.com"))
        db.session.commit()
    return texto


def exportar(app):
    from app.backup import exportar as exp

    with app.app_context():
        return exp(CHAVE)


def assinado(dados):
    from app.backup import assinar

    conteudo = {k: v for k, v in dados.items() if k != "assinatura"}
    return {**conteudo, "assinatura": assinar(conteudo, CHAVE)}


def test_bkp_01_estrutura(app, mundo):
    from app.backup import contagens

    dados = exportar(app)
    assert set(dados) == {"formato", "versao_formato", "gerado_em", "versao_app", "tabelas", "contagens", "assinatura"}
    assert dados["formato"] == "gamelog-backup" and dados["versao_formato"] == 1
    assert dados["gerado_em"].endswith("+00:00")
    ordem = list(dados["tabelas"])
    assert ordem.index("user") < ordem.index("review") < ordem.index("comment")
    assert ordem.index("game") < ordem.index("game_genres")
    assert dados["contagens"] == {t: len(linhas) for t, linhas in dados["tabelas"].items()}
    with app.app_context():
        assert contagens() == dados["contagens"]


def test_bkp_02_assinatura_canonica(app, mundo):
    from app.backup import canonico

    dados = exportar(app)
    conteudo = {k: v for k, v in dados.items() if k != "assinatura"}
    esperado = hmac.new(CHAVE.encode(), json.dumps(conteudo, sort_keys=True, separators=(",", ":"),
                                                   ensure_ascii=False).encode(), hashlib.sha256).hexdigest()
    assert canonico(conteudo) == json.dumps(conteudo, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assert dados["assinatura"] == esperado
    assert CHAVE not in json.dumps(dados)


def test_bkp_03_exclusoes_cobrem_o_modelo(app, mundo):
    from app.backup import EXCLUIDAS, incluidas
    from app.tokens import hash_token

    assert EXCLUIDAS == {"login_attempt", "limite_taxa_token", "instancia"}
    with app.app_context():
        assert {t.name for t in incluidas()} == TABELAS_NO_BACKUP, \
            "tabela nova: decida se ela entra no backup ou em EXCLUIDAS"
        senha = User.query.filter_by(username_lower="ana").one().password_hash
    dados = exportar(app)
    assert "login_attempt" not in dados["tabelas"] and "limite_taxa_token" not in dados["tabelas"]
    assert "instancia" not in dados["tabelas"]
    assert [j["status"] for j in dados["tabelas"]["job"]] == ["pendente"]
    assert dados["tabelas"]["token_api"][0]["token_hash"] == hash_token(mundo)
    assert any(u["password_hash"] == senha for u in dados["tabelas"]["user"])
    assert all("session_version" not in u for u in dados["tabelas"]["user"])


def test_bkp_04_serializadores_e_tipos(app, mundo):
    from app import serializadores

    dados = exportar(app)
    with app.app_context():
        g = Game.query.one()
        assert dados["tabelas"]["game"][0]["_api"] == serializadores.jogo(g)
        r = Review.query.one()
        assert dados["tabelas"]["review"][0]["_api"] == serializadores.avaliacao(r)
        assert dados["tabelas"]["sessao_jogo"][0]["_api"] == serializadores.sessao(SessaoJogo.query.one())
        assert dados["tabelas"]["game_list"][0]["_api"]["nome"] == "Minha"
    linha_game = dados["tabelas"]["game"][0]
    assert linha_game["release_date"] == "2020-05-01" and linha_game["data_aproximada"] is False
    assert dados["tabelas"]["review"][0]["spoiler"] is True
    assert datetime.fromisoformat(dados["tabelas"]["review"][0]["created_at"])
    assert "_api" not in dados["tabelas"]["user"][0]


def restaurar(app, dados):
    from app.backup import restaurar as rest

    avisos = []
    with app.app_context():
        try:
            resultado = rest(dados, avisos.append)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
    return resultado, avisos


def sem_metadados(dados):
    return {k: v for k, v in dados.items() if k not in ("gerado_em", "assinatura")}


def test_bkp_06_ida_e_volta(app, mundo):
    antes = exportar(app)
    make_user(app, "carla")
    with app.app_context():
        Review.query.delete()
        db.session.commit()
    (tabelas, linhas), avisos = restaurar(app, antes)
    assert avisos == [] and linhas == sum(antes["contagens"].values()) and tabelas == len(antes["tabelas"])
    depois = exportar(app)
    assert sem_metadados(depois) == sem_metadados(antes)


def test_bkp_06_rollback_em_linha_invalida(app, mundo):
    from app.backup import contagens

    dados = exportar(app)
    ruim = copy.deepcopy(dados)
    ruim["tabelas"]["review"][0]["user_id"] = 9999
    make_user(app, "carla")
    with app.app_context():
        antes = contagens()
    with pytest.raises(Exception):
        restaurar(app, ruim)
    with app.app_context():
        assert contagens() == antes
        assert User.query.filter_by(username_lower="carla").count() == 1


def test_bkp_07_coluna_nova_e_removida(app, mundo):
    from app.backup import ErroBackup

    dados = exportar(app)
    velho = copy.deepcopy(dados)
    for u in velho["tabelas"]["user"]:
        u.pop("bio")
        u["coluna_velha"] = 1
    velho["tabelas"]["tabela_velha"] = [{"id": 1}]
    _, avisos = restaurar(app, velho)
    assert sorted(avisos) == ["coluna user.coluna_velha ignorada", "tabela tabela_velha ignorada"]
    with app.app_context():
        assert {u.bio for u in User.query} == {""}
    sem_email = copy.deepcopy(dados)
    for u in sem_email["tabelas"]["user"]:
        u.pop("email")
    with pytest.raises(ErroBackup, match="user.email"):
        restaurar(app, sem_email)
    with app.app_context():
        assert User.query.count() == 2


def test_bkp_restauracao_invalida_sessoes(app, client, mundo):
    login(client)
    assert client.get("/conta/seguranca").status_code == 200
    with app.app_context():
        antes = {u.id: u.session_version for u in User.query}
    restaurar(app, exportar(app))
    with app.app_context():
        assert all(u.session_version != antes[u.id] for u in User.query)
    assert client.get("/conta/seguranca").status_code == 302


def test_bkp_mapa_registra_continuidade():
    from app.backup import BACKUPS_MANTIDOS
    from app.privacidade.mapa import registro_operacoes

    linha = next(l for l in registro_operacoes() if l["finalidade"] == "continuidade")
    assert BACKUPS_MANTIDOS == 7
    assert linha["retencoes"] == ["7 arquivos mais recentes"] and linha["tabelas"] == ["todas"]


def test_bkp_05_conferir(app, mundo):
    from app.backup import ErroBackup, conferir

    dados = exportar(app)
    conferir(dados, CHAVE)
    adulterado = copy.deepcopy(dados)
    adulterado["tabelas"]["review"][0]["score"] = 9
    casos = [(adulterado, "assinatura inválida"), ({**dados, "formato": "outro"}, "formato desconhecido"),
             ({**dados, "versao_formato": 2}, "versão do formato não suportada"), ([], "arquivo malformado"),
             ({k: v for k, v in dados.items() if k != "assinatura"}, "arquivo malformado")]
    for arquivo, motivo in casos:
        with pytest.raises(ErroBackup, match=motivo):
            conferir(arquivo, CHAVE)
    with pytest.raises(ErroBackup, match="assinatura inválida"):
        conferir(dados, "outra-chave")


def cli(app, *args):
    return app.test_cli_runner().invoke(args=["backup", *args])


def test_bkp_01_cli_exportar(app, mundo, tmp_path):
    import re

    saida = tmp_path / "b.json"
    r = cli(app, "exportar", "--saida", str(saida))
    assert r.exit_code == 0, r.output
    assert re.fullmatch(rf"tabelas=\d+ linhas=\d+ arquivo={re.escape(str(saida))}\n", r.stdout)
    assert json.loads(saida.read_text())["formato"] == "gamelog-backup"
    assert not list(tmp_path.glob("*.tmp"))


def test_bkp_02_sem_chave(app, mundo, tmp_path, monkeypatch):
    arquivo = tmp_path / "b.json"
    cli(app, "exportar", "--saida", str(arquivo))
    monkeypatch.delenv("BACKUP_KEY")
    outro = tmp_path / "c.json"
    r = cli(app, "exportar", "--saida", str(outro))
    assert r.exit_code == 1 and "erro=BACKUP_KEY não configurada" in r.output and "Traceback" not in r.output
    assert not outro.exists()
    r = cli(app, "importar", "--arquivo", str(arquivo), "--confirmar")
    assert r.exit_code == 1 and "erro=BACKUP_KEY não configurada" in r.output


def test_bkp_02_chave_nunca_na_saida(app, mundo, tmp_path):
    arquivo = tmp_path / "b.json"
    r1 = cli(app, "exportar", "--saida", str(arquivo))
    r2 = cli(app, "importar", "--arquivo", str(arquivo), "--confirmar")
    assert CHAVE not in r1.output + r2.output and CHAVE not in arquivo.read_text()


def test_bkp_05_adulterado_um_byte(app, mundo, tmp_path):
    from app.backup import contagens

    arquivo = tmp_path / "b.json"
    cli(app, "exportar", "--saida", str(arquivo))
    texto = arquivo.read_text()
    with app.app_context():
        antes = contagens()
    alvo = texto.index('"score": 8') + len('"score": ')
    (tmp_path / "x.json").write_text(texto[:alvo] + "9" + texto[alvo + 1:])
    r = cli(app, "importar", "--arquivo", str(tmp_path / "x.json"), "--confirmar")
    assert r.exit_code == 1 and r.output.strip() == "erro=assinatura inválida"
    (tmp_path / "t.json").write_text(texto[:100])
    assert cli(app, "importar", "--arquivo", str(tmp_path / "t.json")).output.strip() == "erro=arquivo malformado"
    dados = json.loads(texto)
    dados["versao_formato"] = 2
    (tmp_path / "v.json").write_text(json.dumps(dados))
    r = cli(app, "importar", "--arquivo", str(tmp_path / "v.json"))
    assert r.exit_code == 1 and "versão do formato não suportada" in r.output
    assert cli(app, "importar", "--arquivo", str(tmp_path / "nada.json")).exit_code == 1
    with app.app_context():
        assert contagens() == antes


def test_bkp_05_previa_sem_confirmar(app, mundo, tmp_path):
    import re

    arquivo = tmp_path / "b.json"
    cli(app, "exportar", "--saida", str(arquivo))
    make_user(app, "carla")
    r = cli(app, "importar", "--arquivo", str(arquivo))
    assert r.exit_code == 0 and re.fullmatch(r"tabelas=\d+ linhas=\d+\n", r.stdout)
    with app.app_context():
        assert User.query.filter_by(username_lower="carla").count() == 1
    r = cli(app, "importar", "--arquivo", str(arquivo), "--confirmar")
    assert r.exit_code == 0 and r.stdout.startswith("restaurado tabelas=")
    with app.app_context():
        assert User.query.filter_by(username_lower="carla").count() == 0


@pytest.fixture
def pasta(monkeypatch, tmp_path):
    destino = tmp_path / "backups"
    monkeypatch.setenv("BACKUP_DIR", str(destino))
    return destino


def entrar_admin(app, client, nome="root"):
    make_user(app, nome, role="admin")
    login(client, nome)


def enviar(client, conteudo):
    import io

    return client.post("/admin/backup/previa", data={"arquivo": (io.BytesIO(conteudo), "b.json")},
                       content_type="multipart/form-data")


def id_da_previa(resposta):
    import re

    return re.search(r'name="previa" value="([0-9a-f]{32})"', resposta.get_data(as_text=True)).group(1)


def test_bkp_08_acesso(app, client, mundo, pasta):
    rotas = [("get", "/admin/backup"), ("post", "/admin/backup/baixar"), ("post", "/admin/backup/previa"),
             ("post", "/admin/backup/restaurar")]
    for metodo, url in rotas:
        r = getattr(client, metodo)(url)
        assert r.status_code == 302 and "/entrar" in r.headers["Location"], url
    login(client)
    for metodo, url in rotas:
        assert getattr(client, metodo)(url).status_code == 403, url


def test_bkp_08_baixar_e_auditoria(app, client, mundo, pasta):
    import re

    from app.backup import conferir

    entrar_admin(app, client)
    pagina = client.get("/admin/backup").get_data(as_text=True)
    assert "user" in pagina and "Baixar backup" in pagina
    r = client.post("/admin/backup/baixar")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    assert re.search(r'filename="?gamelog-backup-\d{8}-\d{4}\.json', r.headers["Content-Disposition"])
    conferir(json.loads(r.data), CHAVE)
    with app.app_context():
        assert AuditLog.query.filter_by(action="backup.exportar").count() == 1


def test_bkp_08_previa_e_confirmacao(app, client, mundo, pasta):
    entrar_admin(app, client)
    arquivo = client.post("/admin/backup/baixar").data
    make_user(app, "carla")
    r = enviar(client, arquivo)
    assert r.status_code == 200 and "tabelas=" in r.get_data(as_text=True)
    previa = id_da_previa(r)
    with app.app_context():
        assert User.query.filter_by(username_lower="carla").count() == 1
        assert AuditLog.query.filter_by(action="backup.previa").count() == 1
    sem_confirmar = client.post("/admin/backup/restaurar", data={"previa": previa})
    assert sem_confirmar.status_code == 400
    r = client.post("/admin/backup/restaurar", data={"previa": previa, "confirmar": "1"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/admin/backup")
    with app.app_context():
        assert User.query.filter_by(username_lower="carla").count() == 0
        restauracao = AuditLog.query.filter_by(action="backup.restaurar").one()
        assert "admin=root" in restauracao.detail and restauracao.admin.username == "root"
    assert not list(pasta.glob("previa-*.json"))
    assert client.get("/admin/backup").status_code == 200


def test_bkp_08_arquivo_invalido(app, client, mundo, pasta):
    entrar_admin(app, client)
    r = enviar(client, b'{"formato": "x"}')
    assert r.status_code == 400 and "arquivo malformado" in r.get_data(as_text=True)
    with app.app_context():
        assert "resultado=erro" in AuditLog.query.filter_by(action="backup.previa").one().detail


def test_bkp_09_admin_fora_do_backup_vai_ao_login(app, client, mundo, pasta):
    from app.backup import exportar as exp

    with app.app_context():
        arquivo = json.dumps(exp(CHAVE)).encode()
    entrar_admin(app, client)
    previa = id_da_previa(enviar(client, arquivo))
    r = client.post("/admin/backup/restaurar", data={"previa": previa, "confirmar": "1"})
    assert r.status_code == 302 and "/entrar" in r.headers["Location"]
    assert "/entrar" in client.get("/admin/backup").headers["Location"]
    with app.app_context():
        registro = AuditLog.query.filter_by(action="backup.restaurar").one()
        assert registro.admin_id is None and "admin=root" in registro.detail


def test_bkp_08_previa_maliciosa_e_tamanho(app, client, mundo, pasta):
    entrar_admin(app, client)
    for valor in ("../../etc/passwd", "ab", "../" + "a" * 32, ""):
        r = client.post("/admin/backup/restaurar", data={"previa": valor, "confirmar": "1"})
        assert r.status_code == 400, valor
    grande = client.post("/admin/backup/previa", data={"arquivo": "x"},
                         environ_overrides={"CONTENT_LENGTH": str(21 * 1024 * 1024)})
    assert grande.status_code == 413
    inexistente = client.post("/admin/backup/restaurar", data={"previa": "a" * 32, "confirmar": "1"})
    assert inexistente.status_code == 400


def arquivos_antigos(pasta, n=8):
    pasta.mkdir(parents=True, exist_ok=True)
    nomes = [f"gamelog-backup-202611{d:02d}-030000.json" for d in range(1, n + 1)]
    for nome in nomes:
        (pasta / nome).write_text("{}")
    (pasta / f"previa-{'a' * 32}.json").write_text("{}")
    (pasta / "outro.txt").write_text("x")
    return nomes


def test_bkp_10_retencao_7(app, mundo, pasta):
    from app.jobs import enfileirar, executar

    antigos = arquivos_antigos(pasta)
    with app.app_context():
        job = enfileirar("backup")
        db.session.commit()
        executar(job.id)
        assert db.session.get(Job, job.id).status == "ok"
    backups = sorted(p.name for p in pasta.glob("gamelog-backup-*.json"))
    assert len(backups) == 7 and antigos[0] not in backups and antigos[1] not in backups
    novo = backups[-1]
    assert novo not in antigos
    from app.backup import conferir

    conferir(json.loads((pasta / novo).read_text()), CHAVE)
    assert (pasta / f"previa-{'a' * 32}.json").exists() and (pasta / "outro.txt").exists()


def test_bkp_10_falha_nao_apaga(app, mundo, pasta, monkeypatch):
    from app.jobs import enfileirar, executar

    arquivos_antigos(pasta)
    monkeypatch.delenv("BACKUP_KEY")
    with app.app_context():
        job = enfileirar("backup")
        db.session.commit()
        executar(job.id)
        feito = db.session.get(Job, job.id)
        assert feito.status == "morto" and "BACKUP_KEY" in feito.ultimo_erro
    assert len(list(pasta.glob("gamelog-backup-*.json"))) == 8


def test_bkp_10_agenda_padrao(app):
    from app.jobs import AGENDAS_PADRAO

    assert AGENDAS_PADRAO["backup"][:2] == ("0 3 * * *", "backup")


class Download:
    def __init__(self, corpo):
        self.corpo, self.headers = corpo, {"Content-Type": "application/json", "Content-Length": str(len(corpo))}

    def read(self, n=-1):
        return self.corpo

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def app_vazio(tmp_path, chave):
    from app import create_app

    novo = create_app({"TESTING": True, "CSRF": False, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path}/vazio.db"})
    yield novo
    with novo.app_context():
        db.session.remove()
        db.engine.dispose()


def arquivo_de(app, tmp_path):
    caminho = tmp_path / "inicial.json"
    assert cli(app, "exportar", "--saida", str(caminho)).exit_code == 0
    return caminho


def test_bkp_11_banco_vazio_restaura(app, mundo, app_vazio, tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_RESTAURAR_DE", str(arquivo_de(app, tmp_path)))
    r = cli(app_vazio, "restaurar-inicial")
    assert r.exit_code == 0 and r.stdout.startswith("restaurado=1 tabelas=")
    with app_vazio.app_context():
        assert {u.username for u in User.query} == {"ana", "bia"}


def test_bkp_11_banco_populado_ou_sem_variavel(app, mundo, app_vazio, tmp_path, monkeypatch):
    from app.backup import contagens

    caminho = arquivo_de(app, tmp_path)
    assert cli(app_vazio, "restaurar-inicial").stdout == "restaurado=0\n"
    monkeypatch.setenv("BACKUP_RESTAURAR_DE", str(caminho))
    make_user(app, "carla")
    with app.app_context():
        antes = contagens()
    r = cli(app, "restaurar-inicial")
    assert r.exit_code == 0 and r.stdout == "restaurado=0\n"
    with app.app_context():
        assert contagens() == antes


def test_bkp_11_assinatura_invalida_sai_com_0(app, mundo, app_vazio, tmp_path, monkeypatch):
    caminho = arquivo_de(app, tmp_path)
    dados = json.loads(caminho.read_text())
    dados["tabelas"]["user"][0]["bio"] = "adulterada"
    caminho.write_text(json.dumps(dados))
    monkeypatch.setenv("BACKUP_RESTAURAR_DE", str(caminho))
    r = cli(app_vazio, "restaurar-inicial")
    assert r.exit_code == 0 and r.stdout == "erro=assinatura inválida\n"
    monkeypatch.setenv("BACKUP_RESTAURAR_DE", str(tmp_path / "nada.json"))
    assert cli(app_vazio, "restaurar-inicial").stdout == "erro=arquivo não encontrado\n"
    with app_vazio.app_context():
        assert User.query.count() == 0


def test_bkp_11_https_pela_fonte_comum(app, mundo, app_vazio, tmp_path, monkeypatch):
    from app.fontes import base

    corpo = arquivo_de(app, tmp_path).read_bytes()
    pedidos = []
    monkeypatch.setattr(base, "abrir_sem_seguir", lambda req, timeout: pedidos.append(req) or Download(corpo))
    monkeypatch.setenv("BACKUP_RESTAURAR_DE", "http://x.test/b.json?token=segredo")
    r = cli(app_vazio, "restaurar-inicial")
    assert r.stdout == "erro=URL precisa ser https\n" and pedidos == [] and "segredo" not in r.output
    monkeypatch.setenv("BACKUP_RESTAURAR_DE", "https://x.test/b.json?token=segredo")
    r = cli(app_vazio, "restaurar-inicial")
    assert r.exit_code == 0 and r.stdout.startswith("restaurado=1") and "segredo" not in r.output
    assert [p.full_url for p in pedidos] == ["https://x.test/b.json?token=segredo"]
    assert pedidos[0].get_header("User-agent").startswith("GameLog/")


def test_bkp_11_falha_de_rede_sem_url_na_saida(app_vazio, monkeypatch):
    from urllib.error import URLError

    from app.fontes import base

    def falha(req, timeout):
        raise URLError("https://x.test/b.json?token=segredo recusou")

    monkeypatch.setattr(base, "abrir_sem_seguir", falha)
    monkeypatch.setenv("BACKUP_RESTAURAR_DE", "https://x.test/b.json?token=segredo")
    r = cli(app_vazio, "restaurar-inicial")
    assert r.exit_code == 0 and r.stdout == "erro=download falhou: URLError\n"


def test_bkp_02_render_yaml():
    from pathlib import Path

    texto = (Path(__file__).resolve().parent.parent / "render.yaml").read_text()
    for chave_env in ("BACKUP_KEY", "BACKUP_RESTAURAR_DE"):
        assert f"- key: {chave_env}\n        sync: false" in texto
    inicio = next(linha for linha in texto.splitlines() if "startCommand" in linha)
    assert inicio.index("backup restaurar-inicial") < inicio.index("app seed")



def test_bkp_10_previas_esquecidas_saem_no_rotacionar(tmp_path):
    import os
    import time

    from app.backup import rotacionar

    velha, nova = tmp_path / f"previa-{'a' * 32}.json", tmp_path / f"previa-{'b' * 32}.json"
    velha.write_text("{}")
    nova.write_text("{}")
    dois_dias = time.time() - 2 * 86400
    os.utime(velha, (dois_dias, dois_dias))
    rotacionar(str(tmp_path))
    assert not velha.exists() and nova.exists()


@pytest.mark.parametrize("estragar, esperado", [
    (lambda p: p.write_text(json.dumps({**json.loads(p.read_text()), "assinatura": "ç" * 64})),
     "erro=assinatura inválida\n"),
    (lambda p: p.write_text(p.read_text().replace('"bio": ""', '"bio": "\\udc80"', 1)),
     "erro=arquivo malformado\n"),
    (lambda p: p.write_text('{"formato":'), "erro=arquivo malformado\n"),
    (lambda p: p.write_bytes(b"\xff\xfe"), "erro=arquivo malformado\n"),
    (lambda p: (p.unlink(), p.mkdir()), "erro=arquivo ilegível\n"),
    (lambda p: p.write_text(p.read_text().replace('"versao_formato": 1', '"versao_formato": "\\udc80"', 1)),
     "erro=versão do formato não suportada\n"),
    (lambda p: p.write_text("[" * 100000), "erro=arquivo malformado\n"),
])
def test_bkp_11_falhas_nao_derrubam_o_deploy(app, mundo, app_vazio, tmp_path, monkeypatch, estragar, esperado):
    caminho = arquivo_de(app, tmp_path)
    estragar(caminho)
    monkeypatch.setenv("BACKUP_RESTAURAR_DE", str(caminho))
    r = cli(app_vazio, "restaurar-inicial")
    assert r.exit_code == 0 and r.stdout == esperado and "Traceback" not in r.output
    with app_vazio.app_context():
        assert User.query.count() == 0


def test_bkp_11_erro_inesperado_sai_com_0(app_vazio, tmp_path, monkeypatch):
    import app.backup as backup

    def explode(origem):
        raise RuntimeError("x")

    monkeypatch.setattr(backup, "obter_inicial", explode)
    monkeypatch.setenv("BACKUP_RESTAURAR_DE", str(tmp_path / "b.json"))
    r = cli(app_vazio, "restaurar-inicial")
    assert r.exit_code == 0 and r.stdout.startswith("erro=") and "RuntimeError" in r.stdout


def test_bkp_05_assinatura_nao_ascii_na_previa(app, client, mundo, pasta):
    entrar_admin(app, client)
    dados = json.loads(client.post("/admin/backup/baixar").data)
    r = enviar(client, json.dumps({**dados, "assinatura": "ç" * 64}).encode())
    assert r.status_code == 400 and "assinatura inválida" in r.get_data(as_text=True)


def test_bkp_03_instancia_do_destino_fica(app, mundo, app_vazio, tmp_path, monkeypatch):
    from app.models import Instancia

    caminho = arquivo_de(app, tmp_path)
    with app.app_context():
        origem = db.session.get(Instancia, 1).token
    with app_vazio.app_context():
        destino = db.session.get(Instancia, 1).token
    velho = json.loads(caminho.read_text())
    velho["tabelas"]["instancia"] = [{"id": 1, "token": origem}]
    velho["contagens"]["instancia"] = 1
    caminho.write_text(json.dumps(assinado(velho)))
    monkeypatch.setenv("BACKUP_RESTAURAR_DE", str(caminho))
    r = cli(app_vazio, "restaurar-inicial")
    assert r.exit_code == 0 and r.stdout.startswith("restaurado=1") and r.stderr == ""
    with app_vazio.app_context():
        assert db.session.get(Instancia, 1).token == destino != origem


def test_bkp_08_upload_sem_content_length_acima_do_limite(app, client, mundo, pasta):
    import io

    entrar_admin(app, client)
    corpo = (b'--x\r\nContent-Disposition: form-data; name="arquivo"; filename="b.json"\r\n\r\n'
             + b"a" * (20 * 1024 * 1024 + 1) + b"\r\n--x--\r\n")
    r = client.post("/admin/backup/previa", input_stream=io.BytesIO(corpo),
                    content_type="multipart/form-data; boundary=x",
                    environ_overrides={"wsgi.input_terminated": True, "HTTP_TRANSFER_ENCODING": "chunked"})
    assert r.status_code == 413
    assert not list(pasta.glob("previa-*.json"))


def test_bkp_09_homonimo_no_backup_vai_ao_login(app, client, mundo, pasta):
    from app.backup import exportar as exp

    with app.app_context():
        dados = exp(CHAVE)
    for u in dados["tabelas"]["user"]:
        if u["username_lower"] == "ana":
            u["username"], u["username_lower"] = "root", "root"
    entrar_admin(app, client)
    previa = id_da_previa(enviar(client, json.dumps(assinado(dados)).encode()))
    r = client.post("/admin/backup/restaurar", data={"previa": previa, "confirmar": "1"})
    assert r.status_code == 302 and "/entrar" in r.headers["Location"]
    with app.app_context():
        assert AuditLog.query.filter_by(action="backup.restaurar").one().admin_id is None


def test_bkp_11_redirecionamento_nao_e_seguido(app_vazio, monkeypatch):
    from urllib.error import HTTPError

    from app.fontes import base

    def redireciona(req, timeout):
        raise HTTPError(req.full_url, 302, "Found", {"Location": "https://cdn.test/b.json?sig=segredo"}, None)

    monkeypatch.setattr(base, "abrir_sem_seguir", redireciona)
    monkeypatch.setenv("BACKUP_RESTAURAR_DE", "https://x.test/b.json")
    r = cli(app_vazio, "restaurar-inicial")
    assert r.exit_code == 0 and r.stdout == "erro=download falhou: Redirecionado\n" and "segredo" not in r.output


@pytest.mark.parametrize("conteudo, motivo", [
    (lambda d: json.dumps({**d, "versao_formato": "\udc80"}).encode(), "versão do formato não suportada"),
    (lambda d: b"[" * 100000, "arquivo malformado"),
])
def test_bkp_05_previa_com_arquivo_hostil_responde_400(app, client, mundo, pasta, conteudo, motivo):
    entrar_admin(app, client)
    dados = json.loads(client.post("/admin/backup/baixar").data)
    r = enviar(client, conteudo(dados))
    assert r.status_code == 400 and motivo in r.get_data(as_text=True)
