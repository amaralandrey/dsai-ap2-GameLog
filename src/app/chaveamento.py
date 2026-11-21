"""Algoritmos de chaveamento dos torneios (CHV-01..14): funções puras, sem Flask nem banco.

Os participantes chegam na ordem das sementes (o 1º é o melhor) e os resultados como tuplas
`(partida_id, vencedor | None, placar_a, placar_b)`. Toda chave é reconstruída só a partir dessas duas
entradas, então a mesma entrada sempre gera a mesma chave: nada aqui sorteia, exceto `sementes(...,
aleatorio=n)`, que usa um `random.Random(n)` reprodutível.

Identificadores das partidas:
- eliminação simples: `E<rodada>-<n>`;
- eliminação dupla: `V<rodada>-<n>` (vencedores), `P<rodada>-<n>` (perdedores), `F1` e `F2` (final e reset);
- pontos corridos: `L<rodada>-<n>`;
- suíço: `S<rodada>-<n>`.
"""
import math
import random
import re
from dataclasses import dataclass, field

MIN_PARTICIPANTES, MAX_ELIMINACAO = 2, 128


class _Bye:
    """Lugar vazio da chave: quem enfrenta um bye avança sem jogar (CHV-02)."""

    def __repr__(self):
        return "BYE"


BYE = _Bye()


class ResultadoInvalido(ValueError):
    pass


class CorrecaoBloqueada(ValueError):
    def __init__(self, partidas):
        super().__init__(f"partidas seguintes já jogadas: {', '.join(partidas)}")
        self.partidas = partidas


@dataclass
class Partida:
    id: str
    rodada: int
    chave: str
    a: object = None  # None: ainda não definido; BYE: lugar vazio
    b: object = None
    origem_a: tuple = None  # ("vencedor" | "perdedor", partida_id)
    origem_b: tuple = None
    vencedor: object = None
    placar_a: int = None
    placar_b: int = None
    jogada: bool = False  # tem resultado registrado; bye e reset dispensado não contam como jogo

    @property
    def decidida(self):
        return self.vencedor is not None

    @property
    def perdedor(self):
        if not self.decidida:
            return None
        return self.b if self.vencedor == self.a else self.a


@dataclass
class Chave:
    formato: str
    participantes: list
    partidas: dict = field(default_factory=dict)  # id -> Partida, na ordem de criação

    def adicionar(self, partida):
        self.partidas[partida.id] = partida
        return partida


# ---------- sementes (CHV-11) ----------
def sementes(ids, pontuacao, aleatorio=None):
    """Ordem das sementes: maior pontuação primeiro, id como desempate, sem pontuação no fim por id.

    Com `aleatorio=n`, embaralha de forma reprodutível; o torneio guarda `n`.
    """
    if aleatorio is not None:
        ordem = sorted(ids)
        random.Random(aleatorio).shuffle(ordem)
        return ordem
    com = sorted((i for i in ids if pontuacao.get(i) is not None), key=lambda i: (-pontuacao[i], i))
    return com + sorted(i for i in ids if pontuacao.get(i) is None)


def _validar(participantes, maximo=None):
    if len(participantes) < MIN_PARTICIPANTES or (maximo and len(participantes) > maximo):
        raise ValueError(f"o formato aceita de {MIN_PARTICIPANTES} a {maximo or 'N'} participantes")
    if len(set(participantes)) != len(participantes):
        raise ValueError("participante repetido")


def _potencia_de_2(n):
    tamanho = 1
    while tamanho < n:
        tamanho *= 2
    return tamanho


def ordem_padrao(tamanho):
    """Sementes da 1ª rodada, em pares: 1×N, depois o 2 do outro lado, de modo que 1 e 2 só se vejam na final."""
    ordem = [1]
    while len(ordem) < tamanho:
        soma = 2 * len(ordem) + 1
        ordem = [s for semente in ordem for s in (semente, soma - semente)]
    return ordem


# ---------- avanço e correção (CHV-08, CHV-09, CHV-12) ----------
def _lugar(chave, origem):
    """Quem ocupa o lugar que vem de `origem`, ou None se a partida de origem ainda não terminou."""
    lado, pid = origem
    partida = chave.partidas[pid]
    if not partida.decidida:
        return None
    return partida.vencedor if lado == "vencedor" else partida.perdedor


def _resolver(chave):
    """Preenche os lugares vindos de partidas decididas e decide as partidas com bye, até parar de mudar."""
    mudou = True
    while mudou:
        mudou = False
        for p in chave.partidas.values():
            if p.decidida:
                continue
            for lado in ("a", "b"):
                origem = getattr(p, f"origem_{lado}")
                if getattr(p, lado) is None and origem is not None and (quem := _lugar(chave, origem)) is not None:
                    setattr(p, lado, quem)
                    mudou = True
            if p.a is not None and p.b is not None and BYE in (p.a, p.b):
                p.vencedor = p.b if p.a is BYE else p.a  # bye: decidida na hora, sem jogo; dois byes dão BYE
                mudou = True
            elif p.id == "F2" and p.a is not None and p.b is not None and _reset_dispensado(chave):
                p.vencedor = chave.partidas["F1"].vencedor  # o campeão da chave de vencedores já ganhou a final
                mudou = True


def _reset_dispensado(chave):
    f1 = chave.partidas["F1"]
    return f1.decidida and f1.jogada and f1.vencedor == f1.a


def _conferir_vencedor(p, vencedor, empate_permitido):
    if vencedor is None and not empate_permitido:
        raise ResultadoInvalido("eliminação não aceita empate: informe o vencedor")
    if vencedor is not None and vencedor not in (p.a, p.b):
        raise ResultadoInvalido(f"o vencedor da partida {p.id} precisa ser um dos dois participantes")


def _aplicar(p, resultado, empate_permitido):
    """Registra o resultado na partida, que precisa ter os dois participantes e ainda não ter resultado."""
    _, vencedor, placar_a, placar_b = resultado
    if p.decidida or p.jogada:
        raise ResultadoInvalido(f"a partida {p.id} já foi decidida")
    if p.a is None or p.b is None:
        raise ResultadoInvalido(f"a partida {p.id} ainda não tem os dois participantes")
    _conferir_vencedor(p, vencedor, empate_permitido)
    p.vencedor, p.placar_a, p.placar_b, p.jogada = vencedor, placar_a, placar_b, True


def avancar(chave, resultado):
    """Registra o resultado e leva vencedor (e perdedor, na dupla) às partidas seguintes (CHV-08, CHV-10)."""
    p = chave.partidas.get(resultado[0])
    if p is None:
        raise ResultadoInvalido(f"partida desconhecida: {resultado[0]}")
    _aplicar(p, resultado, empate_permitido=False)  # uma Chave só existe nos formatos de eliminação
    _resolver(chave)
    return chave


def _dependentes(chave, pid):
    """Partidas que recebem alguém de `pid`, direta ou indiretamente, na ordem da chave."""
    alcancadas, fila = set(), [pid]
    while fila:
        atual = fila.pop()
        for p in chave.partidas.values():
            if p.id not in alcancadas and atual in {o[1] for o in (p.origem_a, p.origem_b) if o}:
                alcancadas.add(p.id)
                fila.append(p.id)
    if "F2" in chave.partidas and "F1" in alcancadas | {pid}:
        alcancadas.add("F2")  # o reset depende do resultado da final, não só de quem a jogou
    return [p for p in chave.partidas if p in alcancadas]


def corrigir(chave, partida_id, novo_resultado):
    """Refaz o resultado de uma partida jogada e desfaz o avanço que dependia dela (CHV-09)."""
    p = chave.partidas.get(partida_id)
    if p is None or not p.jogada:
        raise ResultadoInvalido(f"a partida {partida_id} não tem resultado para corrigir")
    _conferir_vencedor(p, novo_resultado[1], empate_permitido=False)  # antes de apagar o resultado confirmado
    seguintes = _dependentes(chave, partida_id)
    jogadas = [pid for pid in seguintes if chave.partidas[pid].jogada]
    if jogadas:
        raise CorrecaoBloqueada(jogadas)
    for pid in seguintes:
        s = chave.partidas[pid]
        s.vencedor = s.placar_a = s.placar_b = None
        if s.origem_a:
            s.a = None
        if s.origem_b:
            s.b = None
    p.vencedor = p.placar_a = p.placar_b = None
    p.jogada = False
    return avancar(chave, (partida_id, *novo_resultado[1:]))


def campeao(chave):
    """O vencedor quando a chave termina; None antes disso (CHV-12)."""
    final = chave.partidas["F2"] if "F2" in chave.partidas else list(chave.partidas.values())[-1]
    return final.vencedor if final.decidida and final.vencedor is not BYE else None


def proximas_partidas(chave):
    """Partidas com os dois participantes definidos e sem resultado (CHV-12)."""
    return [p for p in chave.partidas.values()
            if not p.decidida and p.a not in (None, BYE) and p.b not in (None, BYE)]


# ---------- eliminação simples (CHV-02) ----------
def _primeira_rodada(chave, participantes, prefixo, nome_da_chave):
    tamanho = _potencia_de_2(len(participantes))
    ordem = ordem_padrao(tamanho)

    def vaga(semente):
        return participantes[semente - 1] if semente <= len(participantes) else BYE
    for i in range(tamanho // 2):
        chave.adicionar(Partida(f"{prefixo}1-{i + 1}", 1, nome_da_chave, a=vaga(ordem[2 * i]), b=vaga(ordem[2 * i + 1])))
    return tamanho


def _rodadas_seguintes(chave, tamanho, prefixo, nome_da_chave):
    rodada, partidas = 2, tamanho // 4
    while partidas >= 1:
        for i in range(partidas):
            chave.adicionar(Partida(f"{prefixo}{rodada}-{i + 1}", rodada, nome_da_chave,
                                    origem_a=("vencedor", f"{prefixo}{rodada - 1}-{2 * i + 1}"),
                                    origem_b=("vencedor", f"{prefixo}{rodada - 1}-{2 * i + 2}")))
        rodada, partidas = rodada + 1, partidas // 2
    return rodada - 1


def eliminacao_simples(participantes):
    """Chave de 2 a 128 participantes, completada com byes para as melhores sementes."""
    _validar(participantes, MAX_ELIMINACAO)
    chave = Chave("eliminacao_simples", list(participantes))
    tamanho = _primeira_rodada(chave, participantes, "E", "eliminacao")
    _rodadas_seguintes(chave, tamanho, "E", "eliminacao")
    _resolver(chave)
    return chave


# ---------- eliminação dupla (CHV-03) ----------
def eliminacao_dupla(participantes):
    """Chave de vencedores (`V`) e de perdedores (`P`), final `F1` e reset `F2`.

    Posições fixas na chave de perdedores, com T = tamanho da chave (potência de 2) e R = log2(T):
    - `P1-i` recebe os perdedores de `V1-(2i−1)` e `V1-(2i)`;
    - nas rodadas pares `P(2k)-i` (k = 1..R−1), o lado A é o vencedor de `P(2k−1)-i` e o lado B é o
      perdedor da rodada k+1 dos vencedores. Com k ímpar, a ordem é invertida (`V(k+1)-(M−i+1)`, com M
      partidas na rodada); com k par, B vem de `V(k+1)-i`. A inversão evita que `P2-i` junte de novo os
      dois jogadores dos mesmos jogos da 1ª rodada;
    - nas rodadas ímpares `P(2k+1)-i`, jogam os vencedores de `P(2k)-(2i−1)` e `P(2k)-(2i)`.

    A final `F1` é entre o campeão dos vencedores (A) e o dos perdedores (B). Se B vencer, os dois têm
    uma derrota e jogam o reset `F2`; se A vencer, o `F2` é dispensado. Ninguém sai antes da 2ª derrota.
    """
    _validar(participantes, MAX_ELIMINACAO)
    chave = Chave("eliminacao_dupla", list(participantes))
    tamanho = _primeira_rodada(chave, participantes, "V", "vencedores")
    rodadas_v = _rodadas_seguintes(chave, tamanho, "V", "vencedores")
    if rodadas_v == 1:  # dois participantes: o perdedor da única partida já é o campeão dos perdedores
        campeao_p = ("perdedor", "V1-1")
    else:
        for i in range(tamanho // 4):
            chave.adicionar(Partida(f"P1-{i + 1}", 1, "perdedores", origem_a=("perdedor", f"V1-{2 * i + 1}"),
                                    origem_b=("perdedor", f"V1-{2 * i + 2}")))
        partidas = tamanho // 4
        for k in range(1, rodadas_v):
            par = 2 * k
            for i in range(partidas):
                j = partidas - i if k % 2 else i + 1
                chave.adicionar(Partida(f"P{par}-{i + 1}", par, "perdedores", origem_a=("vencedor", f"P{par - 1}-{i + 1}"),
                                        origem_b=("perdedor", f"V{k + 1}-{j}")))
            if k < rodadas_v - 1:
                partidas //= 2
                for i in range(partidas):
                    chave.adicionar(Partida(f"P{par + 1}-{i + 1}", par + 1, "perdedores",
                                            origem_a=("vencedor", f"P{par}-{2 * i + 1}"),
                                            origem_b=("vencedor", f"P{par}-{2 * i + 2}")))
        campeao_p = ("vencedor", f"P{2 * (rodadas_v - 1)}-1")
    chave.adicionar(Partida("F1", rodadas_v + 1, "final", origem_a=("vencedor", f"V{rodadas_v}-1"), origem_b=campeao_p))
    chave.adicionar(Partida("F2", rodadas_v + 2, "final", origem_a=("vencedor", "F1"), origem_b=("perdedor", "F1")))
    _resolver(chave)
    return chave


# ---------- pontos corridos (CHV-04) ----------
def pontos_corridos(participantes, ida_e_volta=False):
    """Todas as rodadas pelo método do círculo; com número ímpar, cada um folga uma rodada por turno.

    A folga aparece como partida contra BYE, sem pontos. Na volta, o mando (lado A) se inverte.
    """
    _validar(participantes)
    roda = list(participantes) + ([BYE] if len(participantes) % 2 else [])
    m = len(roda)
    ida = []
    for r in range(1, m):
        for i in range(m // 2):
            a, b = roda[i], roda[m - 1 - i]
            if i == 0 and r % 2 == 0:
                a, b = b, a  # alterna o mando de quem fica fixo na roda
            if a is BYE:
                a, b = b, a  # a folga fica sempre no lado B
            ida.append((r, a, b))
        roda = [roda[0], roda[-1]] + roda[1:-1]
    volta = [(r + m - 1, a, b) if b is BYE else (r + m - 1, b, a) for r, a, b in ida]
    turnos = [ida, volta] if ida_e_volta else [ida]
    partidas, contador = [], {}
    for turno in turnos:
        for r, a, b in turno:
            contador[r] = contador.get(r, 0) + 1
            p = Partida(f"L{r}-{contador[r]}", r, "liga", a=a, b=b)
            if b is BYE:
                p.vencedor = a  # folga: decidida, sem jogo e sem pontos
            partidas.append(p)
    return partidas


# ---------- suíço (CHV-05) ----------
PONTOS = {"vitoria": 3, "empate": 1, "derrota": 0}
LIMITE_DE_TENTATIVAS = 200_000


def rodadas_suico(n):
    """Número padrão de rodadas: ⌈log2 N⌉."""
    return max(1, math.ceil(math.log2(n)))


def _pontos(partidas, com_bye):
    pontos = {}
    for p in partidas:
        if p.b is BYE:
            if com_bye:
                pontos[p.a] = pontos.get(p.a, 0) + PONTOS["vitoria"]
        elif p.jogada:
            for quem in (p.a, p.b):
                ganho = "empate" if p.vencedor is None else "vitoria" if p.vencedor == quem else "derrota"
                pontos[quem] = pontos.get(quem, 0) + PONTOS[ganho]
    return pontos


def _parear(fila, ja_jogaram):
    """Pares sem revanche, do topo para baixo, com volta atrás; None se não houver (ou se demorar demais)."""
    tentativas = [0]

    def buscar(restantes):
        tentativas[0] += 1
        if tentativas[0] > LIMITE_DE_TENTATIVAS:
            return None
        if not restantes:
            return []
        x = restantes[0]
        for j in range(1, len(restantes)):
            if frozenset((x, restantes[j])) in ja_jogaram:
                continue
            resto = buscar(restantes[1:j] + restantes[j + 1:])
            if resto is not None:
                return [(x, restantes[j])] + resto
        return None
    return buscar(fila)


def _emparelhar_suico(participantes, anteriores, rodada):
    pontos = _pontos(anteriores, com_bye=True)
    fila = sorted(participantes, key=lambda x: (-pontos.get(x, 0), participantes.index(x)))
    partidas = []
    if len(fila) % 2:  # bye para o de menor pontuação que ainda não teve bye
        tiveram = {p.a for p in anteriores if p.b is BYE}
        folga = next((x for x in reversed(fila) if x not in tiveram), fila[-1])
        fila.remove(folga)
        partidas.append(Partida("", rodada, "suico", a=folga, b=BYE, vencedor=folga))
    ja_jogaram = {frozenset((p.a, p.b)) for p in anteriores if p.b is not BYE}
    pares = _parear(fila, ja_jogaram) or list(zip(fila[::2], fila[1::2]))  # sem alternativa: revanche
    jogos = [Partida("", rodada, "suico", a=a, b=b) for a, b in pares]
    for n, p in enumerate(jogos + partidas, start=1):
        p.id = f"S{rodada}-{n}"
    return jogos + partidas


def _rodada_suica(pid):
    """Rodada de um id `S<rodada>-<n>`; qualquer outro id é de uma partida desconhecida."""
    if not (m := re.fullmatch(r"S([1-9]\d*)-[1-9]\d*", str(pid))):
        raise ResultadoInvalido(f"partida desconhecida: {pid}")
    return int(m[1])


def _rodadas_do_suico(participantes, resultados, ate):
    """Partidas das rodadas 1..`ate`, com os resultados aplicados; cada rodada exige a anterior completa.

    Resultados de rodadas depois de `ate` ficam de fora. Os das rodadas 1..`ate` precisam bater com uma
    partida gerada, e cada partida aceita um resultado só.
    """
    por_id = {}
    for r in resultados:
        _rodada_suica(r[0])
        if r[0] in por_id:
            raise ResultadoInvalido(f"a partida {r[0]} já foi decidida")
        por_id[r[0]] = r
    todas = []
    for rodada in range(1, ate + 1):
        if rodada > 1 and (faltam := [p.id for p in todas if p.rodada == rodada - 1 and p.b is not BYE
                                      and p.id not in por_id]):
            raise ResultadoInvalido(f"faltam resultados da rodada {rodada - 1}: {', '.join(faltam)}")
        for p in _emparelhar_suico(participantes, todas, rodada):
            if p.id in por_id and p.b is not BYE:
                _aplicar(p, por_id[p.id], empate_permitido=True)
            todas.append(p)
    jogaveis = {p.id for p in todas if p.b is not BYE}
    if desconhecidas := [pid for pid in por_id if _rodada_suica(pid) <= ate and pid not in jogaveis]:
        raise ResultadoInvalido(f"partida desconhecida: {', '.join(desconhecidas)}")
    return todas


def suico(participantes, resultados, rodada):
    """Partidas da `rodada`: mesma pontuação junta, sementes como desempate, sem revanche se houver saída."""
    _validar(participantes)
    return [p for p in _rodadas_do_suico(participantes, resultados, rodada) if p.rodada == rodada]


# ---------- classificação (CHV-06, CHV-07) ----------
CRITERIOS = ("pontos", "confronto_direto", "saldo", "buchholz", "semente")


@dataclass
class Linha:
    posicao: int
    participante: object
    pontos: int
    vitorias: int
    empates: int
    derrotas: int
    saldo: int
    buchholz: int
    criterio: str  # o critério que separou esta linha da de cima; None na 1ª


def _partidas_com_resultados(formato, participantes, resultados, ida_e_volta):
    if formato in FORMATOS_DE_ELIMINACAO:
        return list(montar(formato, participantes, resultados).partidas.values())
    if formato == "suico":
        ate = max((_rodada_suica(r[0]) for r in resultados), default=0)
        return _rodadas_do_suico(participantes, resultados, ate)
    if formato != "pontos_corridos":
        raise ValueError(f"formato desconhecido: {formato}")
    partidas = {p.id: p for p in pontos_corridos(participantes, ida_e_volta)}
    for r in resultados:
        if r[0] not in partidas or partidas[r[0]].b is BYE:
            raise ResultadoInvalido(f"partida desconhecida: {r[0]}")
        _aplicar(partidas[r[0]], r, empate_permitido=True)
    return list(partidas.values())


def classificacao(formato, participantes, resultados, ida_e_volta=False):
    """Tabela ordenada: pontos, confronto direto, saldo, Buchholz (só no suíço) e semente, nessa ordem.

    `ida_e_volta` só vale em pontos corridos e precisa ser o mesmo da geração das rodadas.
    """
    _validar(participantes)
    partidas = _partidas_com_resultados(formato, participantes, resultados, ida_e_volta)
    jogadas = [p for p in partidas if p.jogada]
    pontos = {x: 0 for x in participantes} | _pontos(partidas, com_bye=formato == "suico")
    conta = {x: {"v": 0, "e": 0, "d": 0, "saldo": 0, "adversarios": []} for x in participantes}
    for p in jogadas:
        for quem, outro, meu, dele in ((p.a, p.b, p.placar_a, p.placar_b), (p.b, p.a, p.placar_b, p.placar_a)):
            c = conta[quem]
            c["v" if p.vencedor == quem else "e" if p.vencedor is None else "d"] += 1
            c["saldo"] += (meu or 0) - (dele or 0)
            c["adversarios"].append(outro)
    empatados = {}
    for x in participantes:
        empatados.setdefault(pontos[x], set()).add(x)
    confronto = _pontos([p for p in jogadas if len(empatados[pontos[p.a]] & {p.a, p.b}) == 2], com_bye=False)
    buchholz = {x: sum(pontos[o] for o in conta[x]["adversarios"]) if formato == "suico" else 0 for x in participantes}

    def chave_de(x):
        return (-pontos[x], -confronto.get(x, 0), -conta[x]["saldo"], -buchholz[x], participantes.index(x))
    ordem = sorted(participantes, key=chave_de)
    tabela = []
    for i, x in enumerate(ordem):
        criterio = None
        if i:
            anterior, atual = chave_de(ordem[i - 1]), chave_de(x)
            criterio = CRITERIOS[next(k for k in range(len(CRITERIOS)) if anterior[k] != atual[k])]
        c = conta[x]
        tabela.append(Linha(i + 1, x, pontos[x], c["v"], c["e"], c["d"], c["saldo"], buchholz[x], criterio))
    return tabela


def montar(formato, participantes, resultados):
    """Reconstrói uma chave de eliminação aplicando os resultados na ordem em que ficam aplicáveis."""
    chave = FORMATOS_DE_ELIMINACAO[formato](participantes)
    pendentes = list(resultados)
    while pendentes:
        aplicavel = next((r for r in pendentes if (p := chave.partidas.get(r[0])) is not None
                          and p.a is not None and p.b is not None), None)
        if aplicavel is None:
            raise ResultadoInvalido(f"resultado sem partida pronta: {pendentes[0][0]}")
        avancar(chave, aplicavel)
        pendentes.remove(aplicavel)
    return chave


FORMATOS_DE_ELIMINACAO = {"eliminacao_simples": eliminacao_simples, "eliminacao_dupla": eliminacao_dupla}
