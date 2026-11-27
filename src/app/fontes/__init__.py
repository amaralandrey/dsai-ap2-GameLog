import re
from contextlib import contextmanager
from urllib.parse import urlsplit

from sqlalchemy.orm import selectinload

from ..menores.classificacao import aplicar as aplicar_classificacao
from ..models import Developer, Game, Genre, JogoFonte, Platform, db
from ..util import slugify
from . import arquivo, base, gog, rawg, steam
from .normalizar import generos, ler_data, plataformas

FONTES = {"steam": steam, "arquivo": arquivo, "gog": gog, "rawg": rawg}  # nova fonte: módulo com listar(limite, termo)
HOSTS_LOJA = {
    "steam": {"store.steampowered.com"},
    "gog": {"www.gog.com"},
    "epic": {"store.epicgames.com", "www.epicgames.com"},
}
EPIC_PRODUTO = re.compile(r"/(?:store/[^/]+/product|(?:[^/]+/)?p)/([A-Za-z0-9-]+)(?:/|$)")  # formato antigo e novo
ID_NA_URL = {  # identificador de cada loja na URL do link (GOG-06)
    "steam": re.compile(r"/app/(\d+)(?:/|$)"),
    "gog": re.compile(r"/game/([a-z0-9_]+)(?:/|$)"),
    "epic": re.compile(r"/(?:p|product)/([A-Za-z0-9-]+)(?:/|$)"),
}


class Interrompido(RuntimeError):
    """O lote parou no meio (HTTP 429 persistente, GOG-11); `parcial` traz as contagens do que já foi gravado."""

    def __init__(self, motivo, parcial):
        super().__init__(motivo)
        self.motivo, self.parcial = motivo, parcial


def indisponivel(modulo):
    """Motivo de a fonte estar desligada (ex.: RAWG sem chave, GOG-04), ou None se ela pode rodar."""
    verificar = getattr(modulo, "indisponivel", None)
    return verificar() if callable(verificar) else None


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


def external_id_da_url(loja, url):
    """Id do jogo na loja a partir do link: app da Steam, slug da GOG ou da Epic; None se não reconhecer (GOG-06)."""
    try:
        partes = urlsplit((url or "").strip())
    except ValueError:
        return None
    m = ID_NA_URL[loja].search(partes.path) if partes.hostname in HOSTS_LOJA.get(loja, ()) else None
    return m.group(1) if m and partes.scheme in ("http", "https") else None


def jogo_por_fonte(fonte, external_id):
    """Identidade fonte + id externo lida de JogoFonte (FNT-06, IMP-04)."""
    v = JogoFonte.query.filter_by(fonte=fonte, external_id=str(external_id)).first()
    return v.game if v else None


def vincular(game, fonte, external_id, url=None):
    """Cria ou devolve o vínculo do jogo; None se o id já é de outro jogo (FNT-04). URL fora de FNT-07 não é
    gravada, mas o vínculo existe."""
    external_id = str(external_id)[:80]
    v = next((x for x in game.fontes if x.fonte == fonte), None)
    if v is None:
        v = JogoFonte.query.filter_by(fonte=fonte, external_id=external_id).first()
        if v is not None and v.game is not game:
            return None
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
    return [v for v in vinculos if v] + _vincular_origens(game, j.get("origens"))


def _vincular_origens(game, origens):
    """Outras fontes em que o item está (snapshot ← RAWG); id que já é de outro jogo não vira vínculo (SNP-06, SNP-07)."""
    vinculos = []
    for fonte, eid in origens or []:
        atual = next((v for v in game.fontes if v.fonte == fonte), None)
        if atual is None and fonte in FONTES and not JogoFonte.query.filter_by(fonte=fonte, external_id=str(eid)).first():
            atual = vincular(game, fonte, eid)
        if atual is not None:
            vinculos.append(atual)
    return vinculos


def vincular_lojas(game, lojas):
    """Links de loja vindos de outra fonte (RAWG): vínculo novo só se o id não for de outro jogo (GOG-06, MRG-02)."""
    vinculos = []
    for loja, url in lojas or []:
        eid = external_id_da_url(loja, url)
        atual = next((v for v in game.fontes if v.fonte == loja), None)
        if atual:  # já vinculado a essa loja: só completa o link que falta (FNT-07)
            atual.url_loja = atual.url_loja or normalizar_url_loja(loja, url)
            vinculos.append(atual)
        elif eid and not JogoFonte.query.filter_by(fonte=loja, external_id=eid).first():
            vinculos.append(vincular(game, loja, eid, url))
    return vinculos


def lancamento(j):
    """Data do item e se ela é só o ano; a fonte manda a data já lida ou o texto (GOG-09)."""
    data, aproximada = ler_data(j.get("release_date"))
    return data, aproximada or bool(j.get("data_aproximada"))


def vincular_existentes():
    """Todo jogo com `source` ganha o vínculo dela; idempotente, roda no seed (FNT-05)."""
    for game in Game.query.filter(Game.source.in_(list(FONTES))).options(selectinload(Game.fontes)):
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
    """Cria um jogo ou liga o item a um jogo que já existe (MRG-02).

    Devolve (status, jogo) com status 'ok', 'vinculado', 'duplicado' ou 'invalido'.
    """
    from ..catalogo_merge import assumir_vinculo, reconhecer, registrar_pendencia  # import tardio: usa FONTES

    data, aproximada = lancamento(j)
    if not j.get("title") or not data:
        return "invalido", None
    existente = jogo_por_fonte(fonte, j["external_id"])
    if existente:
        return "duplicado", existente
    decisao = reconhecer(fonte, j, j["title"], data)
    if decisao.acao == "repetido":
        return "duplicado", decisao.jogo
    if decisao.acao == "vincular":
        assumir_vinculo(decisao.jogo, fonte, j["external_id"])
        vincular_origem(decisao.jogo, fonte, j)
        vincular_lojas(decisao.jogo, j.get("lojas"))
        aplicar_classificacao(decisao.jogo, j.get("classificacao"), j.get("classificacao_origem") or fonte)
        return "vinculado", decisao.jogo
    slug = slugify(j["title"]) or j["external_id"]
    if Game.query.filter_by(slug=slug).first():
        slug = f"{slug}-{fonte}-{j['external_id']}"
    game = Game(
        title=j["title"][:150], slug=slug[:170], synopsis=j["synopsis"] or "Sem descrição.",
        release_date=data, data_aproximada=aproximada,
        developer=_obter(Developer, j["developer"] or "Desenvolvedora desconhecida"),
        genres=[_obter(Genre, g) for g in generos(j["genres"])],
        platforms=[_obter(Platform, p) for p in plataformas(j["platforms"])],
        source=fonte, external_id=j["external_id"], cover_url=j["cover_url"],
    )
    db.session.add(game)
    aplicar_classificacao(game, j.get("classificacao"), j.get("classificacao_origem") or fonte)
    vincular_origem(game, fonte, j)
    vincular_lojas(game, j.get("lojas"))
    if decisao.acao == "pendencia":
        db.session.flush()
        registrar_pendencia(game, decisao.jogo, decisao.semelhanca)  # MRG-03
    return "ok", game


def importar(fonte, limite=40, termo=None, modulo=None):
    """Devolve (importados, ignorados, falhas). `modulo` permite injetar uma fonte falsa nos testes.

    Item ligado a um jogo existente conta como importado (MRG-02). HTTP 429 persistente levanta Interrompido com as contagens do que já foi gravado (GOG-11).
    """
    ok = ign = erro = 0
    with registrada(fonte, modulo) as modulo:
        try:
            for j in modulo.listar(limite, termo):
                if "erro" in j:
                    erro += 1
                    continue
                try:
                    status, _ = importar_item(fonte, j)
                    db.session.commit()
                except Exception as exc:  # IMP-05: item problemático é pulado sem sujar a sessão
                    db.session.rollback()
                    if base.limitado(exc):
                        raise
                    erro += 1
                    continue
                novo = status in ("ok", "vinculado")
                ok, ign = ok + novo, ign + (not novo)
        except Exception as exc:
            if base.limitado(exc):
                raise Interrompido("rate-limit", (ok, ign, erro)) from exc
            raise
    return ok, ign, erro
