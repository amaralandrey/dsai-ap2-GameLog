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
import random
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


def _empate_permitido(chave):
    return chave.formato in ("pontos_corridos", "suico")


def avancar(chave, resultado):
    """Registra o resultado e leva vencedor (e perdedor, na dupla) às partidas seguintes (CHV-08, CHV-10)."""
    pid, vencedor, placar_a, placar_b = resultado
    p = chave.partidas.get(pid)
    if p is None:
        raise ResultadoInvalido(f"partida desconhecida: {pid}")
    if p.decidida:
        raise ResultadoInvalido(f"a partida {pid} já foi decidida")
    if p.a is None or p.b is None:
        raise ResultadoInvalido(f"a partida {pid} ainda não tem os dois participantes")
    if vencedor is None and not _empate_permitido(chave):
        raise ResultadoInvalido("eliminação não aceita empate: informe o vencedor")
    if vencedor is not None and vencedor not in (p.a, p.b):
        raise ResultadoInvalido(f"o vencedor da partida {pid} precisa ser um dos dois participantes")
    p.vencedor, p.placar_a, p.placar_b, p.jogada = vencedor, placar_a, placar_b, True
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
      perdedor da rodada k+1 dos vencedores. Com k ímpar, B vem de `V(k+1)-i`; com k par, a ordem é
      invertida (`V(k+1)-(M−i+1)`, com M partidas na rodada), para adiar revanches;
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
                j = i + 1 if k % 2 else partidas - i
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
