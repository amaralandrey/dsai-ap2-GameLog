import os
import secrets
from urllib.parse import urlencode

import sqlite3

from flask import Flask, abort, render_template, request, session

from sqlalchemy import event
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Engine

from .cabecalhos import CAPAS_HOSTS_PERMITIDOS, aplicar as cabecalhos_de_seguranca
from .models import Instancia, db
from .util import avatar_style, current_user, fold

BLUEPRINTS = []  # preenchido por register_blueprints(); um módulo por spec
CSRF_ISENTAS = {"/jobs/ciclo"}  # autenticadas por Bearer, sem cookie (JOB-11)
CSRF_ISENTOS_PREFIXOS = ("/api/v2/",)


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY"),
        SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL", "sqlite:///gamelog.db"),
        CSRF=True,
        PRODUCAO=os.environ.get("GAMELOG_PRODUCAO") == "1",  # definida no render.yaml
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_HTTPONLY=True,
        CAPAS_HOSTS_PERMITIDOS=CAPAS_HOSTS_PERMITIDOS,  # INC-10 e o proxy de capas (RTN-08)
        CAPAS_CACHE_DIR=os.environ.get("CAPAS_CACHE_DIR") or os.path.join(app.instance_path, "capas"),
        PROXY_CAPAS_ATIVO=True,
    )
    app.config.update(config or {})
    if app.config["PRODUCAO"]:
        if not app.config["SECRET_KEY"]:  # chave aleatória por worker derrubaria as sessões entre os 2 workers
            raise RuntimeError("SECRET_KEY não configurada em produção")
        app.config["SESSION_COOKIE_SECURE"] = True
    app.config["SECRET_KEY"] = app.config["SECRET_KEY"] or secrets.token_hex(32)
    db.init_app(app)

    @event.listens_for(Engine, "connect")
    def _sqlite_fold(conn, _):  # busca sem acento/caixa (BUS-01)
        if isinstance(conn, sqlite3.Connection):
            conn.create_function("fold", 1, fold, deterministic=True)
            conn.execute("PRAGMA foreign_keys=ON")  # ON DELETE CASCADE dos comentários (COM-06)
            conn.execute("PRAGMA busy_timeout=5000")  # 2 workers do gunicorn escrevem no mesmo arquivo
            if conn.execute("PRAGMA database_list").fetchone()[2]:  # vazio em :memory:
                conn.execute("PRAGMA journal_mode=WAL")

    @app.before_request
    def csrf_protect():
        isenta = request.path in CSRF_ISENTAS or request.path.startswith(CSRF_ISENTOS_PREFIXOS)
        if request.method == "POST" and app.config["CSRF"] and not isenta:
            token = session.get("csrf")
            if not token or not secrets.compare_digest(token.encode(), request.form.get("csrf_token", "").encode()):
                abort(400, "Token CSRF inválido")

    app.after_request(cabecalhos_de_seguranca)  # INC-10
    app.jinja_env.globals["avatar_style"] = avatar_style

    def page_url(n):  # preserva filtros multi-valor na paginação (BUS-05)
        args = [(k, v) for k, v in request.args.items(multi=True) if k != "page"] + [("page", n)]
        return f"{request.path}?{urlencode(args)}"

    app.jinja_env.globals["page_url"] = page_url
    app.jinja_env.filters["dictupdate"] = lambda d, u: {**d, **u}
    from .diario import formatar_minutos, selo_horas

    app.jinja_env.filters["horas"] = formatar_minutos  # "12h 30min" (DIA-06, DIA-09)
    app.jinja_env.filters["selo_horas"] = selo_horas
    app.jinja_env.filters["zip"] = lambda a, *outros: list(zip(a, *outros))
    from .precos import dia, reais

    app.jinja_env.filters["reais"] = reais  # "R$ 1.234,56" (PRC-08)
    app.jinja_env.filters["dia"] = dia
    from .tempo import data_hora

    app.jinja_env.filters["data_hora"] = data_hora
    from .menores.politica import e_menor, pode, visibilidade_padrao

    app.jinja_env.globals.update(pode=pode, e_menor=e_menor, visibilidade_padrao=visibilidade_padrao)  # PMN-01: as telas perguntam à mesma política

    @app.context_processor
    def inject():
        def csrf_token():
            session.setdefault("csrf", secrets.token_hex(16))
            return session["csrf"]

        me = current_user()
        cabecalho = None
        if me is not None and not request.path.startswith("/static"):
            from .notificacoes import cabecalho_do_usuario

            cabecalho = cabecalho_do_usuario(me.id)  # NTF-10: uma consulta para o cabeçalho
        return {"me": me, "csrf_token": csrf_token, "cabecalho": cabecalho}

    def erro(code, msg):
        def handler(e):
            if request.path == "/api" or request.path.startswith("/api/"):  # API-08: JSON, nunca HTML
                from .api import erro as erro_json
                return erro_json(code, msg)
            return render_template("erro.html", code=code, msg=msg), code
        return handler

    for code, msg in {400: "Requisição inválida", 403: "Acesso negado", 404: "Página não encontrada",
                      405: "Método não permitido", 500: "Erro interno"}.items():
        app.register_error_handler(code, erro(code, msg))

    from . import cli, routes

    routes.register(app)
    cli.register(app)
    with app.app_context():
        db.create_all()
        # os 2 workers e o `flask seed` disputam a criação: quem chegar depois lê o token gravado
        db.session.execute(insert(Instancia).values(id=1, token=secrets.token_hex(16)).on_conflict_do_nothing())
        db.session.commit()
        app.config["INSTANCIA"] = db.session.get(Instancia, 1).token
    return app
