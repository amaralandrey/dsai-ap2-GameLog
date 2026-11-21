"""Rankings e temporadas (RNK-01..12)."""
from datetime import date, datetime

import pytest
from sqlalchemy import event

from app import rankings, tempo
from app.conquistas_catalogo import sincronizar
from app.menores import politica
from app.models import (
    AuditLog, Desclassificacao, ExtratoXP, Follow, Game, Like, Notificacao, Review, SessaoJogo, SnapshotRanking,
    Status, Temporada, User, db,
)
from conftest import login, make_game, make_user


@pytest.fixture(autouse=True)
def relogio(monkeypatch):
    monkeypatch.setattr(tempo, "agora", lambda: datetime(2026, 11, 16, 12, 0, tzinfo=tempo.FUSO))


NOMES = ("ana", "bia", "carla", "duda")


@pytest.fixture
def base(app):
    with app.app_context():
        sincronizar()
    for n in NOMES:
        make_user(app, n)
    make_user(app, "chefe", role="admin")
    for i in range(5):
        make_game(app, f"Jogo {i}")
    with app.app_context():
        rankings.abrir_temporada()
    return app


def uid(app, nome):
    with app.app_context():
        return User.query.filter_by(username=nome).one().id


def xp(app, nome, valor, quando=datetime(2026, 11, 10, 15), chave=None):
    with app.app_context():
        db.session.add(ExtratoXP(user_id=uid(app, nome), origem_tipo="ajuste", origem_id=chave or f"{nome}{valor}{quando}",
                                 xp=valor, motivo="teste", created_at=quando))
        db.session.commit()


def seguir(app, quem, alvo):
    with app.app_context():
        db.session.add(Follow(follower_id=uid(app, quem), followed_id=uid(app, alvo)))
        db.session.commit()


def cliente(app, nome=None):
    c = app.test_client()
    if nome:
        login(c, nome)
    return c


def ordem(html):
    return [n for _, n in sorted((html.index(f">{n}</a>"), n) for n in NOMES + ("chefe",) if f">{n}</a>" in html)]


# ---------- RNK-01 ----------
def test_rnk_01_abrir_temporada_idempotente(app):
    r = app.test_cli_runner().invoke(args=["rankings", "abrir-temporada"])
    assert r.output.strip() == "temporada=2026-T4 criada=1"
    r = app.test_cli_runner().invoke(args=["rankings", "abrir-temporada"])
    assert r.output.strip() == "temporada=2026-T4 criada=0"
    with app.app_context():
        t = Temporada.query.one()
        assert (t.inicio, t.fim) == (date(2026, 10, 1), date(2026, 12, 31))


def test_rnk_10_abrir_temporada_nao_sobrepoe_a_do_admin(app):
    with app.app_context():
        db.session.add(Temporada(nome="Natal", slug="natal", inicio=date(2026, 12, 1), fim=date(2027, 1, 15)))
        db.session.commit()
    r = app.test_cli_runner().invoke(args=["rankings", "abrir-temporada"])
    assert "temporada=natal criada=0" in r.output and "aviso=" in r.output
    with app.app_context():
        assert [t.slug for t in Temporada.query] == ["natal"]


@pytest.mark.parametrize("dia,slug,inicio,fim", [
    (date(2026, 1, 1), "2026-T1", date(2026, 1, 1), date(2026, 3, 31)),
    (date(2026, 6, 30), "2026-T2", date(2026, 4, 1), date(2026, 6, 30)),
    (date(2026, 9, 1), "2026-T3", date(2026, 7, 1), date(2026, 9, 30)),
])
def test_rnk_01_trimestres(dia, slug, inicio, fim):
    assert rankings.trimestre(dia) == (slug, inicio, fim)


# ---------- RNK-02, RNK-03 ----------
def test_rnk_02_cada_categoria_na_janela(base):
    with base.app_context():
        g = Game.query.first()
        a, b = uid(base, "ana"), uid(base, "bia")
        db.session.add_all([
            Status(user_id=a, game_id=g.id, value="jogado", zerado_em=date(2026, 10, 5)),
            Status(user_id=b, game_id=g.id, value="jogado", zerado_em=date(2026, 9, 30)),
            SessaoJogo(user_id=a, game_id=g.id, jogada_em=date(2026, 11, 1), duracao_min=90),
            SessaoJogo(user_id=b, game_id=g.id, jogada_em=date(2026, 11, 1), duracao_min=9000, origem="steam"),
        ])
        r = Review(user_id=b, game_id=g.id, score=8, text="x")
        db.session.add(r)
        db.session.flush()
        db.session.add(Like(user_id=a, review_id=r.id, created_at=datetime(2026, 11, 2)))
        db.session.add(Like(user_id=uid(base, "carla"), review_id=r.id, created_at=None))  # antiga, sem data
        db.session.commit()
    xp(base, "carla", 30)
    v = cliente(base)
    def tabela(cat, janela="atual"):
        return v.get(f"/rankings?escopo=global&categoria={cat}&janela={janela}").get_data(as_text=True)
    assert ordem(tabela("zerados")) == ["ana"]
    assert ordem(tabela("zerados", "sempre")) == ["bia", "ana"]  # empate: quem chegou antes
    horas = tabela("horas")
    assert ordem(horas) == ["ana"] and "1,5 h" in horas
    assert "<td>1</td>" in tabela("curtidas") and "<td>2</td>" in tabela("curtidas", "sempre")
    assert ordem(tabela("xp")) == ["carla"]


def test_rnk_03_valores_desconhecidos_voltam_ao_padrao(base):
    xp(base, "ana", 10)
    html = cliente(base).get("/rankings?escopo=global&categoria=xyz&janela=nada").get_data(as_text=True)
    assert '<option value="xp" selected>XP</option>' in html and ordem(html) == ["ana"]


def test_rnk_03_janela_de_temporada_pelo_slug(base):
    with base.app_context():
        db.session.add(Temporada(nome="Antiga", slug="antiga", inicio=date(2026, 1, 1), fim=date(2026, 1, 31)))
        db.session.commit()
    xp(base, "ana", 10, datetime(2026, 1, 10))
    xp(base, "bia", 10)
    assert ordem(cliente(base).get("/rankings?escopo=global&janela=antiga").get_data(as_text=True)) == ["ana"]


def test_rnk_02_janela_de_xp_e_curtidas_segue_o_dia_de_belem(base):
    with base.app_context():
        db.session.add(Temporada(nome="T1", slug="t1", inicio=date(2026, 1, 1), fim=date(2026, 3, 31)))
        g = Game.query.first()
        r = Review(user_id=uid(base, "carla"), game_id=g.id, score=8, text="x")
        db.session.add(r)
        db.session.flush()
        db.session.add_all([Like(user_id=uid(base, "ana"), review_id=r.id, created_at=datetime(2026, 4, 1, 1)),
                            Like(user_id=uid(base, "bia"), review_id=r.id, created_at=datetime(2026, 1, 1, 2))])
        db.session.commit()
    xp(base, "ana", 10, datetime(2026, 4, 1, 1))  # 22:00 de 31/03 em Belém: ainda é a T1
    xp(base, "bia", 10, datetime(2026, 1, 1, 2))  # 23:00 de 31/12 em Belém: fica fora
    v = cliente(base)
    assert ordem(v.get("/rankings?escopo=global&categoria=xp&janela=t1").get_data(as_text=True)) == ["ana"]
    assert "<td>1</td>" in v.get("/rankings?escopo=global&categoria=curtidas&janela=t1").get_data(as_text=True)


# ---------- RNK-04, RNK-05 ----------
def test_rnk_04_escopos(base, monkeypatch):
    for n, v in zip(NOMES, (40, 30, 20, 10)):
        xp(base, n, v)
    seguir(base, "ana", "bia")
    ana = cliente(base, "ana")
    assert ordem(ana.get("/rankings").get_data(as_text=True)) == ["ana", "bia"]  # seguindo é o padrão
    assert ordem(ana.get("/rankings?escopo=global").get_data(as_text=True)) == ["ana", "bia", "carla", "duda"]
    visitante = cliente(base)
    assert ordem(visitante.get("/rankings").get_data(as_text=True)) == ["ana", "bia", "carla", "duda"]
    assert visitante.get("/rankings?escopo=seguindo").headers["Location"].startswith("/entrar")
    assert visitante.get("/rankings?escopo=clube&clube=x").headers["Location"].startswith("/entrar")
    monkeypatch.setitem(politica.ACOES, "ranking_global", lambda user, alvo=None: user.username != "carla")
    assert "carla" not in ordem(visitante.get("/rankings?escopo=global").get_data(as_text=True))


def test_rnk_05_privado_admin_e_bloqueio(base, monkeypatch):
    for n, v in zip(NOMES + ("chefe",), (40, 30, 20, 10, 99)):
        xp(base, n, v)
    with base.app_context():
        User.query.filter_by(username="bia").one().perfil_privado = True
        db.session.commit()
    seguir(base, "ana", "bia")
    seguir(base, "ana", "carla")
    seguir(base, "carla", "ana")  # carla vê ana; bia não segue a ana, então ana não vê bia
    ana = cliente(base, "ana")
    assert ordem(ana.get("/rankings").get_data(as_text=True)) == ["ana", "carla"]
    assert "chefe" not in ordem(ana.get("/rankings?escopo=global").get_data(as_text=True))
    assert "bia" not in ordem(ana.get("/rankings?escopo=global").get_data(as_text=True))
    monkeypatch.setattr(rankings, "bloqueados_com", lambda user: {uid(base, "carla")} if user.username == "ana" else set())
    assert "carla" not in ordem(ana.get("/rankings?escopo=global").get_data(as_text=True))
    assert "carla" in ordem(cliente(base, "duda").get("/rankings?escopo=global").get_data(as_text=True))


# ---------- RNK-06, RNK-07 ----------
def test_rnk_06_cartao_fora_da_pagina(base):
    with base.app_context():
        for i in range(25):
            u = User(username=f"x{i:02d}", username_lower=f"x{i:02d}", email=f"x{i}@e.com", password_hash="x",
                     display_name=f"x{i:02d}")
            db.session.add(u)
            db.session.flush()
            db.session.add(ExtratoXP(user_id=u.id, origem_tipo="ajuste", origem_id="1", xp=100 + i, motivo="t",
                                     created_at=datetime(2026, 11, 10)))
        db.session.commit()
    xp(base, "ana", 5)
    html = cliente(base, "ana").get("/rankings?escopo=global").get_data(as_text=True)
    assert "Você está em <strong>26º</strong> com <strong>5</strong>" in html and ">ana</a>" not in html
    assert "Você ainda não pontuou nesta temporada" in cliente(base, "bia").get("/rankings?escopo=global").get_data(as_text=True)


def test_rnk_07_desempate_por_instante_e_por_id(base):
    xp(base, "duda", 10, datetime(2026, 11, 1))
    xp(base, "carla", 10, datetime(2026, 11, 5))
    xp(base, "bia", 10, datetime(2026, 11, 5))
    xp(base, "ana", 20, datetime(2026, 11, 9))
    html = cliente(base).get("/rankings?escopo=global").get_data(as_text=True)
    assert ordem(html) == ["ana", "duda", "bia", "carla"]  # bia tem id menor que carla
    assert ordem(cliente(base).get("/rankings?escopo=global").get_data(as_text=True)) == ordem(html)


def test_rnk_07_estorno_de_xp_nao_atrasa_o_instante(base):
    xp(base, "ana", 10, datetime(2026, 11, 8))
    xp(base, "bia", 15, datetime(2026, 11, 5))
    xp(base, "bia", -5, datetime(2026, 11, 10))
    html = cliente(base).get("/rankings?escopo=global").get_data(as_text=True)
    assert ordem(html) == ["bia", "ana"]


# ---------- RNK-08, RNK-09 ----------
@pytest.fixture
def encerravel(base):
    with base.app_context():
        db.session.add(Temporada(nome="2026-T3", slug="2026-T3", inicio=date(2026, 7, 1), fim=date(2026, 9, 30)))
        db.session.commit()
    for n, v in zip(NOMES, (40, 30, 20, 10)):
        xp(base, n, v, datetime(2026, 8, 1))
    return base


def test_rnk_08_encerrar_antes_do_fim_falha(base):
    r = base.test_cli_runner().invoke(args=["rankings", "encerrar-temporada", "2026-T4"])
    assert r.exit_code == 1 and "erro=temporada em andamento" in r.output


def test_rnk_08_encerrar_grava_retrato_premia_e_nao_repete(encerravel):
    cli = encerravel.test_cli_runner()
    assert cli.invoke(args=["rankings", "encerrar-temporada", "2026-T3"]).output.strip() == "encerrada=1"
    with encerravel.app_context():
        linhas = SnapshotRanking.query.filter_by(categoria="xp").order_by(SnapshotRanking.posicao).all()
        assert [(s.posicao, s.valor) for s in linhas] == [(1, 40), (2, 30), (3, 20), (4, 10)]
        assert Notificacao.query.filter_by(tipo="sistema").count() == 3
    assert cli.invoke(args=["rankings", "encerrar-temporada", "2026-T3"]).output.strip() == "ja_encerrada=1"
    with encerravel.app_context():
        assert SnapshotRanking.query.filter_by(categoria="xp").count() == 4
        from app.models import Conquista, UsuarioConquista
        podio = Conquista.query.filter_by(codigo="podio-xp").one()
        assert UsuarioConquista.query.filter(UsuarioConquista.conquista_id == podio.id,
                                             UsuarioConquista.desbloqueada_em.isnot(None)).count() == 3


def test_rnk_09_retrato_imutavel_e_hall_da_fama(encerravel):
    encerravel.test_cli_runner().invoke(args=["rankings", "encerrar-temporada", "2026-T3"])
    with encerravel.app_context():
        ExtratoXP.query.filter_by(user_id=uid(encerravel, "ana")).delete()
        User.query.filter_by(username="bia").one().username = "removido-2"
        db.session.commit()
    html = cliente(encerravel).get("/rankings?escopo=global&janela=2026-T3").get_data(as_text=True)
    assert ordem(html)[0] == "ana" and "usuário removido" in html
    hall = cliente(encerravel).get("/rankings/hall-da-fama").get_data(as_text=True)
    assert "2026-T3" in hall and "🥇" in hall and "usuário removido" in hall


def test_rnk_05_retrato_no_seguindo_esconde_perfil_privado(encerravel):
    encerravel.test_cli_runner().invoke(args=["rankings", "encerrar-temporada", "2026-T3"])
    seguir(encerravel, "ana", "bia")
    seguir(encerravel, "ana", "carla")
    with encerravel.app_context():
        User.query.filter_by(username="bia").one().perfil_privado = True
        db.session.commit()
    html = cliente(encerravel, "ana").get("/rankings?escopo=seguindo&janela=2026-T3").get_data(as_text=True)
    assert ordem(html) == ["ana", "carla"]


# ---------- RNK-10 ----------
def test_rnk_10_admin_temporada_desclassificacao_e_reversao(base):
    for n, v in zip(NOMES, (40, 30, 20, 10)):
        xp(base, n, v)
    chefe = cliente(base, "chefe")
    r = chefe.post("/admin/rankings", data={"acao": "temporada", "nome": "Especial", "inicio": "2026-11-01",
                                            "fim": "2026-11-30"})
    assert r.status_code == 400 and "sobrepõem" in r.get_data(as_text=True)
    chefe.post("/admin/rankings", data={"acao": "temporada", "nome": "Verão", "inicio": "2027-01-01", "fim": "2027-01-31"})
    r = chefe.post("/admin/rankings", data={"acao": "desclassificar", "temporada": "2026-T4", "categoria": "xp",
                                            "usuario": "ana", "motivo": ""})
    assert r.status_code == 400 and "motivo é obrigatório" in r.get_data(as_text=True)
    chefe.post("/admin/rankings", data={"acao": "desclassificar", "temporada": "2026-T4", "categoria": "xp",
                                        "usuario": "ana", "motivo": "farm de XP"})
    assert "ana" not in ordem(cliente(base).get("/rankings?escopo=global").get_data(as_text=True))
    with base.app_context():
        did = Desclassificacao.query.one().id
    chefe.post(f"/admin/rankings/desclassificacoes/{did}/reverter")
    assert ordem(cliente(base).get("/rankings?escopo=global").get_data(as_text=True))[0] == "ana"
    with base.app_context():
        assert [a.action for a in AuditLog.query.order_by(AuditLog.id)] == [
            "ranking.temporada", "ranking.desclassificar", "ranking.reverter"]
    assert cliente(base, "ana").get("/admin/rankings").status_code == 403


def test_rnk_10_nao_reverte_temporada_encerrada(encerravel):
    with encerravel.app_context():
        t = Temporada.query.filter_by(slug="2026-T3").one()
        d = Desclassificacao(temporada_id=t.id, categoria="xp", user_id=uid(encerravel, "ana"), motivo="x",
                             admin_id=uid(encerravel, "chefe"))
        db.session.add(d)
        db.session.commit()
        did = d.id
    encerravel.test_cli_runner().invoke(args=["rankings", "encerrar-temporada", "2026-T3"])
    with encerravel.app_context():
        assert SnapshotRanking.query.filter_by(categoria="xp", posicao=1).one().user_id == uid(encerravel, "bia")
    assert cliente(encerravel, "chefe").post(f"/admin/rankings/desclassificacoes/{did}/reverter").status_code == 400


# ---------- RNK-11 ----------
def test_rnk_11_orcamento_de_consultas(base):
    def contar(n_extra):
        with base.app_context():
            for i in range(n_extra):
                u = User(username=f"y{n_extra}{i}", username_lower=f"y{n_extra}{i}", email=f"y{n_extra}{i}@e.com",
                         password_hash="x", display_name="y")
                db.session.add(u)
                db.session.flush()
                db.session.add(ExtratoXP(user_id=u.id, origem_tipo="a", origem_id="1", xp=5, motivo="t",
                                         created_at=datetime(2026, 11, 10)))
                db.session.add(Follow(follower_id=uid(base, "ana"), followed_id=u.id))
            db.session.commit()
        consultas = []

        def conta(*a):
            consultas.append(1)
        c = cliente(base, "ana")
        with base.app_context():
            event.listen(db.engine, "before_cursor_execute", conta)
            c.get("/rankings")
            event.remove(db.engine, "before_cursor_execute", conta)
        return len(consultas)
    poucos, muitos = contar(3), contar(40)
    assert poucos <= 6 and muitos == poucos
