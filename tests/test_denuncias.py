from datetime import timedelta

import pytest

from app import denuncias
from app.models import (Clube, Comment, ComunicacaoAutoridade, EventoDenuncia, MembroClube, Notificacao, PedidoTitular,
                        PostClube, Report, Review, User, db, now)
from app.privacidade import retencao
from conftest import login, make_game, make_user

GRAVE = {"reason": "risco_menor", "subcategoria": "exploracao_sexual", "comment": "Pede fotos a uma criança de 11 anos."}
LEVE = {"reason": "risco_menor", "subcategoria": "contato_inadequado", "comment": "Insiste em conversar com menores."}
FUNDAMENTO = "Conteúdo viola a proteção a crianças e adolescentes."


def cliente(app, nome):
    c = app.test_client()
    login(c, nome)
    return c


@pytest.fixture
def mundo(app):
    """Ana (autora) com avaliação, comentário e post de clube; Bia denuncia; root e root2 são admins."""
    ids = {n: make_user(app, n) for n in ("ana", "bia")}
    ids["root"] = make_user(app, "root", role="admin")
    slug = make_game(app, "Hades")
    with app.app_context():
        game_id = db.session.execute(db.text("select id from game where slug = :s"), {"s": slug}).scalar()
        review = Review(user_id=ids["ana"], game_id=game_id, score=8, text="texto da ana")
        de_bia = Review(user_id=ids["bia"], game_id=game_id, score=6, text="texto da bia")
        clube = Clube(nome="Roguelikes", nome_lower="roguelikes", slug="roguelikes")
        db.session.add_all([review, de_bia, clube])
        db.session.flush()
        comentario = Comment(review_id=de_bia.id, user_id=ids["ana"], text="comentário da ana")
        post = PostClube(clube_id=clube.id, user_id=ids["ana"], texto="post da ana")
        db.session.add_all([comentario, post] + [MembroClube(clube_id=clube.id, user_id=ids[n]) for n in ("ana", "bia")])
        db.session.commit()
        ids.update(review=review.id, comentario=comentario.id, post=post.id)
    return ids


def reports(app, **filtro):
    with app.app_context():
        return Report.query.filter_by(**filtro).order_by(Report.id).all()


def denunciar_review(c, mundo, dados):
    return c.post(f"/avaliacoes/{mundo['review']}/denunciar", data=dados, follow_redirects=True)


def decidir(c, rid, decisao, fundamentacao=FUNDAMENTO):
    return c.post(f"/admin/denuncias/{rid}/decidir", data={"decisao": decisao, "fundamentacao": fundamentacao})


# ---------- DEN-01 ----------
def test_den_01_cada_tipo_de_alvo(app, mundo):
    bia = cliente(app, "bia")
    denunciar_review(bia, mundo, {"reason": "spam"})
    bia.post(f"/denunciar/comentario/{mundo['comentario']}", data={"reason": "ofensivo"})
    bia.post(f"/denunciar/perfil/{mundo['ana']}", data=LEVE)
    bia.post(f"/clubes/roguelikes/mural/{mundo['post']}/denunciar", data={"reason": "spam"})
    assert [(r.alvo_tipo, r.alvo_id) for r in reports(app)] == [
        ("avaliacao", mundo["review"]), ("comentario", mundo["comentario"]), ("perfil", mundo["ana"]),
        ("post_clube", mundo["post"])]
    r = bia.post(f"/denunciar/comentario/{mundo['comentario']}", data={"reason": "spam"}, follow_redirects=True)
    assert "Você já denunciou este comentário." in r.get_data(as_text=True) and len(reports(app)) == 4


def test_den_01_proprio_conteudo_e_recusado(app, mundo):
    ana = cliente(app, "ana")
    r = ana.post(f"/denunciar/comentario/{mundo['comentario']}", data={"reason": "spam"}, follow_redirects=True)
    assert "Você não pode denunciar o próprio comentário." in r.get_data(as_text=True)
    assert "o próprio perfil" in ana.post(f"/denunciar/perfil/{mundo['ana']}", data=LEVE,
                                          follow_redirects=True).get_data(as_text=True)
    assert reports(app) == []


def test_den_01_denuncia_antiga_lida_como_avaliacao(app, mundo):
    with app.app_context():
        db.session.execute(db.text("INSERT INTO report (review_id, reporter_id, reason, comment, status, created_at) "
                                   "VALUES (:r, :b, 'spam', '', 'pendente', '2026-01-01')"),
                           {"r": mundo["review"], "b": mundo["bia"]})
        db.session.commit()
        rep = Report.query.one()
        assert rep.alvo_tipo == "avaliacao" and denuncias.alvo_da_denuncia(rep).id == mundo["review"]
    assert "texto da ana" in cliente(app, "root").get("/admin/denuncias").get_data(as_text=True)


# ---------- DEN-02 ----------
@pytest.mark.parametrize("dados,msg", [
    ({**LEVE, "subcategoria": "inventada"}, "Escolha o tipo de risco a menor."),
    ({**LEVE, "comment": "curto"}, "Descreva o risco em 10 a 1000 caracteres."),
])
def test_den_02_subcategoria_e_comentario_obrigatorios(app, mundo, dados, msg):
    assert msg in denunciar_review(cliente(app, "bia"), mundo, dados).get_data(as_text=True)
    assert reports(app) == []


# ---------- DEN-03 ----------
PUBLICA = {"nome": "Maria Souza", "email": "maria@ex.com", "qualidade": "responsavel",
           "subcategoria": "aliciamento", "descricao": "Esse perfil chamou minha filha para outro aplicativo."}


def test_den_03_notificacao_publica_identificada(app, mundo, client):
    anonima = client.post("/denunciar-risco-a-menor", data={**PUBLICA, "nome": "", "email": "",
                                                            "link": "http://x/u/ana"})
    assert anonima.status_code == 400 and "não recebemos notificação anônima" in anonima.get_data(as_text=True)
    assert client.post("/denunciar-risco-a-menor", data={**PUBLICA, "qualidade": "curioso",
                                                          "link": "/u/ana"}).status_code == 400
    assert client.post("/denunciar-risco-a-menor", data={**PUBLICA, "link": "https://gamelog/u/ana"}).status_code == 302
    rep = reports(app)[0]
    assert (rep.alvo_tipo, rep.alvo_id, rep.reporter_id, rep.qualidade) == ("perfil", mundo["ana"], None, "responsavel")
    client.post("/denunciar-risco-a-menor", data={**PUBLICA, "link": "https://outro.site/coisa"})
    assert (reports(app)[1].alvo_tipo, reports(app)[1].link) == ("link", "https://outro.site/coisa")


# ---------- DEN-04 ----------
def test_den_04_aba_de_risco_primeiro_com_prazo_e_atraso(app, mundo, monkeypatch):
    monkeypatch.setenv("PRAZO_DENUNCIA_MENOR_HORAS", "48")
    bia = cliente(app, "bia")
    denunciar_review(bia, mundo, {"reason": "spam"})
    bia.post(f"/denunciar/comentario/{mundo['comentario']}", data=LEVE)
    bia.post(f"/denunciar/perfil/{mundo['ana']}", data={**LEVE, "subcategoria": "outro_risco"})
    with app.app_context():
        primeiro, segundo = Report.query.filter_by(reason="risco_menor").order_by(Report.id).all()
        assert segundo.prazo_em - segundo.created_at == timedelta(hours=48)
        primeiro.prazo_em = now() - timedelta(hours=1)
        db.session.commit()
    root = cliente(app, "root")
    html = root.get("/admin/denuncias").get_data(as_text=True)
    risco = html.split("Risco a menores")[1].split("Outras denúncias")[0]
    assert risco.index("Contato inadequado") < risco.index("Outro risco a menor") and "atrasada" in risco
    assert "texto da ana" in html.split("Outras denúncias")[1]
    assert "2 pendentes" in root.get("/admin/").get_data(as_text=True)


# ---------- DEN-05 ----------
def test_den_05_grave_oculta_na_hora_e_autor_ve_o_aviso(app, mundo):
    denunciar_review(cliente(app, "bia"), mundo, GRAVE)
    with app.app_context():
        assert Review.query.get(mundo["review"]).hidden and Report.query.one().cautelar
    assert "texto da ana" not in cliente(app, "bia").get("/jogos/hades").get_data(as_text=True)
    assert "ocultada pela moderação" in cliente(app, "ana").get("/jogos/hades").get_data(as_text=True)


def test_den_05_outras_subcategorias_so_entram_na_fila(app, mundo):
    cliente(app, "bia").post(f"/denunciar/comentario/{mundo['comentario']}", data=LEVE)
    with app.app_context():
        assert not db.session.get(Comment, mundo["comentario"]).oculto and not Report.query.one().cautelar


# ---------- DEN-06 ----------
def test_den_06_decisao_exige_fundamentacao(app, mundo):
    denunciar_review(cliente(app, "bia"), mundo, LEVE)
    rid = reports(app)[0].id
    root = cliente(app, "root")
    assert decidir(root, rid, "ocultar", "curta demais").status_code == 400
    assert reports(app)[0].status == "pendente"
    decidir(root, rid, "ocultar")
    rep = reports(app)[0]
    assert (rep.status, rep.decisao, rep.fundamentacao) == ("ocultada", "ocultar", FUNDAMENTO)


def test_den_06_suspender_e_restaurar(app, mundo):
    ana = cliente(app, "ana")
    bia = cliente(app, "bia")
    denunciar_review(bia, mundo, GRAVE)
    bia.post(f"/denunciar/perfil/{mundo['ana']}", data=LEVE)
    graves, perfil = reports(app)
    root = cliente(app, "root")
    decidir(root, graves.id, "restaurar")
    with app.app_context():
        assert not db.session.get(Review, mundo["review"]).hidden
    assert decidir(root, perfil.id, "ocultar").status_code == 400
    decidir(root, perfil.id, "suspender")
    with app.app_context():
        assert db.session.get(User, mundo["ana"]).suspensa_em is not None
    assert ana.get("/feed").status_code == 302
    assert app.test_client().get("/u/ana").status_code == 404


# ---------- DEN-07 ----------
def test_den_07_autor_notificado_e_recurso_unico(app, mundo):
    make_user(app, "root2", role="admin")
    denunciar_review(cliente(app, "bia"), mundo, LEVE)
    rid = reports(app)[0].id
    decidir(cliente(app, "root"), rid, "ocultar")
    with app.app_context():
        n = Notificacao.query.filter_by(user_id=mundo["ana"], tipo="sistema").one()
        assert FUNDAMENTO in n.texto and n.alvo_url == f"/denuncias/{rid}/recurso"
    ana = cliente(app, "ana")
    ana.post(f"/denuncias/{rid}/recurso", data={"texto": "Minha avaliação não fala de menores."})
    rep = reports(app)[0]
    assert rep.status == "pendente" and rep.recurso_em is not None
    ana.post(f"/denuncias/{rid}/recurso", data={"texto": "Segunda tentativa de recurso."})
    assert reports(app)[0].recurso_texto == "Minha avaliação não fala de menores."
    assert "recurso" in cliente(app, "root").get("/admin/denuncias").get_data(as_text=True)
    assert decidir(cliente(app, "root"), rid, "ocultar").status_code == 403
    decidir(cliente(app, "root2"), rid, "restaurar")
    with app.app_context():
        assert not db.session.get(Review, mundo["review"]).hidden
    assert cliente(app, "bia").get(f"/denuncias/{rid}/recurso").status_code == 404


def test_den_07_recurso_so_em_15_dias(app, mundo):
    denunciar_review(cliente(app, "bia"), mundo, LEVE)
    rid = reports(app)[0].id
    decidir(cliente(app, "root"), rid, "ocultar")
    with app.app_context():
        db.session.get(Report, rid).decided_at = now() - timedelta(days=16)
        db.session.commit()
    cliente(app, "ana").post(f"/denuncias/{rid}/recurso", data={"texto": "Recurso fora do prazo."})
    assert reports(app)[0].recurso_em is None


# ---------- DEN-08 ----------
def test_den_08_comunicacao_pendente_e_registrada(app, mundo):
    denunciar_review(cliente(app, "bia"), mundo, GRAVE)
    rid = reports(app)[0].id
    root = cliente(app, "root")
    assert "comunicação pendente" in root.get("/admin/denuncias").get_data(as_text=True)
    assert root.post(f"/admin/denuncias/{rid}/comunicacao", data={"orgao": "PF"}).status_code == 400
    root.post(f"/admin/denuncias/{rid}/comunicacao",
              data={"orgao": "Polícia Federal", "protocolo": "PF-123", "data": "2026-11-20"})
    with app.app_context():
        c = ComunicacaoAutoridade.query.one()
        assert (c.orgao, c.protocolo, c.registrado_por) == ("Polícia Federal", "PF-123", mundo["root"])
    assert "comunicação pendente" not in root.get("/admin/denuncias").get_data(as_text=True)


# ---------- DEN-09 ----------
def test_den_09_conteudo_preservado_nao_e_apagado(app, mundo):
    bia = cliente(app, "bia")
    denunciar_review(bia, mundo, GRAVE)
    bia.post(f"/denunciar/comentario/{mundo['comentario']}", data={**GRAVE, "subcategoria": "aliciamento"})
    ana = cliente(app, "ana")
    ana.post(f"/avaliacoes/{mundo['review']}/apagar")
    ana.post(f"/comentarios/{mundo['comentario']}/apagar")
    with app.app_context():
        assert db.session.get(Review, mundo["review"]).hidden
        assert db.session.get(Comment, mundo["comentario"]).oculto
        assert db.session.get(User, mundo["ana"]).preservar_ate > now() + timedelta(days=170)
        assert Report.query.count() == 2


def test_den_09_exclusao_preservada_desliga_e_o_expurgo_anonimiza_depois(app, mundo):
    denunciar_review(cliente(app, "bia"), mundo, GRAVE)
    cliente(app, "ana").post("/conta/excluir", data={"senha": "senha1234", "username": "ana"})
    with app.app_context():
        ana = db.session.get(User, mundo["ana"])
        assert ana.username == "ana" and ana.suspensa_em is not None
        assert PedidoTitular.query.filter_by(tipo="exclusao").one().status == "aberto"
        assert retencao.contas(now())["exclusoes_concluidas"] == 0
        assert retencao.contas(ana.preservar_ate + timedelta(days=1))["exclusoes_concluidas"] == 1
        assert db.session.get(User, mundo["ana"]).username == f"removido-{mundo['ana']}"
        assert PedidoTitular.query.filter_by(tipo="exclusao").one().status == "concluido"
    assert app.test_client().get("/u/ana").status_code == 404


# ---------- DEN-10 ----------
def test_den_10_trilha_completa_e_imutavel(app, mundo):
    make_user(app, "root2", role="admin")
    denunciar_review(cliente(app, "bia"), mundo, GRAVE)
    rid = reports(app)[0].id
    root = cliente(app, "root")
    decidir(root, rid, "ocultar")
    cliente(app, "ana").post(f"/denuncias/{rid}/recurso", data={"texto": "Peço revisão da decisão."})
    root.post(f"/admin/denuncias/{rid}/comunicacao", data={"orgao": "PF", "protocolo": "1", "data": "2026-11-20"})
    with app.app_context():
        tipos = [e.tipo for e in EventoDenuncia.query.filter_by(report_id=rid).order_by(EventoDenuncia.id)]
        assert tipos == ["criada", "ocultada_cautelar", "preservacao", "decidida", "preservacao", "recurso",
                         "comunicada"]
        e = EventoDenuncia.query.first()
        e.detalhe = "editado"
        with pytest.raises(ValueError):
            db.session.flush()
        db.session.rollback()
        with pytest.raises(ValueError):
            db.session.delete(EventoDenuncia.query.first())
            db.session.flush()
    html = root.get(f"/admin/denuncias/{rid}").get_data(as_text=True)
    assert "ocultada_cautelar" in html and "comunicada" in html and "ana@ex.com" in html


# ---------- DEN-11 ----------
def test_den_11_menor_de_12_contesta_a_idade_e_avisa_quem_denunciou(app, mundo):
    cliente(app, "bia").post(f"/denunciar/perfil/{mundo['ana']}",
                             data={**LEVE, "subcategoria": "usuario_menor_de_12"})
    decidir(cliente(app, "root"), reports(app)[0].id, "suspender")
    with app.app_context():
        assert db.session.get(User, mundo["ana"]).idade_contestada_em is not None
        aviso = Notificacao.query.filter_by(user_id=mundo["bia"], tipo="sistema").one()
        assert aviso.texto == "Sua denúncia de risco a menor foi analisada pela moderação."


# ---------- DEN-12 ----------
def test_den_12_post_de_clube_grave_e_ocultado_e_vai_a_fila_do_admin(app, mundo):
    cliente(app, "bia").post(f"/clubes/roguelikes/mural/{mundo['post']}/denunciar", data=GRAVE)
    with app.app_context():
        assert db.session.get(PostClube, mundo["post"]).oculto
    assert "post da ana" in cliente(app, "root").get("/admin/denuncias").get_data(as_text=True).split(
        "Outras denúncias")[0]
