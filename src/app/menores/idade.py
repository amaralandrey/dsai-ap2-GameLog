"""Verificação de idade (IDA-01..12): faixa etária, adaptadores de verificação e histórico.

Quem consulta usa só `faixa_etaria(user)`; a data de nascimento nunca sai do servidor (IDA-12). A
autodeclaração é o primeiro sinal, não a prova: um adaptador `confiavel` (responsável ou provedor) pode
corrigir a data, e entre sinais de mesma confiança vale o mais protetivo (IDA-08).
"""
import os
from collections import namedtuple
from datetime import date, datetime

from .. import tempo
from ..models import VerificacaoIdade, db, now

IDADE_MINIMA, IDADE_MAXIMA = 12, 120
ADULTO, ADOLESCENTE, VINCULADO = "adulto", "adolescente", "adolescente_vinculado"
MENOR_DE_12 = "O GameLog é para pessoas a partir de 12 anos"

Resultado = namedtuple("Resultado", "nascimento metodo confiavel")


class IdadeInvalida(ValueError):
    """Mensagem para o campo de data de nascimento."""


def idade(nascimento, hoje=None):
    hoje = hoje or tempo.hoje()
    return hoje.year - nascimento.year - ((hoje.month, hoje.day) < (nascimento.month, nascimento.day))


def faixa_de(nascimento):
    anos = idade(nascimento)
    return ADULTO if anos >= 18 else ADOLESCENTE if anos >= 16 else VINCULADO


def faixa_etaria(user):
    """Calculada a cada chamada; sem data ou com idade contestada, a faixa mais protetiva (IDA-03, IDA-11)."""
    if user.nascimento is None or user.idade_contestada_em is not None:
        return VINCULADO
    return faixa_de(user.nascimento)


def ler_nascimento(texto):
    """Data válida e plausível (IDA-01) ou IdadeInvalida; menor de 12 é checado à parte (IDA-02)."""
    texto = (texto or "").strip()
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            nascimento = datetime.strptime(texto, formato).date()
            break
        except ValueError:
            continue
    else:
        raise IdadeInvalida("Informe uma data de nascimento válida (dia, mês e ano).")
    if nascimento > tempo.hoje():
        raise IdadeInvalida("A data de nascimento não pode estar no futuro.")
    hoje = tempo.hoje()
    limite = hoje.replace(year=hoje.year - IDADE_MAXIMA, day=28 if (hoje.month, hoje.day) == (2, 29) else hoje.day)
    if nascimento < limite:
        raise IdadeInvalida(f"A data de nascimento não pode ser de mais de {IDADE_MAXIMA} anos atrás.")
    return nascimento


# ---------- adaptadores (IDA-07) ----------
class Autodeclaracao:
    nome, rotulo, confiavel = "autodeclaracao", "Informar a data de novo", False

    def disponivel(self):
        return True

    def verificar(self, user, dados):
        return Resultado(ler_nascimento(dados.get("nascimento")), self.nome, self.confiavel)


class Responsavel:
    """Confirmação no aceite do vínculo (RSP-02); não tem tela própria aqui."""
    nome, rotulo, confiavel = "responsavel", "Confirmação pelo responsável", True

    def disponivel(self):
        return False

    def verificar(self, user, dados):
        return Resultado(ler_nascimento(dados.get("nascimento")), self.nome, self.confiavel)


class ProvedorSimulado:
    """Resultados fixos por código de teste, sem rede; só com VERIFICACAO_PROVEDOR=simulado."""
    nome, rotulo, confiavel = "provedor_simulado", "Provedor de verificação (simulado)", True
    CODIGOS = {"ADULTO": 30, "ADOLESCENTE": 17, "VINCULADO": 14, "CRIANCA": 10}

    def disponivel(self):
        return os.environ.get("VERIFICACAO_PROVEDOR") == "simulado"

    def verificar(self, user, dados):
        anos = self.CODIGOS.get((dados.get("codigo") or "").strip().upper())
        if anos is None:
            raise IdadeInvalida("Código de verificação não reconhecido.")
        hoje = tempo.hoje()
        nascimento = date(hoje.year - anos, hoje.month, 1)
        return Resultado(nascimento, self.nome, self.confiavel)


ADAPTADORES = {a.nome: a for a in (Autodeclaracao(), Responsavel(), ProvedorSimulado())}


# ---------- aplicação de uma verificação (IDA-06, IDA-08, IDA-10) ----------
def _vale(user, resultado):
    """A data que fica: um sinal confiável corrige um não confiável; fora isso, a mais protetiva (a mais nova)."""
    if user.nascimento is None or (resultado.confiavel and not user.nascimento_confiavel):
        return resultado.nascimento, resultado.confiavel
    if resultado.nascimento > user.nascimento:
        return resultado.nascimento, user.nascimento_confiavel and resultado.confiavel
    return user.nascimento, user.nascimento_confiavel


def registrar(user, resultado):
    """Aplica o resultado, grava o histórico e devolve a faixa; quem chama faz o commit.

    Menor de 12 tem a conta suspensa (IDA-05). Uma verificação confiável encerra a contestação do admin
    (IDA-11). A conta fica aguardando responsável enquanto a faixa for a de 12 a 15 anos (IDA-04).
    """
    divergente = user.nascimento is not None and faixa_de(user.nascimento) != faixa_de(resultado.nascimento)
    user.nascimento, user.nascimento_confiavel = _vale(user, resultado)
    if resultado.confiavel:
        user.idade_contestada_em = None
    if idade(user.nascimento) < IDADE_MINIMA:
        user.suspensa_em = user.suspensa_em or now()
    faixa = faixa_etaria(user)
    user.aguardando_responsavel = faixa == VINCULADO
    db.session.add(VerificacaoIdade(user_id=user.id, metodo=resultado.metodo, faixa_resultante=faixa,
                                    confiavel=resultado.confiavel, divergente=divergente))
    return faixa
