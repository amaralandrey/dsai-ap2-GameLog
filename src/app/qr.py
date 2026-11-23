"""QR Code em SVG gerado no servidor, só com a biblioteca padrão (CTA-06, GLOBAL-11).

Modo byte, correção de erro nível M, versões 1 a 10 (até 213 bytes): cobre o URI `otpauth://` do 2FA.
Segue a ISO/IEC 18004: Reed-Solomon em GF(256), blocos intercalados, padrões fixos e a máscara de menor penalidade.
"""
# nível M, por versão: (códigos de correção por bloco, número de blocos)
BLOCOS_M = {1: (10, 1), 2: (16, 1), 3: (26, 1), 4: (18, 2), 5: (24, 2), 6: (16, 4), 7: (18, 4), 8: (22, 4),
            9: (22, 5), 10: (26, 5)}
NIVEL_M = 0  # bits do nível M na informação de formato
MASCARAS = (
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
)


class TextoLongo(ValueError):
    """O texto não cabe na versão 10, nível M."""


# ---------- Reed-Solomon ----------
def multiplicar(x, y):
    """Produto em GF(2^8) com o polinômio 0x11D."""
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def divisor(grau):
    """Coeficientes do gerador de grau `grau`, sem o termo líder."""
    resultado, raiz = [0] * (grau - 1) + [1], 1
    for _ in range(grau):
        for j in range(grau):
            resultado[j] = multiplicar(resultado[j], raiz)
            if j + 1 < grau:
                resultado[j] ^= resultado[j + 1]
        raiz = multiplicar(raiz, 2)
    return resultado


def correcao(dados, gerador):
    """Resto da divisão dos dados pelo gerador: os códigos de correção do bloco."""
    resto = [0] * len(gerador)
    for byte in dados:
        fator = byte ^ resto.pop(0)
        resto.append(0)
        for i, coef in enumerate(gerador):
            resto[i] ^= multiplicar(coef, fator)
    return resto


# ---------- dados ----------
def modulos_de_dados(versao):
    """Módulos livres para dados e correção numa versão."""
    total = (16 * versao + 128) * versao + 64
    if versao >= 2:
        n = versao // 7 + 2
        total -= (25 * n - 10) * n - 55
        if versao >= 7:
            total -= 36
    return total


def capacidade(versao):
    ecc, blocos = BLOCOS_M[versao]
    return modulos_de_dados(versao) // 8 - ecc * blocos


def escolher_versao(dados):
    for versao in BLOCOS_M:
        bits_contagem = 8 if versao < 10 else 16
        if 4 + bits_contagem + 8 * len(dados) <= capacidade(versao) * 8:
            return versao
    raise TextoLongo(f"{len(dados)} bytes não cabem num QR de versão 10")


def codificar(dados, versao):
    """Bits do modo byte, com terminador e preenchimento, agrupados em bytes."""
    bits = [0, 1, 0, 0] + _bits(len(dados), 8 if versao < 10 else 16)
    for byte in dados:
        bits += _bits(byte, 8)
    limite = capacidade(versao) * 8
    bits += [0] * min(4, limite - len(bits))
    bits += [0] * (-len(bits) % 8)
    palavras = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    for i in range(limite // 8 - len(palavras)):
        palavras.append(0xEC if i % 2 == 0 else 0x11)
    return palavras


def _bits(valor, n):
    return [(valor >> i) & 1 for i in reversed(range(n))]


def intercalar(palavras, versao):
    """Divide em blocos, calcula a correção de cada um e intercala (blocos curtos primeiro)."""
    ecc, nblocos = BLOCOS_M[versao]
    total = modulos_de_dados(versao) // 8
    curtos, tamanho_curto = nblocos - total % nblocos, total // nblocos
    gerador, blocos, k = divisor(ecc), [], 0
    for i in range(nblocos):
        dados = palavras[k:k + tamanho_curto - ecc + (0 if i < curtos else 1)]
        k += len(dados)
        blocos.append((dados, correcao(dados, gerador)))
    saida = []
    for i in range(tamanho_curto - ecc + 1):
        saida += [dados[i] for dados, _ in blocos if i < len(dados)]
    for i in range(ecc):
        saida += [c[i] for _, c in blocos]
    return saida


# ---------- matriz ----------
class Matriz:
    def __init__(self, versao):
        self.versao, self.lado = versao, versao * 4 + 17
        self.modulos = [[False] * self.lado for _ in range(self.lado)]
        self.fixo = [[False] * self.lado for _ in range(self.lado)]

    def marcar(self, x, y, escuro):
        self.modulos[y][x], self.fixo[y][x] = escuro, True

    def padroes(self):
        lado = self.lado
        for i in range(lado):
            self.marcar(6, i, i % 2 == 0)
            self.marcar(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (lado - 4, 3), (3, lado - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    if 0 <= cx + dx < lado and 0 <= cy + dy < lado:
                        self.marcar(cx + dx, cy + dy, max(abs(dx), abs(dy)) not in (2, 4))
        pos = posicoes_alinhamento(self.versao)
        cantos = {(0, 0), (0, len(pos) - 1), (len(pos) - 1, 0)}
        for i, cx in enumerate(pos):
            for j, cy in enumerate(pos):
                if (i, j) not in cantos:
                    for dy in range(-2, 3):
                        for dx in range(-2, 3):
                            self.marcar(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)
        self.formato(0)
        if self.versao >= 7:
            bits = informacao_versao(self.versao)
            for i in range(18):
                a, b, escuro = lado - 11 + i % 3, i // 3, (bits >> i) & 1 == 1
                self.marcar(a, b, escuro)
                self.marcar(b, a, escuro)

    def formato(self, mascara):
        bits, lado = informacao_formato(mascara), self.lado
        bit = [(bits >> i) & 1 == 1 for i in range(15)]
        for i in range(6):
            self.marcar(8, i, bit[i])
        self.marcar(8, 7, bit[6])
        self.marcar(8, 8, bit[7])
        self.marcar(7, 8, bit[8])
        for i in range(9, 15):
            self.marcar(14 - i, 8, bit[i])
        for i in range(8):
            self.marcar(lado - 1 - i, 8, bit[i])
        for i in range(8, 15):
            self.marcar(8, lado - 15 + i, bit[i])
        self.marcar(8, lado - 8, True)  # módulo escuro fixo

    def preencher(self, palavras):
        """Coloca os bits em zigue-zague, de duas em duas colunas, da direita para a esquerda."""
        lado, i, total = self.lado, 0, len(palavras) * 8
        direita = lado - 1
        while direita >= 1:
            if direita == 6:
                direita = 5
            for vert in range(lado):
                for j in range(2):
                    x = direita - j
                    subindo = (direita + 1) & 2 == 0
                    y = lado - 1 - vert if subindo else vert
                    if not self.fixo[y][x] and i < total:
                        self.modulos[y][x] = (palavras[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            direita -= 2

    def mascarar(self, mascara):
        regra = MASCARAS[mascara]
        for y in range(self.lado):
            for x in range(self.lado):
                if not self.fixo[y][x] and regra(x, y):
                    self.modulos[y][x] = not self.modulos[y][x]


def posicoes_alinhamento(versao):
    if versao == 1:
        return []
    n, lado = versao // 7 + 2, versao * 4 + 17
    passo = (versao * 8 + n * 3 + 5) // (n * 4 - 4) * 2
    return sorted([lado - 7 - i * passo for i in range(n - 1)] + [6])


def informacao_formato(mascara):
    """15 bits: nível e máscara, com BCH(15,5) e a máscara 0x5412."""
    dados = NIVEL_M << 3 | mascara
    resto = dados
    for _ in range(10):
        resto = (resto << 1) ^ ((resto >> 9) * 0x537)
    return (dados << 10 | resto) ^ 0x5412


def informacao_versao(versao):
    """18 bits da versão (7 em diante), com BCH(18,6)."""
    resto = versao
    for _ in range(12):
        resto = (resto << 1) ^ ((resto >> 11) * 0x1F25)
    return versao << 12 | resto


# ---------- penalidade (escolha da máscara) ----------
PADRAO_LOCALIZADOR = ([True, False, True, True, True, False, True, False, False, False, False],
                      [False, False, False, False, True, False, True, True, True, False, True])


def penalidade(modulos):
    lado, pontos = len(modulos), 0
    linhas = modulos + [list(col) for col in zip(*modulos)]
    for linha in linhas:
        corrida = 1
        for a, b in zip(linha, linha[1:]):
            if a == b:
                corrida += 1
            else:
                pontos += corrida - 2 if corrida >= 5 else 0
                corrida = 1
        pontos += corrida - 2 if corrida >= 5 else 0
        for i in range(len(linha) - 10):
            if linha[i:i + 11] in PADRAO_LOCALIZADOR:
                pontos += 40
    for y in range(lado - 1):
        for x in range(lado - 1):
            if modulos[y][x] == modulos[y][x + 1] == modulos[y + 1][x] == modulos[y + 1][x + 1]:
                pontos += 3
    escuros = sum(map(sum, modulos))
    return pontos + abs(escuros * 20 - lado * lado * 10) // (lado * lado) * 10


def matriz(texto):
    """Linhas de booleanos (True = escuro) do QR de `texto`."""
    dados = texto.encode("utf-8")
    versao = escolher_versao(dados)
    palavras = intercalar(codificar(dados, versao), versao)
    melhor = None
    for mascara in range(8):
        m = Matriz(versao)
        m.padroes()
        m.preencher(palavras)
        m.mascarar(mascara)
        m.formato(mascara)
        nota = penalidade(m.modulos)
        if melhor is None or nota < melhor[0]:
            melhor = (nota, m.modulos)
    return melhor[1]


def svg(texto, modulo=6, margem=4):
    """SVG com um caminho só para os módulos escuros e a zona de silêncio de 4 módulos."""
    linhas = matriz(texto)
    lado = len(linhas) + 2 * margem
    caminho = "".join(f"M{x + margem},{y + margem}h1v1h-1z" for y, linha in enumerate(linhas)
                      for x, escuro in enumerate(linha) if escuro)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {lado} {lado}" width="{lado * modulo}" '
            f'height="{lado * modulo}" shape-rendering="crispEdges" role="img" aria-label="QR Code">'
            f'<rect width="{lado}" height="{lado}" fill="#fff"/><path d="{caminho}" fill="#000"/></svg>')
