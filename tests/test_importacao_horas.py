"""Importação de horas e do diário (HRS-01..12), sem rede: a Steam é a fixture `api` do conftest."""
import io
import json
from datetime import date, datetime, time

import pytest

from app import tempo
from app.models import Activity, Game, JogoFonte, PreviaImportacao, SessaoJogo, Status, User, db
from conftest import http, login, make_game, make_user

STEAM_ID = "76561197960287930"
URL = "IPlayerService/GetOwnedGames"


@pytest.fixture(autouse=True)
def relogio(monkeypatch):
    monkeypatch.setattr(tempo, "agora", lambda: datetime(2026, 11, 16, 12, 0, tzinfo=tempo.FUSO))


@pytest.fixture
def ana(app, client):
    make_user(app, "ana")
    make_game(app, "Hades")
    make_game(app, "Celeste")
    login(client, "ana")
    return client


def sessoes(app):
    with app.app_context():
        return SessaoJogo.query.order_by(SessaoJogo.id).all()


def uid(app, nome="ana"):
    with app.app_context():
        return User.query.filter_by(username=nome).one().id


def editar(client, steam_id, nome="ana"):
    return client.post(f"/u/{nome}/editar", data={"display_name": nome, "bio": "", "steam_id": steam_id})


# ---------- HRS-01 ----------
@pytest.mark.parametrize("valor", [STEAM_ID, f"https://steamcommunity.com/profiles/{STEAM_ID}",
                                   f"https://steamcommunity.com/profiles/{STEAM_ID}/"])
def test_hrs_01_vincula_por_id_ou_url(app, ana, valor):
    assert editar(ana, valor).status_code == 302
    with app.app_context():
        assert User.query.filter_by(username="ana").one().steam_id == STEAM_ID


@pytest.mark.parametrize("valor", ["123", "86561197960287930", "https://steamcommunity.com/id/apelido",
                                   f"https://evil.com/profiles/{STEAM_ID}"])
def test_hrs_01_formato_invalido_volta_com_400(app, ana, valor):
    r = editar(ana, valor)
    assert r.status_code == 400 and "SteamID64" in r.get_data(as_text=True)


def test_hrs_01_id_unico_e_removivel(app, ana):
    make_user(app, "bia")
    bia = app.test_client()
    login(bia, "bia")
    editar(ana, STEAM_ID)
    r = editar(bia, STEAM_ID, "bia")
    assert r.status_code == 400 and "já está vinculada a outro usuário" in r.get_data(as_text=True)
    editar(ana, "")
    with app.app_context():
        assert User.query.filter_by(username="ana").one().steam_id is None


# ---------- HRS-02..05 ----------
@pytest.fixture
def steam(app, ana, api, monkeypatch):
    """Chave falsa, conta vinculada, Hades vinculado pelo JogoFonte e Celeste importado da Steam."""
    monkeypatch.setenv("STEAM_API_KEY", "chave-secreta-de-teste")
    editar(ana, STEAM_ID)
    with app.app_context():
        hades, celeste = Game.query.filter_by(slug="hades").one(), Game.query.filter_by(slug="celeste").one()
        db.session.add(JogoFonte(game_id=hades.id, fonte="steam", external_id="1145360"))
        celeste.source, celeste.external_id = "steam", "504230"
        db.session.commit()
    return api


def biblioteca(*jogos):
    return {"response": {"game_count": len(jogos),
                         "games": [{"appid": a, "name": "x", "playtime_forever": m} for a, m in jogos]}}


def importar(client):
    return client.post("/diario/importar/steam", follow_redirects=True).get_data(as_text=True)


def test_hrs_02_chamada_pela_base_comum_sem_vazar_a_chave(app, ana, steam):
    from app.fontes.base import USER_AGENT

    steam.rotas[URL] = biblioteca((1145360, 600))
    html = importar(ana)
    pedido = steam.pedidos[0]
    assert pedido.get_header("User-agent") == USER_AGENT
    assert "steamid=" + STEAM_ID in pedido.full_url and "include_appinfo=1" in pedido.full_url
    assert "include_played_free_games=1" in pedido.full_url
    assert "chave-secreta-de-teste" not in html


def test_hrs_03_cria_sessoes_do_catalogo_e_conta(app, ana, steam):
    steam.rotas[URL] = biblioteca((1145360, 600), (504230, 90), (999, 50), (1, 0))
    html = importar(ana)
    assert "jogos=3 sessoes=2 sem_novidade=0 fora_do_catalogo=1" in html
    s = sessoes(app)
    assert [(x.duracao_min, x.origem, x.jogada_em, x.inicio) for x in s] == [
        (600, "steam", date(2026, 11, 16), None), (90, "steam", date(2026, 11, 16), None)]


def test_hrs_04_idempotente_e_so_a_diferenca(app, ana, steam):
    steam.rotas[URL] = (biblioteca((1145360, 600)), biblioteca((1145360, 600)), biblioteca((1145360, 660)),
                        biblioteca((1145360, 500)))
    importar(ana)
    assert "sessoes=0 sem_novidade=1" in importar(ana)
    assert "sessoes=1 sem_novidade=0" in importar(ana)
    assert "sessoes=0 sem_novidade=1" in importar(ana)  # diferença negativa nunca cria
    assert [x.duracao_min for x in sessoes(app)] == [600, 60]


def test_hrs_04_saldo_da_steam_nao_muda_status_nem_tem_limite(app, ana, steam):
    steam.rotas[URL] = biblioteca((1145360, 5000))
    ana.post("/jogos/hades/status", data={"status": "planejado"})
    importar(ana)
    with app.app_context():
        assert Status.query.one().value == "planejado"
    assert sessoes(app)[0].duracao_min == 5000 and not sessoes(app)[0].zerou
    assert "importado da Steam" in ana.get("/diario").get_data(as_text=True)


def test_hrs_04_sessao_da_steam_nao_e_editavel(app, ana):
    with app.app_context():
        g = Game.query.filter_by(slug="hades").one()
        s = SessaoJogo(user_id=uid(app), game_id=g.id, jogada_em=date(2026, 11, 1), duracao_min=3000, origem="steam")
        db.session.add(s)
        db.session.commit()
        sid = s.id
    assert ana.get(f"/sessoes/{sid}/editar").status_code == 404
    r = ana.post(f"/sessoes/{sid}/editar", data={"jogada_em": "2026-11-01", "duracao": "60", "zerou": "1"})
    assert r.status_code == 404
    with app.app_context():
        assert db.session.get(SessaoJogo, sid).duracao_min == 3000 and Status.query.count() == 0


def test_hrs_05_sem_chave_nao_chama_a_rede(app, ana, api, monkeypatch):
    monkeypatch.delenv("STEAM_API_KEY", raising=False)
    editar(ana, STEAM_ID)
    assert "Importar da Steam" not in ana.get("/diario/importar").get_data(as_text=True)
    assert "Importação da Steam indisponível neste servidor" in importar(ana)
    assert api.pedidos == []


@pytest.mark.parametrize("resposta,mensagem", [
    ({"response": {}}, "o perfil da Steam precisa ser público"),
    ({"response": {"game_count": 0, "games": []}}, "o perfil da Steam precisa ser público"),
    (http(500), "Não foi possível falar com a Steam"),
    (http(429), "Não foi possível falar com a Steam"),
    (http(403), "Não foi possível falar com a Steam"),
])
def test_hrs_05_falhas_nao_gravam(app, ana, steam, resposta, mensagem):
    steam.rotas[URL] = resposta
    html = importar(ana)
    assert mensagem in html and "chave-secreta" not in html
    assert sessoes(app) == []


# ---------- HRS-06..09: CSV ----------
def enviar(client, texto, nome="diario.csv"):
    dados = texto.encode("utf-8") if isinstance(texto, str) else texto
    return client.post("/diario/importar", data={"arquivo": (io.BytesIO(dados), nome)},
                       content_type="multipart/form-data")


def previa(app, client, texto):
    r = enviar(client, texto)
    assert r.status_code == 302, r.get_data(as_text=True)
    with app.app_context():
        p = PreviaImportacao.query.order_by(PreviaImportacao.id.desc()).first()
        return p.id, json.loads(p.linhas)


def classes(linhas):
    return [(x["classe"], x["motivo"]) for x in linhas]


def test_hrs_06_bom_ponto_e_virgula_e_formatos(app, ana):
    texto = "﻿data;jogo;duracao;inicio;plataforma;zerei;anotacao\n16/11/2026;HADES;1h30;20:00;pc;sim;boa\n"
    _, linhas = previa(app, ana, texto)
    assert classes(linhas) == [("nova", "")]
    assert linhas[0]["dados"]["zerou"] is True and linhas[0]["dados"]["duracao_min"] == 90


@pytest.mark.parametrize("conteudo,mensagem", [
    ("jogo,duracao\nhades,90\n", "Faltam colunas obrigatórias"),
    (b"\x89PNG\r\n\x1a\n\x00\x00", "não é um CSV"),
    ("data,jogo,duracao\n" + "2026-11-01,hades,1\n" * 5001, "5000 linhas"),
    ("data,jogo,duracao\n" + "x" * (1024 * 1024), "1 MB"),
])
def test_hrs_06_arquivo_recusado_com_400(app, ana, conteudo, mensagem):
    r = enviar(ana, conteudo)
    assert r.status_code == 400 and mensagem in r.get_data(as_text=True)


@pytest.mark.parametrize("linha,motivo", [
    ("2026-13-01,hades,90", "data inválida"), ("2026-11-01,inexistente,90", "jogo não encontrado"),
    ("2026-11-01,hades,dez", "duração inválida"), ("2026-11-01,hades,0", "duração fora do intervalo"),
    ("2026-11-17,hades,90", "A data não pode estar no futuro"), ("2026-11-01,hades,90,25:00", "início inválido"),
    ("2026-11-01,hades,90,,Xbox", "plataforma não é do jogo"), ("2026-11-01,hades,90,,,talvez", "zerei deve ser"),
])
def test_hrs_07_cada_erro_com_motivo(app, ana, linha, motivo):
    _, linhas = previa(app, ana, "data,jogo,duracao,inicio,plataforma,zerei\n" + linha + "\n")
    assert linhas[0]["classe"] == "erro" and motivo in linhas[0]["motivo"]
    assert sessoes(app) == []


def test_hrs_07_previa_nao_grava_e_so_o_dono_confirma(app, ana):
    pid, _ = previa(app, ana, "data,jogo,duracao\n2026-11-01,hades,90\n2026-11-02,celeste,30\n")
    assert sessoes(app) == []
    assert "novas: <strong>2</strong>" in ana.get(f"/diario/importar/{pid}").get_data(as_text=True)
    make_user(app, "bia")
    bia = app.test_client()
    login(bia, "bia")
    assert bia.post(f"/diario/importar/{pid}/confirmar").status_code == 404
    assert bia.get(f"/diario/importar/{pid}").status_code == 404
    ana.post(f"/diario/importar/{pid}/confirmar")
    assert [(s.duracao_min, s.origem) for s in sessoes(app)] == [(90, "csv"), (30, "csv")]


def test_hrs_07_previa_expira_em_uma_hora(app, ana):
    from datetime import timedelta

    pid, _ = previa(app, ana, "data,jogo,duracao\n2026-11-01,hades,90\n")
    with app.app_context():
        p = db.session.get(PreviaImportacao, pid)
        p.created_at -= timedelta(minutes=61)
        db.session.commit()
    assert ana.post(f"/diario/importar/{pid}/confirmar").status_code == 404


def test_hrs_08_reenvio_e_repeticao_no_arquivo_sao_duplicadas(app, ana):
    texto = "data,jogo,duracao,inicio,zerei\n2026-11-01,hades,90,20:00,sim\n2026-11-01,hades,90,20:00,sim\n"
    pid, linhas = previa(app, ana, texto)
    assert [x["classe"] for x in linhas] == ["nova", "duplicada"]
    ana.post(f"/diario/importar/{pid}/confirmar")
    with app.app_context():
        st = Status.query.one()
        assert (st.value, st.zerado_em) == ("jogado", date(2026, 11, 1))  # DIA-03 e DIA-04 valem para o CSV
    _, linhas = previa(app, ana, texto)
    assert [x["classe"] for x in linhas] == ["duplicada", "duplicada"]


def test_hrs_08_confirmar_duas_previas_do_mesmo_arquivo_grava_uma_vez(app, ana):
    texto = "data,jogo,duracao,inicio\n2026-11-01,hades,90,20:00\n"
    p1, _ = previa(app, ana, texto)
    p2, _ = previa(app, ana, texto)
    ana.post(f"/diario/importar/{p1}/confirmar")
    r = ana.post(f"/diario/importar/{p2}/confirmar", follow_redirects=True)
    assert len(sessoes(app)) == 1 and "Ficaram de fora 1, que viraram duplicadas" in r.get_data(as_text=True)


def test_hrs_09_confirmar_confere_sessao_registrada_depois_da_previa(app, ana):
    pid, _ = previa(app, ana, "data,jogo,duracao,inicio\n2026-11-01,hades,90,20:00\n")
    ana.post("/jogos/celeste/sessoes", data={"jogada_em": "2026-11-01", "duracao": "60", "inicio": "20:30"})
    ana.post(f"/diario/importar/{pid}/confirmar")
    assert [s.origem for s in sessoes(app)] == ["manual"]


def test_hrs_08_linha_de_origem_steam_e_ignorada(app, ana):
    _, linhas = previa(app, ana, "data,jogo,duracao,origem\n2026-11-01,hades,90,steam\n2026-11-01,hades,30,csv\n")
    assert [x["classe"] for x in linhas] == ["steam", "nova"]


def test_hrs_09_sobreposicao_atravessando_a_meia_noite(app, ana):
    texto = "data,jogo,duracao,inicio\n2026-11-01,hades,3h,23:00\n2026-11-02,celeste,30,01:30\n2026-11-02,celeste,30,02:00\n"
    _, linhas = previa(app, ana, texto)
    assert classes(linhas)[0] == ("nova", "")
    assert classes(linhas)[1] == ("erro", "sobrepõe a sessão de 01/11 às 23:00")
    assert classes(linhas)[2] == ("nova", "")


def test_hrs_09_sessao_que_atravessa_a_meia_noite_ve_as_do_dia_seguinte(app, ana):
    assert ana.post("/jogos/hades/sessoes", data={"jogada_em": "2026-11-02", "duracao": "60", "inicio": "00:30"}).status_code == 302
    r = ana.post("/jogos/celeste/sessoes", data={"jogada_em": "2026-11-01", "duracao": "120", "inicio": "23:30"})
    assert r.status_code == 400 and "Sobrepõe a sessão de 02/11 às 00:30" in r.get_data(as_text=True)
    _, linhas = previa(app, ana, "data,jogo,duracao,inicio\n2026-11-01,celeste,120,23:30\n")
    assert classes(linhas) == [("erro", "sobrepõe a sessão de 02/11 às 00:30")]


def test_hrs_07_consultas_da_previa_nao_crescem_com_as_linhas(app, ana):
    from sqlalchemy import event

    def consultas(n):
        texto = "data,jogo,duracao,inicio\n" + "".join(
            f"2026-10-{d:02d},{('hades', 'Celeste')[d % 2]},30,{h:02d}:00\n" for d in range(1, 11) for h in range(n))
        contadas = []

        def conta(*a):
            contadas.append(1)
        with app.app_context():
            event.listen(db.engine, "before_cursor_execute", conta)
            assert enviar(ana, texto).status_code == 302
            event.remove(db.engine, "before_cursor_execute", conta)
        return len(contadas)
    assert consultas(10) == consultas(1)  # 100 linhas custam o mesmo que 10


def test_hrs_09_soma_do_dia_acima_de_24h(app, ana):
    _, linhas = previa(app, ana, "data,jogo,duracao\n2026-11-01,hades,20h\n2026-11-01,celeste,5h\n")
    assert classes(linhas)[1] == ("erro", "a soma das sessões do dia passaria de 24 horas")


def test_hrs_09_a_mesma_regra_vale_no_formulario_e_ignora_a_steam(app, ana):
    with app.app_context():
        g = Game.query.filter_by(slug="hades").one()
        db.session.add(SessaoJogo(user_id=uid(app), game_id=g.id, jogada_em=date(2026, 11, 1), duracao_min=3000,
                                  origem="steam"))
        db.session.add(SessaoJogo(user_id=uid(app), game_id=g.id, jogada_em=date(2026, 11, 1), duracao_min=60,
                                  inicio=time(20, 0)))
        db.session.commit()
    r = ana.post("/jogos/celeste/sessoes", data={"jogada_em": "2026-11-01", "duracao": "60", "inicio": "20:30"})
    assert r.status_code == 400 and "Sobrepõe a sessão de 01/11 às 20:00" in r.get_data(as_text=True)
    assert ana.post("/jogos/celeste/sessoes", data={"jogada_em": "2026-11-01", "duracao": "22h"}).status_code == 302
    r = ana.post("/jogos/celeste/sessoes", data={"jogada_em": "2026-11-01", "duracao": "2h"})
    assert r.status_code == 400 and "passaria de 24 horas" in r.get_data(as_text=True)


# ---------- HRS-10, HRS-11 ----------
def test_hrs_10_uma_atividade_por_importacao(app, ana, steam):
    steam.rotas[URL] = biblioteca((1145360, 600), (504230, 60))
    importar(ana)
    importar(ana)  # sem novidade: nenhuma atividade
    pid, _ = previa(app, ana, "data,jogo,duracao\n2026-11-01,hades,90\n2026-11-02,hades,90\n2026-11-03,hades,90\n")
    ana.post(f"/diario/importar/{pid}/confirmar")
    with app.app_context():
        assert [a.valor for a in Activity.query.filter_by(kind="importacao").order_by(Activity.id)] == [2, 3]
        assert Activity.query.filter_by(kind="sessao").count() == 0


def test_hrs_11_exportar_e_reimportar_da_tudo_duplicado(app, ana):
    with app.app_context():
        g = Game.query.filter_by(slug="hades").one()
        db.session.add(SessaoJogo(user_id=uid(app), game_id=g.id, jogada_em=date(2026, 11, 1), duracao_min=999,
                                  origem="steam"))
        db.session.commit()
    ana.post("/jogos/hades/sessoes", data={"jogada_em": "2026-11-02", "duracao": "90", "inicio": "10:00",
                                           "anotacao": "=SOMA(A1)", "zerou": "on"})
    ana.post("/jogos/celeste/sessoes", data={"jogada_em": "2026-11-03", "duracao": "30"})
    r = ana.get("/diario/exportar.csv")
    texto = r.get_data(as_text=True)
    assert r.mimetype == "text/csv" and texto.splitlines()[0] == "data,jogo,duracao,inicio,plataforma,zerei,anotacao,origem"
    assert "'=SOMA(A1)" in texto and ",hades," in texto
    _, linhas = previa(app, ana, texto)
    assert sorted(x["classe"] for x in linhas) == ["duplicada", "duplicada", "steam"]


def test_hrs_11_anotacao_de_1000_caracteres_com_formula_volta_duplicada(app, ana):
    ana.post("/jogos/hades/sessoes", data={"jogada_em": "2026-11-02", "duracao": "90", "anotacao": "-" * 1000})
    _, linhas = previa(app, ana, ana.get("/diario/exportar.csv").get_data(as_text=True))
    assert classes(linhas) == [("duplicada", "")]


@pytest.mark.parametrize("texto,esperado", [("=1", "'=1"), ("+1", "'+1"), ("-1", "'-1"), ("@x", "'@x"),
                                            ("\tx", "'\tx"), ("\rx", "'\rx"), ("ok", "ok")])
def test_hrs_11_sem_formula(texto, esperado):
    from app.diario_import import com_formula, sem_formula

    assert sem_formula(texto) == esperado and com_formula(esperado) == texto
