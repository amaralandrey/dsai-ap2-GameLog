"""Gráficos SVG gerados no servidor, sem JavaScript nem dependência nova (RET-01..03, GLOBAL-14).

Todo texto que vem de dados passa por `escape`; não há `<script>`, atributos `on*`, `<foreignObject>` nem
referência externa. As cores são classes CSS (`serie-N`) e `currentColor`, para seguir o tema do `style.css`.
A mesma entrada sempre gera a mesma string.
"""
from html import escape
from math import floor, log10

from .chaveamento import BYE

LARGURA, ALTURA = 640, 260
MARGEM_ESQ, MARGEM_DIR, MARGEM_TOPO, MARGEM_BASE = 48, 12, 28, 40
MAX_SERIES_LINHA = 4


def numero(valor):
    """Inteiro sem casas; fração com uma casa e vírgula."""
    if float(valor).is_integer():
        return str(int(valor))
    return f"{valor:.1f}".replace(".", ",")


def escala(maximo):
    """Topo do eixo: o maior valor arredondado para cima em 1, 2 ou 5 × 10ⁿ (RET-03)."""
    if maximo <= 0:
        return 1
    potencia = 10 ** floor(log10(maximo))
    return next(passo * potencia for passo in (1, 2, 5, 10) if maximo <= passo * potencia)


def _validar(series):
    for _, valores in series:
        for v in valores:
            if v < 0:
                raise ValueError("valores negativos não são aceitos")


def _abrir(titulo, largura=LARGURA, altura=ALTURA, classe="grafico"):
    return [f'<svg xmlns="http://www.w3.org/2000/svg" class="{classe}" viewBox="0 0 {largura} {altura}" '
            f'role="img" aria-label="{escape(titulo)}">', f"<title>{escape(titulo)}</title>"]


def _sem_dados(partes, largura=LARGURA, altura=ALTURA):
    partes.append(f'<text x="{largura // 2}" y="{altura // 2}" text-anchor="middle" fill="currentColor">Sem dados</text>')
    partes.append("</svg>")
    return "".join(partes)


def _eixos(partes, topo, rotulos):
    """Linha de base, três marcas no eixo y e os rótulos do eixo x."""
    base_y, area_h = ALTURA - MARGEM_BASE, ALTURA - MARGEM_BASE - MARGEM_TOPO
    partes.append(f'<line class="eixo" x1="{MARGEM_ESQ}" y1="{base_y}" x2="{LARGURA - MARGEM_DIR}" y2="{base_y}" '
                  'stroke="currentColor"/>')
    for i in range(3):
        valor = topo * i / 2
        y = base_y - area_h * i / 2
        partes.append(f'<text class="marca" x="{MARGEM_ESQ - 6}" y="{y + 4:.1f}" text-anchor="end" '
                      f'fill="currentColor">{numero(valor)}</text>')
    passo = (LARGURA - MARGEM_ESQ - MARGEM_DIR) / max(len(rotulos), 1)
    for i, rotulo in enumerate(rotulos):
        x = MARGEM_ESQ + passo * (i + 0.5)
        partes.append(f'<text class="rotulo" x="{x:.1f}" y="{ALTURA - MARGEM_BASE + 18}" text-anchor="middle" '
                      f'fill="currentColor">{escape(str(rotulo))}</text>')
    return base_y, area_h, passo


def _y(valor, topo, base_y, area_h):
    return base_y - area_h * valor / topo


def barras(series, rotulos, titulo):
    """Barras agrupadas: uma barra por série em cada rótulo, cada uma com a dica "<rótulo>: <valor>"."""
    _validar(series)
    partes = _abrir(titulo)
    if not rotulos or not series or not any(valores for _, valores in series):
        return _sem_dados(partes)
    topo = escala(max((v for _, valores in series for v in valores), default=0))
    base_y, area_h, passo = _eixos(partes, topo, rotulos)
    largura_barra = passo * 0.8 / len(series)
    for s, (nome, valores) in enumerate(series):
        for i, valor in enumerate(valores):
            if valor <= 0:
                continue
            x = MARGEM_ESQ + passo * i + passo * 0.1 + largura_barra * s
            y = _y(valor, topo, base_y, area_h)
            dica = f"{rotulos[i]}: {numero(valor)}" if len(series) == 1 else f"{nome} — {rotulos[i]}: {numero(valor)}"
            partes.append(f'<rect class="serie-{s}" x="{x:.1f}" y="{y:.1f}" width="{largura_barra:.1f}" '
                          f'height="{base_y - y:.1f}"><title>{escape(dica)}</title></rect>')
    partes.append("</svg>")
    return "".join(partes)


def linha(series, rotulos, titulo):
    """Uma polilinha por série (até 4) e um ponto com dica em cada valor."""
    if len(series) > MAX_SERIES_LINHA:
        raise ValueError(f"no máximo {MAX_SERIES_LINHA} séries")
    _validar(series)
    partes = _abrir(titulo)
    if not rotulos or not series or not any(valores for _, valores in series):
        return _sem_dados(partes)
    topo = escala(max((v for _, valores in series for v in valores), default=0))
    base_y, area_h, passo = _eixos(partes, topo, rotulos)
    for s, (nome, valores) in enumerate(series):
        pontos = [(MARGEM_ESQ + passo * (i + 0.5), _y(v, topo, base_y, area_h)) for i, v in enumerate(valores)]
        if len(pontos) > 1:
            caminho = " ".join(f"{x:.1f},{y:.1f}" for x, y in pontos)
            partes.append(f'<polyline class="serie-{s}" fill="none" points="{caminho}"/>')
        for i, ((x, y), valor) in enumerate(zip(pontos, valores)):
            dica = f"{nome} — {rotulos[i]}: {numero(valor)}"
            partes.append(f'<circle class="serie-{s}" cx="{x:.1f}" cy="{y:.1f}" r="3.5">'
                          f"<title>{escape(dica)}</title></circle>")
    legenda_x = MARGEM_ESQ
    for s, (nome, _) in enumerate(series):
        partes.append(f'<rect class="serie-{s}" x="{legenda_x}" y="6" width="10" height="10"/>'
                      f'<text x="{legenda_x + 14}" y="15" fill="currentColor">{escape(str(nome))}</text>')
        legenda_x += 24 + 8 * len(str(nome))
    partes.append("</svg>")
    return "".join(partes)


def sparkline(valores, titulo, largura=160, altura=36):
    """Linha pequena, sem eixos, para caber ao lado de um número."""
    _validar([("", valores)])
    partes = _abrir(titulo, largura, altura, "sparkline")
    if not valores:
        return _sem_dados(partes, largura, altura)
    topo = max(valores) or 1
    passo = (largura - 4) / max(len(valores) - 1, 1)
    pontos = [(2 + passo * i, altura - 2 - (altura - 4) * v / topo) for i, v in enumerate(valores)]
    if len(pontos) == 1:
        partes.append(f'<circle class="serie-0" cx="{pontos[0][0]:.1f}" cy="{pontos[0][1]:.1f}" r="2"/>')
    else:
        caminho = " ".join(f"{x:.1f},{y:.1f}" for x, y in pontos)
        partes.append(f'<polyline class="serie-0" fill="none" points="{caminho}"/>')
    partes.append("</svg>")
    return "".join(partes)


CAIXA_L, CAIXA_A, COLUNA_L, LINHA_TOPO = 150, 40, 180, 30
NOMES_DAS_CHAVES = {"eliminacao": "Rodada", "vencedores": "Vencedores", "perdedores": "Perdedores"}


def _colunas_da_chave(chave):
    """As partidas agrupadas por rodada de cada chave, na ordem em que a chave as criou."""
    colunas = {}
    for p in chave.partidas.values():
        colunas.setdefault((p.chave, p.rodada), []).append(p)
    return list(colunas.values())


def _rotulo_da_coluna(p):
    if p.chave == "final":
        return "Reset" if p.dispensada_se else "Final"
    return f"{NOMES_DAS_CHAVES[p.chave]} {p.rodada}"


def chave(chave, titulo="Chave do torneio", nomes=None):
    """Uma coluna por rodada e uma caixa por partida, com o vencedor em destaque (CHV-13).

    `nomes` traduz o id do participante para o nome exibido; sem ele, aparece o próprio id.
    """
    def nome(x):
        if x is None:
            return "a definir"
        return "bye" if x is BYE else str((nomes or {}).get(x, x))
    colunas = _colunas_da_chave(chave)
    largura, altura = COLUNA_L * len(colunas), LINHA_TOPO + max(len(c) for c in colunas) * (CAIXA_A + 16)
    partes = _abrir(titulo, largura, altura, "grafico chave")
    for c, partidas in enumerate(colunas):
        x = c * COLUNA_L + 8
        rotulo = escape(_rotulo_da_coluna(partidas[0]))
        partes.append(f'<text class="rotulo" x="{x}" y="16" fill="currentColor">{rotulo}</text>')
        faixa = (altura - LINHA_TOPO) / len(partidas)
        for i, p in enumerate(partidas):
            y = LINHA_TOPO + faixa * i + (faixa - CAIXA_A) / 2
            placar = f" ({p.placar_a} × {p.placar_b})" if p.jogada else ""
            dica = escape(f"{p.id}: {nome(p.a)} × {nome(p.b)}{placar}")
            partes.append(f'<g class="partida"><title>{dica}</title><rect x="{x}" y="{y:.1f}" width="{CAIXA_L}" '
                          f'height="{CAIXA_A}" fill="none" stroke="currentColor"/>')
            for lado, deslocamento in ((p.a, 0), (p.b, CAIXA_A / 2)):
                venceu = p.decidida and lado == p.vencedor
                if venceu:
                    partes.append(f'<rect class="serie-0" x="{x}" y="{y + deslocamento:.1f}" width="4" '
                                  f'height="{CAIXA_A / 2:.1f}"/>')
                peso = ' class="vencedor" font-weight="bold"' if venceu else ""
                partes.append(f'<text x="{x + 10}" y="{y + deslocamento + 14:.1f}" fill="currentColor"{peso}>'
                              f"{escape(nome(lado))}</text>")
            partes.append("</g>")
    partes.append("</svg>")
    return "".join(partes)
