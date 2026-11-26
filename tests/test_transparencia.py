"""Transparência: política, termos e canal do titular (PRV-01..12), sem rede e com o relógio fixo."""
from dataclasses import replace
from datetime import date, datetime

import pytest

from app import tempo
from app.menores import idade
from app.models import (AceiteDocumento, AuditLog, AvisoDocumentoFechado, DocumentoLegal, PedidoTitular, User, db,
                        now)
from app.precos import dia
from app.privacidade import mapa
from app.privacidade import transparencia as prv
from conftest import login, make_game, make_user

HOJE = date(2026, 11, 16)
CADASTRO = {"username": "novo", "email": "novo@ex.com", "password": "senha1234", "nascimento": "1990-01-01",
            "aceite": "1"}


@pytest.fixture(autouse=True)
def relogio(monkeypatch):
    monkeypatch.setattr(tempo, "agora", lambda: datetime(2026, 11, 16, 12, 0, tzinfo=tempo.FUSO))
    monkeypatch.delenv("ENCARREGADO_NOME", raising=False)
    monkeypatch.delenv("ENCARREGADO_EMAIL", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)


@pytest.fixture
def docs(app):
    """Versão 1 da política e dos termos, como o seed publica."""
    with app.app_context():
        prv.publicar_iniciais()


def publicar(app, tipo="politica", texto="Texto novo.", resumo="", exige=False):
    with app.app_context():
        doc = prv.publicar(tipo, texto, resumo, exige)
        db.session.commit()
        return doc.id


def logado(app, nome="ana", **campos):
    uid = make_user(app, nome)
    if campos:
        with app.app_context():
            u = db.session.get(User, uid)
            for k, v in campos.items():
                setattr(u, k, v)
            db.session.commit()
    c = app.test_client()
    login(c, nome)
    return c


# ---------- PRV-01 ----------
def test_prv01_versao_crescente_por_tipo_e_imutavel(app, docs):
    publicar(app, "politica", "v2")
    publicar(app, "termos", "t2")
    publicar(app, "politica", "v3")
    with app.app_context():
        assert [(d.tipo, d.versao) for d in DocumentoLegal.query.order_by(DocumentoLegal.id)] == [
            ("politica", 1), ("termos", 1), ("politica", 2), ("termos", 2), ("politica", 3)]
        doc = prv.atual("politica")
        assert (doc.versao, doc.texto) == (3, "v3")
        doc.texto = "corrigido"
        with pytest.raises(ValueError, match="publique outra versão"):
            db.session.commit()
        db.session.rollback()
        with pytest.raises(ValueError):
            prv.publicar("cookies", "x")
        with pytest.raises(ValueError):
            prv.publicar("termos", "   ")


def test_prv01_admin_publica_com_auditoria(app, client, docs):
    assert client.get("/admin/privacidade/documentos").status_code == 302
    assert logado(app, "ana").get("/admin/privacidade/documentos").status_code == 403
    make_user(app, "chefe", role="admin")
    login(client, "chefe")
    assert "Primeira versão" not in client.get("/admin/privacidade/documentos").get_data(as_text=True)
    assert client.post("/admin/privacidade/documentos", data={"tipo": "termos", "texto": ""}).status_code == 400
    r = client.post("/admin/privacidade/documentos", data={"tipo": "termos", "texto": "Novos termos.",
                                                           "resumo_mudancas": "Moderação", "exige_novo_aceite": "1"})
    assert r.headers["Location"] == "/admin/privacidade/documentos"
    assert client.get(r.headers["Location"]).headers["Location"].startswith("/termos/aceitar")  # o admin também aceita
    with app.app_context():
        doc = prv.atual("termos")
        assert (doc.versao, doc.resumo_mudancas, doc.exige_novo_aceite) == (2, "Moderação", True)
        assert [(a.action, a.detail) for a in AuditLog.query] == [("privacidade.publicar", "termos v2 (novo aceite)")]


def test_prv01_seed_publica_a_versao_1_e_registra_o_aceite(app):
    from app.seed import run

    with app.app_context():
        run(42)
        run(42)  # idempotente
        assert [(d.tipo, d.versao) for d in DocumentoLegal.query.order_by(DocumentoLegal.tipo)] == [
            ("politica", 1), ("termos", 1)]
        assert prv.atual("politica").texto == (prv.TEXTOS / "politica.txt").read_text(encoding="utf-8").strip()
        jogador = User.query.filter_by(username="jogador01").one()
        assert prv.pendentes_de_aceite(jogador) == []
        assert AceiteDocumento.query.count() == 2 * User.query.count() == 60


# ---------- PRV-02 ----------
def test_prv02_versao_atual_antiga_e_inexistente(app, client, docs):
    publicar(app, "politica", "Texto da segunda versão.")
    with app.app_context():
        data = dia(prv.atual("politica").publicado_em)
    html = client.get("/privacidade").get_data(as_text=True)
    assert f"Versão 2, publicada em {data}" in html and "Texto da segunda versão." in html
    antiga = client.get("/privacidade?versao=1").get_data(as_text=True)
    assert "Versão 1" in antiga and "Quem somos" in antiga and "Texto da segunda versão." not in antiga
    for url in ("/privacidade?versao=3", "/privacidade?versao=abc", "/termos?versao=2", "/termos?versao=0"):
        assert client.get(url).status_code == 404, url
    termos = client.get("/termos?versao=1")
    assert termos.status_code == 200 and "Sobre o serviço" in termos.get_data(as_text=True)


def test_prv02_rodape_com_privacidade_e_termos(app, client):
    for c in (client, logado(app)):
        html = c.get("/buscar").get_data(as_text=True)
        rodape = html[html.index("<footer"):]
        assert '<a href="/privacidade">Privacidade</a>' in rodape and '<a href="/termos">Termos</a>' in rodape


# ---------- PRV-03 ----------
def test_prv03_resumo_primeiro_e_secoes_geradas_do_mapa(app, client, docs):
    assert len(prv.resumo()) <= 10
    assert [t for t, _ in prv.resumo()][:4] == ["O que coletamos", "Para quê", "Por quanto tempo",
                                                "Com quem compartilhamos"]
    html = client.get("/privacidade").get_data(as_text=True)
    ordem = [html.index(t) for t in ("Em poucas palavras", "Quais dados tratamos", 'id="suboperadores"', "Quem somos")]
    assert ordem == sorted(ordem)
    assert "Segurança da conta e prevenção de abuso" in html and "legítimo interesse" in html
    assert "30 dias" in html and "Render" in html


def test_prv03_anotacao_nova_aparece_sem_editar_o_texto(app, client, monkeypatch):
    assert "45 dias" not in client.get("/privacidade").get_data(as_text=True)
    campos = dict(mapa.CAMPOS)
    campos["user.apelido"] = replace(campos["user.bio"], finalidade="comunicacao", retencao_dias=45)
    monkeypatch.setattr(mapa, "CAMPOS", campos)
    assert "45 dias" in client.get("/privacidade").get_data(as_text=True)
    assert any("45 dias" in texto for _, texto in prv.resumo())


# ---------- PRV-04 ----------
def test_prv04_caixa_desmarcada_com_links(client):
    html = client.get("/cadastro").get_data(as_text=True)
    caixa = html[html.index('name="aceite"') - 40:html.index("política de privacidade</a>")]
    assert "checked" not in caixa and 'href="/termos"' in caixa and 'href="/privacidade"' in caixa
    assert "Li e aceito os" in caixa


def test_prv04_sem_aceite_400_sem_conta(app, client, docs):
    r = client.post("/cadastro", data={**CADASTRO, "aceite": ""})
    assert r.status_code == 400 and prv.RECUSA in r.get_data(as_text=True)
    with app.app_context():
        assert User.query.count() == AceiteDocumento.query.count() == 0


def test_prv04_com_aceite_grava_a_versao_atual_dos_dois(app, client, docs):
    publicar(app, "politica", "v2")
    assert client.post("/cadastro", data=CADASTRO).status_code == 302
    with app.app_context():
        u = User.query.one()
        aceitos = sorted((a.documento.tipo, a.documento.versao) for a in AceiteDocumento.query.filter_by(user_id=u.id))
        assert aceitos == [("politica", 2), ("termos", 1)]


def test_prv04_erros_juntos_por_campo_e_nada_gravado(app, client, docs):
    from app.models import VerificacaoIdade

    r = client.post("/cadastro", data={**CADASTRO, "username": "x", "nascimento": "2010-02-30", "aceite": ""})
    html = r.get_data(as_text=True)
    assert r.status_code == 400
    for msg in ("Use de 3 a 20 caracteres", "data de nascimento válida", prv.RECUSA):
        assert html.count(msg) == 1, msg
    assert html.index("Use de 3 a 20") < html.index("data de nascimento válida") < html.index(prv.RECUSA)
    with app.app_context():
        assert User.query.count() == VerificacaoIdade.query.count() == AceiteDocumento.query.count() == 0


def test_prv04_recusa_por_idade_vem_antes_das_outras_mensagens(client):
    nascimento = date(HOJE.year - 11, 1, 1).isoformat()
    html = client.post("/cadastro", data={**CADASTRO, "username": "x", "nascimento": nascimento,
                                          "aceite": ""}).get_data(as_text=True)
    assert html.count(idade.MENOR_DE_12) == 1
    assert html.index(idade.MENOR_DE_12) < html.index("Use de 3 a 20") < html.index(prv.RECUSA)


def test_prv04_cadastro_tem_ponto_de_extensao_por_partes():
    from app import auth

    from app.privacidade import consentimentos

    assert [p.validar for p in auth.PARTES_DO_CADASTRO] == [auth.validar_nascimento,
                                                            consentimentos.validar_cadastro, prv.validar_aceite]
    assert prv.validar_aceite({}) == ({"aceite": prv.RECUSA}, None)


# ---------- PRV-05 ----------
def test_prv05_novo_aceite_leva_a_termos_aceitar_em_qualquer_pagina(app, docs):
    make_game(app)
    c = logado(app)
    assert c.get("/listas").status_code == 200
    publicar(app, "termos", "Termos 2", "Agora há torneios.", exige=True)
    for rota in ("/listas", "/", "/jogos/jogo-teste"):
        r = c.get(rota)
        assert r.status_code == 302 and r.headers["Location"] == f"/termos/aceitar?next={rota}", rota
    assert c.post("/jogos/jogo-teste/avaliar", data={"score": 8}).headers["Location"].startswith("/termos/aceitar")
    html = c.get("/termos/aceitar").get_data(as_text=True)
    assert "Agora há torneios." in html and "<button>Aceitar</button>" in html
    assert f'href="{prv.DESTINO_NAO_ACEITO}">Não aceito</a>' in html
    r = c.post("/termos/aceitar?next=/listas")
    assert r.headers["Location"] == "/listas" and c.get("/listas").status_code == 200
    with app.app_context():
        assert prv.pendentes_de_aceite(User.query.one()) == []


def test_prv05_rotas_liberadas_sem_aceite(app, docs):
    c = logado(app)
    publicar(app, "politica", "Política 2", exige=True)
    for rota in ("/privacidade", "/termos", "/privacidade/suboperadores", "/privacidade/contato",
                 "/conta/privacidade", "/termos/aceitar", prv.DESTINO_NAO_ACEITO):
        assert c.get(rota).status_code == 200, rota
    assert c.get("/conta/excluir").status_code == 404  # DIR: livre do redirecionamento, ainda não existe
    assert c.post("/sair").headers["Location"] == "/"


def test_prv05_versao_aceita_depois_de_versao_exigida_nao_bloqueia(app, docs):
    publicar(app, "politica", "v2", exige=True)
    publicar(app, "politica", "v3")
    c = app.test_client()
    c.post("/cadastro", data=CADASTRO)  # aceita a v3, que é posterior à v2 exigida
    assert c.get("/listas").status_code == 200


@pytest.mark.parametrize("campos,destino", [
    ({"nascimento": None}, "/conta/nascimento"),
    ({"nascimento": date(2012, 1, 1), "aguardando_responsavel": True}, "/conta/pendente"),
])
def test_prv05_idade_vem_antes_do_novo_aceite(app, docs, campos, destino):
    c = logado(app, **campos)
    publicar(app, "termos", "Termos 2", exige=True)
    assert c.get("/listas").headers["Location"].startswith(destino)
    assert c.get("/").status_code == 200  # página pública: a pendência de idade não desvia, e o aceite espera


# ---------- PRV-06 ----------
def test_prv06_faixa_sem_bloqueio_some_ao_fechar(app, client, docs):
    c = logado(app)
    doc_id = publicar(app, "politica", "v2", "Novo suboperador de e-mail.")
    with app.app_context():
        data = dia(db.session.get(DocumentoLegal, doc_id).publicado_em)
    r = c.get("/listas")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert f"A política de privacidade mudou em {data}." in html and "Novo suboperador de e-mail." in html
    assert "mudou em" not in client.get("/listas", follow_redirects=True).get_data(as_text=True)  # visitante
    assert c.post(f"/privacidade/aviso/{doc_id}/fechar", data={"next": "/listas"}).headers["Location"] == "/listas"
    c.post(f"/privacidade/aviso/{doc_id}/fechar")
    assert "mudou em" not in c.get("/listas").get_data(as_text=True)
    with app.app_context():
        assert AvisoDocumentoFechado.query.count() == 1
    publicar(app, "politica", "v3", "Outra mudança.")
    assert "Outra mudança." in c.get("/listas").get_data(as_text=True)  # uma vez por versão
    assert c.post("/privacidade/aviso/999/fechar").status_code == 404


# ---------- PRV-07 ----------
def test_prv07_canal_sem_encarregado(app, client, docs):
    html = client.get("/privacidade").get_data(as_text=True)
    secao = html[html.index("Fale com a gente sobre seus dados"):]
    assert 'href="/privacidade/contato"' in secao
    dados = {"assunto": "Meus dados", "mensagem": "Quero saber o que vocês guardam."}
    r = client.post("/privacidade/contato", data=dados)
    assert r.status_code == 400 and "e-mail para a resposta" in r.get_data(as_text=True)
    assert client.post("/privacidade/contato", data={**dados, "email": "v@ex.com",
                                                     "mensagem": "x" * 2001}).status_code == 400
    r = client.post("/privacidade/contato", data={**dados, "email": "V@ex.com"}, follow_redirects=True)
    with app.app_context():
        pedido = PedidoTitular.query.one()
        assert (pedido.tipo, pedido.user_id, pedido.email_contato, pedido.status) == ("contato", None, "v@ex.com",
                                                                                      "aberto")
        assert pedido.protocolo.startswith("20261116-")
    assert f"Protocolo {pedido.protocolo}" in r.get_data(as_text=True)


def test_prv07_canal_de_quem_esta_logado_usa_o_email_da_conta(app):
    c = logado(app)
    c.post("/privacidade/contato", data={"assunto": "Oi", "mensagem": "Corrigir meu nome."})
    with app.app_context():
        pedido = PedidoTitular.query.one()
        assert (pedido.user_id, pedido.email_contato) == (User.query.one().id, "ana@ex.com")


def test_prv07_encarregado_configurado(client, monkeypatch):
    monkeypatch.setenv("ENCARREGADO_NOME", "Fernanda Borges")
    monkeypatch.setenv("ENCARREGADO_EMAIL", "dpo@gamelog.exemplo")
    html = client.get("/privacidade").get_data(as_text=True)
    secao = html[html.index("Fale com a gente sobre seus dados"):html.index('id="texto"')]
    assert "Fernanda Borges" in secao and "dpo@gamelog.exemplo" in secao
    assert 'href="/privacidade/contato"' not in secao
    assert client.get("/privacidade/contato?assunto=exclusao").get_data(as_text=True).count("Excluir minha conta") == 1


# ---------- PRV-08 ----------
def test_prv08_suboperadores_com_e_sem_smtp(client, monkeypatch):
    html = client.get("/privacidade/suboperadores").get_data(as_text=True)
    for s in mapa.SUBOPERADORES:
        assert s.nome in html and s.pais in html and s.mecanismo in html and s.finalidade.split(";")[0] in html
    assert "Provedor de e-mail" not in html
    monkeypatch.setenv("SMTP_HOST", "smtp.exemplo.com")
    assert "Provedor de e-mail (smtp.exemplo.com)" in client.get("/privacidade/suboperadores").get_data(as_text=True)
    assert "Provedor de e-mail" in client.get("/privacidade").get_data(as_text=True)


# ---------- PRV-09 ----------
def _cookies(resposta):
    return {h.split("=", 1)[0] for h in resposta.headers.getlist("Set-Cookie")}


def test_prv09_cookies_gravados_estao_todos_na_politica(app, client):
    make_game(app)
    nomes = {c["nome"] for c in prv.COOKIES}
    assert app.config["SESSION_COOKIE_NAME"] in nomes
    vistos = set()
    for metodo, url, dados in [("get", "/cadastro", None), ("post", "/cadastro", CADASTRO), ("post", "/sair", {}),
                               ("get", "/entrar", None), ("post", "/entrar", {"email": "novo@ex.com",
                                                                              "password": "senha1234"}),
                               ("get", "/jogos/jogo-teste", None),
                               ("post", "/jogos/jogo-teste/avaliar", {"score": 8, "text": "Bom."}),
                               ("post", "/sair", {})]:
        r = getattr(client, metodo)(url, data=dados)
        assert r.status_code < 400, url
        vistos |= _cookies(r)
    assert vistos and vistos <= nomes, f"cookie fora da política: {vistos - nomes}"
    html = client.get("/privacidade").get_data(as_text=True)
    secao = html[html.index('id="cookies"'):html.index('id="contato"')]
    assert all(c["nome"] in secao and c["duracao"] in secao for c in prv.COOKIES)


# ---------- PRV-10 ----------
@pytest.mark.parametrize("nascimento,recolhida", [(date(2009, 1, 1), True), (date(2012, 1, 1), True),
                                                  (date(1990, 1, 1), False)])
def test_prv10_menor_ve_o_resumo_e_a_politica_recolhida(app, docs, nascimento, recolhida):
    c = logado(app, nascimento=nascimento)
    html = c.get("/privacidade").get_data(as_text=True)
    assert ("Ver a política completa" in html) is recolhida
    if recolhida:
        assert html.index("Em poucas palavras") < html.index("<details") < html.index("Quais dados tratamos")
        assert html.index("Quem somos") < html.index("</details>")


def test_prv10_resumo_junto_da_caixa_de_aceite_no_cadastro(client):
    nascimento = date(HOJE.year - 14, 1, 1).isoformat()
    for html in (client.get("/cadastro").get_data(as_text=True),
                 client.post("/cadastro", data={**CADASTRO, "aceite": "", "nascimento": nascimento}).get_data(as_text=True)):
        resumo = html.index("A política de privacidade em poucas palavras")
        assert resumo < html.index('name="aceite"') and html.index('name="nascimento"') < resumo
        for titulo, _ in prv.resumo():
            assert titulo in html[resumo:html.index('name="aceite"')]


# ---------- PRV-11 ----------
def test_prv11_historico_de_aceites_so_de_insercao(app, docs):
    c = logado(app)
    publicar(app, "politica", "v2", exige=True)
    c.post("/termos/aceitar")
    html = c.get("/conta/privacidade").get_data(as_text=True)
    tabela = html[html.index("<table"):html.index("</table>")]
    assert tabela.count("Política de privacidade") == 2 and tabela.count("Termos de uso") == 1
    assert 'href="/privacidade?versao=2"' in tabela and dia(now()) in tabela
    with app.app_context():
        aceite = AceiteDocumento.query.first()
        aceite.aceito_em = datetime(2020, 1, 1)
        with pytest.raises(ValueError):
            db.session.commit()
    assert app.test_client().get("/conta/privacidade").status_code == 302


def test_prv11_admin_ve_contagem_de_contas_ativas_sem_lista(app, client, docs):
    make_user(app, "bia")
    make_user(app, "caio")
    make_user(app, "chefe", role="admin")
    with app.app_context():
        User.query.filter_by(username="caio").one().suspensa_em = datetime(2026, 11, 1)
        db.session.commit()
        contas = prv.contas_por_versao()
        assert contas[prv.atual("politica").id] == contas[prv.atual("termos").id] == 2
    login(client, "chefe")
    html = client.get("/admin/privacidade/documentos").get_data(as_text=True)
    tabela = html[html.index("<table"):html.index("</table>")]
    assert tabela.count("<td>2</td>") == 2 and "bia" not in tabela and "chefe" not in tabela


# ---------- PRV-12 ----------
def test_prv12_dados_de_cadastro_dos_testes_existentes_enviam_aceite_e_nascimento():
    from test_auth import OK

    assert OK["aceite"] and OK["nascimento"]
