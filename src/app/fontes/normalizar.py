"""Nomes de gênero e plataforma, datas e descrições das fontes no formato do catálogo (GOG-02, GOG-08, GOG-09)."""
import html
import re
from datetime import date, datetime, timezone

from ..util import slugify

# chave: slug do nome como a fonte manda; valor: o nome já usado no catálogo (vindo da Steam em português)
GENEROS = {
    "action": "Ação", "adventure": "Aventura", "role-playing": "RPG", "role-playing-rpg": "RPG", "rpg": "RPG",
    "strategy": "Estratégia", "simulation": "Simulação", "simulator": "Simulação", "sports": "Esportes",
    "racing": "Corrida", "puzzle": "Puzzle", "platformer": "Plataforma", "shooter": "Tiro", "fighting": "Luta",
    "horror": "Terror", "music": "Música", "roguelike": "Roguelike", "rogue-like": "Roguelike",
    "massively-multiplayer": "Multijogador Massivo", "mmo": "Multijogador Massivo",
    "free-to-play": "Gratuitos para Jogar", "early-access": "Acesso Antecipado",
}
PLATAFORMAS = {
    "win": "Windows", "windows": "Windows", "pc": "Windows",
    "osx": "macOS", "mac": "macOS", "macos": "macOS", "mac-os": "macOS", "mac-os-x": "macOS",
    "linux": "Linux",
}
ANO = re.compile(r"\d{4}")
TIMESTAMP = re.compile(r"-?\d+(\.\d+)?")
ISO = re.compile(r"\d{4}-\d{2}-\d{2}([T ].+)?")
GOG = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})")  # formato do catálogo da GOG
BLOCO = re.compile(r"<\s*(br|/p|/div|/li|/h\d|hr)\b[^>]*>", re.I)
TAG = re.compile(r"<[^>]*>")


def _mapear(nomes, tabela):
    """Nomes do catálogo, sem repetir nem vazios; nome sem mapeamento é mantido (GOG-08)."""
    vistos, saida = set(), []
    for valor in nomes or []:
        nome = str(valor or "").strip()
        nome = tabela.get(slugify(nome), nome)[:80]
        if slugify(nome) and slugify(nome) not in vistos:
            vistos.add(slugify(nome))
            saida.append(nome)
    return saida


def generos(nomes):
    return _mapear(nomes, GENEROS)


def plataformas(nomes):
    return _mapear(nomes, PLATAFORMAS)


def ler_data(valor):
    """(data, aproximada) de ISO-8601, AAAA.MM.DD, timestamp Unix ou só o ano; formato desconhecido dá (None, False)."""
    if isinstance(valor, datetime):
        return valor.date(), False
    if isinstance(valor, date):
        return valor, False
    texto = "" if valor is None or isinstance(valor, bool) else str(valor).strip()
    try:
        if ANO.fullmatch(texto):  # só o ano: 1º de janeiro, marcada como aproximada
            return date(int(texto), 1, 1), True
        if TIMESTAMP.fullmatch(texto):
            return datetime.fromtimestamp(float(texto), timezone.utc).date(), False
        if ISO.fullmatch(texto):
            return datetime.fromisoformat(texto).date(), False
        if m := GOG.fullmatch(texto):
            return date(*map(int, m.groups())), False
    except (ValueError, OverflowError, OSError):  # 2015-02-30, ano 0000, timestamp fora do intervalo
        pass
    return None, False


def texto_puro(conteudo):
    """Descrição HTML como texto: sem tags, entidades resolvidas, parágrafos preservados (GOG-02)."""
    texto = html.unescape(TAG.sub("", BLOCO.sub("\n", conteudo or ""))).replace("\xa0", " ")
    linhas = (" ".join(linha.split()) for linha in texto.splitlines())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(linhas)).strip()
