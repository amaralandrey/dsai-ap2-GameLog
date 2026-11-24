"""Acesso HTTP comum às fontes: GET de JSON com novas tentativas e User-Agent do GameLog (FNT-01, FNT-02, FNT-10)."""
import json
import os
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from flask import has_request_context

VERSAO = "2.0"
REPOSITORIO = "https://github.com/amaralandrey/dsai-ap2-GameLog"
USER_AGENT = f"GameLog/{VERSAO} (+{REPOSITORIO})"  # nunca imita navegador (FNT-02, issue #9)
TENTATIVAS = 3
TIMEOUT_PADRAO = 15
TIMEOUT_WEB = 5  # dentro de uma requisição do admin: uma tentativa só, curta (FNT-10)

esperar = time.sleep  # os testes trocam por uma função que não dorme


def na_web():
    return has_request_context()


def timeout():
    """Segundos por tentativa: 5 na web; fora dela, FONTES_TIMEOUT (padrão 15; valor inválido volta ao padrão)."""
    if na_web():
        return TIMEOUT_WEB
    try:
        valor = float(os.environ.get("FONTES_TIMEOUT", TIMEOUT_PADRAO))
    except ValueError:
        return TIMEOUT_PADRAO
    return valor if valor > 0 else TIMEOUT_PADRAO


def requisicao(url, method="GET", headers=None):
    """Request com o User-Agent do GameLog; um User-Agent vindo de `headers` é ignorado."""
    extras = {k: v for k, v in (headers or {}).items() if k.lower() != "user-agent"}
    return Request(url, method=method, headers={**extras, "User-Agent": USER_AGENT})


def limitado(exc):
    """HTTP 429 que sobrou depois das tentativas: a fonte pede para parar (GOG-11)."""
    return isinstance(exc, HTTPError) and exc.code == 429


def _repetir(exc):
    """Erro de rede, 429 e 5xx valem nova tentativa; outro 4xx é resposta definitiva."""
    if isinstance(exc, HTTPError):
        return exc.code == 429 or exc.code >= 500
    return True


def get_json(url, params=None, headers=None):
    """JSON da URL. Esgotadas as tentativas, levanta o último erro; JSON inválido levanta ValueError na hora."""
    if params:
        url = f"{url}{'&' if '?' in url else '?'}{urlencode(params)}"
    tentativas = 1 if na_web() else TENTATIVAS
    for n in range(tentativas):
        try:
            with urlopen(requisicao(url, headers=headers), timeout=timeout()) as r:
                return json.load(r)
        except OSError as exc:  # HTTPError, URLError e timeout são OSError
            if n == tentativas - 1 or not _repetir(exc):
                raise
        esperar(2 ** n)  # 1 s, 2 s, ...


class Redirecionado(Exception):
    """Resposta 3xx; quem baixa decide se o destino vale (RTN-08)."""

    def __init__(self, destino):
        super().__init__(f"redirecionado para {destino}")
        self.destino = destino


class ArquivoInvalido(ValueError):
    """Corpo acima do limite."""


class _NaoSegue(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # o urllib levanta o HTTPError do 3xx em vez de seguir


abrir_sem_seguir = build_opener(_NaoSegue).open  # os testes trocam por uma resposta falsa


def baixar(url, limite):
    """(Content-Type, corpo) de um GET binário, numa tentativa só e sem seguir redirecionamento.

    Leva só o User-Agent do GameLog: nada do visitante (cookie, IP, cabeçalhos) é repassado (RTN-10).
    """
    try:
        with abrir_sem_seguir(requisicao(url), timeout=timeout()) as r:
            tipo = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            tamanho = r.headers.get("Content-Length")
            if tamanho and tamanho.isdigit() and int(tamanho) > limite:
                raise ArquivoInvalido(f"{tamanho} bytes")
            corpo = r.read(limite + 1)
    except HTTPError as exc:
        if 300 <= exc.code < 400 and exc.headers.get("Location"):
            raise Redirecionado(urljoin(url, exc.headers["Location"])) from None
        raise
    if len(corpo) > limite:
        raise ArquivoInvalido(f"mais de {limite} bytes")
    return tipo, corpo


def paginar(pagina, limite):
    """Ids de páginas sucessivas: `pagina(n)` devolve (ids, há_mais); para no limite ou quando a fonte acaba."""
    ids, n = [], 1
    while len(ids) < limite:
        novos, mais = pagina(n)
        ids += [i for i in novos if i not in ids]
        if not (novos and mais):
            break
        n += 1
    return ids[:limite]


def percorrer(ids, detalhe):
    """Detalhe de cada id; falha de um item vira {'erro': ...} (IMP-05), mas 429 persistente para tudo (GOG-11)."""
    for eid in ids:
        try:
            j = detalhe(eid)
        except Exception as exc:
            if limitado(exc):
                raise
            yield {"erro": f"{eid}: {exc}"}
            continue
        if j:
            yield j
