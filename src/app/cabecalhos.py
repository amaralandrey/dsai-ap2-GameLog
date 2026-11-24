"""Cabeçalhos de segurança de toda resposta (INC-10).

`CAPAS_HOSTS_PERMITIDOS` (config do app) é a lista única de hosts de capa: a CSP a usa no `img-src`, e o proxy de
capas (RTN-07, RTN-08) a usa para decidir o que buscar. Com o proxy ligado (`PROXY_CAPAS_ATIVO`), as capas externas
só ficam permitidas na prévia de `/admin/importar` (RTN-09).
"""
from flask import current_app, request

CAPAS_HOSTS_PERMITIDOS = (
    "cdn.cloudflare.steamstatic.com", "shared.cloudflare.steamstatic.com", "cdn.akamai.steamstatic.com",
    "shared.akamai.steamstatic.com", "shared.fastly.steamstatic.com", "steamcdn-a.akamaihd.net",
    "images.gog-statics.com", "images-1.gog-statics.com", "images-2.gog-statics.com", "images-3.gog-statics.com",
    "images-4.gog-statics.com", "media.rawg.io",
)
HSTS = "max-age=31536000"
PERMISSOES = "camera=(), microphone=(), geolocation=()"


def proxy_capas_ativo():
    return bool(current_app.config.get("PROXY_CAPAS_ATIVO"))


def _api(caminho):
    return caminho == "/api" or caminho.startswith("/api/")


def img_src(caminho):
    fontes = ["'self'", "data:"]
    if not proxy_capas_ativo() or caminho.startswith("/admin/importar"):
        fontes += [f"https://{host}" for host in current_app.config["CAPAS_HOSTS_PERMITIDOS"]]
    return " ".join(fontes)


def politica(caminho):
    return "; ".join((
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",  # atributos style= dos templates e a cor do avatar (CAD-08)
        f"img-src {img_src(caminho)}",
        "frame-ancestors 'none'",
    ))


def aplicar(resposta):
    """`after_request`: a API só ganha `nosniff`; as páginas HTML levam o conjunto todo."""
    resposta.headers["X-Content-Type-Options"] = "nosniff"
    if _api(request.path):
        return resposta
    if current_app.config["PRODUCAO"]:
        resposta.headers["Strict-Transport-Security"] = HSTS
    if resposta.mimetype == "text/html":
        resposta.headers["Content-Security-Policy"] = politica(request.path)
        resposta.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resposta.headers["Permissions-Policy"] = PERMISSOES
    return resposta
