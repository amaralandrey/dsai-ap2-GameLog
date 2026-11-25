from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app import emails, jobs, tempo
from app.models import Consentimento, EmailSaida, PreferenciaNotificacao, User, db
from app.privacidade import consentimentos as cns
from app.privacidade import anonimizar, mapa
from conftest import login, make_user

ADOLESCENTE, VINCULADO = date(2009, 6, 1), date(2012, 6, 1)
CADASTRO = {"username": "novo", "email": "novo@ex.com", "password": "senha1234", "nascimento": "1990-01-01",
            "aceite": "1"}


@pytest.fixture(autouse=True)
def relogio(monkeypatch):
    r = SimpleNamespace(momento=datetime(2026, 11, 16, 12, 0, tzinfo=tempo.FUSO))
    monkeypatch.setattr(tempo, "agora", lambda: r.momento)
    for nome in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(nome, raising=False)
    return r


def usuario(app, nome="ana", nascimento=None, **campos):
    uid = make_user(app, nome)
    with app.app_context():
        u = db.session.get(User, uid)
        u.nascimento = nascimento or u.nascimento
        for k, v in campos.items():
            setattr(u, k, v)
        db.session.commit()
    return uid


def logado(app, nome="ana", nascimento=None):
    usuario(app, nome, nascimento)
    c = app.test_client()
    login(c, nome)
    return c


def com(app, uid, funcao, *args):
    with app.app_context():
        resultado = funcao(db.session.get(User, uid), *args)
        db.session.commit()
        return resultado


def tem(app, uid, finalidade="email_resumo"):
    with app.app_context():
        return cns.tem_consentimento(db.session.get(User, uid), finalidade)


def linhas(app, uid):
    with app.app_context():
        return [(c.finalidade, c.concedido, c.origem)
                for c in Consentimento.query.filter_by(user_id=uid).order_by(Consentimento.id)]


def pagina(client):
    return client.get("/conta/privacidade").get_data(as_text=True)


def test_cns01_sem_linha_nao_ha_consentimento(app):
    ana = usuario(app)
    assert tem(app, ana) is False and tem(app, ana, "ofertas") is False


def test_cns01_vale_a_linha_mais_recente(app):
    ana = usuario(app)
    com(app, ana, cns.conceder, "email_resumo", "configuracoes")
    assert tem(app, ana) is True
    com(app, ana, cns.revogar, "email_resumo", "descadastro")
    assert tem(app, ana) is False
    com(app, ana, cns.conceder, "email_resumo", "contexto")
    assert tem(app, ana) is True
    assert linhas(app, ana) == [("email_resumo", True, "configuracoes"), ("email_resumo", False, "descadastro"),
                                ("email_resumo", True, "contexto")]


def test_cns01_so_insercao_e_origens_fixas(app):
    ana = usuario(app)
    for origem in ("cadastro", "configuracoes", "contexto", "descadastro", "seed"):
        com(app, ana, cns.conceder, "email_resumo", origem)
    with app.app_context():
        linha = Consentimento.query.first()
        assert linha.versao_texto == 1 and linha.created_at == tempo.agora_utc()
        linha.concedido = False
        with pytest.raises(ValueError, match="nova linha"):
            db.session.commit()
        db.session.rollback()
        with pytest.raises(ValueError, match="origem"):
            cns.conceder(db.session.get(User, ana), "email_resumo", "admin")


def test_cns02_finalidades_fixas_com_texto_versionado():
    assert tuple(cns.FINALIDADES) == mapa.FINALIDADES_CONSENTIMENTO == ("email_resumo", "ofertas")
    for nome in cns.FINALIDADES:
        atual = cns.texto_atual(nome)
        assert atual.versao >= 1 and len(atual.texto) > 40
    assert "e-mail" in cns.texto_atual("email_resumo").texto and "promoção" in cns.texto_atual("ofertas").texto


def test_cns02_finalidade_desconhecida(app):
    ana = usuario(app)
    with app.app_context(), pytest.raises(cns.FinalidadeDesconhecida):
        cns.tem_consentimento(db.session.get(User, ana), "marketing")
    c = app.test_client()
    login(c, "ana")
    for acao in ("permitir", "revogar"):
        assert c.post(f"/conta/privacidade/consentimentos/marketing/{acao}").status_code == 400
    assert linhas(app, ana) == []


def test_cns03_pagina_mostra_texto_estado_e_botao(app):
    c = logado(app)
    html = pagina(c)
    for nome in cns.FINALIDADES:
        assert cns.texto_atual(nome).texto in html
        assert f"/conta/privacidade/consentimentos/{nome}/permitir" in html
    assert "Não permitido" in html and 'name="csrf_token"' in html


def test_cns03_permitir_e_revogar_na_mesma_pagina(app):
    c = logado(app)
    ana = 1
    r = c.post("/conta/privacidade/consentimentos/email_resumo/permitir")
    assert r.status_code == 302 and r.headers["Location"] == "/conta/privacidade"
    assert linhas(app, ana) == [("email_resumo", True, "configuracoes")]
    assert tem(app, ana) and not tem(app, ana, "ofertas")
    html = pagina(c)
    assert "/conta/privacidade/consentimentos/email_resumo/revogar" in html
    assert "/conta/privacidade/consentimentos/ofertas/permitir" in html
    c.post("/conta/privacidade/consentimentos/email_resumo/revogar")
    assert linhas(app, ana)[-1] == ("email_resumo", False, "configuracoes") and not tem(app, ana)


def test_cns03_visitante_vai_ao_login(client):
    r = client.post("/conta/privacidade/consentimentos/email_resumo/permitir")
    assert r.status_code == 302 and "/entrar" in r.headers["Location"]


def test_cns04_caixas_desmarcadas_e_separadas_do_aceite(client):
    html = client.get("/cadastro").get_data(as_text=True)
    for nome in cns.FINALIDADES:
        campo = f'name="consentir_{nome}"'
        assert campo in html
        assert "checked" not in html.split(campo, 1)[1].split(">", 1)[0]
        assert cns.texto_atual(nome).texto in html
    assert html.index("consentir_ofertas") < html.index('name="aceite"')


def cadastrar(client, app, **extra):
    r = client.post("/cadastro", data={**CADASTRO, **extra})
    with app.app_context():
        u = User.query.filter_by(username="novo").first()
    return r, u.id if u else None


def test_cns04_cadastro_sem_nenhuma_caixa(app, client):
    r, uid = cadastrar(client, app)
    assert r.status_code == 302 and uid and linhas(app, uid) == []


def test_cns04_cada_caixa_marcada_grava_uma_linha(app, client):
    _, uid = cadastrar(client, app, consentir_email_resumo="1", consentir_ofertas="1")
    assert linhas(app, uid) == [("email_resumo", True, "cadastro"), ("ofertas", True, "cadastro")]


@pytest.mark.parametrize("nascimento,esperado", [
    ("2012-06-01", []),
    ("2009-06-01", [("email_resumo", True, "cadastro")]),
])
def test_cns04_faixa_do_mesmo_formulario(app, client, nascimento, esperado):
    r, uid = cadastrar(client, app, nascimento=nascimento, consentir_email_resumo="1", consentir_ofertas="1")
    assert r.status_code == 302 and linhas(app, uid) == esperado


def test_cns04_cadastro_recusado_nao_grava_nada(app, client):
    r, uid = cadastrar(client, app, aceite="", consentir_email_resumo="1")
    assert r.status_code == 400 and uid is None
    with app.app_context():
        assert Consentimento.query.count() == 0


def salvar_preferencias(client, **campos):
    return client.post("/notificacoes/preferencias", data={"site_seguidor": "on", **campos})


def test_cns05_pedido_no_contexto_com_caixa_desmarcada(app):
    c = logado(app)
    html = c.get("/notificacoes/preferencias").get_data(as_text=True)
    campo = 'name="consentir_email_resumo"'
    assert campo in html and "checked" not in html.split(campo, 1)[1].split(">", 1)[0]
    assert cns.texto_atual("email_resumo").texto in html


def test_cns05_aceitar_no_contexto_grava_origem_contexto(app):
    c = logado(app)
    assert salvar_preferencias(c, consentir_email_resumo="1").status_code == 302
    assert linhas(app, 1) == [("email_resumo", True, "contexto")]


def test_cns05_recusar_nao_impede_o_resto(app):
    c = logado(app)
    assert salvar_preferencias(c, email_seguidor="on").status_code == 302
    with app.app_context():
        assert PreferenciaNotificacao.query.filter_by(user_id=1, tipo="seguidor").one().email is True
    assert linhas(app, 1) == [] and c.get("/").status_code == 200


def test_cns05_partial_serve_para_outras_telas(app):
    usuario(app)
    with app.test_request_context():
        p = cns.pedido(db.session.get(User, 1), "ofertas")
        html = app.jinja_env.get_template("privacidade/_pedido_consentimento.html").render(p=p)
    assert 'name="consentir_ofertas"' in html and "checked" not in html and cns.texto_atual("ofertas").texto in html


def test_cns06_interruptor_concede_e_revoga_sem_preferencia_propria(app):
    c = logado(app)
    salvar_preferencias(c, consentir_email_resumo="1")
    salvar_preferencias(c, consentir_email_resumo="1")
    salvar_preferencias(c)
    assert linhas(app, 1) == [("email_resumo", True, "contexto"), ("email_resumo", False, "configuracoes")]
    with app.app_context():
        assert {p.tipo for p in PreferenciaNotificacao.query.filter_by(user_id=1)} <= set(emails.TIPOS)
    c.post("/conta/privacidade/consentimentos/email_resumo/permitir")
    html = c.get("/notificacoes/preferencias").get_data(as_text=True)
    assert "checked" in html.split('name="consentir_email_resumo"', 1)[1].split(">", 1)[0]


def resumo_na_fila(app, uid):
    with app.app_context():
        e = emails.enviar_email(db.session.get(User, uid), "resumo_semanal", semana="2026-W47",
                                itens=[("bia criou a lista Favoritos", "/listas/1")])
        db.session.commit()
        return e.id


def rodar(app):
    with app.app_context():
        jobs.ciclo()


def status(app, eid):
    with app.app_context():
        e = db.session.get(EmailSaida, eid)
        return e.status, e.erro


def test_cns06_revogado_entre_enfileirar_e_enviar_fica_suprimido(app):
    ana = usuario(app)
    com(app, ana, cns.conceder, "email_resumo", "configuracoes")
    eid = resumo_na_fila(app, ana)
    com(app, ana, cns.revogar, "email_resumo", "configuracoes")
    rodar(app)
    assert status(app, eid) == ("suprimido", "sem consentimento email_resumo")
    com(app, ana, cns.conceder, "email_resumo", "configuracoes")
    eid = resumo_na_fila(app, ana)
    rodar(app)
    assert status(app, eid) == ("enviado", None)


def test_cns06_resumo_semanal_so_para_quem_consente(app):
    ana, bia = usuario(app), usuario(app, "bia")
    com(app, ana, cns.conceder, "email_resumo", "configuracoes")
    with app.app_context():
        assert emails.elegivel_ao_resumo(db.session.get(User, ana))
        assert not emails.elegivel_ao_resumo(db.session.get(User, bia))


def nova_versao(monkeypatch, exige_renovar):
    atual = cns.FINALIDADES["email_resumo"]
    texto = cns.Texto(atual.textos[-1].versao + 1, "Texto novo do resumo semanal.", exige_renovar)
    monkeypatch.setitem(cns.FINALIDADES, "email_resumo", atual._replace(textos=(*atual.textos, texto)))


def test_cns07_mudanca_sem_a_marca_mantem(app, monkeypatch):
    ana = usuario(app)
    com(app, ana, cns.conceder, "email_resumo", "configuracoes")
    nova_versao(monkeypatch, exige_renovar=False)
    assert tem(app, ana) is True


def test_cns07_mudanca_com_a_marca_pede_renovacao(app, monkeypatch):
    c = logado(app)
    c.post("/conta/privacidade/consentimentos/email_resumo/permitir")
    nova_versao(monkeypatch, exige_renovar=False)
    nova_versao(monkeypatch, exige_renovar=True)
    assert tem(app, 1) is False
    html = pagina(c)
    assert "O texto mudou" in html and "Texto novo do resumo semanal." in html
    assert "/conta/privacidade/consentimentos/email_resumo/permitir" in html
    c.post("/conta/privacidade/consentimentos/email_resumo/permitir")
    assert tem(app, 1) is True and "O texto mudou" not in pagina(c)
    with app.app_context():
        assert [h.versao_texto for h in cns.historico(db.session.get(User, 1))] == [3, 1]


def test_cns08_historico_do_mais_recente_para_o_mais_antigo(app):
    c = logado(app)
    c.post("/conta/privacidade/consentimentos/ofertas/permitir")
    c.post("/conta/privacidade/consentimentos/email_resumo/permitir")
    c.post("/conta/privacidade/consentimentos/ofertas/revogar")
    with app.app_context():
        h = cns.historico(db.session.get(User, 1))
        assert [(x.finalidade, x.concedido, x.versao_texto, x.origem) for x in h] == [
            ("ofertas", False, 1, "configuracoes"), ("email_resumo", True, 1, "configuracoes"),
            ("ofertas", True, 1, "configuracoes")]
    html = pagina(c)
    tabela = html.split("<h3>Histórico</h3>", 1)[1]
    assert tabela.index("Revogou") < tabela.index("Permitiu") and "16/11/2026" in tabela
    assert mapa.campos_exportaveis("consentimento") == ["finalidade", "concedido", "versao_texto", "origem",
                                                        "created_at"]


@pytest.fixture
def responsavel(app, monkeypatch):
    mae, lia = usuario(app, "mae"), usuario(app, "lia", VINCULADO)
    monkeypatch.setattr(cns, "e_responsavel_vinculado", lambda r, m: (r.id, m.id) == (mae, lia))
    return SimpleNamespace(mae=mae, lia=lia)


def pelo_responsavel(app, funcao, quem, menor, finalidade="email_resumo"):
    with app.app_context():
        resultado = funcao(db.session.get(User, quem), db.session.get(User, menor), finalidade)
        db.session.commit()
        return resultado


def test_cns09_responsavel_vinculado_concede(app, responsavel):
    pelo_responsavel(app, cns.conceder_pelo_responsavel, responsavel.mae, responsavel.lia)
    assert linhas(app, responsavel.lia) == [("email_resumo", True, "responsavel")] and tem(app, responsavel.lia)
    pelo_responsavel(app, cns.revogar_pelo_responsavel, responsavel.mae, responsavel.lia)
    assert linhas(app, responsavel.lia)[-1] == ("email_resumo", False, "responsavel")


def test_cns09_quem_nao_e_o_responsavel_nao_concede(app, responsavel):
    estranho = usuario(app, "estranho")
    for funcao in (cns.conceder_pelo_responsavel, cns.revogar_pelo_responsavel):
        with pytest.raises(cns.NaoPodeConceder):
            pelo_responsavel(app, funcao, estranho, responsavel.lia)
    assert linhas(app, responsavel.lia) == []


def test_cns09_sem_rsp_ninguem_e_responsavel(app):
    mae, lia = usuario(app, "mae"), usuario(app, "lia", VINCULADO)
    with pytest.raises(cns.NaoPodeConceder):
        pelo_responsavel(app, cns.conceder_pelo_responsavel, mae, lia)


def test_cns09_concessao_propria_nao_vale_quando_a_faixa_vira_12_a_15(app):
    rui = usuario(app, "rui")
    com(app, rui, cns.conceder, "email_resumo", "configuracoes")
    with app.app_context():
        db.session.get(User, rui).nascimento = VINCULADO
        db.session.commit()
    assert tem(app, rui) is False


def test_cns09_adolescente_nao_concede_mas_revoga(app, responsavel):
    c = app.test_client()
    login(c, "lia")
    html = pagina(c)
    assert "quem permite é o seu responsável" in html and "email_resumo/permitir" not in html
    assert c.post("/conta/privacidade/consentimentos/email_resumo/permitir").status_code == 403
    with app.app_context(), pytest.raises(cns.NaoPodeConceder):
        cns.conceder(db.session.get(User, responsavel.lia), "email_resumo", "configuracoes")
    pelo_responsavel(app, cns.conceder_pelo_responsavel, responsavel.mae, responsavel.lia)
    assert "Permitido" in pagina(c) and "email_resumo/revogar" in pagina(c)
    assert c.post("/conta/privacidade/consentimentos/email_resumo/revogar").status_code == 302
    assert linhas(app, responsavel.lia)[-1] == ("email_resumo", False, "configuracoes")
    assert not tem(app, responsavel.lia)


def test_cns09_interruptor_do_adolescente_so_desliga(app, responsavel):
    c = app.test_client()
    login(c, "lia")
    html = c.get("/notificacoes/preferencias").get_data(as_text=True)
    assert "disabled" in html.split('name="consentir_email_resumo"', 1)[1].split(">", 1)[0]
    salvar_preferencias(c, consentir_email_resumo="1")
    assert linhas(app, responsavel.lia) == []
    pelo_responsavel(app, cns.conceder_pelo_responsavel, responsavel.mae, responsavel.lia)
    salvar_preferencias(c)
    assert linhas(app, responsavel.lia)[-1] == ("email_resumo", False, "configuracoes")


def test_cns10_ofertas_some_e_403_para_menor(app):
    c = logado(app, "teo", ADOLESCENTE)
    html = pagina(c)
    assert "Avisos de ofertas" not in html and "ofertas/permitir" not in html
    assert "email_resumo/permitir" in html
    assert c.post("/conta/privacidade/consentimentos/ofertas/permitir").status_code == 403
    assert linhas(app, 1) == []


def test_cns10_nem_o_responsavel_concede_ofertas(app, responsavel):
    with pytest.raises(cns.NaoPodeConceder):
        pelo_responsavel(app, cns.conceder_pelo_responsavel, responsavel.mae, responsavel.lia, "ofertas")


def test_cns10_linha_antiga_de_ofertas_nao_vale_para_menor(app):
    teo = usuario(app, "teo")
    com(app, teo, cns.conceder, "ofertas", "configuracoes")
    with app.app_context():
        db.session.get(User, teo).nascimento = ADOLESCENTE
        db.session.commit()
    assert tem(app, teo, "ofertas") is False


def test_cns10_aos_18_nada_e_concedido_sozinho(app, relogio):
    c = logado(app, "teo", date(2008, 11, 20))
    assert "Avisos de ofertas" not in pagina(c)
    relogio.momento = datetime(2026, 11, 21, 12, 0, tzinfo=tempo.FUSO)
    assert tem(app, 1, "ofertas") is False and linhas(app, 1) == []
    assert "/conta/privacidade/consentimentos/ofertas/permitir" in pagina(c)


def test_cns11_so_admin(app, client):
    assert client.get("/admin/privacidade/consentimentos").status_code == 302
    assert logado(app).get("/admin/privacidade/consentimentos").status_code == 403


def test_cns11_totais_por_finalidade_sem_dados_individuais(app, client, monkeypatch):
    make_user(app, "chefe", role="admin")
    nomes = ("xconsentiu", "xrevogou", "xsuspensa", "xmenor", "xantiga", "xanonima")
    uids = {n: usuario(app, n) for n in nomes}
    for n in nomes:
        com(app, uids[n], cns.conceder, "email_resumo", "configuracoes")
        com(app, uids[n], cns.conceder, "ofertas", "configuracoes")
    com(app, uids["xrevogou"], cns.revogar, "email_resumo", "configuracoes")
    with app.app_context():
        db.session.get(User, uids["xsuspensa"]).suspensa_em = tempo.agora_utc()
        db.session.get(User, uids["xmenor"]).nascimento = ADOLESCENTE
        anonimizar.anonimizar_conta(db.session.get(User, uids["xanonima"]))
        db.session.commit()
    nova_versao(monkeypatch, exige_renovar=True)
    com(app, uids["xconsentiu"], cns.conceder, "email_resumo", "configuracoes")
    with app.app_context():
        assert cns.contagem_por_finalidade() == {"email_resumo": 1, "ofertas": 3}
    login(client, "chefe")
    html = client.get("/admin/privacidade/consentimentos").get_data(as_text=True)
    assert "<td>1</td>" in html and "<td>3</td>" in html
    assert not any(n in html for n in nomes)
    assert client.post("/admin/privacidade/consentimentos").status_code == 405
    ana = usuario(app, "ana")
    for acao in ("permitir", "revogar"):
        client.post(f"/conta/privacidade/consentimentos/email_resumo/{acao}", data={"user_id": ana})
    assert linhas(app, ana) == []
