"""Algoritmos de chaveamento (CHV-01..14): funções puras, sem rede e sem banco."""
import ast
from pathlib import Path

import pytest

from app import chaveamento as ch
from app.chaveamento import BYE, CorrecaoBloqueada, ResultadoInvalido

TAMANHOS = [2, 3, 5, 8, 16]


def nomes(n):
    return [f"p{i}" for i in range(1, n + 1)]


def jogar_tudo(chave, quem_vence=lambda p: p.a):
    """Decide as partidas prontas até a chave terminar; por padrão vence sempre o lado A."""
    while prontas := ch.proximas_partidas(chave):
        p = prontas[0]
        ch.avancar(chave, (p.id, quem_vence(p), 1, 0))
    return chave


def melhor_semente(participantes):
    return lambda p: min((p.a, p.b), key=participantes.index)


# ---------- CHV-01 ----------
def test_chv_01_modulo_puro_sem_flask_nem_models():
    arvore = ast.parse(Path(ch.__file__).read_text(encoding="utf-8"))
    importados = {n.module or "" for n in ast.walk(arvore) if isinstance(n, ast.ImportFrom)}
    importados |= {a.name for n in ast.walk(arvore) if isinstance(n, ast.Import) for a in n.names}
    assert not {m for m in importados if "flask" in m or "models" in m or m.startswith(".")}


# ---------- CHV-02 ----------
def test_chv_02_ordem_padrao_das_sementes():
    assert ch.ordem_padrao(4) == [1, 4, 2, 3]
    assert ch.ordem_padrao(8) == [1, 8, 4, 5, 2, 7, 3, 6]


@pytest.mark.parametrize("n", TAMANHOS)
def test_chv_02_byes_nas_melhores_sementes(n):
    ps = nomes(n)
    chave = ch.eliminacao_simples(ps)
    tamanho = ch._potencia_de_2(n)
    byes = [p for p in chave.partidas.values() if p.rodada == 1 and BYE in (p.a, p.b)]
    assert len(byes) == tamanho - n
    assert sorted(p.vencedor for p in byes) == sorted(ps[:tamanho - n])  # as melhores sementes passam direto
    assert not any(p.jogada for p in byes)


@pytest.mark.parametrize("n", TAMANHOS)
def test_chv_02_1_e_2_so_na_final_e_campeao(n):
    ps = nomes(n)
    chave = jogar_tudo(ch.eliminacao_simples(ps), melhor_semente(ps))
    final = list(chave.partidas.values())[-1]
    assert {final.a, final.b} == {"p1", "p2"} and ch.campeao(chave) == "p1"
    jogadas = [p for p in chave.partidas.values() if p.jogada]
    assert len(jogadas) == n - 1  # bye não conta como jogo
    assert all({p.a, p.b} != {"p1", "p2"} for p in jogadas[:-1])


@pytest.mark.parametrize("n", [1, 129])
def test_chv_02_limites_de_participantes(n):
    with pytest.raises(ValueError):
        ch.eliminacao_simples(nomes(n))


# ---------- CHV-08, CHV-10, CHV-12 ----------
def test_chv_08_resultados_invalidos():
    chave = ch.eliminacao_simples(nomes(4))
    with pytest.raises(ResultadoInvalido, match="ainda não tem"):
        ch.avancar(chave, ("E2-1", "p1", 1, 0))
    with pytest.raises(ResultadoInvalido, match="desconhecida"):
        ch.avancar(chave, ("X9", "p1", 1, 0))
    with pytest.raises(ResultadoInvalido, match="um dos dois"):
        ch.avancar(chave, ("E1-1", "p2", 1, 0))
    ch.avancar(chave, ("E1-1", "p1", 2, 1))
    with pytest.raises(ResultadoInvalido, match="já foi decidida"):
        ch.avancar(chave, ("E1-1", "p4", 0, 2))
    assert chave.partidas["E2-1"].a == "p1"


def test_chv_10_eliminacao_nao_aceita_empate():
    with pytest.raises(ResultadoInvalido, match="empate"):
        ch.avancar(ch.eliminacao_simples(nomes(2)), ("E1-1", None, 1, 1))


def test_chv_12_campeao_e_proximas_partidas():
    chave = ch.eliminacao_simples(nomes(3))
    assert [p.id for p in ch.proximas_partidas(chave)] == ["E1-2"] and ch.campeao(chave) is None
    ch.avancar(chave, ("E1-2", "p3", 0, 1))
    assert [(p.id, p.a, p.b) for p in ch.proximas_partidas(chave)] == [("E2-1", "p1", "p3")]
    ch.avancar(chave, ("E2-1", "p3", 0, 1))
    assert ch.campeao(chave) == "p3" and ch.proximas_partidas(chave) == []


# ---------- CHV-09 ----------
def test_chv_09_correcao_desfaz_o_avanco_nao_jogado():
    chave = ch.eliminacao_simples(nomes(8))
    for pid, v in (("E1-1", "p1"), ("E1-2", "p4")):
        ch.avancar(chave, (pid, v, 1, 0))
    assert (chave.partidas["E2-1"].a, chave.partidas["E2-1"].b) == ("p1", "p4")
    ch.corrigir(chave, "E1-2", ("E1-2", "p5", 3, 2))
    p = chave.partidas["E2-1"]
    assert (p.a, p.b, p.decidida) == ("p1", "p5", False)
    assert (chave.partidas["E1-2"].vencedor, chave.partidas["E1-2"].placar_a) == ("p5", 3)


def test_chv_09_correcao_bloqueada_quando_a_seguinte_ja_foi_jogada():
    chave = ch.eliminacao_simples(nomes(4))
    for pid, v in (("E1-1", "p1"), ("E1-2", "p2"), ("E2-1", "p1")):
        ch.avancar(chave, (pid, v, 1, 0))
    with pytest.raises(CorrecaoBloqueada) as exc:
        ch.corrigir(chave, "E1-2", ("E1-2", "p3", 0, 1))
    assert exc.value.partidas == ["E2-1"]
    with pytest.raises(ResultadoInvalido):
        ch.corrigir(ch.eliminacao_simples(nomes(3)), "E1-1", ("E1-1", "p1", 1, 0))  # bye não se corrige


# ---------- CHV-11 ----------
def test_chv_11_sementes_por_pontuacao_id_e_sem_pontuacao_no_fim():
    assert ch.sementes([5, 3, 9, 1, 7], {3: 10, 9: 10, 1: 50}) == [1, 3, 9, 5, 7]


def test_chv_11_sorteio_reprodutivel():
    ids = list(range(1, 17))
    a, b = ch.sementes(ids, {}, aleatorio=42), ch.sementes(list(reversed(ids)), {}, aleatorio=42)
    assert a == b and sorted(a) == ids and a != ids and ch.sementes(ids, {}, aleatorio=7) != a


# ---------- CHV-07 ----------
def test_chv_07_mesma_entrada_mesma_chave():
    resultados = [("E1-2", "p4", 1, 0), ("E1-4", "p6", 0, 2)]
    a = ch.montar("eliminacao_simples", nomes(6), resultados)
    b = ch.montar("eliminacao_simples", nomes(6), list(reversed(resultados)))
    assert a == b
