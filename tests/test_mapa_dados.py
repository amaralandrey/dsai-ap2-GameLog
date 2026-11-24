"""Mapa de dados (MAP-01..10)."""
import csv
import io
from dataclasses import replace

import pytest
from sqlalchemy import Column, Integer, MetaData, Table

from app.privacidade import mapa
from app.privacidade.mapa import Campo

from conftest import login, make_user

VALIDO = Campo("atividade", "conteudo", "contrato", None, True, "manter")


def test_tarefas_de_job_e_dado_pessoal_nos_args():
    """Tarefas de e-mail levam o id do usuário em `args` e `chave_unica` (EML-08); tarefa nova revisa o mapa."""
    from app import emails, jobs  # noqa: F401  (emails registra as tarefas de e-mail)
    from app.privacidade import retencao  # noqa: F401  (registra o expurgo, RTN-01)
    from app.privacidade import incidentes  # noqa: F401  (incidentes_prazos, sem usuário em args)
    from app import backup  # noqa: F401

    for chave in ("job.args", "job.chave_unica", "agendamento.args"):
        assert mapa.CAMPOS[chave].categoria == "tecnico" and not mapa.CAMPOS[chave].exportar
    assert mapa.CAMPOS["job.tipo"].categoria == "nao_pessoal"
    assert mapa.retencao("job") == 30 and mapa.retencao("email_saida") == 90  # RTN-02
    assert set(jobs.TAREFAS) == {"sync_games", "enriquecer_snapshot", "precos", "enviar_email",
                                 "email_resumo_semanal", "email_resumo", "torneios", "incidentes_prazos",
                                 "expurgo", "backup"}, \
        "tarefa nova: confira se o que ela leva em args está anotado em job.args e agendamento.args"


def test_map02_toda_coluna_do_banco_esta_no_mapa(app):  # MAP-02, MAP-03, GLOBAL-12
    with app.app_context():
        faltando, sobrando = mapa.verificar_mapa()
    assert faltando == [], f"colunas sem anotação no mapa de dados: {faltando}"
    assert sobrando == [], f"anotações sem coluna no banco: {sobrando}"


def test_map04_o_mapa_atual_e_valido():  # MAP-04..06
    assert mapa.validar_mapa() == []


def _metadata():
    md = MetaData()
    Table("t", md, Column("id", Integer, primary_key=True), Column("a", Integer))
    Table("cat", md, Column("id", Integer, primary_key=True))
    return md


def test_map03_coluna_sem_anotacao_e_anotacao_sobrando():  # MAP-03
    campos = {"t.id": VALIDO, "t.velha": VALIDO, "sumiu.*": replace(VALIDO, categoria="nao_pessoal"),
              "cat.*": VALIDO}
    assert mapa.verificar_mapa(campos, _metadata()) == (["t.a"], ["sumiu.*", "t.velha"])


def test_map02_tabela_inteira_cobre_as_colunas_mas_nao_junto_com_coluna_avulsa():  # MAP-02
    nao_pessoal = Campo("nao_pessoal", "catalogo", "nao_se_aplica", None, False, "manter")
    campos = {"t.*": nao_pessoal, "cat.*": nao_pessoal}
    assert mapa.verificar_mapa(campos, _metadata()) == ([], [])
    assert any("t.*" in e for e in mapa.validar_mapa({**campos, "t.a": nao_pessoal}))


@pytest.mark.parametrize("mudanca", [
    {"categoria": "sensivel"}, {"base_legal": "vontade"}, {"anonimizar": "embaralhar"},
    {"finalidade": "marketing"}, {"retencao_dias": 0}, {"retencao_dias": "30"}, {"exportar": "sim"},
])
def test_map01_valores_fora_das_listas_sao_recusados(mudanca):  # MAP-01, MAP-04
    assert mapa.validar_campo("t.a", VALIDO) == []
    assert mapa.validar_campo("t.a", replace(VALIDO, **mudanca))


def test_map04_legitimo_interesse_exige_avaliacao():  # MAP-04
    sem = replace(VALIDO, base_legal="legitimo_interesse")
    assert any("balanceamento" in e for e in mapa.validar_campo("t.a", sem))
    assert mapa.validar_campo("t.a", replace(sem, avaliacao_li="   ")) != []
    assert mapa.validar_campo("t.a", replace(sem, avaliacao_li="necessário e proporcional")) == []


def test_map04_nao_pessoal_exige_nao_se_aplica_e_nao_exporta():  # MAP-04
    nao_pessoal = Campo("nao_pessoal", "catalogo", "nao_se_aplica", None, False, "manter")
    assert mapa.validar_campo("t.*", nao_pessoal) == []
    assert mapa.validar_campo("t.*", replace(nao_pessoal, base_legal="contrato")) != []
    assert mapa.validar_campo("t.*", replace(nao_pessoal, exportar=True)) != []
    assert mapa.validar_campo("t.a", replace(VALIDO, base_legal="nao_se_aplica")) != []


def test_map05_consentimento_exige_finalidade_conhecida():  # MAP-05
    consentido = replace(VALIDO, base_legal="consentimento")
    assert mapa.validar_campo("t.a", consentido) != []
    assert any("finalidade de consentimento" in e
               for e in mapa.validar_campo("t.a", replace(consentido, finalidade_consentimento="marketing")))
    for finalidade in ("email_resumo", "ofertas"):
        assert mapa.validar_campo("t.a", replace(consentido, finalidade_consentimento=finalidade)) == []


def test_map06_autenticacao_nunca_e_exportavel():  # MAP-06
    senha = Campo("autenticacao", "conta", "contrato", None, False, "substituir")
    assert mapa.validar_campo("user.password_hash", senha) == []
    assert any("autenticação" in e for e in mapa.validar_campo("user.password_hash", replace(senha, exportar=True)))
    assert "password_hash" not in mapa.campos_exportaveis("user")
    assert "session_version" not in mapa.campos_exportaveis("user")


def test_map09_consultas_leem_so_o_mapa(monkeypatch):  # MAP-09
    assert "bio" in mapa.campos_exportaveis("user")
    assert mapa.regra_anonimizacao("user", "bio") == "apagar"
    assert mapa.regra_anonimizacao("game", "title") == "manter"  # coberta por game.*
    assert mapa.campos_exportaveis("game") == []
    assert mapa.retencao("login_attempt") == 30
    assert mapa.retencao("notificacao") == 180
    assert mapa.retencao("audit_log") == 730
    assert mapa.retencao("review") is None
    with pytest.raises(KeyError):
        mapa.regra_anonimizacao("user", "nao_existe")

    campos = dict(mapa.CAMPOS)
    campos["user.bio"] = replace(campos["user.bio"], exportar=False, anonimizar="substituir")
    campos["login_attempt.at"] = replace(campos["login_attempt.at"], retencao_dias=7)
    monkeypatch.setattr(mapa, "CAMPOS", campos)
    assert "bio" not in mapa.campos_exportaveis("user")
    assert mapa.regra_anonimizacao("user", "bio") == "substituir"
    assert mapa.retencao("login_attempt") == 7


def test_map07_suboperadores_e_provedor_de_email(monkeypatch):  # MAP-07
    monkeypatch.delenv("SMTP_HOST", raising=False)
    nomes = [s.nome for s in mapa.suboperadores()]
    assert nomes[:2] == ["Render", "Valve (Steam)"] and "GOG" in nomes and "RAWG" in nomes
    assert not any("e-mail" in n for n in nomes)
    render = mapa.suboperadores()[0]
    assert render.pais == "Estados Unidos" and "19/2024" in render.mecanismo

    monkeypatch.setenv("SMTP_HOST", "smtp.exemplo.com")
    assert any("smtp.exemplo.com" in s.nome for s in mapa.suboperadores())


def test_map03_comando_verificar_mapa(app, monkeypatch):  # MAP-03
    r = app.test_cli_runner().invoke(args=["privacidade", "verificar-mapa"])
    assert r.exit_code == 0
    assert r.output.startswith("colunas=") and r.output.strip().endswith("faltando=0 sobrando=0")

    campos = dict(mapa.CAMPOS)
    del campos["user.bio"]
    campos["user.apelido"] = campos["user.display_name"]
    monkeypatch.setattr(mapa, "CAMPOS", campos)
    r = app.test_cli_runner().invoke(args=["privacidade", "verificar-mapa"])
    assert r.exit_code == 1
    assert "faltando=1 sobrando=1" in r.output.splitlines()[0]
    assert "faltando: user.bio" in r.output and "sobrando: user.apelido" in r.output


def test_map08_registro_so_para_admin(app, client):  # MAP-08, GLOBAL-05
    assert client.get("/admin/privacidade/registro").status_code == 302
    make_user(app, "ana")
    login(client, "ana")
    assert client.get("/admin/privacidade/registro").status_code == 403
    assert client.get("/admin/privacidade/registro.csv").status_code == 403


def test_map08_registro_agrupado_por_finalidade(app, client):  # MAP-08
    make_user(app, "chefe", role="admin")
    login(client, "chefe")
    html = client.get("/admin/privacidade/registro").get_data(as_text=True)
    assert "Segurança da conta e prevenção de abuso" in html
    assert "login_attempt" in html and "legitimo_interesse" in html and "30 dias" in html
    assert "Render" in html


def test_map08_registro_csv(app, client):  # MAP-08
    make_user(app, "chefe", role="admin")
    login(client, "chefe")
    r = client.get("/admin/privacidade/registro.csv")
    assert r.mimetype == "text/csv" and "utf-8" in r.content_type
    assert "attachment" in r.headers["Content-Disposition"]
    linhas = list(csv.DictReader(io.StringIO(r.get_data(as_text=True))))
    assert list(linhas[0]) == ["finalidade", "descricao", "categorias", "tabelas", "bases_legais", "retencao",
                               "exportado", "anonimizacao", "suboperadores"]
    assert [l["finalidade"] for l in linhas] == [l["finalidade"] for l in mapa.registro_operacoes()]
    seguranca = next(l for l in linhas if l["finalidade"] == "seguranca")
    assert seguranca["tabelas"] == "instancia; login_attempt"
    assert seguranca["retencao"] == "30 dias; não se aplica"
    assert seguranca["exportado"] == "não" and seguranca["anonimizacao"] == "apagar_linha; manter"
    conta = next(l for l in linhas if l["finalidade"] == "conta")
    assert "autenticacao" in conta["categorias"].split("; ") and conta["exportado"] == "parcial"
