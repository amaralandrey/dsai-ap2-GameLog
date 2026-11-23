"""Proxy de capas (RTN-07..10): o navegador do visitante só fala com o GameLog, que baixa a capa uma vez e guarda.

Só URLs `https` dos hosts de `CAPAS_HOSTS_PERMITIDOS` são buscadas, sem seguir redirecionamento para outro host;
qualquer recusa ou falha responde a capa SVG e não grava no cache, então a próxima requisição tenta de novo.
"""
import hashlib
import os
import tempfile
from urllib.parse import urlsplit

from flask import Blueprint, Response, abort, current_app

from ..fontes import base
from ..models import Game
from ..util import cover_svg

bp = Blueprint("capas", __name__)

TIPOS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
LIMITE = 2 * 1024 * 1024
MAX_REDIRECIONAMENTOS = 3
CACHE = "public, max-age=86400"


class CapaRecusada(Exception):
    """A origem não serve: host, esquema, redirecionamento, tipo ou tamanho fora da regra (RTN-08)."""


def permitida(url, host=None):
    """`https`, host da lista e, num redirecionamento, o mesmo host da URL original."""
    partes = urlsplit(url or "")
    try:
        porta = partes.port
    except ValueError:
        return False
    return (partes.scheme == "https" and partes.hostname in current_app.config["CAPAS_HOSTS_PERMITIDOS"]
            and porta in (None, 443) and (host is None or partes.hostname == host))


def baixar(url):
    """(Content-Type, corpo) da capa; levanta CapaRecusada ou o erro de rede."""
    if not permitida(url):
        raise CapaRecusada("host ou esquema fora da lista")
    host = urlsplit(url).hostname
    for _ in range(MAX_REDIRECIONAMENTOS + 1):
        try:
            tipo, corpo = base.baixar(url, LIMITE)
        except base.Redirecionado as exc:
            if not permitida(exc.destino, host):
                raise CapaRecusada("redirecionamento para outro host") from None
            url = exc.destino
            continue
        except base.ArquivoInvalido as exc:
            raise CapaRecusada(f"arquivo grande demais: {exc}") from None
        if tipo not in TIPOS:
            raise CapaRecusada(f"tipo {tipo or 'vazio'}")
        return tipo, corpo
    raise CapaRecusada("redirecionamentos demais")


def _arquivo(url):
    """Caminho no cache, sem extensão; o nome vem da URL de origem, então trocar a capa troca o arquivo."""
    return os.path.join(current_app.config["CAPAS_CACHE_DIR"], hashlib.sha256(url.encode()).hexdigest())


def do_cache(url):
    caminho = _arquivo(url)
    for tipo, ext in TIPOS.items():
        if os.path.exists(f"{caminho}.{ext}"):
            with open(f"{caminho}.{ext}", "rb") as f:
                return tipo, f.read()
    return None


def guardar(url, tipo, corpo):
    """Grava num temporário e renomeia: dois workers baixando a mesma capa nunca deixam arquivo pela metade."""
    pasta = current_app.config["CAPAS_CACHE_DIR"]
    os.makedirs(pasta, exist_ok=True)
    fd, temporario = tempfile.mkstemp(dir=pasta, suffix=".parcial")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(corpo)
        os.replace(temporario, f"{_arquivo(url)}.{TIPOS[tipo]}")
    except OSError:
        os.unlink(temporario)
        raise


def svg(game, cache=CACHE):
    return Response(cover_svg(game.slug, game.title), mimetype="image/svg+xml", headers={"Cache-Control": cache})


@bp.route("/capas/<slug>")
def capa(slug):
    game = Game.query.filter_by(slug=slug).first()
    if game is None or game.removed_at is not None:
        abort(404)
    if not game.cover_url:
        return svg(game)
    achado = do_cache(game.cover_url)
    if achado is None:
        try:
            achado = baixar(game.cover_url)
        except (CapaRecusada, OSError) as exc:  # OSError: rede, timeout e HTTPError
            current_app.logger.warning("capa de %s não baixada: %s", slug, exc)
            return svg(game, cache="no-cache")
        try:
            guardar(game.cover_url, *achado)
        except OSError as exc:  # disco cheio ou sem permissão: serve agora e tenta gravar na próxima
            current_app.logger.warning("capa de %s não gravada no cache: %s", slug, exc)
    tipo, corpo = achado
    return Response(corpo, mimetype=tipo, headers={"Cache-Control": CACHE})
