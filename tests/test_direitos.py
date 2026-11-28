import csv
import io
import json
import re
import zipfile
from datetime import date, timedelta

import pytest
from sqlalchemy import event

from app import conta as conta_mod, recuperacao, tempo, tokens
from app.models import (AuditLog, Consentimento, ControleParental, EmailSaida, Follow, Game, GameList, PedidoTitular,
                        Review, Status, TokenApi, User, VinculoResponsavel, db, now)
from app.privacidade import consentimentos, direitos
from app.recomendacoes import vizinhos
from conftest import login, make_game, make_user


def cliente(app, nome="ana", senha="senha1234"):
    c = app.test_client()
    login(c, nome, senha)
    return c


def jogo(app, titulo="Hades"):
    slug = make_game(app, titulo)
    with app.app_context():
        return Game.query.filter_by(slug=slug).one().id


@pytest.fixture
def ana(app):
    """Ana com avaliação (uma oculta), status, lista, consentimento, seguindo a Bia e seguida por ela."""
    uid, bia = make_user(app, "ana"), make_user(app, "bia")
    hades, celeste = jogo(app), jogo(app, "Celeste")
    with app.app_context():
        db.session.add_all([Review(user_id=uid, game_id=hades, score=9, text="ótimo"),
                            Review(user_id=uid, game_id=celeste, score=3, text="oculta", hidden=True),
                            Review(user_id=bia, game_id=hades, score=7),
                            Status(user_id=uid, game_id=hades, value="zerado"),
                            GameList(user_id=uid, name="Favoritos"),
                            Follow(follower_id=uid, followed_id=bia), Follow(follower_id=bia, followed_id=uid)])
        consentimentos.conceder(db.session.get(User, uid), "email_resumo", "configuracoes")
        tokens.criar(db.session.get(User, uid), "cli", ("ler",), 30)
        db.session.commit()
    return uid


def baixar(c, senha="senha1234", url="/conta/privacidade/exportar", **extra):
    return c.post(url, data={"senha": senha, **extra})


def abrir_zip(resposta):
    z = zipfile.ZipFile(io.BytesIO(resposta.data))
    return {nome: z.read(nome).decode("utf-8") for nome in z.namelist()}


def tabela(arquivos, nome):
    return list(csv.DictReader(io.StringIO(arquivos[f"{nome}.csv"])))


# ---------- DIR-01 ----------
@pytest.mark.parametrize("valor,dias", [(None, 15), ("30", 30), ("abc", 15)])
def test_dir_01_prazo_padrao_e_configurado(app, monkeypatch, valor, dias):
    if valor:
        monkeypatch.setenv("PRAZO_TITULAR_DIAS", valor)
    make_user(app, "ana")
    cliente(app).post("/conta/privacidade/pedidos", data={"tipo": "acesso", "detalhe": "Quero a declaração completa."})
    with app.app_context():
        p = PedidoTitular.query.one()
        assert (p.tipo, p.status) == ("acesso", "aberto")
        assert p.prazo_em - p.criado_em == timedelta(days=dias)


# ---------- DIR-02 ----------
def test_dir_02_acesso_simplificado_na_hora(app, ana):
    html = cliente(app).get("/conta/privacidade/direitos").get_data(as_text=True)
    for texto in ("ana@ex.com", "<th>Avaliações</th><td>2</td>", "<th>Seguidores</th><td>1</td>",
                  "<th>Registros de consentimento</th><td>1</td>", "Render"):
        assert texto in html


def test_dir_02_conta_pendente_tambem_acessa(app):
    uid = make_user(app, "lia")
    with app.app_context():
        u = db.session.get(User, uid)
        u.nascimento, u.aguardando_responsavel = date(tempo.hoje().year - 14, 1, 1), True
        db.session.commit()
    c = cliente(app, "lia")
    assert c.get("/feed").headers["Location"] == "/conta/pendente"
    assert c.get("/conta/privacidade/direitos").status_code == 200
    assert c.get("/conta/excluir").status_code == 200


# ---------- DIR-03 ----------
def test_dir_03_zip_com_json_e_csv_pede_a_senha(app, ana):
    c = cliente(app)
    errado = baixar(c, "errada")
    assert errado.status_code == 302 and errado.mimetype != "application/zip"
    r = baixar(c)
    assert r.mimetype == "application/zip" and "gamelog-ana.zip" in r.headers["Content-Disposition"]
    arquivos = abrir_zip(r)
    assert {"dados.json", "avaliacoes.csv", "status.csv", "listas.csv", "seguindo.csv", "seguidores.csv",
            "consentimentos.csv"} <= set(arquivos)
    assert [(s["game_id"], s["value"]) for s in tabela(arquivos, "status")] == [("hades", "zerado")]
    with app.app_context():
        p = PedidoTitular.query.filter_by(tipo="portabilidade").one()
        assert (p.status, p.user_id) == ("concluido", ana)


def test_dir_03_com_2fa_pede_o_codigo(app, ana, monkeypatch):
    c = cliente(app)
    with app.app_context():
        db.session.get(User, ana).totp_ativado_em = now()
        db.session.commit()
    monkeypatch.setattr(conta_mod, "segundo_fator", lambda user, codigo: codigo == "123456")
    assert baixar(c).mimetype != "application/zip"
    assert baixar(c, codigo="123456").mimetype == "application/zip"


# ---------- DIR-04 ----------
def test_dir_04_so_dados_da_pessoa_e_sem_autenticacao(app, ana):
    arquivos = abrir_zip(baixar(cliente(app)))
    tudo = "".join(arquivos.values())
    with app.app_context():
        u = db.session.get(User, ana)
        segredos = [u.password_hash, TokenApi.query.one().token_hash]
    for proibido in (*segredos, "password_hash", "totp_segredo", "token_hash", "codigo_hash", "bia@ex.com"):
        assert proibido not in tudo
    assert [s["followed_id"] for s in tabela(arquivos, "seguindo")] == ["bia"]
    avaliacoes = {a["text"]: a for a in tabela(arquivos, "avaliacoes")}
    assert set(avaliacoes) == {"ótimo", "oculta"}
    assert (avaliacoes["oculta"]["oculta"], avaliacoes["ótimo"]["oculta"]) == ("True", "False")
    dados = json.loads(arquivos["dados.json"])
    assert dados["versao_formato"] == 1 and dados["gerado_em"] and dados["usuario"]["username"] == "ana"
    assert arquivos["avaliacoes.csv"].splitlines()[0].startswith("game_id,score")


# ---------- DIR-05 ----------
def test_dir_05_correcao_vira_pedido_na_fila(app):
    make_user(app, "ana")
    c = cliente(app)
    assert c.post("/conta/privacidade/pedidos", data={"tipo": "correcao", "detalhe": ""}).status_code == 302
    c.post("/conta/privacidade/pedidos", data={"tipo": "correcao", "detalhe": "Minha data de cadastro está errada."})
    c.post("/conta/privacidade/pedidos", data={"tipo": "exclusao", "detalhe": "pela fila não"})
    with app.app_context():
        assert [(p.tipo, p.detalhe) for p in PedidoTitular.query] == [
            ("correcao", "Minha data de cadastro está errada.")]
    assert "/u/ana/editar" in c.get("/conta/privacidade/direitos").get_data(as_text=True)


# ---------- DIR-06 ----------
def excluir(c, senha="senha1234", username="ana", **extra):
    return c.post("/conta/excluir", data={"senha": senha, "username": username, **extra})


def test_dir_06_confirmacao_com_senha_e_nome(app, ana):
    c = cliente(app)
    assert excluir(c, senha="errada").status_code == 400
    r = excluir(c, username="outra")
    assert r.status_code == 400 and "nome de usuário" in r.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(User, ana).username == "ana"


def test_dir_06_anonimiza_coluna_por_coluna_e_derruba_sessoes(app, ana):
    c = cliente(app)
    outra = cliente(app)
    assert excluir(c).headers["Location"] == "/"
    with app.app_context():
        u = db.session.get(User, ana)
        assert (u.username, u.display_name, u.email) == (f"removido-{ana}", "Usuário removido",
                                                         f"removido-{ana}@removido.invalid")
        assert u.password_hash == "!" and u.bio == "" and u.nascimento is None and u.steam_id is None
        assert TokenApi.query.count() == 0
    assert outra.get("/feed").status_code == 302
    assert c.get("/feed").status_code == 302


# ---------- DIR-07 ----------
def test_dir_07_avaliacoes_ficam_como_usuario_removido(app, ana, client):
    excluir(cliente(app))
    html = client.get("/jogos/hades").get_data(as_text=True)
    assert "Usuário removido" in html and f"/u/removido-{ana}" not in html
    assert client.get(f"/u/removido-{ana}").status_code == 404
    assert client.get(f"/u/removido-{ana}/conquistas").status_code == 404
    with app.app_context():
        assert Review.query.filter_by(user_id=ana).count() == 2
        assert Status.query.filter_by(user_id=ana).count() == GameList.query.filter_by(user_id=ana).count() == 0
        assert Follow.query.filter((Follow.follower_id == ana) | (Follow.followed_id == ana)).count() == 0
        assert Consentimento.query.filter_by(user_id=ana).count() == 1
        p = PedidoTitular.query.filter_by(tipo="exclusao").one()
        assert (p.status, p.user_id) == ("concluido", ana)


def test_dir_07_login_falha_e_nome_fica_livre(app, ana):
    excluir(cliente(app))
    r = app.test_client().post("/entrar", data={"email": "ana@ex.com", "password": "senha1234"},
                               follow_redirects=True)
    assert "E-mail ou senha incorretos." in r.get_data(as_text=True)
    assert make_user(app, "ana", email="nova@ex.com")
    with app.app_context():
        assert recuperacao.conta_anonimizada(db.session.get(User, ana))
        assert not recuperacao.conta_anonimizada(User.query.filter_by(username="ana").one())


# ---------- DIR-08 ----------
def test_dir_08_email_de_confirmacao_para_o_endereco_antigo(app, ana):
    excluir(cliente(app))
    with app.app_context():
        e = EmailSaida.query.filter_by(modelo="conta_excluida").one()
        assert (e.para, e.user_id) == ("ana@ex.com", None)


# ---------- DIR-09 ----------
def test_dir_09_oposicao_tira_as_notas_dos_vizinhos(app):
    ana, bia = make_user(app, "ana"), make_user(app, "bia")
    ids = [jogo(app, t) for t in ("Hades", "Celeste", "Tunic")]
    with app.app_context():
        for uid in (ana, bia):
            db.session.add_all(Review(user_id=uid, game_id=g, score=8) for g in ids)
        db.session.commit()

    def vizinhos_da_bia():
        with app.app_context():
            return [nome for _, nome, _ in vizinhos(bia)]

    assert vizinhos_da_bia() == ["ana"]
    c = cliente(app)
    c.post("/conta/privacidade/oposicao", data={"ligado": "1"})
    assert vizinhos_da_bia() == []
    with app.app_context():
        assert [n for _, n, _ in vizinhos(ana)] == ["bia"]
    c.post("/conta/privacidade/oposicao", data={})
    assert vizinhos_da_bia() == ["ana"]


# ---------- DIR-10 ----------
def test_dir_10_fila_com_atraso_e_resposta_obrigatoria(app):
    ana = make_user(app, "ana")
    make_user(app, "chefe", role="admin")
    with app.app_context():
        u = db.session.get(User, ana)
        atrasado = direitos.novo_pedido(u, "correcao", "Correção", "corrigir A", u.email)
        atrasado.prazo_em = now() - timedelta(days=1)
        logo = direitos.novo_pedido(u, "informacao", "Informação", "com quem B", u.email)
        logo.prazo_em = now() + timedelta(days=2)
        direitos.novo_pedido(u, "acesso", "Acesso", "declaração C", u.email)
        db.session.commit()
        pid = atrasado.id
    admin = cliente(app, "chefe")
    html = admin.get("/admin/privacidade/pedidos").get_data(as_text=True)
    assert html.index("corrigir A") < html.index("com quem B") < html.index("declaração C")
    assert "atrasado" in html and "vence em até 3 dias" in html
    assert cliente(app).get("/admin/privacidade/pedidos").status_code == 403
    admin.post(f"/admin/privacidade/pedidos/{pid}", data={"decisao": "concluido", "resposta": ""})
    admin.post(f"/admin/privacidade/pedidos/{pid}", data={"decisao": "concluido", "resposta": "Corrigido."})
    with app.app_context():
        p = db.session.get(PedidoTitular, pid)
        assert (p.status, p.resposta, p.decidido_por) == ("concluido", "Corrigido.",
                                                         User.query.filter_by(username="chefe").one().id)
        assert AuditLog.query.filter_by(action="privacidade.pedido").count() == 1
        assert EmailSaida.query.filter_by(modelo="pedido_respondido").one().para == "ana@ex.com"
    assert "Corrigido." in cliente(app).get("/conta/privacidade/direitos").get_data(as_text=True)


# ---------- DIR-11 ----------
def link_de_confirmacao(app):
    with app.app_context():
        e = EmailSaida.query.filter_by(modelo="confirmar_pedido").one()
        return re.search(r"/privacidade/pedidos/confirmar/\S+", e.corpo_texto).group(0)


def test_dir_11_pedido_sem_login_so_vale_depois_do_clique(app, client, monkeypatch):
    client.post("/privacidade/contato", data={"assunto": "Dados", "mensagem": "Quero saber.", "email": "v@ex.com"})
    with app.app_context():
        p = PedidoTitular.query.one()
        assert (p.status, p.prazo_em) == ("aguardando_confirmacao", None)
    link = link_de_confirmacao(app)
    assert client.get(link + "x").status_code == 400
    real = tempo.agora
    monkeypatch.setattr(tempo, "agora", lambda: real() + timedelta(hours=49))
    assert client.get(link).status_code == 400
    monkeypatch.setattr(tempo, "agora", real)
    with app.app_context():
        assert PedidoTitular.query.one().status == "aguardando_confirmacao"
    assert client.get(link).status_code == 302
    with app.app_context():
        p = PedidoTitular.query.one()
        assert p.status == "aberto" and p.prazo_em - now() > timedelta(days=14)
        prazo = p.prazo_em
    monkeypatch.setattr(tempo, "agora", lambda: real() + timedelta(hours=1))
    client.get(link)
    with app.app_context():
        assert PedidoTitular.query.one().prazo_em == prazo


# ---------- DIR-12 ----------
@pytest.fixture
def familia(app):
    mae, lia, estranho = make_user(app, "mae"), make_user(app, "lia"), make_user(app, "estranho")
    with app.app_context():
        db.session.get(User, lia).nascimento = date(tempo.hoje().year - 14, 1, 1)
        db.session.add_all([VinculoResponsavel(menor_id=lia, email_convite="mae@ex.com", responsavel_id=mae,
                                               aceito_em=now()), ControleParental(menor_id=lia)])
        db.session.commit()
    return {"mae": mae, "lia": lia, "estranho": estranho}


def test_dir_12_responsavel_baixa_e_exclui_com_a_propria_senha(app, familia):
    mae = cliente(app, "mae")
    assert baixar(mae, url=f"/responsavel/{familia['lia']}/dados").mimetype == "application/zip"
    assert mae.post(f"/responsavel/{familia['lia']}/excluir", data={"senha": "errada"}).status_code == 302
    with app.app_context():
        assert db.session.get(User, familia["lia"]).username == "lia"
    mae.post(f"/responsavel/{familia['lia']}/excluir", data={"senha": "senha1234"})
    with app.app_context():
        assert db.session.get(User, familia["lia"]).username == f"removido-{familia['lia']}"
        assert VinculoResponsavel.query.count() == 0


def test_dir_12_terceiros_recebem_403_e_o_menor_exerce_sozinho(app, familia):
    estranho = cliente(app, "estranho")
    for rota in ("dados", "excluir"):
        assert baixar(estranho, url=f"/responsavel/{familia['lia']}/{rota}").status_code == 403
    assert baixar(cliente(app, "lia")).mimetype == "application/zip"


# ---------- DIR-13 ----------
def consultas_da_exportacao(app, uid):
    contadas = []

    def conta(*_):
        contadas.append(1)
    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", conta)
        direitos.zip_dos_dados(db.session.get(User, uid))
        event.remove(db.engine, "before_cursor_execute", conta)
    return len(contadas)


def test_dir_13_consultas_nao_crescem_com_os_registros_e_zip_em_memoria(app, ana, monkeypatch):
    import tempfile

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", lambda *a, **k: pytest.fail("arquivo temporário"))
    monkeypatch.setattr(tempfile, "mkstemp", lambda *a, **k: pytest.fail("arquivo temporário"))
    antes = consultas_da_exportacao(app, ana)
    ids = [jogo(app, f"Jogo {i}") for i in range(15)]
    with app.app_context():
        db.session.add_all([Review(user_id=ana, game_id=g, score=5) for g in ids]
                           + [Status(user_id=ana, game_id=g, value="planejado") for g in ids])
        db.session.commit()
    depois = consultas_da_exportacao(app, ana)
    assert antes == depois <= 4 * len(direitos.fontes()) + 4


# ---------- DIR-14 ----------
def test_dir_14_fontes_cobrem_as_tabelas_da_pessoa_pelo_mapa(app):
    with app.app_context():
        nomes = {arquivo for arquivo, _, _ in direitos.fontes()}
    assert {"avaliacoes", "sessoes", "listas", "status", "seguindo", "seguidores", "consentimentos",
            "comentarios"} <= nomes
    assert not {"login_attempt", "codigo_recuperacao", "token_api"} & nomes
