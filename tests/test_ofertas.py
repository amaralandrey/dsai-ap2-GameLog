from datetime import date, timedelta

import pytest

from app import jobs, ofertas, precos, tempo
from app.models import (AlertaPreco, Consentimento, EmailSaida, Game, JogoFonte, Notificacao, PrecoLoja,
                        PreferenciaNotificacao, Status, User, db)
from app.privacidade import consentimentos
from conftest import login, make_game, make_user

STEAM_URL = "https://store.steampowered.com/app/10"
GOG_URL = "https://www.gog.com/game/hades"
MENOR = "Ofertas não estão disponíveis para menores de 18 anos"


def agora():
    return tempo.agora_utc()


def jogo(app, titulo="Hades", steam="10", gog="20", url_gog=GOG_URL):
    slug = make_game(app, titulo)
    with app.app_context():
        g = Game.query.filter_by(slug=slug).one()
        db.session.add_all([JogoFonte(game=g, fonte="steam", external_id=steam, url_loja=STEAM_URL),
                            JogoFonte(game=g, fonte="gog", external_id=gog, url_loja=url_gog)])
        db.session.commit()
        return g.id


def preco(app, game_id, fonte, valor, desconto=0, horas=0, base=None):
    with app.app_context():
        momento = agora() - timedelta(hours=horas)
        db.session.add(PrecoLoja(game_id=game_id, fonte=fonte, moeda="BRL", preco_centavos=valor,
                                 preco_base_centavos=base or (valor * 100 // (100 - desconto) if desconto else valor),
                                 desconto_pct=desconto, coletado_em=momento, verificado_em=momento))
        db.session.commit()


def pessoa(app, nome="ana", consentiu=True, wishlist=(), adolescente=False):
    uid = make_user(app, nome)
    with app.app_context():
        u = db.session.get(User, uid)
        if consentiu:
            consentimentos.conceder(u, "ofertas", "configuracoes")
        for gid in wishlist:
            db.session.add(Status(user_id=uid, game_id=gid, value="planejado"))
        if adolescente:
            u.nascimento = date(tempo.hoje().year - 17, 1, 1)
        db.session.commit()
    return uid


def cliente(app, nome="ana"):
    c = app.test_client()
    login(c, nome)
    return c


def avaliar(app, game_id, desde):
    with app.app_context():
        ofertas.avaliar({game_id}, desde)
        db.session.commit()


def avisos(app, tipo=None):
    with app.app_context():
        q = Notificacao.query.filter(Notificacao.tipo.in_([tipo] if tipo else ["oferta", "preco_alvo"]))
        return [(n.user_id, n.tipo, n.texto) for n in q.order_by(Notificacao.id)]


def alertar(app, uid, game_id, alvo, disparado=None):
    with app.app_context():
        db.session.add(AlertaPreco(user_id=uid, game_id=game_id, preco_alvo_centavos=alvo, disparado_em=disparado))
        db.session.commit()


# ---------- OFR-01 ----------
def test_ofr_01_mais_barato_com_empate_steam_e_link_da_loja(app, client):
    gid = jogo(app)
    preco(app, gid, "steam", 4990)
    preco(app, gid, "gog", 4990, desconto=50)
    html = client.get("/jogos/hades").get_data(as_text=True)
    bloco = html.split("Onde está mais barato")[1].split("</p>")[0]
    assert f'href="{STEAM_URL}" target="_blank" rel="noopener nofollow">Steam' in bloco and "R$ 49,90" in bloco
    preco(app, gid, "gog", 3990, desconto=60)
    bloco = client.get("/jogos/hades").get_data(as_text=True).split("Onde está mais barato")[1].split("</p>")[0]
    assert f'href="{GOG_URL}"' in bloco and "R$ 39,90" in bloco and "−60%" in bloco


def test_ofr_01_sem_preco_atual_nao_tem_bloco_e_menor_nao_ve(app):
    gid = jogo(app)
    preco(app, gid, "steam", 4990, horas=24 * 8)
    pessoa(app, "teen", adolescente=True)
    assert "Onde está mais barato" not in app.test_client().get("/jogos/hades").get_data(as_text=True)
    preco(app, gid, "steam", 2990)
    html = cliente(app, "teen").get("/jogos/hades").get_data(as_text=True)
    assert "Onde está mais barato" not in html and "R$ 29,90" in html


# ---------- OFR-02 ----------
def test_ofr_02_lista_planejados_em_promocao_ordenados(app):
    a, b, c, d, e = (jogo(app, t, steam=str(i), gog=str(i + 50)) for i, t in enumerate(
        ("Alfa", "Beta", "Gama", "Delta", "Épsilon"), start=1))
    pessoa(app, wishlist=(a, b, c, d))
    preco(app, a, "steam", 3000, desconto=50)
    preco(app, b, "steam", 2000, desconto=75)
    preco(app, c, "gog", 1000, desconto=50)
    preco(app, c, "steam", 1500, desconto=50)
    preco(app, d, "steam", 1000)
    preco(app, e, "steam", 500, desconto=90)
    html = cliente(app).get("/ofertas").get_data(as_text=True)
    assert html.index("Beta") < html.index("Gama") < html.index("Alfa")
    assert "Delta" not in html and "Épsilon" not in html and html.count("Gama</a>") == 1
    assert "R$ 10,00 na GOG" in html


def test_ofr_02_vazio_e_login_obrigatorio(app, client):
    assert client.get("/ofertas").status_code == 302
    pessoa(app)
    assert "Nenhum jogo da sua wishlist está em promoção agora" in cliente(app).get("/ofertas").get_data(as_text=True)


def test_ofr_02_promocao_vencida_nao_entra(app):
    gid = jogo(app)
    pessoa(app, wishlist=(gid,))
    preco(app, gid, "steam", 1000, desconto=50, horas=24 * 8)
    assert "Nenhum jogo" in cliente(app).get("/ofertas").get_data(as_text=True)


# ---------- OFR-03 ----------
def test_ofr_03_filtros_combinaveis_e_invalidos_ignorados(app):
    a, b = jogo(app, "Alfa", "1", "51"), jogo(app, "Beta", "2", "52")
    pessoa(app, wishlist=(a, b))
    preco(app, a, "steam", 3000, desconto=20)
    preco(app, b, "gog", 1000, desconto=60)
    c = cliente(app)
    assert "Beta" in c.get("/ofertas?loja=gog").get_data(as_text=True)
    assert "Alfa" not in c.get("/ofertas?loja=gog").get_data(as_text=True)
    assert "Alfa" not in c.get("/ofertas?desconto_min=50").get_data(as_text=True)
    assert "Beta" not in c.get("/ofertas?preco_max=9,99").get_data(as_text=True)
    assert "Beta" in c.get("/ofertas?loja=gog&desconto_min=60&preco_max=10").get_data(as_text=True)
    r = c.get("/ofertas?loja=epic&desconto_min=95&preco_max=abc")
    assert r.status_code == 200 and "Alfa" in r.get_data(as_text=True) and "Beta" in r.get_data(as_text=True)


def test_ofr_03_ler_filtros():
    assert ofertas.ler_filtros({"loja": "steam", "desconto_min": "10", "preco_max": "49,90"}) == {
        "loja": "steam", "desconto_min": 10, "preco_max": 4990}
    assert ofertas.ler_filtros({"loja": "x", "desconto_min": "9", "preco_max": "-1"}) == {}
    assert ofertas.ler_filtros({"desconto_min": "91", "preco_max": "nan"}) == {}


# ---------- OFR-04 ----------
def test_ofr_04_cria_edita_e_zera_disparo(app):
    gid = jogo(app)
    uid = pessoa(app)
    preco(app, gid, "steam", 5000)
    c = cliente(app)
    c.post("/jogos/hades/alerta", data={"preco_alvo": "30"})
    with app.app_context():
        AlertaPreco.query.one().disparado_em = agora()
        db.session.commit()
    c.post("/jogos/hades/alerta", data={"preco_alvo": "25,50"})
    with app.app_context():
        a = AlertaPreco.query.one()
        assert (a.user_id, a.preco_alvo_centavos, a.disparado_em) == (uid, 2550, None)


@pytest.mark.parametrize("valor", ["0", "-5", "abc", "50", "60"])
def test_ofr_04_preco_alvo_invalido(app, valor):
    gid = jogo(app)
    pessoa(app)
    preco(app, gid, "steam", 5000)
    preco(app, gid, "gog", 4000)
    r = cliente(app).post("/jogos/hades/alerta", data={"preco_alvo": valor}, follow_redirects=True)
    assert "Informe um preço-alvo maior que zero e menor que R$ 50,00." in r.get_data(as_text=True)
    with app.app_context():
        assert AlertaPreco.query.count() == 0


def test_ofr_04_limite_de_50_alertas(app):
    uid = pessoa(app)
    ids = [jogo(app, f"Jogo {i}", str(i), str(1000 + i)) for i in range(51)]
    for gid in ids[:50]:
        alertar(app, uid, gid, 100)
    c = cliente(app)
    r = c.post("/jogos/jogo-50/alerta", data={"preco_alvo": "1"}, follow_redirects=True)
    assert "Você já tem 50 alertas de preço" in r.get_data(as_text=True)
    c.post("/jogos/jogo-0/alerta", data={"preco_alvo": "2"})
    with app.app_context():
        assert AlertaPreco.query.count() == 50
        assert AlertaPreco.query.filter_by(game_id=ids[0]).one().preco_alvo_centavos == 200


def test_ofr_04_lista_apaga_e_alerta_de_outro_e_404(app):
    gid = jogo(app)
    ana, bia = pessoa(app), pessoa(app, "bia")
    alertar(app, ana, gid, 1000)
    alertar(app, bia, gid, 2000)
    with app.app_context():
        de_bia = AlertaPreco.query.filter_by(user_id=bia).one().id
        de_ana = AlertaPreco.query.filter_by(user_id=ana).one().id
    c = cliente(app)
    html = c.get("/ofertas/alertas").get_data(as_text=True)
    assert "R$ 10,00" in html and "R$ 20,00" not in html
    assert c.post(f"/ofertas/alertas/{de_bia}/apagar").status_code == 404
    c.post(f"/ofertas/alertas/{de_ana}/apagar")
    with app.app_context():
        assert [a.user_id for a in AlertaPreco.query] == [bia]


# ---------- OFR-05 ----------
def test_ofr_05_promocao_avisa_uma_vez_e_de_novo_depois_de_acabar(app):
    gid = jogo(app)
    uid = pessoa(app, wishlist=(gid,))
    pessoa(app, "bia")
    preco(app, gid, "steam", 5000, horas=48)
    inicio = agora() - timedelta(minutes=1)
    preco(app, gid, "steam", 2500, desconto=50)
    avaliar(app, gid, inicio)
    assert avisos(app) == [(uid, "oferta", "Hades com 50% de desconto na Steam: R$ 25,00")]
    inicio = agora()
    preco(app, gid, "steam", 2400, desconto=50, base=5000)
    preco(app, gid, "gog", 3000)
    avaliar(app, gid, inicio)
    assert len(avisos(app)) == 1
    inicio = agora()
    preco(app, gid, "steam", 2000, desconto=60)
    avaliar(app, gid, inicio)
    assert len(avisos(app)) == 2
    inicio = agora()
    preco(app, gid, "steam", 5000)
    avaliar(app, gid, inicio)
    inicio = agora()
    preco(app, gid, "steam", 2500, desconto=50)
    avaliar(app, gid, inicio)
    assert len(avisos(app)) == 3


def test_ofr_05_job_de_precos_avalia_os_jogos_que_mudaram(app, monkeypatch):
    gid = jogo(app)
    uid = pessoa(app, wishlist=(gid,))

    def coletar(fonte, limite, mudaram):
        if fonte == "steam":
            preco(app, gid, "steam", 1000, desconto=80)
            mudaram.add(gid)

    monkeypatch.setattr(precos, "coletar", coletar)
    with app.app_context():
        jobs.enfileirar("precos")
        assert jobs.ciclo()["ok"] == 1
    assert [a[:2] for a in avisos(app)] == [(uid, "oferta")]


# ---------- OFR-06 ----------
def test_ofr_06_preco_alvo_avisa_e_nao_repete_ate_editar(app):
    gid = jogo(app)
    uid = pessoa(app)
    alertar(app, uid, gid, 3000)
    inicio = agora()
    preco(app, gid, "steam", 3500)
    avaliar(app, gid, inicio)
    assert avisos(app) == []
    preco(app, gid, "gog", 3000)
    avaliar(app, gid, inicio)
    assert avisos(app) == [(uid, "preco_alvo", "Hades chegou a R$ 30,00 na GOG (seu alvo: R$ 30,00)")]
    with app.app_context():
        assert AlertaPreco.query.one().disparado_em is not None
    preco(app, gid, "gog", 2000)
    avaliar(app, gid, inicio)
    assert len(avisos(app)) == 1
    cliente(app).post("/jogos/hades/alerta", data={"preco_alvo": "25"})
    avaliar(app, gid, inicio)
    assert len(avisos(app)) == 2


# ---------- OFR-07 ----------
def test_ofr_07_sem_consentimento_grava_alerta_e_nao_avisa(app):
    gid = jogo(app)
    uid = pessoa(app, consentiu=False, wishlist=(gid,))
    c = cliente(app)
    html = c.get("/jogos/hades").get_data(as_text=True)
    assert 'name="consentir_ofertas"' in html and 'name="consentir_ofertas" value="1" style="width:auto">' in html
    c.post("/jogos/hades/alerta", data={"preco_alvo": "30"})
    inicio = agora()
    preco(app, gid, "steam", 2500, desconto=50)
    avaliar(app, gid, inicio)
    with app.app_context():
        assert avisos(app) == [] and AlertaPreco.query.one().disparado_em is None
        assert not consentimentos.tem_consentimento(db.session.get(User, uid), "ofertas")


def test_ofr_07_caixa_marcada_concede_e_revogar_interrompe(app):
    gid = jogo(app)
    uid = pessoa(app, consentiu=False, wishlist=(gid,))
    c = cliente(app)
    c.post("/jogos/hades/alerta", data={"preco_alvo": "30", "consentir_ofertas": "1"})
    with app.app_context():
        assert Consentimento.query.filter_by(user_id=uid, finalidade="ofertas").one().origem == "contexto"
    assert 'name="consentir_ofertas"' not in c.get("/jogos/hades").get_data(as_text=True)
    c.post("/conta/privacidade/consentimentos/ofertas/revogar")
    inicio = agora()
    preco(app, gid, "steam", 2500, desconto=50)
    avaliar(app, gid, inicio)
    with app.app_context():
        assert avisos(app) == [] and AlertaPreco.query.count() == 1


def test_ofr_07_secao_de_ofertas_do_resumo_so_com_consentimento(app):
    gid = jogo(app)
    ana = pessoa(app, wishlist=(gid,))
    bia = pessoa(app, "bia", consentiu=False, wishlist=(gid,))
    preco(app, gid, "steam", 2500, desconto=50)
    with app.app_context():
        assert ofertas.do_resumo(db.session.get(User, ana)) == [
            ("Hades: 50% de desconto na Steam, R$ 25,00", "/jogos/hades")]
        assert ofertas.do_resumo(db.session.get(User, bia)) == []


def test_ofr_07_resumo_semanal_leva_a_secao_de_ofertas(app):
    from app import emails

    gid = jogo(app)
    uid = pessoa(app, wishlist=(gid,))
    preco(app, gid, "steam", 2500, desconto=50)
    with app.app_context():
        consentimentos.conceder(db.session.get(User, uid), "email_resumo", "configuracoes")
        db.session.commit()
        emails.resumo_do_usuario(uid, tempo.semana_iso(tempo.hoje()))
        e = EmailSaida.query.one()
        assert "Em promoção na sua wishlist" in e.corpo_texto and "Hades: 50% de desconto" in e.corpo_texto


# ---------- OFR-08 ----------
def test_ofr_08_menor_recebe_403_e_nao_ve_formulario(app):
    gid = jogo(app)
    pessoa(app, "teen", adolescente=True, consentiu=False)
    preco(app, gid, "steam", 5000)
    c = cliente(app, "teen")
    for r in (c.get("/ofertas"), c.get("/ofertas/alertas"), c.post("/jogos/hades/alerta", data={"preco_alvo": "1"})):
        assert r.status_code == 403 and MENOR in r.get_data(as_text=True)
    html = c.get("/jogos/hades").get_data(as_text=True)
    assert "/jogos/hades/alerta" not in html and 'href="/ofertas"' not in html
    with app.app_context():
        assert AlertaPreco.query.count() == 0


def test_ofr_08_menor_com_consentimento_antigo_nao_recebe_aviso(app):
    gid = jogo(app)
    uid = pessoa(app, "teen", wishlist=(gid,))
    alertar(app, uid, gid, 5000)
    with app.app_context():
        db.session.get(User, uid).nascimento = date(tempo.hoje().year - 17, 1, 1)
        db.session.commit()
    inicio = agora()
    preco(app, gid, "steam", 2500, desconto=50)
    avaliar(app, gid, inicio)
    assert avisos(app) == []


# ---------- OFR-09 ----------
def test_ofr_09_email_pelo_modelo_oferta(app):
    gid = jogo(app)
    uid = pessoa(app, wishlist=(gid,))
    with app.app_context():
        db.session.add(PreferenciaNotificacao(user_id=uid, tipo="oferta", site=True, email=True))
        db.session.commit()
    inicio = agora()
    preco(app, gid, "steam", 2500, desconto=50)
    avaliar(app, gid, inicio)
    with app.app_context():
        e = EmailSaida.query.one()
        assert (e.modelo, e.tipo, e.status) == ("oferta", "oferta", "pendente")
        assert "R$ 25,00" in e.corpo_texto and "Steam" in e.corpo_texto and STEAM_URL in e.corpo_texto
        assert "/email/descadastro/" in e.corpo_texto


def test_ofr_09_sem_preferencia_por_email_nao_enfileira(app):
    gid = jogo(app)
    alertar(app, pessoa(app), gid, 3000)
    inicio = agora()
    preco(app, gid, "gog", 2000)
    avaliar(app, gid, inicio)
    with app.app_context():
        assert len(avisos(app)) == 1 and EmailSaida.query.count() == 0


# ---------- OFR-10 ----------
def test_ofr_10_link_da_loja_sem_parametros_e_so_http(app):
    gid = jogo(app, url_gog="javascript:alert(1)")
    pessoa(app, wishlist=(gid,))
    preco(app, gid, "gog", 1000, desconto=50)
    c = cliente(app)
    for html in (c.get("/ofertas").get_data(as_text=True), c.get("/jogos/hades").get_data(as_text=True)):
        assert "javascript:" not in html
    preco(app, gid, "steam", 900, desconto=55)
    html = c.get("/ofertas").get_data(as_text=True)
    assert f'<a href="{STEAM_URL}" target="_blank" rel="noopener nofollow">Steam</a>' in html
    assert ofertas.url_segura(STEAM_URL) == STEAM_URL and ofertas.url_segura("ftp://x") == ""


# ---------- OFR-11 ----------
def test_ofr_11_orcamento_de_consultas_nao_cresce_com_pessoas(app, monkeypatch):
    from sqlalchemy import event

    gid = jogo(app)
    preco(app, gid, "steam", 5000, horas=48)
    monkeypatch.setattr(ofertas, "notificar", lambda *a, **k: None)

    def contar():
        inicio = agora()
        preco(app, gid, "steam", 2000, desconto=60)
        contadas = []

        def conta(*_):
            contadas.append(1)
        with app.app_context():
            event.listen(db.engine, "before_cursor_execute", conta)
            ofertas.avaliar({gid}, inicio)
            event.remove(db.engine, "before_cursor_execute", conta)
            db.session.rollback()
        preco(app, gid, "steam", 5000)
        return len(contadas)

    alertar(app, pessoa(app, "p0", wishlist=(gid,)), gid, 4000)
    uma = contar()
    for i in range(1, 10):
        alertar(app, pessoa(app, f"p{i}", wishlist=(gid,)), gid, 4000)
    assert uma == contar() <= 6


# ---------- OFR-12 ----------
def test_ofr_12_testes_sem_rede(app, monkeypatch):
    from app.fontes import base

    monkeypatch.setattr(base, "urlopen", lambda *a, **k: pytest.fail("rede"))
    gid = jogo(app)
    uid = pessoa(app, wishlist=(gid,))
    alertar(app, uid, gid, 3000)
    inicio = agora()
    preco(app, gid, "steam", 2500, desconto=50)
    avaliar(app, gid, inicio)
    assert {t for _, t, _ in avisos(app)} == {"oferta", "preco_alvo"}
