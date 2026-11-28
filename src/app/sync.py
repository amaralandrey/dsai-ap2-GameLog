"""Sincronização idempotente do catálogo com fontes externas (executada por cron, fora do processo web)."""
from .catalogo_merge import assumir_vinculo, reconhecer, registrar_pendencia
from .fontes import FONTES, Interrompido, base, jogo_por_fonte, lancamento, registrada, vincular_lojas, vincular_origem
from .fontes.normalizar import generos, plataformas
from .menores.classificacao import aplicar
from .models import Developer, Game, Genre, Platform, db, now
from .util import slugify

SEM_SINOPSE = "Sem descrição."
SEM_DESENVOLVEDORA = "Desenvolvedora desconhecida"


class FonteIndisponivel(RuntimeError):
    """A fonte falhou ao entregar os itens; não há como sincronizar."""


def _obter(model, nome):
    obj = model.query.filter_by(slug=slugify(nome)).first()
    if not obj:
        obj = model(name=nome[:80], slug=slugify(nome))
        db.session.add(obj)
        db.session.flush()
    return obj


def _slug_livre(titulo, fonte, external_id):
    """Slug só é escolhido na criação; jogos existentes mantêm a URL (nunca muda)."""
    base = (slugify(titulo) or slugify(external_id) or "jogo")[:150]
    slug = base
    if Game.query.filter_by(slug=slug).first():
        sufixo = f"{slugify(fonte) or 'fonte'}-{slugify(external_id) or 'id'}"
        slug, n = f"{base}-{sufixo}"[:170], 1
        while Game.query.filter_by(slug=slug).first():
            n += 1
            slug = f"{base}-{sufixo}-{n}"[:170]
    return slug


def _criar(fonte, j, titulo, data, external_id):
    game = Game(
        title=titulo[:150],
        slug=_slug_livre(titulo, fonte, external_id),
        synopsis=(j.get("synopsis") or "").strip() or SEM_SINOPSE,
        release_date=data[0],
        data_aproximada=data[1],
        developer=_obter(Developer, (j.get("developer") or "").strip()[:80] or SEM_DESENVOLVEDORA),
        genres=[_obter(Genre, nome) for nome in generos(j.get("genres"))],
        platforms=[_obter(Platform, nome) for nome in plataformas(j.get("platforms"))],
        source=fonte,
        external_id=external_id,
        cover_url=(j.get("cover_url") or "").strip() or None,
    )
    db.session.add(game)
    db.session.flush()
    return game


def _atualizar(game, j, titulo, data):
    """Atualiza apenas os campos mutáveis; devolve True se algum valor mudou."""
    mudou = False

    if game.title != titulo[:150]:
        game.title = titulo[:150]
        mudou = True
    if (game.release_date, game.data_aproximada) != data:
        game.release_date, game.data_aproximada = data
        mudou = True

    if "synopsis" in j:
        sinopse = (j.get("synopsis") or "").strip() or SEM_SINOPSE
        if game.synopsis != sinopse:
            game.synopsis = sinopse
            mudou = True

    if "developer" in j:
        nome = (j.get("developer") or "").strip()[:80] or SEM_DESENVOLVEDORA
        if not game.developer or slugify(game.developer.name) != slugify(nome):
            game.developer = _obter(Developer, nome)
            mudou = True

    if "genres" in j:
        nomes = generos(j.get("genres"))
        if {slugify(g.name) for g in game.genres} != {slugify(n) for n in nomes}:
            game.genres = [_obter(Genre, n) for n in nomes]
            mudou = True

    if "platforms" in j:
        nomes = plataformas(j.get("platforms"))
        if {slugify(p.name) for p in game.platforms} != {slugify(n) for n in nomes}:
            game.platforms = [_obter(Platform, n) for n in nomes]
            mudou = True

    if "cover_url" in j:
        capa = (j.get("cover_url") or "").strip() or None
        if game.cover_url != capa:
            game.cover_url = capa
            mudou = True

    return mudou


def dona_dos_dados(game, fonte):
    """Só a fonte de origem do jogo (ou a loja que ela copia) atualiza os metadados; as fontes ligadas
    depois por MRG-02 só tocam no próprio vínculo, para duas fontes não se sobrescreverem a cada execução."""
    return fonte in (game.source, getattr(FONTES.get(game.source), "LOJA_DE_ORIGEM", None))


def _novo_ou_vinculado(fonte, j, titulo, data, external_id):
    """Item sem vínculo: liga a um jogo existente (conta como atualizado) ou cria, com pendência (MRG-02, MRG-03)."""
    decisao = reconhecer(fonte, {**j, "external_id": external_id}, titulo, data[0])
    if decisao.acao == "repetido":
        return decisao.jogo, "ignorado"
    if decisao.acao == "vincular":
        assumir_vinculo(decisao.jogo, fonte, external_id)
        return decisao.jogo, "atualizado"
    game = _criar(fonte, j, titulo, data, external_id)
    if decisao.acao == "pendencia":
        registrar_pendencia(game, decisao.jogo, decisao.semelhanca)
    return game, "novo"


def _sincronizar_item(fonte, j):
    if "erro" in j:
        return "falha"

    external_id = str(j.get("external_id") or "").strip()
    titulo = str(j.get("title") or "").strip()
    data = lancamento(j)
    if not external_id or not titulo or data[0] is None:
        return "ignorado"

    game = jogo_por_fonte(fonte, external_id)
    status = "ignorado"
    if game is not None and game.mesclado_em_id:
        return "ignorado"  # o vínculo ficou no jogo mesclado: nada muda (MRG-08)
    if game is None:
        game, status = _novo_ou_vinculado(fonte, j, titulo, data, external_id)
        if status == "ignorado":
            return status
    elif dona_dos_dados(game, fonte) and _atualizar(game, j, titulo, data):
        status = "atualizado"
    if aplicar(game, j.get("classificacao"), j.get("classificacao_origem") or fonte) and status == "ignorado":
        status = "atualizado"
    vinculos = vincular_origem(game, fonte, {**j, "external_id": external_id}) + vincular_lojas(game, j.get("lojas"))
    for v in vinculos:
        v.ultima_sincronizacao = now()  # vínculos tocados (FNT-06)
    db.session.commit()
    return status


def _motivo(exc):
    return str(exc).strip() or exc.__class__.__name__


def sincronizar(fonte, limite=40, termo=None, modulo=None):
    """Devolve (novos, atualizados, ignorados, falhas); `modulo` injeta a fonte falsa nos testes.

    Falha da fonte (listar/iteração) levanta FonteIndisponivel, e HTTP 429 persistente levanta Interrompido
    com o que já foi gravado (GOG-11); falha de um item é isolada com rollback, conta como falha e a
    sincronização continua.
    """
    with registrada(fonte, modulo) as modulo:
        return _sincronizar(fonte, modulo, limite, termo)


def _falha_da_fonte(exc, parcial):
    if base.limitado(exc):
        return Interrompido("rate-limit", parcial)
    return FonteIndisponivel(_motivo(exc))


def _sincronizar(fonte, modulo, limite, termo):
    try:
        itens = iter(modulo.listar(limite, termo))
    except Exception as exc:
        raise _falha_da_fonte(exc, (0, 0, 0, 0)) from exc

    novos = atualizados = ignorados = falhas = 0
    while True:
        try:
            j = next(itens)
        except StopIteration:
            break
        except Exception as exc:
            raise _falha_da_fonte(exc, (novos, atualizados, ignorados, falhas)) from exc

        try:
            status = _sincronizar_item(fonte, j)
        except Exception:
            db.session.rollback()
            falhas += 1
            continue

        if status == "novo":
            novos += 1
        elif status == "atualizado":
            atualizados += 1
        elif status == "ignorado":
            ignorados += 1
        else:
            falhas += 1

    return novos, atualizados, ignorados, falhas
