"""Relógio do GameLog no fuso de Belém (GLOBAL-09): dia, semana ISO e mês contam em `America/Belem`.

Os testes fixam o relógio trocando `agora` (monkeypatch); todo o resto do código pergunta a hora por aqui.
"""
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo

    FUSO = ZoneInfo("America/Belem")
except Exception:  # sem base de fusos no sistema: Belém não tem horário de verão desde 1988
    FUSO = timezone(timedelta(hours=-3), "America/Belem")


def agora():
    """Data e hora atuais em Belém (com fuso)."""
    return datetime.now(FUSO)


def hoje():
    return agora().date()


def agora_utc():
    """O mesmo instante de `agora`, ingênuo em UTC, como o banco guarda."""
    return agora().astimezone(timezone.utc).replace(tzinfo=None)


def para_belem(momento_utc):
    """Converte um `datetime` ingênuo em UTC (como o banco guarda) para Belém."""
    return momento_utc.replace(tzinfo=timezone.utc).astimezone(FUSO)


def dia_de(momento_utc):
    """Dia em Belém de um instante gravado em UTC."""
    return para_belem(momento_utc).date()


def inicio_do_dia_utc(dia):
    """Instante UTC ingênuo da meia-noite de `dia` em Belém, para filtrar colunas `created_at`."""
    return datetime(dia.year, dia.month, dia.day, tzinfo=FUSO).astimezone(timezone.utc).replace(tzinfo=None)


def semana_iso(dia):
    """Semana ISO no formato `AAAA-Www` (a semana começa na segunda)."""
    ano, semana, _ = dia.isocalendar()
    return f"{ano}-W{semana:02d}"


def segunda_da_semana(texto):
    """Segunda-feira da semana `AAAA-Www`."""
    return date.fromisocalendar(int(texto[:4]), int(texto[6:]), 1)


def semanas_entre(a, b):
    """Quantas semanas ISO vão de `a` até `b` (textos `AAAA-Www`); 1 quando `b` é a seguinte."""
    return (segunda_da_semana(b) - segunda_da_semana(a)).days // 7
