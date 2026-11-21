import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from app import create_app  # noqa: E402
from app.models import db  # noqa: E402


@pytest.fixture(autouse=True)
def sem_chave_rawg(monkeypatch):
    """Os testes nunca usam a chave real da RAWG, mesmo que ela esteja no ambiente de quem roda (GOG-07)."""
    monkeypatch.delenv("RAWG_API_KEY", raising=False)


@pytest.fixture
def app(tmp_path):
    app = create_app({"TESTING": True, "CSRF": False, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path}/t.db"})
    yield app
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()


def make_user(app, username="ana", email=None, password="senha1234", role="jogador"):
    from werkzeug.security import generate_password_hash

    from app.models import User

    with app.app_context():
        u = User(
            username=username,
            username_lower=username.lower(),
            email=email or f"{username}@ex.com",
            password_hash=generate_password_hash(password),
            display_name=username,
            role=role,
        )
        db.session.add(u)
        db.session.commit()
        return u.id


def login(client, username="ana", password="senha1234"):
    return client.post("/entrar", data={"email": f"{username}@ex.com", "password": password})


def make_game(app, title="Jogo Teste", genre="Ação", platform="PC", dev="Dev Um", year=2020):
    from datetime import date

    from app.models import Developer, Game, Genre, Platform
    from app.util import slugify

    with app.app_context():
        g = Genre.query.filter_by(name=genre).first() or Genre(name=genre, slug=slugify(genre))
        p = Platform.query.filter_by(name=platform).first() or Platform(name=platform, slug=slugify(platform))
        d = Developer.query.filter_by(name=dev).first() or Developer(name=dev, slug=slugify(dev))
        game = Game(title=title, slug=slugify(title), synopsis="Sinopse.", release_date=date(year, 5, 1),
                    developer=d, genres=[g], platforms=[p])
        db.session.add(game)
        db.session.commit()
        return game.slug


@pytest.fixture
def corrida(app):
    """Duplo envio: grava `sql` por outra conexão logo antes do primeiro flush, depois da checagem da rota."""
    from sqlalchemy import event, text
    from sqlalchemy.orm import Session

    pendentes = []

    def antes_do_flush(session, ctx, instancias):
        while pendentes:
            sql, params = pendentes.pop(0)
            with db.engine.begin() as conn:
                conn.execute(text(sql), params)

    event.listen(Session, "before_flush", antes_do_flush)
    yield lambda sql, **params: pendentes.append((sql, params))
    event.remove(Session, "before_flush", antes_do_flush)


class Resp(io.BytesIO):
    """Resposta falsa de urlopen: corpo JSON (ou bytes crus) usável com `with`."""

    def __init__(self, corpo):
        super().__init__(corpo if isinstance(corpo, bytes) else json.dumps(corpo).encode())

    def __exit__(self, *a):
        self.close()


def http(code):
    return HTTPError("https://x", code, "erro", {}, None)


@pytest.fixture
def api(monkeypatch):
    """Rede falsa das fontes por trecho de URL (o trecho mais longo que casar vale).

    Cada rota é um corpo, uma exceção ou uma tupla deles, consumida em ordem (o último item se repete).
    """
    from app.fontes import base

    rotas, pedidos = {}, []

    def urlopen(req, timeout):
        pedidos.append(req)
        trecho = max((t for t in rotas if t in req.full_url), key=len)
        r = rotas[trecho]
        if isinstance(r, tuple):
            r, rotas[trecho] = r[0], r[1:] or r
        if isinstance(r, Exception):
            raise r
        return Resp(r)

    monkeypatch.setattr(base, "urlopen", urlopen)
    monkeypatch.setattr(base, "esperar", lambda s: None)
    monkeypatch.delenv("FONTES_TIMEOUT", raising=False)
    return SimpleNamespace(rotas=rotas, pedidos=pedidos, urls=lambda: [p.full_url for p in pedidos])
