"""Acesso HTTP comum às fontes: GET de JSON com novas tentativas e User-Agent do GameLog (FNT-01, FNT-02, FNT-10)."""
import json
import os
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

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
