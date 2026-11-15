import re
from contextlib import contextmanager
from urllib.parse import urlsplit

from ..models import Developer, Game, Genre, JogoFonte, Platform, db
from ..util import slugify
from . import arquivo, steam

FONTES = {"steam": steam, "arquivo": arquivo}  # nova fonte: um módulo com listar(limite, termo) e uma linha aqui
HOSTS_LOJA = {
    "steam": {"store.steampowered.com"},
    "gog": {"www.gog.com"},
    "epic": {"store.epicgames.com", "www.epicgames.com"},
}
EPIC_PRODUTO = re.compile(r"/(?:store/[^/]+/product|(?:[^/]+/)?p)/([A-Za-z0-9-]+)(?:/|$)")  # formato antigo e novo


def normalizar_url_loja(fonte, url):
    """URL https de um host da loja do vínculo; Epic vira store.epicgames.com/p/<slug>; o resto, None (FNT-07)."""
    try:
        partes = urlsplit((url or "").strip())
        porta = partes.port
    except ValueError:
        return None
    if partes.scheme != "https" or partes.hostname not in HOSTS_LOJA.get(fonte, ()) or partes.username or porta:
        return None
    if fonte == "epic":
        m = EPIC_PRODUTO.match(partes.path)
        return f"https://store.epicgames.com/p/{m.group(1)}" if m else None
    return url.strip()[:500]


def jogo_por_fonte(fonte, external_id):
    """Identidade fonte + id externo lida de JogoFonte (FNT-06, IMP-04)."""
    v = JogoFonte.query.filter_by(fonte=fonte, external_id=str(external_id)).first()
    return v.game if v else None


def vincular(game, fonte, external_id, url=None):
    """Cria ou devolve o vínculo; URL fora de FNT-07 não é gravada, mas o vínculo existe."""
    external_id = str(external_id)[:80]
    v = next((x for x in game.fontes if x.fonte == fonte), None)
    if v is None:
        v = JogoFonte.query.filter_by(fonte=fonte, external_id=external_id).first()
    if v is None:
        v = JogoFonte(game=game, fonte=fonte, external_id=external_id)
        db.session.add(v)
    url = normalizar_url_loja(fonte, url)
    if url:
        v.url_loja = url
    return v


def _link(modulo, external_id):
    modelo = getattr(modulo, "URL_LOJA", None)
    return modelo.format(external_id) if modelo else None


def vincular_origem(game, fonte, j, modulo=None):
    """Vínculo da fonte de origem e, se a fonte copia uma loja (arquivo ← Steam), o dessa loja (FNT-05)."""
    modulo = modulo or FONTES.get(fonte)
    eid = str(j["external_id"])
    vinculos = [vincular(game, fonte, eid, j.get("url_loja") or _link(modulo, eid))]
    loja = getattr(modulo, "LOJA_DE_ORIGEM", None)
    if loja:
        vinculos.append(vincular(game, loja, eid, _link(FONTES.get(loja), eid)))
    return vinculos


def vincular_existentes():
    """Todo jogo com `source` ganha o vínculo dela; idempotente, roda no seed (FNT-05)."""
    for game in Game.query.filter(Game.source.in_(list(FONTES))):
        if not any(v.fonte == game.source for v in game.fontes):
            vincular_origem(game, game.source, {"external_id": game.external_id})
    db.session.commit()


@contextmanager
def registrada(fonte, modulo):
    """Fonte injetada (testes, IMP-02) conta como registrada enquanto roda, para o vínculo valer (FNT-04)."""
    nova = modulo is not None and fonte not in FONTES
    if nova:
        FONTES[fonte] = modulo
    try:
        yield modulo or FONTES[fonte]
    finally:
        if nova:
            FONTES.pop(fonte, None)


def _obter(model, nome):
    obj = model.query.filter_by(slug=slugify(nome)).first()
    if not obj:
        obj = model(name=nome[:80], slug=slugify(nome))
        db.session.add(obj)
        db.session.flush()
    return obj


def importar_item(fonte, j):
    """Cria um jogo. Devolve (status, jogo) com status 'ok', 'duplicado' ou 'invalido'."""
    if not j.get("title") or not j.get("release_date"):
        return "invalido", None
    existente = jogo_por_fonte(fonte, j["external_id"])
    if existente:
        return "duplicado", existente
    slug = slugify(j["title"]) or j["external_id"]
    if Game.query.filter_by(slug=slug).first():
        slug = f"{slug}-{fonte}-{j['external_id']}"
    game = Game(
        title=j["title"][:150], slug=slug[:170], synopsis=j["synopsis"] or "Sem descrição.",
        release_date=j["release_date"], developer=_obter(Developer, j["developer"] or "Desenvolvedora desconhecida"),
        genres=[_obter(Genre, g) for g in j["genres"]], platforms=[_obter(Platform, p) for p in j["platforms"]],
        source=fonte, external_id=j["external_id"], cover_url=j["cover_url"],
    )
    db.session.add(game)
    vincular_origem(game, fonte, j)
    return "ok", game


def importar(fonte, limite=40, termo=None, modulo=None):
    """Devolve (importados, ignorados, falhas). `modulo` permite injetar uma fonte falsa nos testes."""
    ok = ign = erro = 0
    with registrada(fonte, modulo) as modulo:
        for j in modulo.listar(limite, termo):
            if "erro" in j:
                erro += 1
                continue
            status, _ = importar_item(fonte, j)
            db.session.commit()
            ok, ign = ok + (status == "ok"), ign + (status != "ok")
    return ok, ign, erro
