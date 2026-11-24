"""Feriados nacionais fixos e contagem de dias úteis para os prazos de incidente (INC-03).

Ficam de fora os feriados móveis (Carnaval, Sexta-feira Santa, Corpus Christi), os estaduais e os municipais.
"""
from datetime import timedelta

FERIADOS_FIXOS = {
    (1, 1): "Confraternização Universal",
    (4, 21): "Tiradentes",
    (5, 1): "Dia do Trabalho",
    (9, 7): "Independência do Brasil",
    (10, 12): "Nossa Senhora Aparecida",
    (11, 2): "Finados",
    (11, 15): "Proclamação da República",
    (11, 20): "Dia Nacional de Zumbi e da Consciência Negra",  # Lei 14.759/2023
    (12, 25): "Natal",
}


def dia_util(dia):
    return dia.weekday() < 5 and (dia.month, dia.day) not in FERIADOS_FIXOS


def somar_dias_uteis(inicio, n):
    """O n-ésimo dia útil depois de `inicio`; o próprio dia de `inicio` não conta."""
    dia = inicio
    while n > 0:
        dia += timedelta(days=1)
        n -= dia_util(dia)
    return dia


def dias_uteis_ate(hoje, prazo):
    """Dias úteis de `hoje` (exclusive) até `prazo` (inclusive); negativo quando o prazo já passou."""
    if prazo < hoje:
        return -dias_uteis_ate(prazo, hoje)
    dia, n = hoje, 0
    while dia < prazo:
        dia += timedelta(days=1)
        n += dia_util(dia)
    return n
