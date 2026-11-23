"""Incidentes e segurança (INC-01..12), sem rede e com o relógio fixo."""
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest

from app import create_app, jobs, tempo
from app.models import AnotacaoIncidente, AuditLog, EmailSaida, GameList, Incidente, Notificacao, User, db
from app.privacidade import feriados, incidentes, mapa

from conftest import login, make_game, make_user

SEGUNDA = datetime(2026, 11, 16, 12, 0, tzinfo=tempo.FUSO)  # sexta, 20/11, é feriado (Consciência Negra)


@pytest.fixture(autouse=True)
def relogio(monkeypatch):
    r = SimpleNamespace(momento=SEGUNDA)
    monkeypatch.setattr(tempo, "agora", lambda: r.momento)
    for nome in ("INCIDENTE_PRAZO_DIAS_UTEIS", "AGENTE_PEQUENO_PORTE", "GDPR_ATIVO"):
        monkeypatch.delenv(nome, raising=False)
    return r


@pytest.fixture
def chefe(app, client):
    make_user(app, "chefe", role="admin")
    login(client, "chefe")
    return client


FORM = {"descricao": "Vazamento de um backup do banco.", "ciencia_em": "2026-11-16T09:30",
        "ocorrencia_em": "2026-11-15", "categorias": ["contato"], "titulares_estimados": "40",
        "medidas": "Backup apagado."}


def registrar(cliente, **mudancas):
    r = cliente.post("/admin/incidentes/novo", data={**FORM, **mudancas})
    assert r.status_code == 302, r.get_data(as_text=True)
    return int(r.headers["Location"].rsplit("/", 1)[1])


def incidente(app, iid):
    with app.app_context():
        return db.session.get(Incidente, iid)


def acoes(app, prefixo="incidente."):
    with app.app_context():
        return [a.action for a in AuditLog.query.order_by(AuditLog.id) if a.action.startswith(prefixo)]


# ---------- INC-01 ----------
def test_inc01_so_admin_acessa(app, client):
    make_user(app)
    assert client.get("/admin/incidentes").status_code == 302  # visitante vai ao login
    login(client)
    for rota in ("/admin/incidentes", "/admin/incidentes/novo"):
        assert client.get(rota).status_code == 403
    assert client.post("/admin/incidentes/novo", data=FORM).status_code == 403
    with app.app_context():
        assert Incidente.query.count() == 0


def test_inc01_registra_com_todos_os_campos(app, chefe):
    iid = registrar(chefe, categorias=["contato", "perfil"], envolve_menores="")
    inc = incidente(app, iid)
    assert inc.descricao == FORM["descricao"] and inc.medidas == "Backup apagado."
    assert inc.ciencia_em == datetime(2026, 11, 16, 12, 30)  # 09:30 em Belém, gravado em UTC
    assert inc.ocorrencia_em == date(2026, 11, 15) and inc.lista_categorias == ["contato", "perfil"]
    assert (inc.titulares_estimados, inc.envolve_menores, inc.situacao) == (40, False, "aberto")
    assert "Vazamento de um backup" in chefe.get(f"/admin/incidentes/{iid}").get_data(as_text=True)


def test_inc01_categorias_vem_do_mapa_de_dados(chefe):
    html = chefe.get("/admin/incidentes/novo").get_data(as_text=True)
    for categoria in mapa.CATEGORIAS:
        assert (f'value="{categoria}"' in html) == (categoria != "nao_pessoal")


def test_inc01_validacao_no_servidor_com_erro_por_campo(app, chefe):
    r = chefe.post("/admin/incidentes/novo", data={"descricao": "curta", "ciencia_em": "2026-11-17T10:00",
                                                    "ocorrencia_em": "ontem", "categorias": ["inventada"],
                                                    "titulares_estimados": "0", "medidas": ""})
    html = r.get_data(as_text=True)
    assert r.status_code == 400
    for trecho in ("A descrição deve ter", "sem data no futuro", "Informe uma data válida",
                   "Escolha ao menos uma categoria", "número estimado de titulares", "As medidas tomadas"):
        assert trecho in html
    assert ">curta</textarea>" in html  # o que foi digitado volta
    r = chefe.post("/admin/incidentes/novo",
                   data={**FORM, "ocorrencia_em": "2026-11-16", "ciencia_em": "2026-11-14T10:00"})
    assert r.status_code == 400 and "A ocorrência não pode ser depois da ciência." in r.get_data(as_text=True)
    with app.app_context():
        assert Incidente.query.count() == 0


# ---------- INC-02 ----------
@pytest.mark.parametrize("mudancas, motivo", [
    ({"categorias": ["autenticacao"]}, "dados de autenticação"),
    ({"envolve_menores": "1"}, "dados de crianças e adolescentes"),
    ({"titulares_estimados": "1000"}, "larga escala"),
])
def test_inc02_risco_relevante_automatico(app, chefe, mudancas, motivo):
    iid = registrar(chefe, **mudancas)
    assert incidente(app, iid).risco == "relevante"
    html = chefe.get(f"/admin/incidentes/{iid}").get_data(as_text=True)
    assert "Avaliação de risco: relevante" in html and motivo in html
    assert acoes(app) == ["incidente.registrar", "incidente.risco"]


def test_inc02_menores_ficam_marcados_na_avaliacao(app, chefe):
    iid = registrar(chefe, envolve_menores="1")
    assert "dados de menores" in chefe.get("/admin/incidentes").get_data(as_text=True)
    assert "dados de menores" in chefe.get(f"/admin/incidentes/{iid}").get_data(as_text=True)


def test_inc02_risco_mostrado_na_hora_do_registro(chefe):
    r = chefe.post("/admin/incidentes/novo", data={**FORM, "titulares_estimados": "999"}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "Risco: a avaliar" in html and "Avaliação de risco: a avaliar" in html


def test_inc02_justificativa_obrigatoria_e_auditada(app, chefe):
    iid = registrar(chefe)
    assert incidente(app, iid).risco == "a_avaliar"
    r = chefe.post(f"/admin/incidentes/{iid}/risco", data={"decisao": "descartar", "justificativa": "  "},
                   follow_redirects=True)
    assert "A justificativa é obrigatória." in r.get_data(as_text=True)
    assert incidente(app, iid).risco == "a_avaliar" and acoes(app) == ["incidente.registrar"]
    chefe.post(f"/admin/incidentes/{iid}/risco", data={"decisao": "descartar", "justificativa": "E-mails cifrados."})
    inc = incidente(app, iid)
    assert (inc.risco, inc.justificativa_risco) == ("descartado", "E-mails cifrados.")
    chefe.post(f"/admin/incidentes/{iid}/risco", data={"decisao": "confirmar", "justificativa": "A chave vazou junto."})
    assert incidente(app, iid).risco == "relevante"
    assert acoes(app) == ["incidente.registrar", "incidente.risco", "incidente.risco"]


def test_inc02_relevante_pelos_criterios_nao_e_descartado(app, chefe):
    iid = registrar(chefe, categorias=["autenticacao"])
    r = chefe.post(f"/admin/incidentes/{iid}/risco", data={"decisao": "descartar", "justificativa": "x"},
                   follow_redirects=True)
    assert "não pode ser descartado" in r.get_data(as_text=True) and incidente(app, iid).risco == "relevante"


# ---------- INC-03 ----------
def test_inc03_dias_uteis_pulam_fim_de_semana_e_feriado():
    assert not feriados.dia_util(date(2026, 11, 20)) and not feriados.dia_util(date(2026, 11, 21))
    assert feriados.dia_util(date(2026, 11, 19))
    assert feriados.somar_dias_uteis(date(2026, 11, 16), 3) == date(2026, 11, 19)
    assert feriados.somar_dias_uteis(date(2026, 11, 18), 3) == date(2026, 11, 24)  # pula 20 (feriado), 21 e 22
    assert feriados.somar_dias_uteis(date(2026, 12, 23), 3) == date(2026, 12, 29)  # pula o Natal
    assert feriados.dias_uteis_ate(date(2026, 11, 18), date(2026, 11, 24)) == 3
    assert feriados.dias_uteis_ate(date(2026, 11, 24), date(2026, 11, 24)) == 0
    assert feriados.dias_uteis_ate(date(2026, 11, 25), date(2026, 11, 24)) == -1


def test_inc03_pagina_mostra_prazo_e_dias_que_faltam(app, chefe, relogio):
    iid = registrar(chefe, ciencia_em="2026-11-16T09:30")
    relogio.momento = datetime(2026, 11, 17, 10, 0, tzinfo=tempo.FUSO)
    html = chefe.get(f"/admin/incidentes/{iid}").get_data(as_text=True)
    assert html.count("19/11/2026 23:59") == 2 and html.count("faltam 2 dias úteis") == 2  # ANPD e titulares
    relogio.momento = datetime(2026, 11, 23, 10, 0, tzinfo=tempo.FUSO)
    assert "vencido" in chefe.get(f"/admin/incidentes/{iid}").get_data(as_text=True)


def test_inc03_prazo_configuravel_e_em_dobro_para_pequeno_porte(app, chefe, monkeypatch):
    iid = registrar(chefe, ciencia_em="2026-11-16T09:30")
    assert "19/11/2026 23:59" in chefe.get(f"/admin/incidentes/{iid}").get_data(as_text=True)
    monkeypatch.setenv("AGENTE_PEQUENO_PORTE", "1")
    html = chefe.get(f"/admin/incidentes/{iid}").get_data(as_text=True)
    assert "25/11/2026 23:59" in html and "faltam 6 dias úteis" in html and "agente de pequeno porte" in html
    monkeypatch.setenv("INCIDENTE_PRAZO_DIAS_UTEIS", "2")
    assert "23/11/2026 23:59" in chefe.get(f"/admin/incidentes/{iid}").get_data(as_text=True)


# ---------- INC-04 ----------
def test_inc04_prazo_gdpr_lado_a_lado_com_destaque(app, chefe, monkeypatch):
    iid = registrar(chefe, ciencia_em="2026-11-16T09:30")
    assert "GDPR" not in chefe.get(f"/admin/incidentes/{iid}").get_data(as_text=True)
    monkeypatch.setenv("GDPR_ATIVO", "1")
    html = chefe.get(f"/admin/incidentes/{iid}").get_data(as_text=True)
    assert "GDPR (72 horas)" in html and "19/11/2026 09:30" in html and "faltam 70 horas" in html
    assert html.count("prazo-destaque") == 1 and html.index("ANPD") < html.index("prazo-destaque")
    with app.test_request_context():
        lista = incidentes.prazos(incidente(app, iid))
    assert [p.nome for p in lista if p.destaque] == ["GDPR (72 horas)"]


def test_inc04_destaque_vai_para_o_prazo_mais_proximo(app, chefe, monkeypatch):
    monkeypatch.setenv("GDPR_ATIVO", "1")
    monkeypatch.setenv("INCIDENTE_PRAZO_DIAS_UTEIS", "1")
    iid = registrar(chefe, ciencia_em="2026-11-16T09:30")
    with app.test_request_context():
        lista = incidentes.prazos(incidente(app, iid))
    assert [p.nome for p in lista if p.destaque] == ["ANPD"]  # 17/11 23:59 vem antes de 19/11 09:30


# ---------- INC-05 ----------
def test_inc05_modelo_da_anpd_com_conteudo_minimo(app, chefe):
    iid = registrar(chefe, categorias=["autenticacao", "contato"], envolve_menores="1")
    r = chefe.get(f"/admin/incidentes/{iid}/anpd.txt")
    texto = r.get_data(as_text=True)
    assert r.mimetype == "text/plain" and "attachment" in r.headers["Content-Disposition"]
    for secao in ("I. Natureza e categorias", "II. Número de titulares", "III. Medidas técnicas", "IV. Riscos",
                  "V. Motivos da demora", "VI. Medidas adotadas", "VII. Datas", "VIII. Encarregado",
                  "IX. Controlador", "X. Operador", "XI. Descrição do incidente", "XII. Total de titulares"):
        assert secao in texto
    for dado in ("autenticação", "Estimativa: 40 titulares", "crianças ou adolescentes", "Backup apagado.",
                 "Vazamento de um backup", "15/11/2026", "16/11/2026 09:30", "Comunicação dentro do prazo."):
        assert dado in texto
    assert texto.count("[PREENCHER") >= 5


def test_inc05_registrar_envio_marca_comunicado(app, chefe):
    iid = registrar(chefe)
    chefe.post(f"/admin/incidentes/{iid}/anpd", data={"enviado_em": "2026-11-17"})
    assert incidente(app, iid).anpd_enviado_em is None  # no futuro
    chefe.post(f"/admin/incidentes/{iid}/anpd", data={"enviado_em": "2026-11-16"})
    inc = incidente(app, iid)
    assert (inc.anpd_enviado_em, inc.situacao) == (date(2026, 11, 16), "comunicado_anpd")
    assert "comunicado à ANPD" in chefe.get(f"/admin/incidentes/{iid}").get_data(as_text=True)
    assert acoes(app)[-1] == "incidente.anpd"


def test_inc05_atrasado_pede_os_motivos_da_demora(app, chefe, relogio):
    iid = registrar(chefe)
    relogio.momento = datetime(2026, 11, 24, 10, 0, tzinfo=tempo.FUSO)
    texto = chefe.get(f"/admin/incidentes/{iid}/anpd.txt").get_data(as_text=True)
    assert "[PREENCHER: a comunicação passou do prazo" in texto


# ---------- INC-06 ----------
def test_inc06_modelo_dos_titulares_com_conteudo_minimo(app, chefe):
    iid = registrar(chefe)
    html = chefe.get(f"/admin/incidentes/{iid}/titulares").get_data(as_text=True)
    for trecho in ("O que aconteceu: Vazamento de um backup", "Quais dados podem ter sido afetados: contato",
                   "Quando soubemos: 16/11/2026", "Como protegemos", "Quais são os riscos",
                   "O que já fizemos e o que vamos fazer: Backup apagado.", "Dúvidas: [PREENCHER"):
        assert trecho in html


def test_inc06_envio_por_email_e_notificacao(app, chefe):
    for nome in ("ana", "bia", "caio"):
        make_user(app, nome)
    iid = registrar(chefe)
    mensagem = "Olá,\r\n\r\nUm backup vazou.\r\n\r\nTroque a sua senha."
    r = chefe.post(f"/admin/incidentes/{iid}/titulares",
                   data={"mensagem": mensagem, "destino": "lista", "nomes": "@ana, BIA"})
    assert r.status_code == 302
    with app.app_context():
        emails = EmailSaida.query.filter_by(modelo="incidente").order_by(EmailSaida.id).all()
        assert [e.para for e in emails] == ["ana@ex.com", "bia@ex.com"] and emails[0].tipo is None  # transacional
        assert "Um backup vazou." in emails[0].corpo_texto and "<p" in emails[0].corpo_html
        avisos = Notificacao.query.filter_by(tipo="sistema").all()
        assert sorted(db.session.get(User, n.user_id).username for n in avisos) == ["ana", "bia"]
    inc = incidente(app, iid)
    assert inc.titulares_avisados == 2 and inc.titulares_avisados_em == tempo.agora_utc()
    assert acoes(app)[-1] == "incidente.titulares"
    chefe.post(f"/admin/incidentes/{iid}/titulares", data={"mensagem": "Aviso.", "destino": "todos"})
    assert incidente(app, iid).titulares_avisados == 2 + 4  # todos: ana, bia, caio e o chefe


def test_inc06_recusa_lista_invalida_e_campo_sem_preencher(app, chefe):
    make_user(app, "ana")
    iid = registrar(chefe)
    r = chefe.post(f"/admin/incidentes/{iid}/titulares",
                   data={"mensagem": "Dúvidas: [PREENCHER: contato]", "destino": "lista", "nomes": "ana fantasma"})
    html = r.get_data(as_text=True)
    assert r.status_code == 400 and "Usuários não encontrados: fantasma" in html and "[PREENCHER]" in html
    r = chefe.post(f"/admin/incidentes/{iid}/titulares", data={"mensagem": "Aviso.", "destino": "lista", "nomes": " "})
    assert r.status_code == 400 and "Cole ao menos um nome" in r.get_data(as_text=True)
    with app.app_context():
        assert EmailSaida.query.count() == 0 and Notificacao.query.count() == 0
    assert incidente(app, iid).titulares_avisados is None


# ---------- INC-07 ----------
def avisos_de_prazo(app):
    with app.app_context():
        return Notificacao.query.filter(Notificacao.tipo == "sistema", Notificacao.texto.like("Incidente #%")).count()


def rodar_lembrete(app):
    with app.app_context():
        jobs.TAREFAS["incidentes_prazos"]()


def test_inc07_job_diario_registrado():
    assert "incidentes_prazos" in jobs.TAREFAS
    cron, tipo, args, ativo = jobs.AGENDAS_PADRAO["incidentes_prazos"]
    assert (cron, tipo, args, ativo) == ("0 8 * * *", "incidentes_prazos", "{}", True)


def test_inc07_avisa_admins_uma_vez_por_dia(app, chefe, relogio):
    make_user(app, "vice", role="admin")
    make_user(app, "ana")
    iid = registrar(chefe, categorias=["autenticacao"])  # prazo 19/11
    registrar(chefe)  # risco a avaliar: sem aviso
    rodar_lembrete(app)
    assert avisos_de_prazo(app) == 0  # segunda: faltam 3 dias úteis
    relogio.momento = datetime(2026, 11, 18, 8, 0, tzinfo=tempo.FUSO)  # falta 1
    rodar_lembrete(app)
    rodar_lembrete(app)
    assert avisos_de_prazo(app) == 2  # um para cada admin, uma vez no dia
    with app.app_context():
        n = Notificacao.query.filter(Notificacao.texto.like("Incidente #%")).first()
        assert n.alvo_url == f"/admin/incidentes/{iid}" and "falta 1 dia útil" in n.texto
        assert {db.session.get(User, x.user_id).username for x in Notificacao.query} == {"chefe", "vice"}
    relogio.momento = datetime(2026, 11, 19, 8, 0, tzinfo=tempo.FUSO)
    rodar_lembrete(app)
    assert avisos_de_prazo(app) == 4


def test_inc07_comunicado_nao_gera_aviso(app, chefe, relogio):
    iid = registrar(chefe, categorias=["autenticacao"])
    chefe.post(f"/admin/incidentes/{iid}/anpd", data={"enviado_em": "2026-11-16"})
    make_user(app, "ana")
    chefe.post(f"/admin/incidentes/{iid}/titulares", data={"mensagem": "Aviso.", "destino": "lista", "nomes": "ana"})
    relogio.momento = datetime(2026, 11, 19, 8, 0, tzinfo=tempo.FUSO)
    rodar_lembrete(app)
    assert avisos_de_prazo(app) == 0


# ---------- INC-08 ----------
def test_inc08_encerrado_nao_muda_e_aceita_anotacao(app, chefe):
    iid = registrar(chefe)
    r = chefe.post(f"/admin/incidentes/{iid}/encerrar", follow_redirects=True)
    assert "Confirme ou descarte o risco" in r.get_data(as_text=True) and incidente(app, iid).encerrado_em is None
    chefe.post(f"/admin/incidentes/{iid}/risco", data={"decisao": "descartar", "justificativa": "Sem dado legível."})
    chefe.post(f"/admin/incidentes/{iid}/encerrar")
    assert incidente(app, iid).situacao == "encerrado"
    chefe.post(f"/admin/incidentes/{iid}/risco", data={"decisao": "confirmar", "justificativa": "mudei de ideia"})
    chefe.post(f"/admin/incidentes/{iid}/anpd", data={"enviado_em": "2026-11-16"})
    assert chefe.get(f"/admin/incidentes/{iid}/titulares").status_code == 302
    inc = incidente(app, iid)
    assert (inc.risco, inc.anpd_enviado_em) == ("descartado", None)
    chefe.post(f"/admin/incidentes/{iid}/anotacoes", data={"texto": "A data de ocorrência certa é 14/11."})
    with app.app_context():
        a = AnotacaoIncidente.query.one()
        assert (a.texto, a.autor.username, a.created_at) == ("A data de ocorrência certa é 14/11.", "chefe",
                                                             tempo.agora_utc())
    assert "A data de ocorrência certa é 14/11." in chefe.get(f"/admin/incidentes/{iid}").get_data(as_text=True)
    assert acoes(app)[-2:] == ["incidente.encerrar", "incidente.anotar"]


def test_inc08_modelo_recusa_edicao_e_exclusao(app, chefe, relogio):
    iid = registrar(chefe, categorias=["autenticacao"])
    with app.app_context():
        inc = db.session.get(Incidente, iid)
        inc.medidas = "Corrigido antes do encerramento."
        inc.encerrado_em = tempo.agora_utc()
        db.session.commit()
        inc.descricao = "reescrita"
        with pytest.raises(incidentes.ErroIncidente):
            db.session.commit()
        db.session.rollback()
        db.session.delete(db.session.get(Incidente, iid))
        with pytest.raises(incidentes.ErroIncidente):
            db.session.commit()
        db.session.rollback()
    assert incidente(app, iid).descricao == FORM["descricao"]
    relogio.momento += timedelta(days=mapa.RETENCAO_INCIDENTE_DIAS + 1)
    with app.app_context():
        db.session.delete(db.session.get(Incidente, iid))  # o expurgo (RTN) só apaga depois de 5 anos
        db.session.commit()
    assert incidente(app, iid) is None


def test_inc08_retencao_de_5_anos_no_mapa():
    assert mapa.retencao("incidente") == mapa.retencao("anotacao_incidente") == 5 * 365 + 1
    assert mapa.CAMPOS["incidente.descricao"].base_legal == "obrigacao_legal"


# ---------- INC-09 ----------
def cookie_de_sessao(app):
    make_user(app)
    r = login(app.test_client())
    return next(c for c in r.headers.getlist("Set-Cookie") if c.startswith("session="))


def test_inc09_cookie_em_producao(tmp_path):
    app = create_app({"TESTING": True, "CSRF": False, "PRODUCAO": True, "SECRET_KEY": "k" * 32,
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path}/p.db"})
    cookie = cookie_de_sessao(app)
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie


def test_inc09_cookie_em_desenvolvimento(app):
    cookie = cookie_de_sessao(app)
    assert "Secure" not in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie


# ---------- INC-10 ----------
def test_inc10_csp_das_paginas_html(client):
    csp = client.get("/").headers["Content-Security-Policy"]
    diretivas = dict(d.split(" ", 1) for d in csp.split("; "))
    assert diretivas["default-src"] == "'self'" and diretivas["script-src"] == "'self'"
    assert diretivas["style-src"] == "'self' 'unsafe-inline'" and diretivas["frame-ancestors"] == "'none'"
    assert "unsafe-inline" not in diretivas["script-src"]
    assert diretivas["img-src"].startswith("'self' data:")
    assert "https://cdn.cloudflare.steamstatic.com" in diretivas["img-src"]


def test_inc10_outros_cabecalhos(client):
    h = client.get("/").headers
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert h["Permissions-Policy"] == "camera=(), microphone=(), geolocation=()"
    assert "Strict-Transport-Security" not in h


def test_inc10_hsts_so_em_producao(tmp_path):
    app = create_app({"TESTING": True, "PRODUCAO": True, "SECRET_KEY": "k" * 32,
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path}/p.db"})
    assert app.test_client().get("/").headers["Strict-Transport-Security"] == "max-age=31536000"


def test_inc10_api_ganha_so_nosniff(client):
    for rota in ("/api/jogos", "/api/rota-inexistente"):
        h = client.get(rota).headers
        assert h["X-Content-Type-Options"] == "nosniff"
        assert not {"Content-Security-Policy", "Referrer-Policy", "Permissions-Policy"} & set(h.keys())
    assert client.get("/api/jogos").headers["Access-Control-Allow-Origin"] == "*"  # API-09 continua


def test_inc10_capas_externas_seguem_a_config(app, client):
    app.config["CAPAS_HOSTS_PERMITIDOS"] = ("capas.exemplo.com",)
    img = client.get("/").headers["Content-Security-Policy"].split("img-src ")[1].split(";")[0]
    assert img == "'self' data: https://capas.exemplo.com"


def test_inc10_com_proxy_de_capas_so_o_importar_ve_capa_externa(app, chefe):
    app.config["PROXY_CAPAS_ATIVO"] = True
    assert "steamstatic" not in chefe.get("/").headers["Content-Security-Policy"]
    assert "steamstatic" in chefe.get("/admin/importar").headers["Content-Security-Policy"]


# ---------- INC-11 ----------
class ScriptInline(HTMLParser):
    def __init__(self):
        super().__init__()
        self.problemas = []

    def handle_starttag(self, tag, attrs):
        nomes = dict(attrs)
        if tag == "script" and not nomes.get("src"):
            self.problemas.append("<script> inline")
        self.problemas += [f"{tag} {nome}=" for nome in nomes if nome.startswith("on")]


def test_inc11_paginas_principais_sem_script_inline(app, chefe):
    slug = make_game(app, "Hades")
    chefe.post(f"/jogos/{slug}/status", data={"status": "jogando"})
    chefe.post("/listas", data={"name": "Favoritos"})
    with app.app_context():
        lista = GameList.query.first().id
    iid = registrar(chefe)
    paginas = ["/", "/buscar", f"/jogos/{slug}", "/u/chefe", "/listas", f"/listas/{lista}", "/feed", "/diario",
               "/meus-jogos", "/recomendacoes", "/clubes", "/torneios", "/rankings", "/notificacoes", "/estatisticas",
               "/comparar", "/conquistas", "/admin/", "/admin/importar", "/admin/jobs", "/admin/incidentes",
               f"/admin/incidentes/{iid}", f"/admin/incidentes/{iid}/titulares", "/admin/incidentes/novo"]
    visitante = app.test_client()
    for cliente, rota in [(chefe, p) for p in paginas] + [(visitante, "/entrar"), (visitante, "/cadastro")]:
        r = cliente.get(rota)
        assert r.status_code == 200, rota
        leitor = ScriptInline()
        leitor.feed(r.get_data(as_text=True))
        assert leitor.problemas == [], f"{rota}: {leitor.problemas}"


def test_inc11_troca_de_status_por_script_estatico(app, chefe):
    slug = make_game(app, "Hades")
    html = chefe.get(f"/jogos/{slug}").get_data(as_text=True)
    assert "data-envio-automatico" in html and "envio-automatico.js" in html
    js = chefe.get("/static/envio-automatico.js")
    assert js.status_code == 200 and b"data-envio-automatico" in js.data and b"submit()" in js.data
    js.close()


# ---------- INC-12 ----------
def test_inc12_cada_criterio_tem_teste_sem_rede():
    nomes = [n for n in globals() if n.startswith("test_inc")]
    for i in range(1, 12):
        assert any(n.startswith(f"test_inc{i:02d}_") for n in nomes), f"INC-{i:02d} sem teste"
