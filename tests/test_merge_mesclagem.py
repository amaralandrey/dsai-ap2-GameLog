import re
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app import create_app
from app.models import (
    Activity, AuditLog, Comment, Dismissed, Game, GameList, JogoFonte, Like, ListItem, PendenciaMerge, Report, Review,
    Status, User, db, game_stats,
)
from app.recomendacoes import recommend
from app.sync import sincronizar
from conftest import login, make_game, make_user

JAN, FEV, MAR = datetime(2026, 1, 1), datetime(2026, 2, 1), datetime(2026, 3, 1)


@pytest.fixture
def mundo(app):
    """Hades (principal) e Hades GOTY (secundário) com histórico dos dois lados e conflitos de MRG-07."""
    for nome in ("ana", "bia", "caio", "dani", "eva"):
        make_user(app, nome)
    make_user(app, "adm", role="admin")
    make_game(app, "Hades", dev="Supergiant", year=2020)
    make_game(app, "Hades GOTY", dev="Supergiant", year=2020)
    with app.app_context():
        u = {x.username: x.id for x in User.query}
        p, s = Game.query.filter_by(slug="hades").one().id, Game.query.filter_by(slug="hades-goty").one().id
        db.session.add_all([
            JogoFonte(game_id=p, fonte="steam", external_id="1145360", url_loja="https://store.steampowered.com/app/1145360"),
            JogoFonte(game_id=s, fonte="steam", external_id="999"),
            JogoFonte(game_id=s, fonte="gog", external_id="1207", url_loja="https://www.gog.com/game/hades"),
            JogoFonte(game_id=s, fonte="rawg", external_id="5"),
        ])

        def review(user, game, score, quando):
            r = Review(user_id=u[user], game_id=game, score=score, text=f"{user} {score}", created_at=quando)
            db.session.add(r)
            db.session.flush()
            return r

        ana_s = review("ana", s, 9, JAN)
        bia_p, bia_s = review("bia", p, 4, JAN), review("bia", s, 8, FEV)  # a do secundário é a mais recente
        caio_p, caio_s = review("caio", p, 7, MAR), review("caio", s, 2, JAN)  # a do principal é a mais recente
        db.session.add_all([
            Like(user_id=u["bia"], review_id=ana_s.id),
            Comment(user_id=u["caio"], review_id=ana_s.id, text="concordo"),
            Report(review_id=ana_s.id, reporter_id=u["caio"], reason="spam"),
            Status(user_id=u["ana"], game_id=s, value="jogado", updated_at=JAN),
            Status(user_id=u["bia"], game_id=p, value="planejado", updated_at=JAN),
            Status(user_id=u["bia"], game_id=s, value="jogando", updated_at=FEV),
            Status(user_id=u["caio"], game_id=p, value="jogado", updated_at=MAR),
            Status(user_id=u["caio"], game_id=s, value="abandonado", updated_at=JAN),
            Activity(user_id=u["ana"], kind="avaliou", game_id=s, review_id=ana_s.id),
            Activity(user_id=u["bia"], kind="avaliou", game_id=s, review_id=bia_s.id),
            Activity(user_id=u["bia"], kind="avaliou", game_id=p, review_id=bia_p.id),
            Activity(user_id=u["ana"], kind="jogado", game_id=s),
            Dismissed(user_id=u["caio"], game_id=s),
            Dismissed(user_id=u["dani"], game_id=p),
            Dismissed(user_id=u["dani"], game_id=s),
        ])
        l1 = GameList(user_id=u["ana"], name="Favoritos")
        l2 = GameList(user_id=u["bia"], name="Roguelikes")
        db.session.add_all([l1, l2])
        db.session.flush()
        db.session.add_all([ListItem(list_id=l1.id, game_id=s, position=1),
                            ListItem(list_id=l2.id, game_id=p, position=1), ListItem(list_id=l2.id, game_id=s, position=2)])
        pend = PendenciaMerge(jogo_a_id=p, jogo_b_id=s, semelhanca=80)
        db.session.add(pend)
        db.session.commit()
        ids = dict(p=p, s=s, pend=pend.id, l1=l1.id, l2=l2.id, ana_s=ana_s.id, bia_p=bia_p.id, bia_s=bia_s.id,
                   caio_p=caio_p.id, caio_s=caio_s.id, **u)
    adm = app.test_client()
    login(adm, "adm")
    return SimpleNamespace(app=app, adm=adm, ids=ids)


def mesclar(m, principal=None, pend=None):
    return m.adm.post("/admin/catalogo/mesclar", data={"pendencia_id": pend or m.ids["pend"],
                                                     "principal_id": principal or m.ids["p"]})


def totais(ids):
    par = (ids["p"], ids["s"])
    return {
        "avaliacoes": Review.query.filter(Review.game_id.in_(par)).count(),
        "status": Status.query.filter(Status.game_id.in_(par)).count(),
        "itens": ListItem.query.filter(ListItem.game_id.in_(par)).count(),
        "atividades": Activity.query.filter(Activity.game_id.in_(par)).count(),
        "dispensas": Dismissed.query.filter(Dismissed.game_id.in_(par)).count(),
        "vinculos": JogoFonte.query.filter(JogoFonte.game_id.in_(par)).count(),
        "curtidas": Like.query.count(), "comentarios": Comment.query.count(), "denuncias": Report.query.count(),
    }


def de(modelo, game_id, **filtro):
    return modelo.query.filter_by(game_id=game_id, **filtro)


# ---------- MRG-15 / MRG-06 / MRG-07 ----------
def test_mrg15_soma_de_avaliacoes_status_e_itens_igual_antes_e_depois(mundo):
    with mundo.app.app_context():
        antes = totais(mundo.ids)
    assert mesclar(mundo).status_code == 302
    with mundo.app.app_context():
        assert totais(mundo.ids) == antes
        assert de(Review, mundo.ids["p"]).count() == 3 and de(Review, mundo.ids["s"]).count() == 2


def test_mrg06_leva_vinculos_avaliacoes_status_listas_feed_e_dispensas(mundo):
    mesclar(mundo)
    i = mundo.ids
    with mundo.app.app_context():
        assert sorted((v.fonte, v.external_id) for v in de(JogoFonte, i["p"])) == [
            ("gog", "1207"), ("rawg", "5"), ("steam", "1145360")]
        assert [(v.fonte, v.external_id) for v in de(JogoFonte, i["s"])] == [("steam", "999")]  # FNT-04
        ana = db.session.get(Review, i["ana_s"])
        assert ana.game_id == i["p"]  # curtida, comentário e denúncia apontam para a avaliação e vão junto
        assert [len(Like.query.filter_by(review_id=ana.id).all()), len(ana.comments),
                Report.query.filter_by(review_id=ana.id).count()] == [1, 1, 1]
        assert de(Status, i["p"], user_id=i["ana"]).one().value == "jogado"
        assert ListItem.query.filter_by(list_id=i["l1"]).one().game_id == i["p"]
        assert {a.kind for a in de(Activity, i["p"], user_id=i["ana"])} == {"avaliou", "jogado"}
        assert de(Dismissed, i["p"], user_id=i["caio"]).count() == 1


def test_mrg07_conflitos_mais_recente_no_principal_e_nada_apagado(mundo):
    mesclar(mundo)
    i = mundo.ids
    with mundo.app.app_context():
        assert db.session.get(Review, i["bia_s"]).game_id == i["p"]  # a mais recente da bia era do secundário
        assert db.session.get(Review, i["bia_p"]).game_id == i["s"]
        assert db.session.get(Review, i["caio_p"]).game_id == i["p"]  # a do caio no principal já era a mais recente
        assert db.session.get(Review, i["caio_s"]).game_id == i["s"]
        assert (de(Status, i["p"], user_id=i["bia"]).one().value, de(Status, i["s"], user_id=i["bia"]).one().value) == (
            "jogando", "planejado")
        assert (de(Status, i["p"], user_id=i["caio"]).one().value, de(Status, i["s"], user_id=i["caio"]).one().value) == (
            "jogado", "abandonado")
        assert sorted((x.game_id, x.position) for x in ListItem.query.filter_by(list_id=i["l2"])) == [
            (i["p"], 1), (i["s"], 2)]  # LIS-06: o item do principal fica; o do secundário fica no secundário
        assert {d.game_id for d in Dismissed.query.filter_by(user_id=i["dani"])} == {i["p"], i["s"]}
        a = Activity.query.filter_by(review_id=i["bia_p"]).one()
        assert a.game_id == i["s"]  # a atividade acompanha a avaliação que ficou no secundário


def test_mrg06_principal_pode_ser_o_segundo_jogo(mundo):
    mesclar(mundo, principal=mundo.ids["s"])
    with mundo.app.app_context():
        p = db.session.get(Game, mundo.ids["p"])
        assert p.mesclado_em_id == mundo.ids["s"] and p.removed_at is not None
        assert db.session.get(Review, mundo.ids["caio_p"]).game_id == mundo.ids["s"]  # MRG-07 vale nos dois sentidos


@pytest.mark.parametrize("principal", ["", "abc", "9999"])
def test_mrg06_principal_fora_do_par_e_recusado(mundo, principal):
    r = mundo.adm.post("/admin/catalogo/mesclar", data={"pendencia_id": mundo.ids["pend"], "principal_id": principal},
                       follow_redirects=True)
    assert "Escolha um dos dois jogos" in r.get_data(as_text=True)
    with mundo.app.app_context():
        assert db.session.get(PendenciaMerge, mundo.ids["pend"]).status == "pendente"
        assert Game.query.filter(Game.removed_at.isnot(None)).count() == 0


@pytest.mark.parametrize("rota", ["mesclar", "ignorar-merge"])
@pytest.mark.parametrize("pend", ["", "x", "9999"])
def test_mrg06_pendencia_inexistente_404(mundo, rota, pend):
    assert mundo.adm.post(f"/admin/catalogo/{rota}", data={"pendencia_id": pend}).status_code == 404


def test_mrg06_jogo_ja_mesclado_em_outra_pendencia_e_recusado(mundo):
    make_game(mundo.app, "Hades Deluxe", dev="Supergiant", year=2020)
    with mundo.app.app_context():
        t = Game.query.filter_by(slug="hades-deluxe").one().id
        outra = PendenciaMerge(jogo_a_id=mundo.ids["s"], jogo_b_id=t, semelhanca=85)
        db.session.add(outra)
        db.session.commit()
        outra_id = outra.id
    mesclar(mundo)
    r = mesclar(mundo, principal=t, pend=outra_id)
    with mundo.app.app_context():
        assert db.session.get(PendenciaMerge, outra_id).status == "pendente"
        assert db.session.get(Game, t).removed_at is None
    assert "já foi mesclado" in mundo.adm.get(r.headers["Location"]).get_data(as_text=True)


# ---------- MRG-08 ----------
def test_mrg08_secundario_removido_aponta_para_o_principal_e_responde_301(mundo):
    mesclar(mundo)
    with mundo.app.app_context():
        s = db.session.get(Game, mundo.ids["s"])
        assert s.removed_at is not None and s.mesclado_em_id == mundo.ids["p"]
    c = mundo.app.test_client()
    for antiga, nova in [("/jogos/hades-goty", "/jogos/hades"), ("/jogos/hades-goty?ordem=recentes", "/jogos/hades?ordem=recentes"),
                         ("/api/jogos/hades-goty", "/api/jogos/hades"),
                         ("/api/jogos/hades-goty/avaliacoes?page=2", "/api/jogos/hades/avaliacoes?page=2")]:
        r = c.get(antiga)
        assert r.status_code == 301 and r.headers["Location"] == nova
    assert c.get("/jogos/hades-goty", follow_redirects=True).status_code == 200
    assert c.get("/jogos/nao-existe").status_code == 404 and c.get("/api/jogos/nao-existe").status_code == 404


def test_mrg08_cadeia_de_mesclagens_leva_ao_ultimo_principal(mundo):
    make_game(mundo.app, "Hades Deluxe", dev="Supergiant", year=2020)
    mesclar(mundo)
    with mundo.app.app_context():
        t = Game.query.filter_by(slug="hades-deluxe").one().id
        db.session.add(PendenciaMerge(jogo_a_id=mundo.ids["p"], jogo_b_id=t, semelhanca=85))
        db.session.commit()
        pend = PendenciaMerge.query.filter_by(jogo_b_id=t).one().id
    mesclar(mundo, principal=t, pend=pend)
    assert mundo.app.test_client().get("/jogos/hades-goty").headers["Location"] == "/jogos/hades-deluxe"


def test_mrg08_principal_removido_depois_faz_o_secundario_responder_404(mundo):
    mesclar(mundo)
    mundo.adm.post(f"/admin/jogos/{mundo.ids['p']}/remover")
    c = mundo.app.test_client()
    assert c.get("/jogos/hades-goty").status_code == 404 and c.get("/api/jogos/hades-goty").status_code == 404


def test_mrg08_restaurar_mesclado_e_recusado_com_mensagem(mundo):
    mesclar(mundo)
    r = mundo.adm.post(f"/admin/jogos/{mundo.ids['s']}/restaurar", follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "foi mesclado em “Hades” e não pode ser restaurado" in html and "mesclado em" in html
    with mundo.app.app_context():
        assert db.session.get(Game, mundo.ids["s"]).removed_at is not None
        assert AuditLog.query.filter_by(action="jogo.restaurar").count() == 0


def test_mrg08_sync_ignora_item_cujo_vinculo_ficou_no_mesclado(mundo):
    mesclar(mundo)
    item = {"external_id": "999", "title": "Título novo", "synopsis": "x", "release_date": date(2020, 1, 1),
            "developer": "Outra", "genres": [], "platforms": [], "cover_url": None}
    with mundo.app.app_context():
        assert sincronizar("steam", modulo=SimpleNamespace(listar=lambda limite, termo=None: [item])) == (0, 0, 1, 0)
        assert db.session.get(Game, mundo.ids["s"]).title == "Hades GOTY"


# ---------- MRG-09 ----------
def test_mrg09_media_histograma_e_recomendacoes_contam_as_avaliacoes_recebidas(mundo):
    with mundo.app.app_context():
        assert game_stats(mundo.ids["p"])["total"] == 2
    mesclar(mundo)
    with mundo.app.app_context():
        st = game_stats(mundo.ids["p"])
        assert (st["avg"], st["total"], st["hist"][9], st["hist"][8], st["hist"][7]) == (8.0, 3, 1, 1, 1)
        recs = {g.id: nota for g, nota, _ in recommend(mundo.ids["eva"])[0]}
        assert recs == {mundo.ids["p"]: 8.0}  # o secundário saiu do catálogo
    d = mundo.app.test_client().get("/api/jogos/hades").get_json()
    assert (d["nota_media"], d["avaliacoes"]) == (8.0, 3)


@pytest.mark.parametrize("rota", ["mesclar", "ignorar-merge"])
def test_mrg09_pendencia_ja_decidida_responde_com_mensagem_e_nao_altera(mundo, rota):
    mesclar(mundo)
    with mundo.app.app_context():
        antes = totais(mundo.ids)
    r = mundo.adm.post(f"/admin/catalogo/{rota}", data={"pendencia_id": mundo.ids["pend"], "principal_id": mundo.ids["s"]},
                       follow_redirects=True)
    assert "Esta pendência já foi decidida." in r.get_data(as_text=True)
    with mundo.app.app_context():
        assert totais(mundo.ids) == antes and db.session.get(PendenciaMerge, mundo.ids["pend"]).status == "mesclada"
        assert db.session.get(Game, mundo.ids["p"]).removed_at is None
        assert AuditLog.query.filter(AuditLog.action.like("catalogo.%")).count() == 1


# ---------- MRG-10 ----------
def test_mrg10_ignorar_marca_e_o_par_nunca_volta(mundo):
    r = mundo.adm.post("/admin/catalogo/ignorar-merge", data={"pendencia_id": mundo.ids["pend"]}, follow_redirects=True)
    assert "não será sugerido de novo" in r.get_data(as_text=True) and "Hades GOTY" not in r.get_data(as_text=True)
    with mundo.app.app_context():
        p = db.session.get(PendenciaMerge, mundo.ids["pend"])
        assert (p.status, p.decidida_por) == ("ignorada", mundo.ids["adm"]) and p.decidida_em is not None
        assert Game.query.filter(Game.removed_at.isnot(None)).count() == 0
    assert mundo.app.test_cli_runner().invoke(args=["sugerir-duplicatas"]).output == "pendencias=0\n"
    with mundo.app.app_context():
        assert PendenciaMerge.query.count() == 1


# ---------- MRG-11 ----------
def test_mrg11_mesclar_e_ignorar_gravam_auditoria_com_ids_e_slugs(mundo):
    make_game(mundo.app, "Celeste")
    make_game(mundo.app, "Celeste 2")
    with mundo.app.app_context():
        c1, c2 = (Game.query.filter_by(slug=s).one().id for s in ("celeste", "celeste-2"))
        db.session.add(PendenciaMerge(jogo_a_id=c1, jogo_b_id=c2, semelhanca=75))
        db.session.commit()
        outra = PendenciaMerge.query.filter_by(jogo_a_id=c1).one().id
    mesclar(mundo)
    mundo.adm.post("/admin/catalogo/ignorar-merge", data={"pendencia_id": outra})
    with mundo.app.app_context():
        log = {a.action: (a.detail, a.admin_id) for a in AuditLog.query}
    i = mundo.ids
    assert log["catalogo.mesclar"] == (f"principal #{i['p']} hades e #{i['s']} hades-goty (secundário)", i["adm"])
    assert log["catalogo.ignorar"] == (f"#{c1} celeste e #{c2} celeste-2", i["adm"])


@pytest.mark.parametrize("metodo, rota", [("get", "/admin/catalogo/duplicatas"), ("post", "/admin/catalogo/mesclar"),
                                          ("post", "/admin/catalogo/ignorar-merge")])
def test_mrg11_rotas_do_catalogo_exigem_admin(mundo, metodo, rota):
    anonimo = getattr(mundo.app.test_client(), metodo)(rota, data={"pendencia_id": mundo.ids["pend"]})
    assert anonimo.status_code == 302 and "/entrar" in anonimo.headers["Location"]
    jogador = mundo.app.test_client()
    login(jogador, "ana")
    assert getattr(jogador, metodo)(rota, data={"pendencia_id": mundo.ids["pend"], "principal_id": mundo.ids["p"]}).status_code == 403
    with mundo.app.app_context():
        assert db.session.get(PendenciaMerge, mundo.ids["pend"]).status == "pendente"


@pytest.mark.parametrize("rota", ["mesclar", "ignorar-merge"])
def test_mrg11_mesclar_e_ignorar_exigem_csrf(tmp_path, rota):
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path}/csrf.db"})
    assert app.config["CSRF"] is True
    assert app.test_client().post(f"/admin/catalogo/{rota}", data={"pendencia_id": 1}).status_code == 400
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


# ---------- MRG-05 ----------
def test_mrg05_lista_os_dois_jogos_lado_a_lado(mundo):
    html = mundo.adm.get("/admin/catalogo/duplicatas").get_data(as_text=True)
    bloco = html[html.index('id="pendencia-'):]
    assert "Semelhança 80" in bloco and bloco.index("Hades<") < bloco.index("Hades GOTY<")
    assert bloco.count("2020 · Supergiant") == 2
    assert "2 avaliações" in bloco and "3 avaliações" in bloco
    assert '<span class="tag">steam</span>' in bloco and '<span class="tag">gog</span>' in bloco
    assert bloco.count('name="principal_id"') == 2 and 'action="/admin/catalogo/ignorar-merge"' in bloco
    assert "Duplicatas</strong><br><span class=\"mut\">1 pendente<" in mundo.adm.get("/admin/").get_data(as_text=True)


def test_mrg05_mais_antiga_primeiro_20_por_pagina_e_so_pendentes(app):
    make_user(app, "adm", role="admin")
    for n in range(23):
        make_game(app, f"Jogo {n:02d}")
    with app.app_context():
        g = [x.id for x in Game.query.order_by(Game.id)]
        for n in range(1, 23):  # ids crescentes com datas decrescentes: a ordem tem de vir da data
            db.session.add(PendenciaMerge(jogo_a_id=g[0], jogo_b_id=g[n], semelhanca=70,
                                          created_at=datetime(2026, 1, 23 - n), status="ignorada" if n == 22 else "pendente"))
        db.session.commit()
    c = app.test_client()
    login(c, "adm")
    p1, p2 = (c.get(f"/admin/catalogo/duplicatas?page={n}").get_data(as_text=True) for n in (1, 2))
    titulos = lambda html: [int(t) for t in re.findall(r">Jogo (\d\d)</a>", html) if t != "00"]
    assert titulos(p1) == list(range(21, 1, -1)) and titulos(p2) == [1]
    assert "Página 1 de 2" in p1 and "Jogo 22" not in p1 + p2


def test_mrg05_sem_pendencias_explica_o_comando(app):
    make_user(app, "adm", role="admin")
    c = app.test_client()
    login(c, "adm")
    assert "sugerir-duplicatas" in c.get("/admin/catalogo/duplicatas").get_data(as_text=True)
