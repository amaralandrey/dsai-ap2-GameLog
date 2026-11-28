import re
from datetime import date, timedelta

from sqlalchemy import null

import pytest

from app import jobs, recuperacao, tempo
from app.menores import responsavel as rsp
from app.models import (Activity, Bloqueio, Comment, Consentimento, ControleParental, EmailSaida, Follow,
                        InscricaoTorneio, Notificacao, Review, Torneio, User, VerificacaoIdade, VinculoResponsavel, db)
from app.privacidade import retencao
from conftest import login, make_game, make_user


def anos_atras(anos):
    hoje = tempo.hoje()
    return date(hoje.year - anos, 1, 1) if (hoje.month, hoje.day) > (1, 1) else date(hoje.year - anos - 1, 1, 1)


def nascimento_iso(anos):
    return anos_atras(anos).isoformat()


def conta(app, nome, anos=None, pendente=None):
    uid = make_user(app, nome)
    if anos is not None:
        with app.app_context():
            u = db.session.get(User, uid)
            u.nascimento = anos_atras(anos)
            u.aguardando_responsavel = anos < 16 if pendente is None else pendente
            u.perfil_privado = anos < 18
            db.session.commit()
    return uid


def cliente(app, nome):
    c = app.test_client()
    login(c, nome)
    return c


def convidar(c, email="mae@ex.com"):
    return c.post("/conta/pendente/convite", data={"email": email})


def ultimo_token(app):
    with app.app_context():
        e = EmailSaida.query.filter_by(modelo="convite_responsavel").order_by(EmailSaida.id.desc()).first()
        return re.search(r"/responsavel/convite/(\S+)", e.corpo_texto).group(1)


def aceitar(c, token, anos=14, declaro=True):
    dados = {"nascimento": nascimento_iso(anos), **({"declaro": "1"} if declaro else {})}
    return c.post(f"/responsavel/convite/{token}", data=dados)


@pytest.fixture
def familia(app):
    """Lia (14 anos, pendente), a mãe e um estranho, todos com conta."""
    ids = {"lia": conta(app, "lia", 14), "mae": conta(app, "mae"), "estranho": conta(app, "estranho")}
    return ids


def vincular(app, menor="lia", responsavel="mae", anos=14):
    convidar(cliente(app, menor), f"{responsavel}@ex.com")
    return aceitar(cliente(app, responsavel), ultimo_token(app), anos)


def vinculos(app):
    with app.app_context():
        return VinculoResponsavel.query.order_by(VinculoResponsavel.id).all()


def notificacoes(app, uid):
    with app.app_context():
        return [n.texto for n in Notificacao.query.filter_by(user_id=uid, tipo="responsavel").order_by(Notificacao.id)]


def avaliacao(app, autor_id):
    """(id do jogo Hades, id de uma avaliação de `autor_id` nele)."""
    slug = make_game(app, "Hades")
    with app.app_context():
        game_id = db.session.execute(db.text("select id from game where slug = :s"), {"s": slug}).scalar()
        r = Review(user_id=autor_id, game_id=game_id, score=8)
        db.session.add(r)
        db.session.commit()
        return game_id, r.id


# ---------- RSP-01 ----------
def test_rsp_01_convite_por_email_sem_mostrar_o_endereco(app, familia):
    c = cliente(app, "lia")
    assert convidar(c, "Mae@Ex.com").status_code == 302
    with app.app_context():
        v = VinculoResponsavel.query.one()
        assert (v.menor_id, v.email_convite, v.responsavel_id, v.aceito_em) == (familia["lia"], "mae@ex.com", None, None)
        e = EmailSaida.query.filter_by(modelo="convite_responsavel").one()
        assert e.para == "mae@ex.com" and "/responsavel/convite/" in e.corpo_texto
    html = c.get("/conta/pendente").get_data(as_text=True)
    assert "mae@ex.com" not in html.lower() and "1 de 3 convites" in html


def test_rsp_01_limite_de_3_convites_por_dia(app, familia):
    c = cliente(app, "lia")
    for i in range(3):
        assert convidar(c, f"r{i}@ex.com").status_code == 302
    r = convidar(c, "r4@ex.com")
    assert r.status_code == 429 and "3 convites hoje" in r.get_data(as_text=True)
    assert len(vinculos(app)) == 3


# ---------- RSP-02 ----------
def test_rsp_02_aceite_por_adulto_confirma_a_idade_e_libera_a_conta(app, familia):
    assert vincular(app).headers["Location"] == f"/responsavel/{familia['lia']}"
    with app.app_context():
        v = VinculoResponsavel.query.one()
        lia = db.session.get(User, familia["lia"])
        assert (v.responsavel_id, v.versao_declaracao, v.ativo) == (familia["mae"], rsp.VERSAO_DECLARACAO, True)
        assert not lia.aguardando_responsavel and lia.nascimento_confiavel
        assert VerificacaoIdade.query.filter_by(user_id=lia.id).one().metodo == "responsavel"
        assert ControleParental.query.filter_by(menor_id=lia.id).one().comentarios is False
    assert cliente(app, "lia").get("/feed").status_code == 200
    assert notificacoes(app, familia["lia"]) == ["mae aceitou ser seu responsável no GameLog."]


def test_rsp_02_sem_declaracao_nao_vincula(app, familia):
    convidar(cliente(app, "lia"))
    r = aceitar(cliente(app, "mae"), ultimo_token(app), declaro=False)
    assert r.status_code == 400 and "Confirme a declaração" in r.get_data(as_text=True)
    assert vinculos(app)[0].aceito_em is None


def test_rsp_02_visitante_vai_ao_cadastro_e_volta(app, familia):
    convidar(cliente(app, "lia"))
    token = ultimo_token(app)
    r = app.test_client().get(f"/responsavel/convite/{token}")
    assert r.status_code == 302 and "/cadastro?next=/responsavel/convite/" in r.headers["Location"]


def test_rsp_02_divergencia_de_nascimento_vale_a_do_responsavel(app, familia):
    vincular(app, anos=17)
    with app.app_context():
        lia = db.session.get(User, familia["lia"])
        assert lia.nascimento == anos_atras(17) and VerificacaoIdade.query.one().divergente
        assert ControleParental.query.count() == 0


# ---------- RSP-03 ----------
def test_rsp_03_link_adulterado_expirado_ou_usado_e_400(app, familia, monkeypatch):
    import itsdangerous.timed

    c = cliente(app, "lia")
    convidar(c)
    token = ultimo_token(app)
    mae = cliente(app, "mae")
    assert mae.get(f"/responsavel/convite/{token}x").status_code == 400
    real = itsdangerous.timed.time.time
    monkeypatch.setattr(itsdangerous.timed.time, "time", lambda: real() + 8 * 86400)
    assert mae.get(f"/responsavel/convite/{token}").status_code == 400
    monkeypatch.undo()
    assert aceitar(mae, token).status_code == 302
    assert aceitar(cliente(app, "estranho"), token).status_code == 400
    assert len([v for v in vinculos(app) if v.ativo]) == 1


@pytest.mark.parametrize("quem,anos", [("teo", 17), ("ana", 14), ("lia", None)])
def test_rsp_03_so_adulto_aceita(app, familia, quem, anos):
    if quem != "lia":
        conta(app, quem, anos, pendente=False)
    convidar(cliente(app, "lia"))
    assert aceitar(cliente(app, quem), ultimo_token(app)).status_code == 403
    assert vinculos(app)[0].aceito_em is None


def test_rsp_03_recusar_deixa_a_conta_pendente(app, familia):
    convidar(cliente(app, "lia"))
    cliente(app, "mae").post(f"/responsavel/convite/{ultimo_token(app)}/recusar")
    v = vinculos(app)[0]
    assert v.recusado_em is not None and v.aceito_em is None
    assert cliente(app, "lia").get("/feed").headers["Location"] == "/conta/pendente"


# ---------- RSP-04 ----------
def test_rsp_04_pendente_ve_o_prazo_e_e_apagada_aos_30_dias(app, familia):
    conta(app, "rui", 13)
    vincular(app)
    with app.app_context():
        rui = db.session.get(User, User.query.filter_by(username="rui").one().id)
        limite = tempo.dia_de(rui.created_at + timedelta(days=30)).strftime("%d/%m/%Y")
    assert limite in cliente(app, "rui").get("/conta/pendente").get_data(as_text=True)
    with app.app_context():
        vencidas = retencao.pendentes_vencidas(tempo.agora_utc() + timedelta(days=31))
        assert [u.username for u in vencidas] == ["rui"]


# ---------- RSP-05 ----------
def test_rsp_05_ate_2_responsaveis_e_painel_isolado(app, familia):
    conta(app, "pai")
    conta(app, "avo")
    vincular(app)
    lia = cliente(app, "lia")
    convidar(lia, "pai@ex.com")
    pai = ultimo_token(app)
    convidar(lia, "avo@ex.com")
    avo = ultimo_token(app)
    aceitar(cliente(app, "pai"), pai)
    r = aceitar(cliente(app, "avo"), avo)
    assert r.status_code == 400 and "2 responsáveis" in r.get_data(as_text=True)
    assert "já tem 2 responsáveis" in lia.post("/conta/pendente/convite", data={"email": "x@ex.com"},
                                                follow_redirects=True).get_data(as_text=True)
    meus = lia.get("/conta/responsaveis").get_data(as_text=True)
    assert "<strong>mae</strong>" in meus and "<strong>pai</strong>" in meus and "@ex.com" not in meus
    assert "lia" in cliente(app, "mae").get("/responsavel").get_data(as_text=True)
    assert cliente(app, "estranho").get(f"/responsavel/{familia['lia']}").status_code == 404
    assert cliente(app, "estranho").post(f"/responsavel/{familia['lia']}/controles").status_code == 404
    with app.app_context():
        assert sorted(rsp.responsaveis_ativos(db.session.get(User, familia["lia"]))) == sorted(
            [familia["mae"], User.query.filter_by(username="pai").one().id])


# ---------- RSP-06 ----------
def test_rsp_06_atividade_metricas_e_pedido_de_seguir(app, familia):
    vincular(app)
    game_id, rid = avaliacao(app, familia["estranho"])
    with app.app_context():
        db.session.add_all([Comment(review_id=rid, user_id=familia["lia"], text="legal"),
                            Activity(user_id=familia["lia"], kind="sessao", game_id=game_id, valor=90),
                            Follow(follower_id=familia["estranho"], followed_id=familia["lia"], aprovado_em=null())])
        db.session.commit()
    mae = cliente(app, "mae")
    html = mae.get(f"/responsavel/{familia['lia']}").get_data(as_text=True)
    assert "comentou uma avaliação de Hades" in html and "jogou Hades por 1h 30min" in html
    assert "<td>7 dias</td><td>0min</td><td>2</td>" in html.replace("\n", "")
    with app.app_context():
        fid = Follow.query.one().id
    mae.post(f"/responsavel/{familia['lia']}/pedidos/{fid}/aprovar")
    with app.app_context():
        assert Follow.query.one().aprovado_em is not None


def test_rsp_06_pedido_de_seguir_avisa_o_responsavel(app, familia):
    vincular(app)
    cliente(app, "estranho").post("/u/lia/seguir")
    assert notificacoes(app, familia["mae"]) == ["@estranho pediu para seguir lia."]


# ---------- RSP-07 ----------
def test_rsp_07_controles_nascem_desligados_e_bloqueiam_comentar_e_clube(app, familia):
    vincular(app)
    cliente(app, "estranho").post("/clubes/novo", data={"nome": "Roguelikes", "descricao": "d"})
    _, rid = avaliacao(app, familia["estranho"])
    lia = cliente(app, "lia")
    assert lia.post(f"/avaliacoes/{rid}/comentar", data={"text": "oi"}).status_code == 403
    lia.post("/clubes/roguelikes/entrar")
    with app.app_context():
        assert db.session.execute(db.text("select count(*) from membro_clube where user_id = :u"),
                                  {"u": familia["lia"]}).scalar() == 0
    cliente(app, "mae").post(f"/responsavel/{familia['lia']}/controles", data={"comentarios": "1", "clubes": "1"})
    assert lia.post(f"/avaliacoes/{rid}/comentar", data={"text": "oi"}).status_code == 302
    lia.post("/clubes/roguelikes/entrar")
    with app.app_context():
        assert Comment.query.count() == 1
        assert db.session.execute(db.text("select count(*) from membro_clube where user_id = :u"),
                                  {"u": familia["lia"]}).scalar() == 1
        c = ControleParental.query.one()
        assert (c.alterado_por, c.torneios) == (familia["mae"], False) and c.alterado_em is not None
    assert notificacoes(app, familia["lia"])[-1] == (
        "Seu responsável mudou: comentar avaliações (ligado), entrar e postar em clubes (ligado).")


def test_rsp_07_perfil_publico_e_resumo_pelo_painel(app, familia):
    vincular(app)
    mae = cliente(app, "mae")
    mae.post(f"/responsavel/{familia['lia']}/controles", data={"perfil_publico": "1"})
    mae.post(f"/responsavel/{familia['lia']}/resumo", data={"ligado": "1"})
    with app.app_context():
        assert db.session.get(User, familia["lia"]).perfil_privado is False
        c = Consentimento.query.filter_by(user_id=familia["lia"], finalidade="email_resumo").one()
        assert (c.concedido, c.origem) == (True, "responsavel")


# ---------- RSP-08 ----------
def torneio_com_pendente(app, lia_id):
    game_id, _ = avaliacao(app, lia_id)
    with app.app_context():
        t = Torneio(slug="copa", nome="Copa", game_id=game_id, formato="pontos", limite=4,
                    prazo_inscricao=tempo.agora_utc() + timedelta(days=3), organizador_id=1, aberto_a_menores=True)
        db.session.add(t)
        db.session.flush()
        i = InscricaoTorneio(torneio_id=t.id, user_id=lia_id, situacao="pendente_responsavel")
        db.session.add(i)
        db.session.commit()
        return i.id


@pytest.mark.parametrize("decisao,situacao", [("aprovar", "inscrito"), ("recusar", "descartada")])
def test_rsp_08_responsavel_decide_a_inscricao(app, familia, decisao, situacao):
    vincular(app)
    iid = torneio_com_pendente(app, familia["lia"])
    mae = cliente(app, "mae")
    assert "Copa" in mae.get(f"/responsavel/{familia['lia']}").get_data(as_text=True)
    mae.post(f"/responsavel/{familia['lia']}/torneios/{iid}/{decisao}")
    with app.app_context():
        assert db.session.get(InscricaoTorneio, iid).situacao == situacao
    assert "torneio Copa" in notificacoes(app, familia["lia"])[-1]


def test_rsp_08_controle_de_torneios_desligado_barra_a_inscricao(app, familia):
    from app.menores.politica import pode

    vincular(app)
    with app.app_context():
        lia = db.session.get(User, familia["lia"])
        aberto = Torneio(aberto_a_menores=True, clube_id=None)
        assert not pode(lia, "torneio_inscrever", aberto)
        ControleParental.query.one().torneios = True
        assert pode(lia, "torneio_inscrever", aberto)


# ---------- RSP-09 ----------
def test_rsp_09_interacoes_remover_seguidor_e_bloquear(app, familia):
    vincular(app)
    conta(app, "teo", 17, pendente=False)
    with app.app_context():
        db.session.add_all([Follow(follower_id=familia["estranho"], followed_id=familia["lia"]),
                            Follow(follower_id=User.query.filter_by(username="teo").one().id,
                                   followed_id=familia["lia"])])
        db.session.commit()
    mae = cliente(app, "mae")
    html = mae.get(f"/responsavel/{familia['lia']}").get_data(as_text=True)
    secao = html.split("Com quem interage")[1].split("</section>")[0]
    assert "@estranho" in secao and "@teo" not in secao and "@mae" not in secao
    mae.post(f"/responsavel/{familia['lia']}/seguidores/{familia['estranho']}/remover")
    with app.app_context():
        assert not Follow.query.filter_by(follower_id=familia["estranho"]).first()
    mae.post(f"/responsavel/{familia['lia']}/bloquear/{familia['estranho']}")
    with app.app_context():
        b = Bloqueio.query.one()
        assert (b.bloqueador_id, b.bloqueado_id, b.criado_por) == (familia["lia"], familia["estranho"], familia["mae"])
    assert cliente(app, "lia").post("/u/estranho/desbloquear").status_code == 403


# ---------- RSP-10 ----------
def test_rsp_10_menor_nao_mexe_na_supervisao(app, familia):
    vincular(app)
    lia = cliente(app, "lia")
    for rota in ("controles", "resumo", "desvincular", "pedidos/1/aprovar"):
        assert lia.post(f"/responsavel/{familia['lia']}/{rota}", data={"comentarios": "1"}).status_code == 403
    with app.app_context():
        db.session.add(Follow(follower_id=familia["estranho"], followed_id=familia["lia"], aprovado_em=null()))
        db.session.commit()
        fid = Follow.query.one().id
    assert lia.post(f"/conta/pedidos/{fid}/aprovar").status_code == 403
    assert vinculos(app)[0].ativo


def test_rsp_10_desvincular_volta_a_esperar_sem_outro_vinculo(app, familia):
    conta(app, "pai")
    vincular(app)
    vincular(app, responsavel="pai")
    cliente(app, "mae").post(f"/responsavel/{familia['lia']}/desvincular")
    with app.app_context():
        assert not db.session.get(User, familia["lia"]).aguardando_responsavel
    cliente(app, "pai").post(f"/responsavel/{familia['lia']}/desvincular")
    with app.app_context():
        assert db.session.get(User, familia["lia"]).aguardando_responsavel
    assert [v.revogado_em is not None for v in vinculos(app)] == [True, True]


# ---------- RSP-11 ----------
def test_rsp_11_aos_16_o_vinculo_encerra_por_idade(app, familia):
    vincular(app)
    with app.app_context():
        db.session.get(User, familia["lia"]).nascimento = anos_atras(16)
        db.session.commit()
        jobs.executar(jobs.enfileirar("responsavel_idade").id)
    v = vinculos(app)[0]
    assert v.encerrado_idade_em is not None and not v.ativo
    assert "fez 16 anos" in notificacoes(app, familia["lia"])[-1]
    assert "lia fez 16 anos" in notificacoes(app, familia["mae"])[-1]
    assert cliente(app, "mae").get(f"/responsavel/{familia['lia']}").status_code == 404
    assert jobs.AGENDAS_PADRAO["responsavel_idade"][:2] == ("10 0 * * *", "responsavel_idade")


def test_rsp_11_menor_de_16_continua_vinculado(app, familia):
    vincular(app)
    with app.app_context():
        rsp.encerrar_por_idade()
        db.session.commit()
    assert vinculos(app)[0].ativo


# ---------- RSP-12 ----------
def test_rsp_12_ganchos_de_cns_e_rsn_usam_o_vinculo(app, familia):
    from app.privacidade import consentimentos

    vincular(app)
    with app.app_context():
        lia, mae, estranho = (db.session.get(User, familia[n]) for n in ("lia", "mae", "estranho"))
        assert consentimentos.e_responsavel_vinculado(mae, lia)
        assert not consentimentos.e_responsavel_vinculado(estranho, lia)
        assert recuperacao.responsaveis_ativos(lia) == [familia["mae"]]
