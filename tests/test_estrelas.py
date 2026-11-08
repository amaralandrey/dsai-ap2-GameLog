"""Avaliação por estrelas (SPEC 2026-11-07-avaliacao-interativa), com notas inteiras de 0 a 10."""
import re

from app.models import Activity, Review, User, db
from conftest import login, make_game, make_user


def avalia(client, slug, nota):
    return client.post(f"/jogos/{slug}/avaliar", data={"score": str(nota), "text": "boa"})


def test_est01_formulario_tem_10_estrelas_e_nota_zero(app, client):
    slug = make_game(app)
    make_user(app, "ana")
    login(client)
    html = client.get(f"/jogos/{slug}").get_data(as_text=True)
    assert 'type="number"' not in html
    valores = re.findall(r'type="radio" (?:id="score-\d+" )?name="score" value="(\d+)"', html)
    assert sorted(map(int, valores)) == list(range(11))
    assert len(re.findall(r'<label for="score-\d+"', html)) == 10
    assert "<legend>Nota (0 a 10)</legend>" in html and "1 de 10" in html  # rótulos acessíveis


def test_est03_backend_recebe_numero_e_marca_estrela_atual(app, client):
    slug = make_game(app)
    make_user(app, "ana")
    login(client)
    avalia(client, slug, 7)
    with app.app_context():
        assert Review.query.one().score == 7
    html = client.get(f"/jogos/{slug}").get_data(as_text=True)
    assert re.search(r'value="7" required checked', html)
    assert not re.search(r'value="(?!7")\d+" required checked', html)


def test_est03_nota_zero_continua_valida(app, client):
    slug = make_game(app)
    make_user(app, "ana")
    login(client)
    avalia(client, slug, 0)
    with app.app_context():
        assert Review.query.one().score == 0
    assert 'value="0" checked' in client.get(f"/jogos/{slug}").get_data(as_text=True)


def test_est04_notas_exibidas_como_estrelas(app, client):
    slug = make_game(app)
    make_user(app, "ana")
    login(client)
    avalia(client, slug, 8)
    estrela = '<span class="stars" role="img" aria-label="Nota 8 de 10" title="8/10"><span class="on">★★★★★★★★</span>★★</span>'
    for url in (f"/jogos/{slug}", "/u/ana"):
        html = client.get(url).get_data(as_text=True)
        assert estrela in html and "★ 8/10" not in html, url
    make_user(app, "bia")
    with app.app_context():
        ana, bia = (User.query.filter_by(username=n).one() for n in ("ana", "bia"))
        from app.models import Follow
        db.session.add(Follow(follower_id=bia.id, followed_id=ana.id))
        db.session.commit()
        assert Activity.query.filter_by(kind="avaliou").count() == 1
    client.post("/sair")
    login(client, "bia")
    assert estrela in client.get("/feed").get_data(as_text=True)
