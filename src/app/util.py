import hashlib
from functools import wraps
from urllib.parse import urlsplit

from flask import abort, current_app, g, redirect, request, session, url_for

from .models import User, db

PER_PAGE = 20


class Page:
    def __init__(self, items, page, pages, total):
        self.items, self.page, self.pages, self.total = items, page, pages, total

    @property
    def has_prev(self):
        return self.page > 1

    @property
    def has_next(self):
        return self.page < self.pages


def paginate(query, page, per_page=PER_PAGE):
    """Página fora do intervalo cai na última válida, nunca erro (GLOBAL-04, BUS-04)."""
    total = query.order_by(None).count()
    pages = max(1, -(-total // per_page))
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    page = min(max(page, 1), pages)
    items = query.limit(per_page).offset((page - 1) * per_page).all()
    return Page(items, page, pages, total)


def current_user():
    if "user" not in g:
        uid = session.get("uid")
        # cookie de um banco anterior (o Render recria o banco a cada boot) não vale: os ids se repetem
        mesmo_banco = session.get("inst") == current_app.config["INSTANCIA"]
        u = db.session.get(User, uid) if uid and mesmo_banco else None
        # logout incrementa session_version e invalida cookies antigos no servidor (CAD-05)
        g.user = u if u and u.session_version == session.get("sv") and u.suspensa_em is None else None
    return g.user


# Rotas que respondem mesmo com a idade pendente (IDA-04, IDA-05)
LIVRES_DA_IDADE = ("/sair", "/privacidade", "/termos", "/conta/privacidade", "/conta/excluir", "/conta/pendente",
                   "/conta/nascimento", "/conta/idade")


def _pendencia_de_idade(u):
    """Para onde vai quem ainda não pode usar o site logado: conta antiga sem data ou à espera do responsável."""
    if request.path in LIVRES_DA_IDADE or request.path.startswith(tuple(f"{p}/" for p in LIVRES_DA_IDADE)):
        return None  # caminho exato ou seguido de "/": `/termos-de-clube` não é livre
    if u.nascimento is None:
        return redirect(url_for("idade.nascimento", next=_destino()))
    from .menores.idade import aguarda_responsavel  # menores.idade importa util

    if aguarda_responsavel(u):
        return redirect("/conta/pendente")
    return None


def _destino():
    """Para onde voltar depois de um desvio (GLOBAL-02): o próprio GET, ou a página de origem de um POST."""
    if request.method == "GET":
        return request.full_path.rstrip("?")
    ref = urlsplit(request.referrer or "")  # só volta a uma página deste host
    return (ref.path or "/") + (f"?{ref.query}" if ref.query else "") if ref.netloc == request.host else "/"


def _login_redirect():
    return redirect(url_for("auth.login", next=_destino()))


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        u = current_user()
        if not u:
            return _login_redirect()
        return _pendencia_de_idade(u) or f(*a, **kw)

    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        u = current_user()
        if not u:
            return _login_redirect()
        if not u.is_admin:
            abort(403)
        return _pendencia_de_idade(u) or f(*a, **kw)

    return wrapper


def safe_next(url):
    """Só aceita caminho local, evita open redirect.

    Navegadores tratam `\\` como `/` e descartam TAB e quebra de linha, então `/\\evil.com` vira `//evil.com`.
    """
    if not url or not url.startswith("/") or url.startswith("//"):
        return "/"
    if "\\" in url or any(ord(c) < 32 or ord(c) == 127 for c in url):
        return "/"
    partes = urlsplit(url)
    return url if partes.scheme == "" and partes.netloc == "" else "/"


def slugify(text):
    import re
    import unicodedata

    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def fold(text):
    """Minúsculas sem acento, para busca (BUS-01)."""
    import unicodedata

    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()


def cover_svg(slug, title=""):
    """Capa determinística a partir do slug (CAT-06)."""
    h = hashlib.sha256(slug.encode()).digest()
    h1, h2 = h[0] * 360 // 256, h[1] * 360 // 256
    shapes = "".join(
        f'<circle cx="{h[2 + i] * 300 // 256}" cy="{h[8 + i] * 400 // 256}" r="{20 + h[14 + i] % 60}" '
        f'fill="hsl({(h1 + h[20 + i]) % 360},70%,65%)" opacity=".35"/>'
        for i in range(5)
    )
    initials = "".join(w[0] for w in title.split()[:2]).upper() or slug[:2].upper()
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 400">'
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="hsl({h1},65%,35%)"/><stop offset="1" stop-color="hsl({h2},65%,20%)"/>'
        f'</linearGradient></defs><rect width="300" height="400" fill="url(#g)"/>{shapes}'
        f'<text x="150" y="225" font-size="90" font-family="sans-serif" font-weight="700" '
        f'fill="#fff" fill-opacity=".85" text-anchor="middle">{initials}</text></svg>'
    )


def avatar_style(username):
    """Cor derivada do nome de usuário (CAD-08)."""
    return f"hsl({hashlib.sha256(username.lower().encode()).digest()[0] * 360 // 256},55%,40%)"
