"""Retenção e minimização (RTN-01..12), sem rede e com o relógio fixo."""
import dataclasses
import os
import re
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest
from sqlalchemy import Column, DateTime, Integer, MetaData, Table

from app import tempo
from app.fontes import base
from app.jobs import AGENDAS_PADRAO, TAREFAS, enfileirar, executar
from app.models import (AuditLog, EmailSaida, Follow, Game, GameList, Job, ListItem, LoginAttempt, Notificacao, Review,
                        Status, User, VerificacaoIdade, db)
from app.privacidade import anonimizar, capas, mapa, retencao
from conftest import login, make_game, make_user

AGORA = datetime(2026, 11, 16, 12, 0, tzinfo=tempo.FUSO)
CAPA = "https://cdn.cloudflare.steamstatic.com/steam/apps/7/library_600x900.jpg"
JPEG = b"\xff\xd8\xff" + b"capa" * 10


class Relogio:
    def __init__(self):
        self.momento = AGORA

    def andar(self, **delta):
        self.momento += timedelta(**delta)


@pytest.fixture
def relogio(monkeypatch):
    r = Relogio()
    monkeypatch.setattr(tempo, "agora", lambda: r.momento)
    return r


def atras(**delta):
    return tempo.agora_utc() - timedelta(**delta)


def expurgar(app, simular=False):
    with app.app_context():
        return retencao.expurgar(simular)


def contar(app, modelo, *filtros):
    with app.app_context():
        return modelo.query.filter(*filtros).count()


def tentativas(app, *dias):
    with app.app_context():
        db.session.add_all(LoginAttempt(email=f"x{i}@ex.com", at=atras(days=d)) for i, d in enumerate(dias))
        db.session.commit()


def sem_prazo(monkeypatch, tabela, dias=None):
    """Troca o prazo de todas as colunas da tabela no mapa (RTN-02)."""
    for chave, campo in list(mapa.CAMPOS.items()):
        if chave.startswith(f"{tabela}."):
            monkeypatch.setitem(mapa.CAMPOS, chave, dataclasses.replace(campo, retencao_dias=dias))


# ---------- RTN-01..04: expurgo ----------
def test_rtn01_expurgo_apaga_so_o_vencido_de_cada_modelo_e_audita(app, relogio):
    admin = make_user(app, "chefe", role="admin")
    uid = make_user(app, "ana")
    tentativas(app, 31, 29)
    with app.app_context():
        db.session.add_all([
            AuditLog(admin_id=admin, action="velho", created_at=atras(days=731)),
            AuditLog(admin_id=admin, action="novo", created_at=atras(days=700)),
            *(Job(tipo="x", status=status, executar_em=atras(days=40), criado_em=atras(days=40),
                  concluido_em=None if status == "pendente" else atras(days=31))
              for status in ("ok", "morto", "falhou", "pendente")),
            Notificacao(user_id=uid, tipo="t", lida_em=atras(days=181), created_at=atras(days=200)),
            Notificacao(user_id=uid, tipo="t", created_at=atras(days=400)),  # não lida: fica
            EmailSaida(user_id=uid, para="a@ex.com", modelo="teste", assunto="a", corpo_texto="t", corpo_html="h",
                       status="enviado", enviado_em=atras(days=91), criado_em=atras(days=91)),
            EmailSaida(user_id=uid, para="a@ex.com", modelo="teste", assunto="a", corpo_texto="t", corpo_html="h",
                       status="falhou", criado_em=atras(days=300)),
        ])
        db.session.commit()
    conta = expurgar(app)
    assert {k: conta[k] for k in retencao.REGRAS} == {"login_attempt": 1, "notificacao": 1, "email_saida": 1,
                                                      "job": 2, "audit_log": 1}
    assert contar(app, LoginAttempt) == 1 and contar(app, Notificacao) == 1 and contar(app, EmailSaida) == 1
    with app.app_context():
        assert sorted(j.status for j in Job.query) == ["falhou", "pendente"]
        log = AuditLog.query.filter_by(action="privacidade.expurgo").one()
        assert log.admin_id is None and log.created_at == tempo.agora_utc()
        assert log.detail.startswith("login_attempt=1 notificacao=1 email_saida=1 job=2 audit_log=1")
        assert AuditLog.query.filter_by(action="velho").count() == 0


def test_rtn01_tarefa_diaria_pela_fila(app, relogio):
    tentativas(app, 40)
    assert "expurgo" in TAREFAS
    cron, tipo, _, ativo = AGENDAS_PADRAO["expurgo"]
    assert tipo == "expurgo" and ativo and cron.split()[2:] == ["*", "*", "*"]
    with app.app_context():
        job = enfileirar("expurgo")
        db.session.commit()
        assert executar(job.id) == "ok"
    assert contar(app, LoginAttempt) == 0


def test_rtn01_modelo_sem_prazo_no_mapa_nunca_e_tocado(app, relogio, monkeypatch):
    admin = make_user(app, "chefe", role="admin")
    with app.app_context():
        db.session.add(AuditLog(admin_id=admin, action="antigo", created_at=atras(days=3000)))
        db.session.commit()
    sem_prazo(monkeypatch, "audit_log")
    conta = expurgar(app)
    assert "audit_log" not in conta
    assert contar(app, AuditLog, AuditLog.action == "antigo") == 1


def test_rtn01_preservar_ate_no_futuro_nunca_e_apagado(app, relogio, monkeypatch):
    """DEN-09: o filtro vale para qualquer tabela que tenha a coluna `preservar_ate`."""
    tabela = Table("rtn_preservada", MetaData(), Column("id", Integer, primary_key=True),
                   Column("criado", DateTime), Column("preservar_ate", DateTime))
    with app.app_context():
        tabela.create(db.engine)
        db.session.execute(tabela.insert(), [
            {"id": 1, "criado": atras(days=60), "preservar_ate": atras(days=-10)},  # preservada até daqui a 10 dias
            {"id": 2, "criado": atras(days=60), "preservar_ate": atras(days=1)},  # preservação vencida
            {"id": 3, "criado": atras(days=60), "preservar_ate": None},
        ])
        db.session.commit()
    monkeypatch.setitem(retencao.REGRAS, "rtn_preservada", retencao.Regra(tabela, "criado"))
    monkeypatch.setitem(mapa.CAMPOS, "rtn_preservada.criado", mapa.Campo("tecnico", "seguranca", "contrato", 30,
                                                                       False, "manter"))
    assert expurgar(app)["rtn_preservada"] == 2
    with app.app_context():
        assert [r.id for r in db.session.execute(tabela.select())] == [1]


def test_rtn02_prazos_iniciais_vem_do_mapa():
    assert {t: mapa.retencao(t) for t in retencao.REGRAS} == {
        "login_attempt": 30, "notificacao": 180, "email_saida": 90, "job": 30, "audit_log": 730}
    assert mapa.retencao("user") == 730  # conta inativa (RTN-05)


def test_rtn02_prazo_alterado_no_mapa_muda_o_expurgo(app, relogio, monkeypatch):
    tentativas(app, 15, 5)
    assert expurgar(app, simular=True)["login_attempt"] == 0
    sem_prazo(monkeypatch, "login_attempt", 10)
    assert expurgar(app)["login_attempt"] == 1
    assert contar(app, LoginAttempt) == 1


def test_rtn03_idempotente_no_mesmo_dia(app, relogio):
    tentativas(app, 31, 32, 33)
    assert expurgar(app)["login_attempt"] == 3
    relogio.andar(hours=2)
    segunda = expurgar(app)
    assert set(segunda.values()) == {0}


def test_rtn03_lote_interrompido_mantem_o_gravado_e_repete(app, relogio, monkeypatch):
    tentativas(app, *[40] * 5)
    monkeypatch.setattr(retencao, "LOTE", 2)
    original, chamadas = retencao.condicao, []

    def falha_no_segundo_lote(*a):
        chamadas.append(1)
        if len(chamadas) == 2:
            raise RuntimeError("disco cheio")
        return original(*a)

    monkeypatch.setattr(retencao, "condicao", falha_no_segundo_lote)
    with app.app_context():
        job = enfileirar("expurgo").id
        db.session.commit()
        assert executar(job) == "pendente"
    assert contar(app, LoginAttempt) == 3  # o primeiro lote ficou gravado
    monkeypatch.setattr(retencao, "condicao", original)
    with app.app_context():
        assert executar(job) == "ok"
    assert contar(app, LoginAttempt) == 0


def test_rtn03_um_commit_por_lote_de_no_maximo_500(app, relogio, monkeypatch):
    assert retencao.LOTE == 500
    tentativas(app, *[40] * 3)
    restantes = []
    monkeypatch.setattr(retencao, "LOTE", 2)
    with app.app_context():
        original = db.session.commit
        monkeypatch.setattr(db.session, "commit", lambda: restantes.append(LoginAttempt.query.count()) or original())
        assert retencao.apagar_vencidas(retencao.REGRAS["login_attempt"], 30, tempo.agora_utc()) == 3
    assert restantes == [1, 0]


def test_rtn04_cli_simular_conta_sem_apagar(app, relogio):
    tentativas(app, 31, 1)
    runner = app.test_cli_runner()
    r = runner.invoke(args=["privacidade", "expurgar", "--simular"])
    assert r.exit_code == 0 and "login_attempt=1" in r.output
    assert contar(app, LoginAttempt) == 2
    assert contar(app, AuditLog) == 0
    r = runner.invoke(args=["privacidade", "expurgar"])
    assert r.exit_code == 0 and "login_attempt=1" in r.output
    assert contar(app, LoginAttempt) == 1 and contar(app, Job) == 0  # rodou na hora, fora da fila


# ---------- RTN-05, RTN-06: contas ----------
def acesso(app, uid, **valores):
    with app.app_context():
        db.session.query(User).filter_by(id=uid).update(valores)
        db.session.commit()


def usuario(app, uid):
    with app.app_context():
        u = db.session.get(User, uid)
        db.session.expunge(u)
        return u


def test_rtn05_login_e_navegacao_marcam_o_acesso_uma_vez_por_dia(app, client, relogio):
    uid = make_user(app, "ana")
    login(client)
    assert usuario(app, uid).ultimo_acesso == tempo.agora_utc()
    entrada = tempo.agora_utc()
    relogio.andar(hours=3)
    client.get("/")
    assert usuario(app, uid).ultimo_acesso == entrada  # mesmo dia: nenhuma escrita
    relogio.andar(days=1)
    client.get("/")
    assert usuario(app, uid).ultimo_acesso == tempo.agora_utc()


def test_rtn05_admin_nunca_e_inativo(app, relogio):
    admin = make_user(app, "chefe", role="admin")
    acesso(app, admin, ultimo_acesso=atras(days=2000), aviso_inatividade_em=atras(days=60))
    conta = expurgar(app)
    assert conta["contas_avisadas"] == conta["contas_anonimizadas"] == 0
    assert usuario(app, admin).username == "chefe"
    assert contar(app, EmailSaida) == 0


def test_rtn06_aviso_30_dias_antes_e_anonimizacao_depois(app, relogio):
    uid = make_user(app, "ana")
    outro = make_user(app, "bia")
    slug = make_game(app)
    acesso(app, uid, ultimo_acesso=atras(days=701), bio="minha bio", steam_id="76561198000000001")
    acesso(app, outro, ultimo_acesso=atras(days=699))
    with app.app_context():
        gid = Game.query.filter_by(slug=slug).one().id
        db.session.add_all([Review(user_id=uid, game_id=gid, score=8, text="Ótimo"),
                            Status(user_id=uid, game_id=gid, value="jogado"),
                            Follow(follower_id=outro, followed_id=uid)])
        db.session.commit()
    conta = expurgar(app)
    assert conta["contas_avisadas"] == 1 and conta["contas_anonimizadas"] == 0
    assert usuario(app, uid).aviso_inatividade_em == tempo.agora_utc()
    assert usuario(app, outro).aviso_inatividade_em is None
    with app.app_context():
        email = EmailSaida.query.filter_by(modelo="conta_inativa").one()
        assert email.user_id == uid and email.tipo is None and "16/12/2026" in email.assunto
    relogio.andar(days=29)
    assert expurgar(app)["contas_anonimizadas"] == 0
    relogio.andar(days=1)
    assert expurgar(app)["contas_anonimizadas"] == 1
    u = usuario(app, uid)
    assert (u.username, u.display_name, u.email) == (f"removido-{uid}", "Usuário removido",
                                                      f"removido-{uid}@removido.invalid")
    assert u.bio == "" and u.steam_id is None and u.ultimo_acesso is None and u.aviso_inatividade_em is None
    with app.app_context():
        r = Review.query.one()
        assert (r.user_id, r.text, r.user.display_name) == (uid, "Ótimo", "Usuário removido")
    assert contar(app, Status) == 0 and contar(app, Follow) == 0
    assert expurgar(app)["contas_avisadas"] == 0  # conta anonimizada não volta a ser avisada


def test_rtn06_login_no_intervalo_cancela_o_aviso(app, client, relogio):
    uid = make_user(app, "ana")
    acesso(app, uid, ultimo_acesso=atras(days=710))
    expurgar(app)
    assert usuario(app, uid).aviso_inatividade_em is not None
    relogio.andar(days=10)
    assert login(client).status_code == 302
    assert usuario(app, uid).aviso_inatividade_em is None
    relogio.andar(days=25)
    conta = expurgar(app)
    assert conta["contas_anonimizadas"] == 0 and conta["contas_avisadas"] == 0
    assert usuario(app, uid).username == "ana"


def test_rtn06_navegacao_do_dia_tambem_cancela_o_aviso(app, client, relogio):
    uid = make_user(app, "ana")
    login(client)
    acesso(app, uid, ultimo_acesso=atras(days=710), aviso_inatividade_em=atras(days=5))
    relogio.andar(days=1)
    client.get("/")
    assert usuario(app, uid).aviso_inatividade_em is None


def test_rtn06_conta_sem_aviso_nunca_e_anonimizada(app, relogio):
    uid = make_user(app, "ana")
    acesso(app, uid, ultimo_acesso=atras(days=3000))
    conta = expurgar(app)
    assert conta["contas_avisadas"] == 1 and conta["contas_anonimizadas"] == 0
    assert usuario(app, uid).username == "ana"


def test_rtn06_conta_pendente_apagada_depois_de_30_dias(app, relogio):
    def pendente(nome, dias):
        uid = make_user(app, nome)
        acesso(app, uid, nascimento=date(2013, 1, 1), aguardando_responsavel=True, created_at=atras(days=dias))
        with app.app_context():
            db.session.add(VerificacaoIdade(user_id=uid, metodo="autodeclaracao", faixa_resultante="vinculado",
                                            confiavel=False))
            db.session.commit()
        return uid

    velha, nova = pendente("velha", 31), pendente("nova", 29)
    contestada = pendente("contestada", 400)
    acesso(app, contestada, idade_contestada_em=atras(days=5))
    tentativas(app, 1)
    acesso(app, velha, email="x0@ex.com")
    assert expurgar(app, simular=True)["contas_pendentes_apagadas"] == 1
    assert expurgar(app)["contas_pendentes_apagadas"] == 1
    with app.app_context():
        assert db.session.get(User, velha) is None
        assert {v.user_id for v in VerificacaoIdade.query} == {nova, contestada}
        assert LoginAttempt.query.count() == 0


def test_rtn06_anonimizar_conta_segue_o_mapa(app):
    uid = make_user(app, "ana")
    outro = make_user(app, "bia")
    slug = make_game(app)
    with app.app_context():
        from app import emails
        gid = Game.query.filter_by(slug=slug).one().id
        lista = GameList(user_id=uid, name="Favoritos")
        db.session.add_all([lista, Review(user_id=uid, game_id=gid, score=7),
                            Follow(follower_id=uid, followed_id=outro), LoginAttempt(email="ana@ex.com")])
        db.session.flush()
        db.session.add(ListItem(list_id=lista.id, game_id=gid, position=1))
        db.session.commit()
        user = db.session.get(User, uid)
        versao = user.session_version
        anonimizar.anonimizar_conta(user)
        db.session.commit()
        user = db.session.get(User, uid)
        assert anonimizar.anonimizada(user) and emails.conta_excluida(user)
        assert user.session_version == versao + 1 and user.password_hash == anonimizar.SENHA_INUTILIZAVEL
        assert user.nascimento is None and user.role == "jogador"
        assert Review.query.count() == 1 and GameList.query.count() == ListItem.query.count() == 0
        assert Follow.query.count() == 0 and LoginAttempt.query.count() == 0
        assert db.session.get(User, outro).username == "bia"


# ---------- RTN-07..10: proxy de capas ----------
class Imagem:
    def __init__(self, corpo=JPEG, tipo="image/jpeg", tamanho=None):
        self.corpo = corpo
        self.headers = {"Content-Type": tipo, **({"Content-Length": str(tamanho)} if tamanho else {})}

    def read(self, n=-1):
        return self.corpo[:n] if n >= 0 else self.corpo

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def redirecionar(destino):
    return HTTPError("https://x", 302, "Found", {"Location": destino}, None)


@pytest.fixture
def cdn(monkeypatch, tmp_path):
    """Rede falsa do proxy: `respostas` sai em ordem (a última se repete); `pedidos` guarda as Requests."""
    rede = SimpleNamespace(respostas=[Imagem()], pedidos=[])

    def abrir(req, timeout):
        rede.pedidos.append(req)
        r = rede.respostas[0] if len(rede.respostas) == 1 else rede.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(base, "abrir_sem_seguir", abrir)
    return rede


@pytest.fixture
def capa_app(app, tmp_path):
    app.config["CAPAS_CACHE_DIR"] = str(tmp_path / "capas")
    return app


def jogo(app, cover_url=CAPA, title="Jogo Teste", removido=False):
    slug = make_game(app, title)
    with app.app_context():
        g = Game.query.filter_by(slug=slug).one()
        g.cover_url = cover_url
        g.removed_at = tempo.agora_utc() if removido else None
        db.session.commit()
    return slug


def no_cache(app):
    pasta = app.config["CAPAS_CACHE_DIR"]
    return sorted(os.listdir(pasta)) if os.path.isdir(pasta) else []


def e_svg(r):
    return r.status_code == 200 and r.mimetype == "image/svg+xml"


def test_rtn07_baixa_uma_vez_e_serve_do_cache(capa_app, client, cdn):
    slug = jogo(capa_app)
    for _ in range(2):
        r = client.get(f"/capas/{slug}")
        assert r.status_code == 200 and r.data == JPEG and r.mimetype == "image/jpeg"
        assert r.headers["Cache-Control"] == "public, max-age=86400"
    assert len(cdn.pedidos) == 1 and cdn.pedidos[0].full_url == CAPA
    assert len(no_cache(capa_app)) == 1 and no_cache(capa_app)[0].endswith(".jpg")


def test_rtn07_webp_e_png_mantem_o_tipo_original(capa_app, client, cdn):
    for tipo in ("image/png", "image/webp"):
        slug = jogo(capa_app, f"{CAPA}?{tipo[6:]}", title=f"Jogo {tipo[6:]}")
        cdn.respostas = [Imagem(b"img", tipo)]
        assert client.get(f"/capas/{slug}").mimetype == tipo


def test_rtn07_sem_capa_externa_responde_o_svg(capa_app, client, cdn):
    slug = jogo(capa_app, cover_url=None)
    r = client.get(f"/capas/{slug}")
    assert e_svg(r) and r.data == client.get(f"/capa/{slug}.svg").data
    assert cdn.pedidos == []


def test_rtn07_jogo_removido_ou_inexistente_404(capa_app, client, cdn):
    slug = jogo(capa_app, removido=True)
    assert client.get(f"/capas/{slug}").status_code == 404
    assert client.get("/capas/nao-existe").status_code == 404
    assert cdn.pedidos == []


@pytest.mark.parametrize("url", [
    "https://evil.example.com/capa.jpg",
    "http://cdn.cloudflare.steamstatic.com/steam/apps/7/library_600x900.jpg",
    "https://cdn.cloudflare.steamstatic.com:8443/capa.jpg",
    "https://127.0.0.1/capa.jpg",
    "file:///etc/passwd",
])
def test_rtn08_host_ou_esquema_fora_da_regra_nem_e_buscado(capa_app, client, cdn, url):
    slug = jogo(capa_app, cover_url=url)
    assert e_svg(client.get(f"/capas/{slug}"))
    assert cdn.pedidos == [] and no_cache(capa_app) == []


def test_rtn08_redirecionamento_para_outro_host_e_recusado(capa_app, client, cdn):
    slug = jogo(capa_app)
    cdn.respostas = [redirecionar("http://169.254.169.254/latest/meta-data"), Imagem()]
    assert e_svg(client.get(f"/capas/{slug}"))
    assert [p.full_url for p in cdn.pedidos] == [CAPA] and no_cache(capa_app) == []
    cdn.respostas = [redirecionar("https://media.rawg.io/outra.jpg"), Imagem()]  # outro host, mesmo da lista
    assert e_svg(client.get(f"/capas/{slug}"))
    assert no_cache(capa_app) == []


def test_rtn08_redirecionamento_no_mesmo_host_e_seguido(capa_app, client, cdn):
    slug = jogo(capa_app)
    cdn.respostas = [redirecionar("/steam/apps/7/outra.jpg"), Imagem()]
    assert client.get(f"/capas/{slug}").data == JPEG
    assert cdn.pedidos[1].full_url == "https://cdn.cloudflare.steamstatic.com/steam/apps/7/outra.jpg"


@pytest.mark.parametrize("resposta", [
    Imagem(b"<html>", "text/html"),
    Imagem(b"<svg/>", "image/svg+xml"),
    Imagem(b"x", "image/jpeg", tamanho=capas.LIMITE + 1),  # o Content-Length já recusa
    Imagem(b"x" * (capas.LIMITE + 1), "image/jpeg"),  # sem Content-Length: corta na leitura
])
def test_rtn08_tipo_ou_tamanho_invalido_nao_e_gravado_nem_servido(capa_app, client, cdn, resposta):
    slug = jogo(capa_app)
    cdn.respostas = [resposta]
    assert e_svg(client.get(f"/capas/{slug}"))
    assert no_cache(capa_app) == []


def test_rtn08_hosts_permitidos_num_lugar_so(app):
    from app import CAPAS_HOSTS_PERMITIDOS

    assert app.config["CAPAS_HOSTS_PERMITIDOS"] == CAPAS_HOSTS_PERMITIDOS
    for host in ("cdn.cloudflare.steamstatic.com", "images.gog-statics.com", "media.rawg.io"):
        assert host in CAPAS_HOSTS_PERMITIDOS


def test_rtn09_paginas_sem_imagem_de_host_externo(capa_app, client, cdn, relogio):
    uid = make_user(capa_app, "ana")
    slug = jogo(capa_app, title="Alfa Externo")
    with capa_app.app_context():
        gid = Game.query.filter_by(slug=slug).one().id
        lista = GameList(user_id=uid, name="Favoritos")
        db.session.add_all([lista, Review(user_id=uid, game_id=gid, score=9, text="Bom"),
                            Status(user_id=uid, game_id=gid, value="jogado")])
        db.session.flush()
        db.session.add(ListItem(list_id=lista.id, game_id=gid, position=1))
        db.session.commit()
        lid = lista.id
    login(client)
    paginas = ["/", "/buscar?q=Alfa", f"/jogos/{slug}", "/u/ana", "/listas", f"/listas/{lid}", "/estatisticas",
               "/meus-jogos"]
    for pagina in paginas:
        r = client.get(pagina)
        html = r.get_data(as_text=True)
        assert r.status_code == 200, pagina
        assert not re.search(r"""src=["']?(https?:)?//""", html), pagina
    assert f'src="/capas/{slug}"' in client.get(f"/jogos/{slug}").get_data(as_text=True)


def test_rtn09_api_aponta_para_o_proxy(capa_app, client):
    slug = jogo(capa_app)
    sem = jogo(capa_app, cover_url=None, title="Sem Capa")
    itens = {j["slug"]: j["capa"] for j in client.get("/api/jogos").get_json()["items"]}
    assert itens[slug] == f"http://localhost/capas/{slug}"
    assert itens[sem] == f"http://localhost/capa/{sem}.svg"


def test_rtn10_falha_de_rede_nao_grava_e_tenta_de_novo(capa_app, client, cdn):
    slug = jogo(capa_app)
    cdn.respostas = [URLError("sem rede"), TimeoutError("timeout"), Imagem()]
    for _ in range(2):
        r = client.get(f"/capas/{slug}")
        assert e_svg(r) and r.headers["Cache-Control"] == "no-cache"
        assert no_cache(capa_app) == []
    assert client.get(f"/capas/{slug}").data == JPEG
    assert len(cdn.pedidos) == 3


def test_rtn10_cliente_comum_sem_dados_do_visitante(capa_app, client, cdn):
    slug = jogo(capa_app)
    client.set_cookie("session", "segredo-do-visitante")
    client.get(f"/capas/{slug}", headers={"X-Forwarded-For": "203.0.113.9", "Referer": "https://gamelog/x",
                                          "Accept-Language": "pt-BR"})
    req = cdn.pedidos[0]
    assert dict(req.header_items()) == {"User-agent": base.USER_AGENT}


# ---------- RTN-11: painel ----------
def test_rtn11_painel_mostra_prazo_vencidas_e_ultimo_expurgo(app, client, relogio):
    make_user(app, "chefe", role="admin")
    make_user(app, "ana")
    tentativas(app, 31, 40, 1)
    login(client, "ana")
    assert client.get("/admin/privacidade/retencao").status_code == 403
    client.post("/sair")
    assert client.get("/admin/privacidade/retencao").status_code == 302
    login(client, "chefe")
    html = client.get("/admin/privacidade/retencao").get_data(as_text=True)
    assert "login_attempt" in html and "30 dias" in html and "730 dias" in html and "ainda não rodou" in html
    assert re.search(r"login_attempt</td>\s*<td>30 dias</td>\s*<td>2</td>", html)
    expurgar(app)
    html = client.get("/admin/privacidade/retencao").get_data(as_text=True)
    assert "16/11/2026 12:00" in html
    assert re.search(r"login_attempt</td>\s*<td>30 dias</td>\s*<td>0</td>\s*<td>2</td>", html)


def test_rtn11_auditoria_mostra_o_sistema_como_autor(app, client, relogio):
    make_user(app, "chefe", role="admin")
    expurgar(app)
    login(client, "chefe")
    html = client.get("/admin/auditoria").get_data(as_text=True)
    assert "privacidade.expurgo" in html and "sistema" in html


# ---------- RTN-12: cobertura do mapa ----------
def test_rtn12_toda_tabela_com_prazo_no_mapa_tem_regra_de_expurgo(app):
    with app.app_context():
        tabelas = {t.name for t in db.metadata.sorted_tables}
    com_prazo = {t for t in tabelas if mapa.retencao(t) is not None}
    assert com_prazo == set(retencao.REGRAS) | {"user"}
