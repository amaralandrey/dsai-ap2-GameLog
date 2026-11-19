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


# ---------- CHV-03 ----------
def derrotas(chave):
    conta = {}
    for p in chave.partidas.values():
        if p.jogada:
            conta[p.perdedor] = conta.get(p.perdedor, 0) + 1
    return conta


@pytest.mark.parametrize("n", TAMANHOS)
def test_chv_03_ninguem_sai_antes_da_segunda_derrota(n):
    ps = nomes(n)
    chave = jogar_tudo(ch.eliminacao_dupla(ps), melhor_semente(ps))
    assert ch.campeao(chave) == "p1"
    assert derrotas(chave) == {p: 2 for p in ps[1:]}  # o campeão invicto dispensa o reset
    f2 = chave.partidas["F2"]
    assert f2.decidida and not f2.jogada


@pytest.mark.parametrize("n", TAMANHOS)
def test_chv_03_campeao_dos_perdedores_vence_a_final_e_ha_reset(n):
    ps = nomes(n)
    chave = jogar_tudo(ch.eliminacao_dupla(ps), lambda p: p.b if p.id == "F1" else min((p.a, p.b), key=ps.index))
    f1, f2 = chave.partidas["F1"], chave.partidas["F2"]
    assert f1.a == "p1" and f1.vencedor == f1.b and f2.jogada and {f2.a, f2.b} == {"p1", f1.b}
    assert ch.campeao(chave) == f2.vencedor
    assert all(v <= 2 for v in derrotas(chave).values())


def test_chv_03_posicoes_fixas_na_chave_de_perdedores():
    chave = ch.eliminacao_dupla(nomes(8))
    origem = {pid: (p.origem_a, p.origem_b) for pid, p in chave.partidas.items() if pid.startswith("P")}
    assert origem["P1-1"] == (("perdedor", "V1-1"), ("perdedor", "V1-2"))
    assert origem["P2-1"] == (("vencedor", "P1-1"), ("perdedor", "V2-1"))  # k = 1: mesma ordem
    assert origem["P3-1"] == (("vencedor", "P2-1"), ("vencedor", "P2-2"))
    assert origem["P4-1"] == (("vencedor", "P3-1"), ("perdedor", "V3-1"))
    grande = ch.eliminacao_dupla(nomes(16))
    assert grande.partidas["P4-1"].origem_b == ("perdedor", "V3-2")  # k = 2: ordem invertida
    assert chave.partidas["F1"].origem_b == ("vencedor", "P4-1")


def test_chv_09_correcao_na_dupla_desfaz_a_queda_para_os_perdedores():
    chave = ch.eliminacao_dupla(nomes(4))
    ch.avancar(chave, ("V1-1", "p1", 1, 0))
    ch.avancar(chave, ("V1-2", "p2", 1, 0))
    assert (chave.partidas["P1-1"].a, chave.partidas["P1-1"].b) == ("p4", "p3")
    ch.corrigir(chave, "V1-2", ("V1-2", "p3", 0, 1))
    assert (chave.partidas["P1-1"].b, chave.partidas["V2-1"].b) == ("p2", "p3")
    ch.avancar(chave, ("P1-1", "p4", 1, 0))
    with pytest.raises(CorrecaoBloqueada, match="P1-1"):
        ch.corrigir(chave, "V1-1", ("V1-1", "p4", 0, 1))


# ---------- CHV-04 ----------
def pares(partidas):
    return [frozenset((p.a, p.b)) for p in partidas if p.b is not BYE]


@pytest.mark.parametrize("n", TAMANHOS)
def test_chv_04_rodizio_completo_com_folga(n):
    ps = nomes(n)
    partidas = ch.pontos_corridos(ps)
    esperados = {frozenset((x, y)) for i, x in enumerate(ps) for y in ps[i + 1:]}
    assert sorted(map(sorted, pares(partidas))) == sorted(map(sorted, esperados))  # cada par uma vez
    rodadas = {p.rodada for p in partidas}
    assert rodadas == set(range(1, n + (n % 2)))
    for r in rodadas:  # ninguém joga duas vezes na mesma rodada
        nesta = [x for p in partidas if p.rodada == r for x in (p.a, p.b) if x is not BYE]
        assert len(nesta) == len(set(nesta)) == n
    folgas = [p.a for p in partidas if p.b is BYE]
    assert sorted(folgas) == (sorted(ps) if n % 2 else [])  # cada um folga exatamente uma vez
    assert all(not p.jogada and p.vencedor == p.a for p in partidas if p.b is BYE)


@pytest.mark.parametrize("n", TAMANHOS)
def test_chv_04_ida_e_volta_inverte_o_mando(n):
    partidas = ch.pontos_corridos(nomes(n), ida_e_volta=True)
    turno = n - 1 + n % 2
    ida = {(p.a, p.b) for p in partidas if p.rodada <= turno and p.b is not BYE}
    volta = {(p.a, p.b) for p in partidas if p.rodada > turno and p.b is not BYE}
    assert volta == {(b, a) for a, b in ida} and len(ida) == n * (n - 1) // 2
    folgas_volta = [p.a for p in partidas if p.rodada > turno and p.b is BYE]
    assert sorted(folgas_volta) == (sorted(nomes(n)) if n % 2 else [])


# ---------- CHV-05 ----------
def jogar_suico(ps, rodadas, quem_vence=None):
    resultados = []
    for r in range(1, rodadas + 1):
        for p in ch.suico(ps, resultados, r):
            if p.b is not BYE:
                v = quem_vence(p) if quem_vence else min((p.a, p.b), key=ps.index)
                resultados.append((p.id, v, 1 if v == p.a else 0, 1 if v == p.b else 0))
    return resultados


@pytest.mark.parametrize("n", TAMANHOS)
def test_chv_05_suico_sem_revanche_e_bye_unico(n):
    ps = nomes(n)
    rodadas = ch.rodadas_suico(n)
    assert rodadas == max(1, (n - 1).bit_length())
    resultados = jogar_suico(ps, rodadas)
    todas = ch._rodadas_do_suico(ps, resultados, rodadas)
    assert len(pares(todas)) == len(set(pares(todas)))
    byes = [p.a for p in todas if p.b is BYE]
    assert len(byes) == (rodadas if n % 2 else 0) and len(byes) == len(set(byes))
    for r in range(1, rodadas + 1):
        nesta = [x for p in todas if p.rodada == r for x in (p.a, p.b) if x is not BYE]
        assert sorted(nesta) == sorted(ps)


def test_chv_05_mesma_pontuacao_juntos_e_bye_para_o_ultimo():
    ps = nomes(5)
    r1 = ch.suico(ps, [], 1)
    assert [(p.a, p.b) for p in r1] == [("p1", "p2"), ("p3", "p4"), ("p5", BYE)]
    resultados = [("S1-1", "p2", 0, 1), ("S1-2", "p3", 1, 0)]
    r2 = ch.suico(ps, resultados, 2)
    # p2, p3 e p5 (bye) têm 3 pontos; p1 e p4 têm 0: o bye vai para o último que ainda não teve
    assert [(p.a, p.b) for p in r2] == [("p2", "p3"), ("p5", "p1"), ("p4", BYE)]
    with pytest.raises(ResultadoInvalido, match="faltam resultados da rodada 1: S1-2"):
        ch.suico(ps, resultados[:1], 2)


def test_chv_05_revanche_so_sem_alternativa():
    resultados = jogar_suico(nomes(2), 1)
    assert [(p.a, p.b) for p in ch.suico(nomes(2), resultados, 2)] == [("p1", "p2")]


# ---------- CHV-06, CHV-07 ----------
def resultado(partidas, x, y, gx, gy):
    p = next(p for p in partidas if {p.a, p.b} == {x, y})
    placar = (gx, gy) if p.a == x else (gy, gx)
    return (p.id, None if gx == gy else (x if gx > gy else y), *placar)


def tabela(formato, ps, resultados):
    return [(l.participante, l.pontos, l.criterio) for l in ch.classificacao(formato, ps, resultados)]


def test_chv_06_cada_criterio_isolado_em_pontos_corridos():
    ps = nomes(3)
    liga = ch.pontos_corridos(ps)

    def r(*a):
        return resultado(liga, *a)
    assert tabela("pontos_corridos", ps, [r("p3", "p1", 1, 0)]) == [
        ("p3", 3, None), ("p2", 0, "pontos"), ("p1", 0, "saldo")]  # p1 perdeu de 1 a 0
    # p1 tem saldo melhor, mas perdeu para p2: o confronto direto vem antes
    assert tabela("pontos_corridos", ps, [r("p2", "p1", 1, 0), r("p1", "p3", 9, 0)])[:2] == [
        ("p2", 3, None), ("p1", 3, "confronto_direto")]
    # empate entre p1 e p2: o saldo separa
    assert tabela("pontos_corridos", ps, [r("p1", "p2", 1, 1), r("p2", "p3", 1, 0), r("p1", "p3", 3, 0)])[:2] == [
        ("p1", 4, None), ("p2", 4, "saldo")]
    linhas = ch.classificacao("pontos_corridos", ps, [r("p1", "p2", 1, 1), r("p2", "p3", 1, 0), r("p1", "p3", 3, 0)])
    assert [(l.posicao, l.vitorias, l.empates, l.derrotas, l.saldo) for l in linhas] == [
        (1, 1, 1, 0, 3), (2, 1, 1, 0, 1), (3, 0, 0, 2, -4)]


def test_chv_06_buchholz_so_no_suico():
    ps = nomes(4)
    resultados = [("S1-1", "p1", 2, 0), ("S1-2", None, 1, 1)]
    assert [(p.a, p.b) for p in ch.suico(ps, resultados, 2)] == [("p1", "p3"), ("p4", "p2")]
    resultados += [("S2-1", "p1", 1, 0), ("S2-2", "p2", 0, 1)]
    linhas = ch.classificacao("suico", ps, resultados)
    assert [(l.participante, l.pontos, l.buchholz, l.criterio) for l in linhas] == [
        ("p1", 6, 4, None), ("p2", 3, 7, "pontos"), ("p3", 1, 7, "pontos"), ("p4", 1, 4, "buchholz")]


def test_chv_07_empate_total_vai_para_a_semente_nunca_para_a_ordem_dos_resultados():
    ps = nomes(4)
    liga = ch.pontos_corridos(ps)
    resultados = [resultado(liga, "p4", "p3", 0, 0), resultado(liga, "p2", "p1", 0, 0)]
    a = ch.classificacao("pontos_corridos", ps, resultados)
    b = ch.classificacao("pontos_corridos", ps, list(reversed(resultados)))
    assert a == b and [l.participante for l in a] == ps and {l.criterio for l in a[1:]} == {"semente"}


@pytest.mark.parametrize("formato", ["eliminacao_simples", "eliminacao_dupla"])
def test_chv_06_classificacao_nos_formatos_de_eliminacao(formato):
    ps = nomes(4)
    chave = jogar_tudo(ch.FORMATOS_DE_ELIMINACAO[formato](ps), melhor_semente(ps))
    resultados = [(p.id, p.vencedor, p.placar_a, p.placar_b) for p in chave.partidas.values() if p.jogada]
    linhas = ch.classificacao(formato, ps, resultados)
    assert linhas[0].participante == "p1" and linhas[0].derrotas == 0
    assert sum(l.vitorias for l in linhas) == sum(l.derrotas for l in linhas) == len(resultados)


def test_chv_10_empate_aceito_so_em_pontos_corridos_e_suico():
    ps = nomes(2)
    assert ch.classificacao("suico", ps, [("S1-1", None, 0, 0)])[0].empates == 1
    with pytest.raises(ResultadoInvalido, match="empate"):
        ch.classificacao("eliminacao_simples", ps, [("E1-1", None, 0, 0)])
    with pytest.raises(ResultadoInvalido, match="um dos dois"):
        ch.classificacao("pontos_corridos", ps, [("L1-1", "p9", 1, 0)])
    with pytest.raises(ResultadoInvalido, match="desconhecida"):
        ch.classificacao("pontos_corridos", ps, [("L9-9", "p1", 1, 0)])
