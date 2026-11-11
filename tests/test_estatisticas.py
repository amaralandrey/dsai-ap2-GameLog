"""Estatísticas globais (SPEC 2026-11-07-estatisticas), um teste por critério EST-01..06."""
import re

from sqlalchemy import event

from app.models import Game, Review, User, db, now
from conftest import login, make_game, make_user


def avalia(app, slug, notas, oculta=False):
    with app.app_context():
        game = Game.query.filter_by(slug=slug).one()
        for i, nota in enumerate(notas):
            nome = f"u{slug[:6]}{i}{'h' if oculta else ''}"
            u = User(username=nome, username_lower=nome, email=f"{nome}@ex.com", password_hash="x", display_name=nome)
            db.session.add(u)
            db.session.flush()
            db.session.add(Review(user_id=u.id, game_id=game.id, score=nota, hidden=oculta))
        db.session.commit()


def secao(html, titulo):
    return html.split(titulo, 1)[1].split("</section>", 1)[0]


def test_est01_rota_publica_e_link_no_rodape(app, client):
    for url in ("/estatisticas", "/stats"):
        assert client.get(url).status_code == 200
    make_user(app, "ana")
    login(client)
    html = client.get("/").get_data(as_text=True)
    assert '<footer><a href="/estatisticas">' in html
    assert client.get("/estatisticas").status_code == 200


def test_est02_contadores(app, client):
    a, b = make_game(app, "Alfa"), make_game(app, "Beta")
    make_game(app, "Removido")
    with app.app_context():
        Game.query.filter_by(slug="removido").one().removed_at = now()
        db.session.commit()
    avalia(app, a, [7, 8])
    avalia(app, b, [5])
    avalia(app, b, [1], oculta=True)
    html = client.get("/estatisticas").get_data(as_text=True)
    assert re.search(r"<strong>2</strong><span class=\"mut\">jogos no catálogo", html)
    assert re.search(r"<strong>4</strong><span class=\"mut\">jogadores cadastrados", html)
    assert re.search(r"<strong>3</strong><span class=\"mut\">avaliações escritas", html)  # ignora a oculta


def test_est03_cinco_mais_avaliados(app, client):
    for i, titulo in enumerate(["Um", "Dois", "Tres", "Quatro", "Cinco", "Seis"]):
        avalia(app, make_game(app, titulo), [6] * (6 - i))
    html = secao(client.get("/estatisticas").get_data(as_text=True), "Os 5 jogos mais avaliados")
    titulos = re.findall(r"<strong>(\w+)</strong>", html)
    assert titulos == ["Um", "Dois", "Tres", "Quatro", "Cinco"]


def test_est04_jogo_do_momento_exige_tres_avaliacoes(app, client):
    avalia(app, make_game(app, "Dez Isolado"), [10, 10])
    avalia(app, make_game(app, "Bom"), [9, 8, 9])
    avalia(app, make_game(app, "Ok"), [6, 7, 6, 7])
    html = secao(client.get("/estatisticas").get_data(as_text=True), "Jogo do Momento")
    assert "Bom" in html and "Dez Isolado" not in html
    assert 'aria-label="Nota 8.7 de 10"' in html


def test_est05_banco_vazio_nao_quebra(app, client):
    r = client.get("/estatisticas")
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "Ainda não há avaliações suficientes" in html and "Ainda não há avaliações." in html
    avalia(app, make_game(app), [10, 10])  # só um jogo com 2 avaliações: ainda sem Jogo do Momento
    html = client.get("/estatisticas").get_data(as_text=True)
    assert "Ainda não há avaliações suficientes" in html and "Jogo Teste" in html


def test_est06_poucas_consultas(app, client):
    for i in range(8):
        avalia(app, make_game(app, f"Jogo {i}", dev=f"Dev {i}"), [5, 6, 7])
    with app.app_context():
        contador = []

        def conta(*_):
            contador.append(1)

        event.listen(db.engine, "before_cursor_execute", conta)
        try:
            assert client.get("/estatisticas").status_code == 200
        finally:
            event.remove(db.engine, "before_cursor_execute", conta)
    # totais + top 5 + momento (+ sessão/usuário); nunca uma consulta por jogo
    assert len(contador) <= 4
