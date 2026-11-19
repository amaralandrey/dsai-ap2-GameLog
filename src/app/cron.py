"""Expressões cron de 5 campos, no relógio de parede de Belém (JOB-07); módulo puro, sem Flask nem banco.

Campos: minuto, hora, dia do mês, mês e dia da semana (0 ou 7 = domingo). Cada campo aceita `*`, números,
listas (`1,15`), intervalos (`1-5`) e passos (`*/15`, `1-30/5`). Como no cron do Unix, quando dia do mês e
dia da semana são os dois restritos, basta um deles bater.
"""
from datetime import datetime, timedelta

CAMPOS = (("minuto", 0, 59), ("hora", 0, 23), ("dia do mês", 1, 31), ("mês", 1, 12), ("dia da semana", 0, 7))
HORIZONTE = timedelta(days=366 * 8)  # cobre 29/02 mesmo atravessando um ano não bissexto de virada de século


class CronInvalido(ValueError):
    pass


def _numero(texto, nome, menor, maior):
    if not texto.isdigit():
        raise CronInvalido(f"{nome}: '{texto}' não é um número")
    n = int(texto)
    if not menor <= n <= maior:
        raise CronInvalido(f"{nome}: {n} fora de {menor} a {maior}")
    return n


def _campo(texto, nome, menor, maior):
    valores = set()
    for parte in texto.split(","):
        faixa, _, passo = parte.partition("/")
        passo = _numero(passo, nome, 1, maior) if passo else 1
        if faixa == "*":
            de, ate = menor, maior
        elif "-" in faixa:
            de, ate = (_numero(x, nome, menor, maior) for x in faixa.split("-", 1))
            if de > ate:
                raise CronInvalido(f"{nome}: intervalo {de}-{ate} invertido")
        else:
            de = ate = _numero(faixa, nome, menor, maior)
        valores.update(range(de, ate + 1, passo))
    return valores


def ler(expressao):
    """(minutos, horas, dias, meses, dias da semana, dia restrito, semana restrita) ou CronInvalido."""
    partes = (expressao or "").split()
    if len(partes) != 5:
        raise CronInvalido("use 5 campos: minuto hora dia-do-mês mês dia-da-semana")
    conjuntos = [_campo(p, *c) for p, c in zip(partes, CAMPOS)]
    semana = {0 if d == 7 else d for d in conjuntos[4]}
    return (*conjuntos[:4], semana, partes[2] != "*", partes[4] != "*")


def _dia_bate(dia, dias, semana, dia_restrito, semana_restrita):
    no_mes, na_semana = dia.day in dias, (dia.isoweekday() % 7) in semana
    if dia_restrito and semana_restrita:
        return no_mes or na_semana
    return no_mes and na_semana


def proxima_execucao(expressao, depois_de):
    """Primeiro minuto estritamente depois de `depois_de` que bate com a expressão, com o mesmo fuso dele."""
    minutos, horas, dias, meses, semana, dia_restrito, semana_restrita = ler(expressao)
    fuso = depois_de.tzinfo
    t = depois_de.replace(tzinfo=None, second=0, microsecond=0) + timedelta(minutes=1)
    fim = t + HORIZONTE
    while t < fim:
        if t.month not in meses:
            t = datetime(t.year + t.month // 12, t.month % 12 + 1, 1)
        elif not _dia_bate(t, dias, semana, dia_restrito, semana_restrita):
            t = datetime(t.year, t.month, t.day) + timedelta(days=1)
        elif t.hour not in horas:
            t = t.replace(minute=0) + timedelta(hours=1)
        elif t.minute not in minutos:
            t += timedelta(minutes=1)
        else:
            return t.replace(tzinfo=fuso)
    raise CronInvalido("a expressão nunca acontece (ex.: 31 de fevereiro)")


def validar(expressao):
    """Mensagem do problema, ou None; inclui a data que nunca acontece."""
    try:
        proxima_execucao(expressao, datetime(2000, 1, 1))
    except CronInvalido as exc:
        return str(exc)
    return None
