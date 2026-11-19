"""Verificação de idade (IDA-01..12), sem rede e com o relógio de Belém fixo em 16/11/2026."""
from datetime import date, datetime

import pytest

from app import tempo
from app.menores import idade as ida
from app.models import User, VerificacaoIdade, db
from conftest import make_user

HOJE = date(2026, 11, 16)


@pytest.fixture(autouse=True)
def relogio(monkeypatch):
    monkeypatch.setattr(tempo, "agora", lambda: datetime(2026, 11, 16, 12, 0, tzinfo=tempo.FUSO))


def anos_atras(anos, dias=0):
    """Nascimento de quem faz `anos` hoje; `dias` > 0 faz o aniversário cair daqui a `dias` dias."""
    return date(HOJE.year - anos, HOJE.month, HOJE.day + dias)


def usuario(app, nome="ana"):
    make_user(app, nome)
    return User.query.filter_by(username=nome).one()


# ---------- IDA-03 ----------
@pytest.mark.parametrize("nascimento,faixa", [
    (anos_atras(18), "adulto"), (anos_atras(18, dias=1), "adolescente"),  # o dia do aniversário e a véspera
    (anos_atras(17), "adolescente"), (anos_atras(16), "adolescente"),
    (anos_atras(16, dias=1), "adolescente_vinculado"), (anos_atras(15), "adolescente_vinculado"),
    (anos_atras(12), "adolescente_vinculado"),
])
def test_ida_03_faixas_nos_limites(nascimento, faixa):
    assert ida.faixa_de(nascimento) == faixa


def test_ida_03_aniversario_muda_a_faixa_sem_job(app, monkeypatch):
    with app.app_context():
        u = usuario(app)
        u.nascimento = anos_atras(18, dias=1)
        assert ida.faixa_etaria(u) == "adolescente"
        monkeypatch.setattr(tempo, "agora", lambda: datetime(2026, 11, 17, 0, 1, tzinfo=tempo.FUSO))
        assert ida.faixa_etaria(u) == "adulto"


def test_ida_03_sem_data_e_a_faixa_mais_protetiva(app):
    with app.app_context():
        u = usuario(app)
        u.nascimento = None
        assert ida.faixa_etaria(u) == "adolescente_vinculado"


# ---------- IDA-01, IDA-02 (validação) ----------
@pytest.mark.parametrize("texto,trecho", [
    ("", "válida"), ("2010-02-30", "válida"), ("31/04/2000", "válida"), ("ontem", "válida"),
    ("2026-11-17", "futuro"), ("1906-11-15", "120 anos"),
])
def test_ida_01_data_invalida_com_mensagem(texto, trecho):
    with pytest.raises(ida.IdadeInvalida, match=trecho):
        ida.ler_nascimento(texto)


def test_ida_01_formatos_aceitos():
    assert ida.ler_nascimento("2000-03-01") == ida.ler_nascimento("01/03/2000") == date(2000, 3, 1)
    assert ida.ler_nascimento("1906-11-16") == date(1906, 11, 16)  # exatamente 120 anos


@pytest.mark.parametrize("nascimento,anos", [(anos_atras(11), 11), (anos_atras(12, dias=1), 11),
                                             (anos_atras(12), 12)])
def test_ida_02_idade_nos_limites_de_12_anos(nascimento, anos):
    assert ida.idade(nascimento) == anos


# ---------- IDA-06, IDA-08 ----------
def test_ida_06_toda_verificacao_grava_historico_so_de_insercao(app):
    with app.app_context():
        u = usuario(app)
        u.nascimento = None
        ida.registrar(u, ida.Resultado(anos_atras(30), "autodeclaracao", False))
        db.session.commit()
        v = VerificacaoIdade.query.one()
        assert (v.metodo, v.faixa_resultante, v.confiavel, v.divergente) == ("autodeclaracao", "adulto", False, False)
        assert {c.name for c in VerificacaoIdade.__table__.columns} == {
            "id", "user_id", "metodo", "faixa_resultante", "confiavel", "divergente", "created_at"}
        v.faixa_resultante = "adolescente"
        with pytest.raises(ValueError, match="só de inserção"):
            db.session.commit()


@pytest.mark.parametrize("sinais,faixa,confiavel", [
    ([(30, False), (14, False)], "adolescente_vinculado", False),  # dois autodeclarados: o mais protetivo
    ([(14, False), (30, False)], "adolescente_vinculado", False),
    ([(14, False), (30, True)], "adulto", True),  # o confiável corrige o autodeclarado
    ([(30, True), (14, False)], "adolescente_vinculado", False),  # sinal mais novo protege mesmo sem prova
    ([(30, True), (40, False)], "adulto", True),  # autodeclaração não deixa menos protetivo
    ([(17, True), (30, True)], "adolescente", True),  # dois confiáveis: o mais protetivo
])
def test_ida_08_divergencia_vale_a_faixa_mais_protetiva(app, sinais, faixa, confiavel):
    with app.app_context():
        u = usuario(app)
        u.nascimento = None
        for anos, conf in sinais:
            ida.registrar(u, ida.Resultado(anos_atras(anos), "responsavel" if conf else "autodeclaracao", conf))
        db.session.commit()
        assert (ida.faixa_etaria(u), u.nascimento_confiavel) == (faixa, confiavel)
        assert u.aguardando_responsavel is (faixa == "adolescente_vinculado")
        assert [v.divergente for v in VerificacaoIdade.query.order_by(VerificacaoIdade.id)][1] is (
            ida.faixa_de(anos_atras(sinais[0][0])) != ida.faixa_de(anos_atras(sinais[1][0])))


def test_ida_05_verificacao_de_menor_de_12_suspende(app):
    with app.app_context():
        u = usuario(app)
        ida.registrar(u, ida.Resultado(anos_atras(10), "provedor_simulado", True))
        db.session.commit()
        assert u.suspensa_em is not None
