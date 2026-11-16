"""Motor de conquistas orientado a eventos (GAM-01..15)."""
from collections import Counter
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import event

from app import gamificacao, tempo
from app.conquistas_catalogo import CATALOGO, sincronizar
from app.menores import politica
from app.models import (
    Activity, AuditLog, Conquista, Game, JogoFonte, Notificacao, Review, SessaoJogo, Status, User,
    UsuarioConquista, db, now,
)
from conftest import login, make_game, make_user


@pytest.fixture(autouse=True)
def relogio(monkeypatch):
    monkeypatch.setattr(tempo, "agora", lambda: datetime(2026, 11, 16, 12, 0, tzinfo=tempo.FUSO))


@pytest.fixture
def base(app):
    with app.app_context():
        sincronizar()
    make_user(app, "ana")
    make_user(app, "bia")
    make_user(app, "chefe", role="admin")
    for i in range(6):
        make_game(app, f"Jogo {i}", genre=f"Gênero {i}", platform=f"Plataforma {i % 3}")
    return app


def uid(app, nome="ana"):
    with app.app_context():
        return User.query.filter_by(username=nome).one().id


def cliente(app, nome="ana"):
    c = app.test_client()
    login(c, nome)
    return c


def tem(app, codigo, nome="ana"):
    with app.app_context():
        uc = (UsuarioConquista.query.join(Conquista).filter(Conquista.codigo == codigo,
                                                            UsuarioConquista.user_id == uid(app, nome)).first())
        return uc


def desbloqueou(app, codigo, nome="ana"):
    uc = tem(app, codigo, nome)
    return uc is not None and uc.desbloqueada_em is not None


def avaliar(client, slug, texto="", nota=8):
    return client.post(f"/jogos/{slug}/avaliar", data={"score": nota, "text": texto})


# ---------- GAM-01, GAM-02 ----------
def test_gam_01_02_catalogo_com_40_e_4_por_categoria_idempotente(app):
    with app.app_context():
        assert sincronizar() == (len(CATALOGO), 0)
        por_categoria = Counter(c.categoria for c in Conquista.query)
        assert Conquista.query.count() >= 40 and all(por_categoria[k] >= 4 for k in gamificacao.CATEGORIAS)
        assert {"critico", "colecionador", "pioneiro", "maratonista", "horas-100", "zerou-10-ano",
                "explorador-multiloja"} <= {c.codigo for c in Conquista.query}
        c = Conquista.query.filter_by(codigo="critico").one()
        c.nome = "Mudado"
        admin = Conquista(codigo="do-admin", nome="Minha", descricao="x", categoria="social", raridade="comum",
                          icone="x", metrica="seguidores", meta=2, do_admin=True)
        db.session.add(admin)
        db.session.commit()
        assert sincronizar() == (0, len(CATALOGO))
        assert Conquista.query.filter_by(codigo="critico").one().nome == "Crítico"
        assert Conquista.query.count() == len(CATALOGO) + 1 and admin.nome == "Minha"


def test_gam_02_comando_sincronizar(app):
    r = app.test_cli_runner().invoke(args=["conquistas", "sincronizar"])
    assert r.output.strip() == f"criadas={len(CATALOGO)} atualizadas=0"


# ---------- GAM-03..06: uma de cada categoria ----------
def test_gam_03_critica_e_eventos_so_das_metricas_que_escutam(base):
    ana = cliente(base)
    avaliar(ana, "jogo-0")
    assert desbloqueou(base, "primeira-avaliacao") and desbloqueou(base, "pioneiro")
    consultas = []

    def conta(conn, cursor, sql, *a):
        consultas.append(sql)
    with base.app_context():
        event.listen(db.engine, "before_cursor_execute", conta)
        gamificacao.emitir_evento(uid(base), "evento_que_ninguem_escuta")
        event.remove(db.engine, "before_cursor_execute", conta)
    assert not any("conquista" in s.lower() for s in consultas)


def test_gam_03_colecao_explorador_diario_e_social(base):
    ana = cliente(base)
    ana.post("/listas", data={"name": "Minha"})
    assert desbloqueou(base, "primeira-lista")
    with base.app_context():
        jogos = Game.query.order_by(Game.id).all()
        for loja, g in zip(("steam", "gog", "epic"), jogos):
            db.session.add(JogoFonte(game_id=g.id, fonte=loja, external_id=f"x{g.id}"))
        db.session.commit()
    for i in range(3):
        ana.post(f"/jogos/jogo-{i}/status", data={"status": "planejado"})
    assert desbloqueou(base, "explorador-multiloja") and desbloqueou(base, "plataformas-3")
    ana.post("/jogos/jogo-4/sessoes", data={"jogada_em": "2026-11-10", "duracao": "6h", "zerou": "on"})
    assert desbloqueou(base, "primeira-sessao") and desbloqueou(base, "maratonista") and desbloqueou(base, "zerou-1")
    cliente(base, "bia").post("/u/ana/seguir")
    assert desbloqueou(base, "primeiro-seguidor")


def test_gam_04_progresso_limitado_a_meta(base):
    ana = cliente(base)
    for i in range(3):
        avaliar(ana, f"jogo-{i}")
    assert tem(base, "critico").progresso == 3 and tem(base, "critico").desbloqueada_em is None
    html = ana.get("/u/ana/conquistas").get_data(as_text=True)
    assert "3 de 5" in html
    for i in range(3, 6):
        avaliar(ana, f"jogo-{i}")
    uc = tem(base, "critico")
    assert uc.progresso == 5 and uc.desbloqueada_em is not None


def test_gam_05_idempotente_com_evento_repetido(base):
    avaliar(cliente(base), "jogo-0")
    with base.app_context():
        for _ in range(3):
            gamificacao.emitir_evento(uid(base), "avaliacao")
        c = Conquista.query.filter_by(codigo="primeira-avaliacao").one()
        assert UsuarioConquista.query.filter_by(conquista_id=c.id).count() == 1
        assert Notificacao.query.filter_by(tipo="conquista", texto=c.nome).count() == 1
        assert Activity.query.filter_by(kind="conquista", user_id=uid(base)).filter(
            Activity.texto.like("%Primeira impressão%")).count() == 1


def test_gam_05_violacao_de_unicidade_vira_ja_desbloqueada(base):
    with base.app_context():
        user, c = db.session.get(User, uid(base)), Conquista.query.filter_by(codigo="critico").one()
        db.session.add(UsuarioConquista(user_id=user.id, conquista_id=c.id, progresso=5, desbloqueada_em=now()))
        db.session.commit()
        repetida = UsuarioConquista(user_id=user.id, conquista_id=c.id, progresso=0)
        db.session.add(repetida)
        assert gamificacao._desbloquear(user, repetida, c) is False


def test_gam_06_avisa_registra_atividade_e_emite_conquista(base):
    recebidos = []
    gamificacao.ouvir(lambda u, tipo, ctx: recebidos.append((tipo, ctx.get("raridade"))))
    try:
        avaliar(cliente(base), "jogo-0")
    finally:
        gamificacao._ouvintes.pop()
    assert ("conquista", "comum") in recebidos and ("conquista", "rara") in recebidos
    with base.app_context():
        n = Notificacao.query.filter_by(user_id=uid(base), tipo="conquista").first()
        assert n.alvo_url == "/u/ana/conquistas"
    bia = cliente(base, "bia")
    bia.post("/u/ana/seguir")
    assert "desbloqueou a conquista" in bia.get("/feed").get_data(as_text=True)
    with base.app_context():
        User.query.filter_by(username="ana").one().perfil_privado = True
        db.session.commit()
    assert "desbloqueou a conquista" not in bia.get("/feed").get_data(as_text=True)


# ---------- GAM-07 ----------
def test_gam_07_oculta_nao_conta_e_desbloqueada_nao_volta(base):
    ana = cliente(base)
    for i in range(4):
        avaliar(ana, f"jogo-{i}")
    with base.app_context():
        Review.query.filter_by(user_id=uid(base)).first().hidden = True
        db.session.commit()
    avaliar(ana, "jogo-4")
    assert not desbloqueou(base, "critico") and tem(base, "critico").progresso == 4
    avaliar(ana, "jogo-5")
    assert desbloqueou(base, "critico")
    with base.app_context():
        rid = Review.query.filter_by(user_id=uid(base), hidden=False).first().id
    ana.post(f"/avaliacoes/{rid}/apagar")
    assert desbloqueou(base, "critico")


def test_gam_07_progresso_bloqueado_diminui(base):
    ana = cliente(base)
    for i in range(3):
        avaliar(ana, f"jogo-{i}")
    with base.app_context():
        rid = Review.query.filter_by(user_id=uid(base)).first().id
    ana.post(f"/avaliacoes/{rid}/apagar")
    assert tem(base, "critico").progresso == 2


# ---------- GAM-08..11 ----------
def secreta(app):
    with app.app_context():
        c = Conquista.query.filter_by(codigo="desapego").one()
        return c.nome, c.descricao


def test_gam_08_secreta_escondida_ate_desbloquear(base):
    nome, descricao = secreta(base)
    ana = cliente(base)
    for tela in ("/conquistas", "/u/ana/conquistas"):
        html = ana.get(tela).get_data(as_text=True)
        assert "Conquista secreta" in html and nome not in html and descricao not in html
    for i in range(5):
        ana.post(f"/jogos/jogo-{i}/status", data={"status": "abandonado"})
    assert desbloqueou(base, "desapego")
    assert nome in ana.get("/u/ana/conquistas").get_data(as_text=True)
    assert nome not in cliente(base, "bia").get("/conquistas").get_data(as_text=True)


def test_gam_09_pagina_filtros_e_privacidade(base):
    ana = cliente(base)
    avaliar(ana, "jogo-0")
    ana.post("/listas", data={"name": "L"})
    visitante = base.test_client()
    html = visitante.get("/u/ana/conquistas").get_data(as_text=True)
    assert "Primeira impressão" in html and "Organizador" in html and "Ainda bloqueadas" not in html
    filtrado = visitante.get("/u/ana/conquistas?categoria=colecao&raridade=xyz").get_data(as_text=True)
    assert "Organizador" in filtrado and "Primeira impressão" not in filtrado
    assert "Ainda bloqueadas" in ana.get("/u/ana/conquistas").get_data(as_text=True)
    with base.app_context():
        User.query.filter_by(username="ana").one().perfil_privado = True
        db.session.commit()
    assert visitante.get("/u/ana/conquistas").status_code == 404
    assert ana.get("/u/ana/conquistas").status_code == 200


def test_gam_09_perfil_mostra_as_6_mais_recentes(base):
    ana = cliente(base)
    avaliar(ana, "jogo-0")
    html = ana.get("/u/ana").get_data(as_text=True)
    secao = html[html.index("<h2>Conquistas</h2>"):]
    assert "Primeira impressão" in secao and "Ver as 2 conquistas" in secao


def test_gam_10_estante_com_no_maximo_3(base):
    ana = cliente(base)
    avaliar(ana, "jogo-0", texto="x" * 140, nota=10)
    ana.post("/listas", data={"name": "L"})
    codigos = ["primeira-avaliacao", "pioneiro", "nota-maxima", "primeira-lista"]
    for codigo in codigos[:3]:
        ana.post(f"/u/ana/conquistas/{codigo}/fixar")
    r = ana.post("/u/ana/conquistas/primeira-lista/fixar", follow_redirects=True)
    assert "A estante tem no máximo 3 conquistas" in r.get_data(as_text=True)
    assert not tem(base, "primeira-lista").fixada and tem(base, "pioneiro").fixada
    assert ana.post("/u/ana/conquistas/critico/fixar").status_code == 404
    assert cliente(base, "bia").post("/u/ana/conquistas/pioneiro/fixar").status_code == 403
    ana.post("/u/ana/conquistas/pioneiro/desafixar")
    ana.post("/u/ana/conquistas/primeira-lista/fixar")
    assert tem(base, "primeira-lista").fixada
    assert 'class="estante"' in ana.get("/u/ana").get_data(as_text=True)


def test_gam_11_porcentagem_sem_admins_em_3_consultas(base):
    assert "0%" in base.test_client().get("/conquistas").get_data(as_text=True)
    avaliar(cliente(base), "jogo-0")
    avaliar(cliente(base, "chefe"), "jogo-1")
    consultas = []

    def conta(*a):
        consultas.append(1)
    with base.app_context():
        event.listen(db.engine, "before_cursor_execute", conta)
        html = base.test_client().get("/conquistas").get_data(as_text=True)
        event.remove(db.engine, "before_cursor_execute", conta)
    assert "50,0%" in html and len(consultas) <= 3


def test_gam_11_banco_vazio(app):
    assert app.test_client().get("/conquistas").status_code == 200


# ---------- GAM-12 ----------
def test_gam_12_admin_cria_edita_desativa_com_auditoria(base):
    chefe = cliente(base, "chefe")
    r = chefe.post("/admin/conquistas", data={"nome": "", "descricao": "d", "metrica": "xyz", "meta": "0",
                                               "categoria": "social", "raridade": "comum"})
    html = r.get_data(as_text=True)
    assert r.status_code == 400 and "métrica do catálogo" in html and "maior ou igual a 1" in html
    chefe.post("/admin/conquistas", data={"codigo": "dois-seguidores", "nome": "Dupla", "descricao": "2 seguidores",
                                          "metrica": "seguidores", "meta": "2", "categoria": "social",
                                          "raridade": "comum"})
    with base.app_context():
        c = Conquista.query.filter_by(codigo="dois-seguidores").one()
        cid = c.id
    chefe.post(f"/admin/conquistas/{cid}/editar", data={"nome": "Dupla", "descricao": "1 seguidor",
                                                         "metrica": "seguidores", "meta": "1", "categoria": "social",
                                                         "raridade": "rara"})
    cliente(base, "bia").post("/u/ana/seguir")
    assert desbloqueou(base, "dois-seguidores")
    chefe.post(f"/admin/conquistas/{cid}/desativar")
    html = base.test_client().get("/u/ana/conquistas").get_data(as_text=True)
    assert "Dupla" in html and "aposentada" in html
    with base.app_context():
        assert [a.action for a in AuditLog.query.order_by(AuditLog.id)] == [
            "conquista.criar", "conquista.editar", "conquista.desativar"]
    cliente(base, "chefe").post("/u/bia/seguir")  # desativada não desbloqueia mais
    assert not desbloqueou(base, "dois-seguidores", "bia")


def test_gam_12_nao_admin_recebe_403(base):
    assert cliente(base).get("/admin/conquistas").status_code == 403
    assert cliente(base).post("/admin/conquistas/1/desativar").status_code == 403


# ---------- GAM-13 ----------
def test_gam_13_recalcular_sem_aviso_para_antigas(base):
    with base.app_context():
        a = uid(base)
        for i, g in enumerate(Game.query.order_by(Game.id).limit(5)):
            db.session.add(Review(user_id=a, game_id=g.id, score=7, created_at=now() - timedelta(days=30 - i)))
        db.session.commit()
    r = base.test_cli_runner().invoke(args=["conquistas", "recalcular", "--usuario", "ana"])
    assert r.output.strip().startswith("usuarios=1 desbloqueadas=")
    assert desbloqueou(base, "critico") and desbloqueou(base, "pioneiro")
    assert tem(base, "critico").desbloqueada_em.date() == (now() - timedelta(days=26)).date()
    with base.app_context():
        assert Notificacao.query.filter_by(tipo="conquista").count() == 0
        assert Activity.query.filter_by(kind="conquista").count() == 0
    r = base.test_cli_runner().invoke(args=["conquistas", "recalcular"])
    assert r.output.strip() == "usuarios=3 desbloqueadas=0"


# ---------- GAM-14, GAM-15 ----------
def test_gam_14_por_tempo_nao_vale_para_menor_protegido(base, monkeypatch):
    monkeypatch.setitem(politica.ACOES, "conquista_por_tempo", lambda user, alvo=None: user.username != "ana")
    ana = cliente(base)
    ana.post("/jogos/jogo-0/sessoes", data={"jogada_em": "2026-11-10", "duracao": "7h"})
    assert not desbloqueou(base, "maratonista") and desbloqueou(base, "primeira-sessao")
    assert "Maratonista" not in ana.get("/u/ana/conquistas").get_data(as_text=True)
    cliente(base, "bia").post("/jogos/jogo-0/sessoes", data={"jogada_em": "2026-11-10", "duracao": "7h"})
    assert desbloqueou(base, "maratonista", "bia")


def test_gam_15_falha_do_motor_nao_quebra_a_acao(base, monkeypatch):
    def explode(*a):
        raise RuntimeError("motor quebrado")
    monkeypatch.setattr(gamificacao, "avaliar", explode)
    r = avaliar(cliente(base), "jogo-0")
    assert r.status_code == 302
    with base.app_context():
        assert Review.query.count() == 1 and UsuarioConquista.query.count() == 0


def test_gam_15_zerou_10_no_ano(base):
    with base.app_context():
        a = uid(base)
        for i in range(10):
            make_game(base, f"Extra {i}")
        for g in Game.query.filter(Game.slug.like("extra-%")):
            db.session.add(Status(user_id=a, game_id=g.id, value="jogado", zerado_em=date(2026, 3, 1)))
            db.session.add(SessaoJogo(user_id=a, game_id=g.id, jogada_em=date(2026, 3, 1), duracao_min=60))
        db.session.commit()
        gamificacao.emitir_evento(a, "sessao")
    assert desbloqueou(base, "zerou-10-ano") and desbloqueou(base, "horas-10")


def test_gam_14_acao_fora_da_politica_nunca_libera(app):
    with pytest.raises(ValueError):
        politica.pode(None, "acao_inventada")
