"""Comentários nas avaliações (SPEC 2026-11-07-comentarios), um teste por critério COM-01..06."""
from sqlalchemy import text

from app.models import Comment, Follow, Review, User, db
from conftest import login, make_game, make_user


def avaliacao(app, client, slug, autor="ana"):
    make_user(app, autor)
    login(client, autor)
    client.post(f"/jogos/{slug}/avaliar", data={"score": "8", "text": "Muito bom"})
    client.post("/sair")
    with app.app_context():
        return Review.query.one().id


def comenta(client, rid, texto, nxt=None):
    data = {"text": texto} | ({"next": nxt} if nxt else {})
    return client.post(f"/avaliacoes/{rid}/comentar", data=data)


def test_com01_formulario_so_para_logado_na_pagina_do_jogo_e_no_feed(app, client):
    slug = make_game(app)
    rid = avaliacao(app, client, slug)
    assert f'action="/avaliacoes/{rid}/comentar"' not in client.get(f"/jogos/{slug}").get_data(as_text=True)
    make_user(app, "bia")
    with app.app_context():
        ana, bia = (User.query.filter_by(username=n).one() for n in ("ana", "bia"))
        db.session.add(Follow(follower_id=bia.id, followed_id=ana.id))
        db.session.commit()
    login(client, "bia")
    for url in (f"/jogos/{slug}", "/feed"):
        html = client.get(url).get_data(as_text=True)
        assert f'action="/avaliacoes/{rid}/comentar"' in html and "Adicionar comentário" in html, url
    r = comenta(client, rid, "Pelo feed", nxt="/feed?page=1")
    assert r.status_code == 302 and r.location.startswith("/feed?page=1#com-")
    assert "Pelo feed" in client.get("/feed").get_data(as_text=True)


def test_com01_anonimo_nao_comenta(app, client):
    slug = make_game(app)
    rid = avaliacao(app, client, slug)
    r = comenta(client, rid, "oi")
    assert r.status_code == 302 and "/entrar" in r.location
    with app.app_context():
        assert Comment.query.count() == 0


def test_com02_ordem_cronologica(app, client):
    slug = make_game(app)
    rid = avaliacao(app, client, slug)
    make_user(app, "bia")
    login(client, "bia")
    for t in ("primeiro", "segundo", "terceiro"):
        comenta(client, rid, t)
    html = client.get(f"/jogos/{slug}").get_data(as_text=True)
    assert html.index("primeiro") < html.index("segundo") < html.index("terceiro")


def test_com03_autor_e_admin_apagam_outros_nao(app, client):
    slug = make_game(app)
    rid = avaliacao(app, client, slug)
    make_user(app, "bia")
    make_user(app, "caio")
    make_user(app, "root", role="admin")
    login(client, "bia")
    comenta(client, rid, "um")
    comenta(client, rid, "dois")
    with app.app_context():
        c1, c2 = (c.id for c in Comment.query.order_by(Comment.id))
    assert f'action="/comentarios/{c1}/apagar"' in client.get(f"/jogos/{slug}").get_data(as_text=True)
    client.post("/sair")
    login(client, "caio")
    assert f'action="/comentarios/{c1}/apagar"' not in client.get(f"/jogos/{slug}").get_data(as_text=True)
    assert client.post(f"/comentarios/{c1}/apagar").status_code == 403
    client.post("/sair")
    login(client, "bia")
    assert client.post(f"/comentarios/{c1}/apagar").status_code == 302
    client.post("/sair")
    login(client, "root")
    assert f'action="/comentarios/{c2}/apagar"' in client.get(f"/jogos/{slug}").get_data(as_text=True)
    assert client.post(f"/comentarios/{c2}/apagar").status_code == 302
    with app.app_context():
        assert Comment.query.count() == 0
        from app.models import AuditLog
        assert AuditLog.query.filter_by(action="comentario.apagar").count() == 1  # só a ação do admin


def test_com04_limite_vazio_e_escape(app, client):
    slug = make_game(app)
    rid = avaliacao(app, client, slug)
    make_user(app, "bia")
    login(client, "bia")
    comenta(client, rid, "   ")
    comenta(client, rid, "x" * 501)
    with app.app_context():
        assert Comment.query.count() == 0
    comenta(client, rid, "x" * 500)
    comenta(client, rid, "<script>alert(1)</script> **negrito**")
    with app.app_context():
        assert Comment.query.count() == 2
    html = client.get(f"/jogos/{slug}").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt; **negrito**" in html


def test_com05_avaliacao_oculta_sem_comentarios(app, client):
    slug = make_game(app)
    rid = avaliacao(app, client, slug)
    make_user(app, "bia")
    login(client, "bia")
    comenta(client, rid, "antes de ocultar")
    with app.app_context():
        db.session.get(Review, rid).hidden = True
        db.session.commit()
    assert comenta(client, rid, "depois").status_code == 404
    client.post("/sair")
    login(client, "ana")  # a autora ainda vê a própria avaliação oculta, mas sem comentários
    html = client.get(f"/jogos/{slug}").get_data(as_text=True)
    assert "ocultada pela moderação" in html
    assert "antes de ocultar" not in html and f'/avaliacoes/{rid}/comentar' not in html
    with app.app_context():
        assert Comment.query.count() == 1


def test_com06_apagar_avaliacao_apaga_comentarios(app, client):
    slug = make_game(app)
    rid = avaliacao(app, client, slug)
    make_user(app, "bia")
    login(client, "bia")
    comenta(client, rid, "vai sumir")
    client.post("/sair")
    login(client, "ana")
    client.post(f"/avaliacoes/{rid}/apagar")
    with app.app_context():
        assert Review.query.count() == 0 and Comment.query.count() == 0


def test_com06_on_delete_cascade_no_banco(app, client):
    slug = make_game(app)
    rid = avaliacao(app, client, slug)
    make_user(app, "bia")
    login(client, "bia")
    comenta(client, rid, "vai sumir")
    with app.app_context():
        db.session.execute(text("DELETE FROM activity WHERE review_id = :id"), {"id": rid})
        db.session.execute(text("DELETE FROM review WHERE id = :id"), {"id": rid})  # sem passar pelo ORM
        db.session.commit()
        assert db.session.execute(text("SELECT COUNT(*) FROM comment")).scalar() == 0
