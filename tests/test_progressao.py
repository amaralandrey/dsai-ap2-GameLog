"""Níveis, XP e sequências semanais (PRG-01..12), com o relógio fixado em Belém."""
from datetime import date, datetime

import pytest

from app import progressao, tempo
from app.menores import politica
from app.models import AuditLog, ExtratoXP, Notificacao, PerfilProgressao, Review, User, db
from conftest import login, make_game, make_user


class Relogio:
    def __init__(self):
        self.momento = datetime(2026, 11, 16, 12, 0, tzinfo=tempo.FUSO)  # segunda-feira, semana 2026-W47

    def ir(self, *args):
        self.momento = datetime(*args, tzinfo=tempo.FUSO)


@pytest.fixture(autouse=True)
def relogio(monkeypatch):
    r = Relogio()
    monkeypatch.setattr(tempo, "agora", lambda: r.momento)
    return r


@pytest.fixture
def base(app):
    from app.conquistas_catalogo import sincronizar

    with app.app_context():
        sincronizar()
    make_user(app, "ana")
    make_user(app, "bia")
    make_user(app, "chefe", role="admin")
    for i in range(20):
        make_game(app, f"Jogo {i}")
    return app


def uid(app, nome="ana"):
    with app.app_context():
        return User.query.filter_by(username=nome).one().id


def cliente(app, nome="ana"):
    c = app.test_client()
    login(c, nome)
    return c


def extrato(app, nome="ana"):
    with app.app_context():
        return [(e.origem_tipo, e.xp, e.motivo) for e in
                ExtratoXP.query.filter_by(user_id=uid(app, nome)).order_by(ExtratoXP.id)]


def perfil(app, nome="ana"):
    with app.app_context():
        return PerfilProgressao.query.filter_by(user_id=uid(app, nome)).first()


def sessao(c, jogo=0, dia="2026-11-16", duracao="60", **extra):
    return c.post(f"/jogos/jogo-{jogo}/sessoes", data={"jogada_em": dia, "duracao": duracao, **extra})


# ---------- PRG-01, PRG-02 ----------
def test_prg_01_perfil_nasce_no_primeiro_xp(base):
    assert perfil(base) is None
    html = base.test_client().get("/u/ana").get_data(as_text=True)
    assert "Nível <strong>1</strong> · 0 XP" in html
    cliente(base).post("/jogos/jogo-0/avaliar", data={"score": 7})
    p = perfil(base)
    conquistas = sum(e[1] for e in extrato(base) if e[0] == "conquista")
    assert (p.xp_total, p.nivel, p.sequencia_semanas, p.ultima_semana_ativa) == (10 + conquistas, 1, 1, "2026-W47")
    assert extrato(base)[0] == ("avaliacao", 10, "avaliação")


def test_prg_02_cada_linha_da_tabela(base):
    ana = cliente(base)
    ana.post("/jogos/jogo-0/avaliar", data={"score": 7})
    ana.post("/jogos/jogo-1/avaliar", data={"score": 7, "text": "x" * 140})
    sessao(ana, 2, zerou="on")
    linhas = extrato(base)
    assert ("avaliacao", 10, "avaliação") in linhas and ("avaliacao", 25, "avaliação com resenha") in linhas
    assert ("sessao", 10, "sessão no diário") in linhas and ("zerado", 50, "zerou um jogo") in linhas
    raridades = {e[0]: e[1] for e in linhas if e[0] == "conquista"}
    assert raridades  # primeira avaliação (comum), pioneiro (raro)...
    with base.app_context():
        assert sorted({e.xp for e in ExtratoXP.query.filter_by(origem_tipo="conquista")}) <= [20, 50, 100, 200]


def test_prg_02_importadas_seguir_curtir_e_lista_nao_dao_xp(base):
    from app.models import Game, SessaoJogo

    ana = cliente(base)
    with base.app_context():
        db.session.add(SessaoJogo(user_id=uid(base), game_id=Game.query.first().id, jogada_em=date(2026, 11, 16),
                                  duracao_min=600, origem="steam"))
        db.session.commit()
        progressao.ao_evento(uid(base), "importacao", {})
        db.session.commit()
    ana.post("/u/bia/seguir")
    ana.post("/listas", data={"name": "L"})
    assert [e for e in extrato(base) if e[0] not in ("conquista",)] == []


# ---------- PRG-03 ----------
def test_prg_03_idempotente_e_estorno(base):
    ana = cliente(base)
    ana.post("/jogos/jogo-0/avaliar", data={"score": 7})
    ana.post("/jogos/jogo-0/avaliar", data={"score": 9, "text": "x" * 200})  # editar não gera XP de novo
    assert [e for e in extrato(base) if e[0] == "avaliacao"] == [("avaliacao", 10, "avaliação")]
    with base.app_context():
        rid = Review.query.one().id
    ana.post(f"/avaliacoes/{rid}/apagar")
    assert ("estorno_avaliacao", -10, "estorno: avaliação") in extrato(base)
    sessao(ana, 1)
    with base.app_context():
        from app.models import SessaoJogo
        sid = SessaoJogo.query.one().id
    ana.post(f"/sessoes/{sid}/editar", data={"jogada_em": "2026-11-15", "duracao": "90"})
    ana.post(f"/sessoes/{sid}/apagar")
    assert [e[1] for e in extrato(base) if e[0].endswith("sessao")] == [10, -10]


def test_prg_03_id_apagado_nao_e_reaproveitado(base):
    ana = cliente(base)
    for jogo in (0, 1):
        ana.post(f"/jogos/jogo-{jogo}/avaliar", data={"score": 7})
        with base.app_context():
            rid = Review.query.one().id
        ana.post(f"/avaliacoes/{rid}/apagar")
        sessao(ana, jogo)
        with base.app_context():
            from app.models import SessaoJogo
            sid = SessaoJogo.query.one().id
        ana.post(f"/sessoes/{sid}/apagar")
    xps = [e[1] for e in extrato(base) if e[0].endswith(("avaliacao", "sessao"))]
    assert xps == [10, -10, 10, -10] * 2


def test_prg_03_zerar_de_novo_nao_da_xp(base):
    ana = cliente(base)
    sessao(ana, 0, zerou="on")
    with base.app_context():
        from app.models import SessaoJogo
        sid = SessaoJogo.query.one().id
    ana.post(f"/sessoes/{sid}/apagar")
    sessao(ana, 0, dia="2026-11-15", zerou="on")
    assert [e[1] for e in extrato(base) if e[0] == "zerado"] == [50]


# ---------- PRG-04 ----------
@pytest.mark.parametrize("xp,nivel", [(0, 1), (99, 1), (100, 2), (299, 2), (300, 3), (4500, 10), (4499, 9)])
def test_prg_04_curva(xp, nivel):
    assert progressao.nivel_de(xp) == nivel


def test_prg_04_varios_niveis_num_lancamento_e_estorno_sem_perder_nivel(base):
    ana = cliente(base)
    chefe = cliente(base, "chefe")
    chefe.post("/admin/xp", data={"usuario": "ana", "xp": "1000", "justificativa": "teste"})
    p = perfil(base)
    assert (p.xp_total, p.nivel) == (1000 + 50, 5)  # o nível 5 desbloqueia "Veterano" (rara, +50)
    with base.app_context():
        assert Notificacao.query.filter_by(user_id=uid(base), tipo="nivel").count() == 1
        assert Notificacao.query.filter_by(tipo="nivel").one().texto == "nível 5"
    chefe.post("/admin/xp", data={"usuario": "ana", "xp": "-900", "justificativa": "estorno manual"})
    p = perfil(base)
    assert (p.xp_total, p.nivel) == (150, 5) and progressao.porcentagem(p) == 0
    assert "Nv 5" in ana.get("/").get_data(as_text=True)


# ---------- PRG-05 ----------
def test_prg_05_teto_diario_e_virada_do_dia_em_belem(base, relogio):
    ana = cliente(base)
    relogio.ir(2026, 11, 16, 20, 0)
    for i in range(6):
        ana.post(f"/jogos/jogo-{i}/avaliar", data={"score": 7, "text": "x" * 140})  # 6 × 25 = 150
    ana.post("/jogos/jogo-6/avaliar", data={"score": 7})
    repetiveis = [e for e in extrato(base) if e[0] == "avaliacao"]
    assert sum(e[1] for e in repetiveis) == 150 and repetiveis[-1] == ("avaliacao", 0, "teto diário")
    relogio.ir(2026, 11, 16, 23, 59)  # ainda dia 16 em Belém (já 17 em UTC)
    ana.post("/jogos/jogo-7/avaliar", data={"score": 7})
    assert [e for e in extrato(base) if e[0] == "avaliacao"][-1] == ("avaliacao", 0, "teto diário")
    relogio.ir(2026, 11, 17, 0, 1)
    ana.post("/jogos/jogo-8/avaliar", data={"score": 7})
    assert [e for e in extrato(base) if e[0] == "avaliacao"][-1] == ("avaliacao", 10, "avaliação")


def test_prg_05_zerar_e_conquista_fora_do_teto(base):
    with base.app_context():
        a = uid(base)
        progressao.lancar(a, "ajuste", "x", 0, "base")
        for i in range(15):
            progressao.lancar(a, "sessao", f"s{i}", 10, "sessão")
        assert progressao.lancar(a, "zerado", "1", 50, "zerou").xp == 50
        assert progressao.lancar(a, "conquista", "9", 200, "conquista").xp == 200
        assert progressao.lancar(a, "sessao", "s99", 10, "sessão").xp == 0
        db.session.commit()


# ---------- PRG-06..08 ----------
def semana(app, relogio, *data, nome="ana"):
    relogio.ir(*data)
    with app.app_context():
        p = progressao.registrar_semana(uid(app, nome))
        db.session.commit()
        return p.sequencia_semanas, p.congelamentos, progressao.sequencia_exibida(p)


def test_prg_06_semanas_iso_seguidas_e_virada_de_ano(base, relogio):
    assert semana(base, relogio, 2026, 12, 21, 10)[0] == 1  # W52
    assert semana(base, relogio, 2026, 12, 27, 10)[0] == 1  # mesma semana (domingo)
    assert semana(base, relogio, 2026, 12, 28, 10)[0] == 2  # 2026-W53
    assert semana(base, relogio, 2027, 1, 4, 10)[0] == 3  # 2027-W01
    assert perfil(base).maior_sequencia == 3 and perfil(base).ultima_semana_ativa == "2027-W01"


def test_prg_07_congelamentos_cobrem_semanas_vazias(base, relogio):
    for dia in (2, 9, 16, 23):  # 4 semanas: ganha 1 congelamento
        semana(base, relogio, 2026, 11, dia, 10)
    assert perfil(base).congelamentos == 1
    relogio.ir(2026, 12, 9, 10)  # pulou W49: ainda coberta
    with base.app_context():
        assert progressao.sequencia_exibida(perfil(base)) == 4
    assert semana(base, relogio, 2026, 12, 9, 10)[:2] == (5, 0)
    relogio.ir(2026, 12, 30, 10)  # duas semanas vazias sem congelamento
    with base.app_context():
        assert progressao.sequencia_exibida(perfil(base)) == 0
    assert semana(base, relogio, 2026, 12, 30, 10)[:2] == (1, 0)
    motivos = [e[2] for e in extrato(base) if e[0] == "congelamento"]
    assert motivos == ["congelamento ganho", "1 congelamento(s) usado(s)"]


def test_prg_07_congelamentos_insuficientes_sao_consumidos(base, relogio):
    for dia in [(2026, 10, d) for d in (5, 12, 19, 26)] + [(2026, 11, d) for d in (2, 9, 16, 23)]:
        semana(base, relogio, *dia, 10)
    assert perfil(base).congelamentos == 2
    assert semana(base, relogio, 2026, 12, 21, 10)[:2] == (1, 0)  # 3 semanas vazias, só 2 congelamentos


def test_prg_08_ganha_ate_2_congelamentos_no_extrato(base, relogio):
    from datetime import timedelta

    inicio = date(2026, 1, 5)
    for i in range(13):
        d = inicio + timedelta(weeks=i)
        semana(base, relogio, d.year, d.month, d.day, 10)
    p = perfil(base)
    assert (p.sequencia_semanas, p.congelamentos) == (13, 2)
    assert all(e[1] == 0 for e in extrato(base) if e[0] == "congelamento")


# ---------- PRG-09, PRG-10 ----------
def test_prg_09_extrato_so_do_dono_e_paginado(base):
    ana = cliente(base)
    with base.app_context():
        for i in range(25):
            progressao.lancar(uid(base), "ajuste", f"a{i}", 1, f"lançamento {i:02d}")
        db.session.commit()
    html = ana.get("/u/ana/xp").get_data(as_text=True)
    assert "lançamento 24" in html and "lançamento 04" not in html
    assert "lançamento 04" in ana.get("/u/ana/xp?page=2").get_data(as_text=True)
    assert cliente(base, "bia").get("/u/ana/xp").status_code == 404
    assert base.test_client().get("/u/ana/xp").status_code == 404


def test_prg_09_perfil_mostra_nivel_e_sequencia_a_quem_ve(base):
    cliente(base).post("/jogos/jogo-0/avaliar", data={"score": 7})
    html = base.test_client().get("/u/ana").get_data(as_text=True)
    assert "sequência de <strong>1</strong> semana" in html and "Extrato de XP" not in html
    with base.app_context():
        User.query.filter_by(username="ana").one().perfil_privado = True
        db.session.commit()
    assert "sequência de" not in base.test_client().get("/u/ana").get_data(as_text=True)


def test_prg_10_aviso_semanal(base, relogio, monkeypatch):
    for nome in ("ana", "bia"):
        for dia in (2, 9):
            semana(base, relogio, 2026, 11, dia, 10, nome=nome)
    monkeypatch.setitem(politica.ACOES, "aviso_engajamento", lambda user, alvo=None: user.username != "bia")
    relogio.ir(2026, 11, 22, 20, 0)  # domingo à noite da semana seguinte, sem ação
    with base.app_context():
        assert progressao.avisar_sequencias() == 1
        assert progressao.avisar_sequencias() == 0  # uma vez por semana
        n, = Notificacao.query.filter_by(tipo="sequencia").all()
        assert n.user_id == uid(base, "ana")
    r = base.test_cli_runner().invoke(args=["progressao", "avisar-sequencias"])
    assert r.output.strip() == "avisados=0"


def test_prg_10_sem_aviso_com_tipo_desligado_ou_quem_ja_agiu(base, relogio):
    from app.models import PreferenciaNotificacao

    for dia in (2, 9, 16):
        semana(base, relogio, 2026, 11, dia, 10)
    for dia in (9, 16):
        semana(base, relogio, 2026, 11, dia, 10, nome="bia")
    with base.app_context():
        db.session.add(PreferenciaNotificacao(user_id=uid(base, "bia"), tipo="sequencia", site=False))
        db.session.commit()
    relogio.ir(2026, 11, 22, 20, 0)
    with base.app_context():
        assert progressao.avisar_sequencias() == 0  # ana já agiu nesta semana; bia desligou


# ---------- PRG-11 ----------
def test_prg_11_ajuste_do_admin_validado_e_auditado(base):
    chefe = cliente(base, "chefe")
    r = chefe.post("/admin/xp", data={"usuario": "ninguem", "xp": "0", "justificativa": ""})
    html = r.get_data(as_text=True)
    assert r.status_code == 400 and "não encontrado" in html and "diferente de zero" in html and "obrigatória" in html
    assert chefe.post("/admin/xp", data={"usuario": "ana", "xp": "5", "justificativa": "x" * 201}).status_code == 400
    chefe.post("/admin/xp", data={"usuario": "ana", "xp": "-5", "justificativa": "correção"})
    assert extrato(base) == [("ajuste", -5, "correção")]
    with base.app_context():
        assert AuditLog.query.one().action == "xp.ajustar"
    assert cliente(base).post("/admin/xp", data={"usuario": "ana", "xp": "5", "justificativa": "x"}).status_code == 403
