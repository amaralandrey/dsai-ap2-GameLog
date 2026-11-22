"""Bloquear e silenciar (BLQ-01..10)."""
import pytest
from sqlalchemy import event

from app import notificacoes
from app.bloqueios import bloqueado_entre, bloquear
from app.conquistas_catalogo import sincronizar
from app.models import Bloqueio, Game, Comment, Follow, GameList, Like, PostClube, Report, Review, Silencio, User, db, game_stats
from app.rankings import bloqueados_com
from conftest import login, make_game, make_user


@pytest.fixture
def base(app):
    with app.app_context():
        sincronizar()
    for n in ("ana", "bia", "carla"):
        make_user(app, n)
    make_game(app, "Hades")
    return app


def uid(app, nome):
    with app.app_context():
        return User.query.filter_by(username=nome).one().id


def cliente(app, nome):
    c = app.test_client()
    login(c, nome)
    return c


def seguir(app, quem, quem_e_seguido):
    with app.app_context():
        db.session.add(Follow(follower_id=uid(app, quem), followed_id=uid(app, quem_e_seguido)))
        db.session.commit()


def avaliar(app, nome, texto="Muito bom"):
    cliente(app, nome).post("/jogos/hades/avaliar", data={"score": "8", "text": texto})
    with app.app_context():
        return Review.query.filter_by(user_id=uid(app, nome)).one().id


def bloqueia(app, quem, alvo):
    return cliente(app, quem).post(f"/u/{alvo}/bloquear")


# ---------- BLQ-01, BLQ-02 ----------
def test_blq01_bloquear_exige_login_nao_vale_para_si_e_nao_duplica(base):
    assert base.test_client().post("/u/bia/bloquear").status_code == 302  # GLOBAL-02: vai ao login
    ana = cliente(base, "ana")
    ana.post("/u/ana/bloquear")
    ana.post("/u/bia/bloquear")
    ana.post("/u/bia/bloquear")
    with base.app_context():
        assert Bloqueio.query.count() == 1
        a, b, c = (db.session.get(User, uid(base, n)) for n in ("ana", "bia", "carla"))
        assert bloqueado_entre(a, b) and bloqueado_entre(b, a) and not bloqueado_entre(a, c)
        with pytest.raises(ValueError):
            bloquear(a, a)


def test_blq02_bloquear_desfaz_follows_e_seguir_tem_mensagem_generica(base):
    seguir(base, "ana", "bia")
    seguir(base, "bia", "ana")
    bloqueia(base, "ana", "bia")
    with base.app_context():
        assert Follow.query.count() == 0
    for quem, alvo in (("ana", "bia"), ("bia", "ana")):
        r = cliente(base, quem).post(f"/u/{alvo}/seguir", follow_redirects=True)
        assert "Não foi possível seguir este usuário" in r.get_data(as_text=True)
    with base.app_context():
        assert Follow.query.count() == 0
    cliente(base, "ana").post("/u/bia/desbloquear")
    with base.app_context():
        assert Bloqueio.query.count() == 0 and Follow.query.count() == 0  # desbloquear não recria


# ---------- BLQ-03 ----------
def test_blq03_perfil_404_nos_dois_sentidos_e_visivel_para_visitante(base):
    bloqueia(base, "ana", "bia")
    for quem, alvo in (("ana", "bia"), ("bia", "ana")):
        c = cliente(base, quem)
        for pagina in ("", "/diario", "/seguidores", "/seguindo", "/retrospectiva"):
            assert c.get(f"/u/{alvo}{pagina}", follow_redirects=True).status_code == 404, (quem, pagina)
    assert base.test_client().get("/u/ana").status_code == 200
    assert cliente(base, "carla").get("/u/ana").status_code == 200


def test_blq03_lista_do_bloqueado_responde_404(base):
    ana = cliente(base, "ana")
    ana.post("/listas", data={"name": "Favoritos", "public": "on"})
    with base.app_context():
        lid = GameList.query.one().id
    assert cliente(base, "bia").get(f"/listas/{lid}").status_code == 200
    bloqueia(base, "ana", "bia")
    assert cliente(base, "bia").get(f"/listas/{lid}").status_code == 404


# ---------- BLQ-04 ----------
def test_blq04_avaliacoes_e_comentarios_somem_nos_dois_sentidos_e_a_media_fica(base):
    avaliar(base, "ana", "Resenha da ana")
    avaliar(base, "carla", "Resenha da carla")
    with base.app_context():
        rid_carla = Review.query.filter_by(user_id=uid(base, "carla")).one().id
    cliente(base, "bia").post(f"/avaliacoes/{rid_carla}/comentar", data={"text": "Comentário da bia"})
    bloqueia(base, "bia", "ana")
    media = cliente(base, "carla").get("/jogos/hades").get_data(as_text=True)
    html_ana = cliente(base, "ana").get("/jogos/hades").get_data(as_text=True)
    html_bia = cliente(base, "bia").get("/jogos/hades").get_data(as_text=True)
    assert "Resenha da ana" not in html_bia and "Resenha da carla" in html_bia
    assert "Comentário da bia" not in html_ana and "Resenha da carla" in html_ana
    assert "Comentário da bia" in media and "Resenha da ana" in media
    with base.app_context():
        assert game_stats(Game.query.one().id)["total"] == 2  # só esconde, não altera dados (PAG)


def test_blq04_feed_esconde_atividade_e_comentario_de_bloqueado(base):
    seguir(base, "carla", "ana")
    seguir(base, "carla", "bia")
    rid = avaliar(base, "ana", "Resenha da ana")
    avaliar(base, "bia", "Resenha da bia")
    cliente(base, "bia").post(f"/avaliacoes/{rid}/comentar", data={"text": "Comentário da bia"})
    with base.app_context():  # carla bloqueia bia depois de seguir: o follow some, mas o comentário fica no banco
        bloquear(db.session.get(User, uid(base, "carla")), db.session.get(User, uid(base, "bia")))
        db.session.commit()
    feed = cliente(base, "carla").get("/feed").get_data(as_text=True)
    assert 'href="/u/ana"' in feed and "Comentário da bia" not in feed and 'href="/u/bia"' not in feed


# ---------- BLQ-05 ----------
def test_blq05_curtir_e_comentar_com_bloqueio_respondem_404(base):
    rid = avaliar(base, "ana")
    bloqueia(base, "ana", "bia")
    bia = cliente(base, "bia")
    assert bia.post(f"/avaliacoes/{rid}/curtir").status_code == 404
    assert bia.post(f"/avaliacoes/{rid}/comentar", data={"text": "oi"}).status_code == 404
    with base.app_context():
        assert Like.query.count() == 0 and Comment.query.count() == 0


def test_blq05_denunciar_continua_aceito_contra_bloqueado(base):
    rid = avaliar(base, "ana")
    bloqueia(base, "bia", "ana")
    cliente(base, "bia").post(f"/avaliacoes/{rid}/denunciar", data={"reason": "ofensivo", "comment": "x"})
    with base.app_context():
        assert Report.query.filter_by(review_id=rid, reporter_id=uid(base, "bia")).count() == 1


def test_blq05_notificacao_entre_bloqueados_e_descartada(base):
    bloqueia(base, "ana", "bia")
    with base.app_context():
        ana, bia = uid(base, "ana"), uid(base, "bia")
        assert notificacoes.notificar(ana, "seguidor", ator_id=bia) is None
        assert notificacoes.notificar(bia, "seguidor", ator_id=ana) is None
        assert notificacoes.notificar(ana, "seguidor", ator_id=uid(base, "carla")) is not None
        assert notificacoes.notificar(ana, "sistema", texto="aviso geral") is not None  # sem ator, passa


# ---------- BLQ-06 ----------
def test_blq06_mural_do_clube_esconde_post_de_quem_tem_bloqueio(base):
    ana, bia = cliente(base, "ana"), cliente(base, "bia")
    ana.post("/clubes/novo", data={"nome": "Roguelikes", "descricao": "d"})
    bia.post("/clubes/roguelikes/entrar")
    cliente(base, "carla").post("/clubes/roguelikes/entrar")
    ana.post("/clubes/roguelikes/mural", data={"texto": "Post da ana"})
    bia.post("/clubes/roguelikes/mural", data={"texto": "Post da bia"})
    with base.app_context():
        assert PostClube.query.count() == 2
    bloqueia(base, "ana", "bia")
    assert "Post da bia" not in ana.get("/clubes/roguelikes").get_data(as_text=True)
    assert "Post da ana" not in bia.get("/clubes/roguelikes").get_data(as_text=True)
    mural_carla = cliente(base, "carla").get("/clubes/roguelikes").get_data(as_text=True)
    assert "Post da ana" in mural_carla and "Post da bia" in mural_carla


def test_blq06_rankings_tiram_quem_tem_bloqueio_com_quem_consulta(base):
    bloqueia(base, "ana", "bia")
    with base.app_context():
        ana, bia, carla = (db.session.get(User, uid(base, n)) for n in ("ana", "bia", "carla"))
        assert bloqueados_com(ana) == {bia.id} and bloqueados_com(bia) == {ana.id}
        assert bloqueados_com(carla) == set()


# ---------- BLQ-07 ----------
def test_blq07_silenciar_nao_desfaz_follow_e_tira_do_feed_e_das_notificacoes(base):
    assert base.test_client().post("/u/bia/silenciar").status_code == 302
    seguir(base, "ana", "bia")
    avaliar(base, "bia", "Resenha da bia")
    ana = cliente(base, "ana")
    assert 'href="/u/bia"' in ana.get("/feed").get_data(as_text=True)
    ana.post("/u/bia/silenciar")
    ana.post("/u/bia/silenciar")
    with base.app_context():
        assert Silencio.query.count() == 1 and Follow.query.count() == 1
        assert notificacoes.notificar(uid(base, "ana"), "seguidor", ator_id=uid(base, "bia")) is None
        assert notificacoes.notificar(uid(base, "bia"), "seguidor", ator_id=uid(base, "ana")) is not None
    assert 'href="/u/bia"' not in ana.get("/feed").get_data(as_text=True)
    assert cliente(base, "bia").get("/u/ana").status_code == 200  # o silenciado continua vendo tudo
    ana.post("/u/bia/dessilenciar")
    assert 'href="/u/bia"' in ana.get("/feed").get_data(as_text=True)


# ---------- BLQ-08 ----------
def test_blq08_lista_de_bloqueios_e_silenciados(base):
    assert base.test_client().get("/conta/bloqueios").status_code == 302
    ana = cliente(base, "ana")
    ana.post("/u/bia/bloquear")
    ana.post("/u/carla/silenciar")
    ana.get("/u/carla")  # consome o aviso "foi silenciado"
    html = ana.get("/conta/bloqueios").get_data(as_text=True)
    assert "@bia" in html and 'action="/u/bia/desbloquear"' in html and "@carla" not in html
    html = ana.get("/conta/bloqueios?aba=silenciados").get_data(as_text=True)
    assert "@carla" in html and 'action="/u/carla/dessilenciar"' in html and "@bia" not in html
    assert "@ana" not in cliente(base, "bia").get("/conta/bloqueios").get_data(as_text=True)  # ninguém sabe


def test_blq08_bloqueio_do_responsavel_o_adolescente_nao_desfaz(base):
    make_user(base, "mae")
    with base.app_context():
        ana, bia, mae = (db.session.get(User, uid(base, n)) for n in ("ana", "bia", "mae"))
        bloquear(ana, bia, criado_por=mae)
        db.session.commit()
    ana = cliente(base, "ana")
    html = ana.get("/conta/bloqueios").get_data(as_text=True)
    assert "@bia" in html and "/u/bia/desbloquear" not in html and "responsável" in html
    assert ana.post("/u/bia/desbloquear").status_code == 403
    with base.app_context():
        assert Bloqueio.query.count() == 1


# ---------- BLQ-09 ----------
def test_blq09_feed_com_bloqueios_e_silenciados_mantem_o_orcamento(base):
    for n in ("duda", "eva", "fabi", "gil"):
        make_user(base, n)
    for n in ("bia", "carla", "duda", "eva", "fabi", "gil"):
        seguir(base, "ana", n)
        avaliar(base, n, f"Resenha de {n}")
    ana = cliente(base, "ana")

    def contar():
        consultas = []
        with base.app_context():
            conta = lambda *a: consultas.append(1)  # noqa: E731
            event.listen(db.engine, "before_cursor_execute", conta)
            assert ana.get("/feed").status_code == 200
            event.remove(db.engine, "before_cursor_execute", conta)
        return len(consultas)

    antes = contar()
    ana.post("/u/bia/silenciar")
    ana.post("/u/carla/silenciar")
    with base.app_context():
        a = db.session.get(User, uid(base, "ana"))
        for n in ("duda", "eva"):
            db.session.add(Bloqueio(bloqueador_id=uid(base, n), bloqueado_id=a.id))
        db.session.commit()
    depois = contar()
    html = ana.get("/feed").get_data(as_text=True)
    assert 'href="/u/fabi"' in html and not any(f'href="/u/{n}"' in html for n in ("bia", "carla", "duda", "eva"))
    assert depois <= antes + 2
