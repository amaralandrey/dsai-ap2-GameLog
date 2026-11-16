"""Notificações (NTF-01..12)."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from app import notificacoes, tempo
from app.models import Follow, Game, Notificacao, PreferenciaNotificacao, Review, Status, User, db, now
from conftest import login, make_game, make_user


@pytest.fixture(autouse=True)
def relogio(monkeypatch):
    monkeypatch.setattr(tempo, "agora", lambda: datetime(2026, 11, 16, 12, 0, tzinfo=tempo.FUSO))


@pytest.fixture(autouse=True)
def sem_filtros(monkeypatch):
    monkeypatch.setattr(notificacoes, "_filtros", [])


def uid(app, nome):
    with app.app_context():
        return User.query.filter_by(username=nome).one().id


def cliente(app, nome):
    c = app.test_client()
    login(c, nome)
    return c


def notifs(app, nome=None, **filtros):
    with app.app_context():
        q = Notificacao.query.filter_by(**filtros)
        if nome:
            q = q.filter_by(user_id=uid(app, nome))
        return q.order_by(Notificacao.id).all()


@pytest.fixture
def gente(app):
    for n in ("ana", "bia", "carla", "duda"):
        make_user(app, n)
    make_game(app, "Hades")
    with app.app_context():
        r = Review(user_id=uid(app, "ana"), game_id=Game.query.one().id, score=9, text="ótimo")
        db.session.add(r)
        db.session.commit()
        return r.id


# ---------- NTF-01, NTF-02, NTF-03, NTF-11 ----------
def test_ntf_01_tipo_invalido_e_alvo_externo(app, gente):
    with app.app_context():
        with pytest.raises(ValueError):
            notificacoes.notificar(uid(app, "ana"), "inexistente")
        for externo in ("//evil.com", "https://evil.com/x", "evil", "/\\evil.com"):
            assert notificacoes.notificar(uid(app, "ana"), "sistema", alvo_url=externo).alvo_url == ""
        assert notificacoes.notificar(uid(app, "ana"), "sistema", alvo_url="/u/ana").alvo_url == "/u/ana"


def test_ntf_02_autonotificacao_e_preferencia_desligada(app, gente):
    with app.app_context():
        ana = uid(app, "ana")
        assert notificacoes.notificar(ana, "seguidor", ator_id=ana) is None
        db.session.add(PreferenciaNotificacao(user_id=ana, tipo="seguidor", site=False))
        db.session.add(PreferenciaNotificacao(user_id=ana, tipo="sistema", site=False))
        db.session.commit()
        assert notificacoes.notificar(ana, "seguidor", ator_id=uid(app, "bia")) is None
        assert notificacoes.notificar(ana, "sistema") is not None  # sistema não desliga no site


def test_ntf_03_filtro_registrado_recusa(app, gente):
    vistos = []

    @notificacoes.registrar_filtro
    def so_sistema(user_id, tipo, ctx):
        vistos.append((user_id, tipo, ctx.get("ator_id")))
        return tipo == "sistema"
    with app.app_context():
        assert notificacoes.notificar(uid(app, "ana"), "seguidor", ator_id=uid(app, "bia")) is None
        assert notificacoes.notificar(uid(app, "ana"), "sistema") is not None
    assert vistos[0] == (uid(app, "ana"), "seguidor", uid(app, "bia"))


def test_ntf_11_deve_enviar_email(app, gente):
    with app.app_context():
        ana = uid(app, "ana")
        n = notificacoes.notificar(ana, "seguidor", ator_id=uid(app, "bia"))
        assert notificacoes.deve_enviar_email(n) is False
        db.session.add(PreferenciaNotificacao(user_id=ana, tipo="seguidor", site=True, email=True))
        db.session.commit()
        assert notificacoes.deve_enviar_email(n) is True and notificacoes.deve_enviar_email(None) is False


# ---------- NTF-04, NTF-05 ----------
def test_ntf_04_curtida_avisa_uma_vez_em_24h_e_agrupa(app, gente):
    bia = cliente(app, "bia")
    bia.post(f"/avaliacoes/{gente}/curtir")
    bia.post(f"/avaliacoes/{gente}/descurtir")
    bia.post(f"/avaliacoes/{gente}/curtir")
    assert len(notifs(app, "ana", tipo="curtida")) == 1
    for n in ("carla", "duda"):
        cliente(app, n).post(f"/avaliacoes/{gente}/curtir")
    assert len(notifs(app, "ana", tipo="curtida")) == 3
    html = cliente(app, "ana").get("/notificacoes").get_data(as_text=True)
    assert "duda e mais 2 pessoas curtiram sua avaliação de Hades" in html.replace("<strong>", "").replace("</strong>", "")
    with app.app_context():
        for n in Notificacao.query.all():
            n.created_at -= timedelta(hours=25)
        db.session.commit()
    bia.post(f"/avaliacoes/{gente}/descurtir")
    bia.post(f"/avaliacoes/{gente}/curtir")
    assert len(notifs(app, "ana", tipo="curtida")) == 4


def test_ntf_04_curtir_a_propria_nao_avisa(app, gente):
    cliente(app, "ana").post(f"/avaliacoes/{gente}/curtir")
    assert notifs(app) == []


def test_ntf_05_comentario_com_link(app, gente):
    cliente(app, "bia").post(f"/avaliacoes/{gente}/comentar", data={"text": "concordo"})
    n, = notifs(app, "ana", tipo="comentario")
    assert n.alvo_url.startswith("/jogos/hades#com-") and n.review_id == gente
    cliente(app, "ana").post(f"/avaliacoes/{gente}/comentar", data={"text": "obrigada"})
    assert len(notifs(app, "ana")) == 1


def test_ntf_05_seguidor_uma_vez_por_par_em_24h(app, gente):
    bia = cliente(app, "bia")
    bia.post("/u/ana/seguir")
    bia.post("/u/ana/deixar-de-seguir")
    bia.post("/u/ana/seguir")
    assert len(notifs(app, "ana", tipo="seguidor")) == 1
    cliente(app, "carla").post("/u/ana/seguir")
    assert len(notifs(app, "ana", tipo="seguidor")) == 2


# ---------- NTF-06 ----------
def planejar(app, nome, slug="hades"):
    with app.app_context():
        db.session.add(Status(user_id=uid(app, nome), game_id=Game.query.filter_by(slug=slug).one().id,
                              value="planejado"))
        db.session.commit()


def seguir(app, quem, alvo):
    with app.app_context():
        db.session.add(Follow(follower_id=uid(app, quem), followed_id=uid(app, alvo)))
        db.session.commit()


def zerar(app, nome, **extra):
    return cliente(app, nome).post("/jogos/hades/sessoes", data={"jogada_em": "2026-11-10", "duracao": "60",
                                                                 "zerou": "on", **extra})


def test_ntf_06_amigo_zerou_so_quem_segue_e_planejou(app, gente):
    seguir(app, "bia", "ana")
    planejar(app, "bia")
    seguir(app, "carla", "ana")  # segue, mas não planejou
    planejar(app, "duda")  # planejou, mas não segue
    zerar(app, "ana")
    assert [n.user_id for n in notifs(app, tipo="amigo_zerou")] == [uid(app, "bia")]
    zerar(app, "ana", jogada_em="2026-11-01")  # zerar de novo não avisa outra vez
    assert len(notifs(app, tipo="amigo_zerou")) == 1


def test_ntf_06_perfil_privado_so_avisa_quem_pode_ver(app, gente):
    for n in ("bia", "carla"):
        seguir(app, n, "ana")
        planejar(app, n)
    seguir(app, "ana", "carla")
    with app.app_context():
        User.query.filter_by(username="ana").one().perfil_privado = True
        db.session.commit()
    zerar(app, "ana")
    assert [n.user_id for n in notifs(app, tipo="amigo_zerou")] == [uid(app, "carla")]


def test_ntf_06_limite_de_200(app, gente):
    with app.app_context():
        g, ana = Game.query.one().id, uid(app, "ana")
        for i in range(205):
            u = User(username=f"f{i}", username_lower=f"f{i}", email=f"f{i}@x.com", password_hash="x",
                     display_name=f"f{i}")
            db.session.add(u)
            db.session.flush()
            db.session.add(Follow(follower_id=u.id, followed_id=ana))
            db.session.add(Status(user_id=u.id, game_id=g, value="planejado"))
        db.session.commit()
    zerar(app, "ana")
    assert len(notifs(app, tipo="amigo_zerou")) == 200


# ---------- NTF-07..10 ----------
def test_ntf_07_lista_paginada_e_esconde_oculta_e_removida(app, gente):
    with app.app_context():
        ana = uid(app, "ana")
        for i in range(22):
            notificacoes.notificar(ana, "sistema", texto=f"aviso {i}")
        notificacoes.notificar(ana, "comentario", ator_id=uid(app, "bia"), review_id=gente, game_id=1,
                               texto="sobre a oculta")
        db.session.commit()
    ana_c = cliente(app, "ana")
    assert "aviso 21" in ana_c.get("/notificacoes").get_data(as_text=True)
    assert "aviso 0" in ana_c.get("/notificacoes?page=2").get_data(as_text=True)
    with app.app_context():
        db.session.get(Review, gente).hidden = True
        db.session.commit()
    paginas = ana_c.get("/notificacoes").get_data(as_text=True) + ana_c.get("/notificacoes?page=2").get_data(as_text=True)
    assert "comentou sua avaliação" not in paginas
    with app.app_context():
        db.session.get(Review, gente).hidden = False
        Game.query.one().removed_at = now()
        db.session.commit()
    assert "comentou sua avaliação" not in ana_c.get("/notificacoes").get_data(as_text=True)


def test_ntf_07_exige_login(client):
    assert client.get("/notificacoes").status_code == 302


def test_ntf_08_ler_redireciona_e_404_de_outra_pessoa(app, gente):
    cliente(app, "bia").post("/u/ana/seguir")
    n, = notifs(app, "ana")
    assert cliente(app, "carla").post(f"/notificacoes/{n.id}/ler").status_code == 404
    r = cliente(app, "ana").post(f"/notificacoes/{n.id}/ler")
    assert r.headers["Location"].endswith("/u/bia") and notifs(app, "ana")[0].lida_em is not None
    with app.app_context():
        sem_alvo = notificacoes.notificar(uid(app, "ana"), "sistema")
        db.session.commit()
        sid = sem_alvo.id
    assert cliente(app, "ana").post(f"/notificacoes/{sid}/ler").headers["Location"].endswith("/notificacoes")


def test_ntf_08_ler_todas(app, gente):
    with app.app_context():
        for _ in range(3):
            notificacoes.notificar(uid(app, "ana"), "sistema")
        notificacoes.notificar(uid(app, "bia"), "sistema")
        db.session.commit()
    cliente(app, "ana").post("/notificacoes/ler-todas")
    assert all(n.lida_em for n in notifs(app, "ana")) and notifs(app, "bia")[0].lida_em is None


def test_ntf_09_preferencias_padrao_e_gravadas(app, gente):
    ana = cliente(app, "ana")
    html = ana.get("/notificacoes/preferencias").get_data(as_text=True)
    assert 'name="site_curtida" style="width:auto" aria-label="Curtidas nas suas avaliações no site" checked' in html
    assert 'name="email_curtida" style="width:auto" aria-label="Curtidas nas suas avaliações por e-mail" >' in html
    ana.post("/notificacoes/preferencias", data={"email_curtida": "on", "site_comentario": "on", "site_xyz": "on"})
    with app.app_context():
        assert notificacoes.preferencia(uid(app, "ana"), "curtida") == (False, True)
        assert notificacoes.preferencia(uid(app, "ana"), "comentario") == (True, False)
        assert notificacoes.preferencia(uid(app, "ana"), "sistema") == (True, False)
        assert PreferenciaNotificacao.query.filter_by(tipo="xyz").count() == 0


def test_ntf_10_sino_com_9_mais_numa_consulta(app, gente):
    ana = cliente(app, "ana")
    assert 'class="contador"' not in ana.get("/").get_data(as_text=True)
    with app.app_context():
        for _ in range(3):
            notificacoes.notificar(uid(app, "ana"), "sistema")
        db.session.commit()
    assert '<span class="contador">3</span>' in ana.get("/").get_data(as_text=True)
    with app.app_context():
        for _ in range(8):
            notificacoes.notificar(uid(app, "ana"), "sistema")
        db.session.commit()
    consultas = []

    def conta(conn, cursor, sql, *a):
        if "notificacao" in sql.lower():
            consultas.append(sql)
    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", conta)
        html = ana.get("/").get_data(as_text=True)
        event.remove(db.engine, "before_cursor_execute", conta)
    assert '<span class="contador">9+</span>' in html and len(consultas) == 1
