"""Clubes de jogos (CLB-01..14)."""
from datetime import date, datetime

import pytest
from sqlalchemy import event

from app import clubes, tempo
from app.conquistas_catalogo import sincronizar
from app.menores import politica
from app.models import (
    Activity, AuditLog, Clube, Conquista, Game, MembroClube, MetaClube, Notificacao, PostClube, Report, SessaoJogo,
    User, UsuarioConquista, db,
)
from conftest import login, make_game, make_user


@pytest.fixture(autouse=True)
def relogio(monkeypatch):
    monkeypatch.setattr(tempo, "agora", lambda: datetime(2026, 11, 16, 12, 0, tzinfo=tempo.FUSO))


NOMES = ("ana", "bia", "carla", "duda", "eva")


@pytest.fixture
def base(app):
    with app.app_context():
        sincronizar()
    for n in NOMES:
        make_user(app, n)
    make_user(app, "chefe", role="admin")
    make_game(app, "Hades", genre="Ação")
    make_game(app, "Celeste", genre="Plataforma")
    return app


def uid(app, nome):
    with app.app_context():
        return User.query.filter_by(username=nome).one().id


def cliente(app, nome=None):
    c = app.test_client()
    if nome:
        login(c, nome)
    return c


def criar(c, nome="Roguelikes", **extra):
    return c.post("/clubes/novo", data={"nome": nome, "descricao": "d", **extra})


def membro(app, nome, slug="roguelikes"):
    with app.app_context():
        c = Clube.query.filter_by(slug=slug).one()
        return MembroClube.query.filter_by(clube_id=c.id, user_id=uid(app, nome)).first()


@pytest.fixture
def clube(base):
    """Clube público da ana, com bia moderadora e carla e duda membros."""
    ana = cliente(base, "ana")
    criar(ana)
    for n in ("bia", "carla", "duda"):
        cliente(base, n).post("/clubes/roguelikes/entrar")
    ana.post(f"/clubes/roguelikes/membros/{uid(base, 'bia')}/promover")
    return base


# ---------- CLB-01, CLB-02 ----------
def test_clb_01_cria_com_dono_e_valida(base):
    ana = cliente(base, "ana")
    assert criar(ana, genero="acao").headers["Location"] == "/clubes/roguelikes"
    assert membro(base, "ana").papel == "dono"
    for nome, msg in (("ab", "3 a 60"), ("ROGUELIKES", "Já existe")):
        r = criar(ana, nome)
        assert r.status_code == 400 and msg in r.get_data(as_text=True)
    r = criar(ana, "Outro", genero="nao-existe", descricao="x" * 501)
    assert r.status_code == 400 and "gênero existente" in r.get_data(as_text=True)


def test_clb_01_limite_de_5_clubes(base):
    ana = cliente(base, "ana")
    for i in range(5):
        criar(ana, f"Clube {i}")
    r = criar(ana, "Sexto")
    assert r.status_code == 400 and "máximo permitido" in r.get_data(as_text=True)
    with base.app_context():
        Clube.query.filter_by(nome="Clube 0").one().removido_em = datetime(2026, 11, 10)
        db.session.commit()
    assert criar(ana, "Sexto").status_code == 302  # clube removido pelo admin não conta


def test_clb_02_lista_busca_privado_e_removido(base):
    ana = cliente(base, "ana")
    criar(ana, "Público Aberto")
    criar(ana, "Segredo", visibilidade="privado")
    visitante = cliente(base)
    html = visitante.get("/clubes").get_data(as_text=True)
    assert "Público Aberto" in html and "Segredo" not in html
    assert "Segredo" in ana.get("/clubes").get_data(as_text=True)
    assert "Público Aberto" not in visitante.get("/clubes?q=segr").get_data(as_text=True)
    assert visitante.get("/clubes/segredo").status_code == 404
    assert cliente(base, "bia").get("/clubes/segredo").status_code == 404
    with base.app_context():
        Clube.query.filter_by(slug="publico-aberto").one().removido_em = datetime(2026, 11, 1)
        db.session.commit()
    assert ana.get("/clubes/publico-aberto").status_code == 404


# ---------- CLB-03, CLB-04 ----------
def test_clb_03_entrada_publica_privada_e_saida(base):
    ana = cliente(base, "ana")
    criar(ana, "Segredo", visibilidade="privado")
    bia = cliente(base, "bia")
    bia.post("/clubes/segredo/entrar")
    bia.post("/clubes/segredo/entrar")
    m = membro(base, "bia", "segredo")
    assert m.situacao == "pendente"
    with base.app_context():
        assert MembroClube.query.filter_by(user_id=uid(base, "bia")).count() == 1
    assert bia.get("/clubes/segredo").status_code == 404
    ana.post(f"/clubes/segredo/pedidos/{uid(base, 'bia')}/aprovar")
    assert membro(base, "bia", "segredo").situacao == "ativo" and bia.get("/clubes/segredo").status_code == 200
    bia.post("/clubes/segredo/sair")
    assert membro(base, "bia", "segredo") is None
    r = ana.post("/clubes/segredo/sair", follow_redirects=True)
    assert "Transfira o papel de dono" in r.get_data(as_text=True) and membro(base, "ana", "segredo") is not None


def test_clb_04_papeis_e_403(clube):
    bia, carla = cliente(clube, "bia"), cliente(clube, "carla")
    assert membro(clube, "bia").papel == "moderador"
    assert carla.post(f"/clubes/roguelikes/membros/{uid(clube, 'duda')}/remover").status_code == 403
    assert bia.post(f"/clubes/roguelikes/membros/{uid(clube, 'carla')}/promover").status_code == 403
    assert bia.post(f"/clubes/roguelikes/membros/{uid(clube, 'ana')}/remover").status_code == 403
    cliente(clube, "ana").post(f"/clubes/roguelikes/membros/{uid(clube, 'carla')}/promover")
    assert bia.post(f"/clubes/roguelikes/membros/{uid(clube, 'carla')}/remover").status_code == 403  # moderador
    assert bia.post(f"/clubes/roguelikes/membros/{uid(clube, 'duda')}/remover").status_code == 302
    assert membro(clube, "duda").situacao == "removido"
    duda = cliente(clube, "duda")
    assert duda.post("/clubes/roguelikes/mural", data={"texto": "oi"}).status_code == 403
    assert duda.post("/clubes/roguelikes/entrar").status_code == 403


def test_clb_04_transferencia_de_dono(clube):
    ana = cliente(clube, "ana")
    ana.post(f"/clubes/roguelikes/membros/{uid(clube, 'carla')}/transferir")
    assert membro(clube, "carla").papel == "dono" and membro(clube, "ana").papel == "moderador"
    assert ana.post(f"/clubes/roguelikes/membros/{uid(clube, 'bia')}/rebaixar").status_code == 403
    ana.post("/clubes/roguelikes/sair")
    assert membro(clube, "ana") is None


def test_clb_04_transferencia_respeita_o_limite_de_dono(clube):
    carla = cliente(clube, "carla")
    for i in range(5):
        criar(carla, f"Clube {i}")
    r = cliente(clube, "ana").post(f"/clubes/roguelikes/membros/{uid(clube, 'carla')}/transferir", follow_redirects=True)
    assert "já é dono de 5 clubes" in r.get_data(as_text=True)
    assert membro(clube, "carla").papel == "membro" and membro(clube, "ana").papel == "dono"


# ---------- CLB-05..08 ----------
def sessao(app, nome, slug, dia, minutos, origem="manual"):
    with app.app_context():
        g = Game.query.filter_by(slug=slug).one()
        db.session.add(SessaoJogo(user_id=uid(app, nome), game_id=g.id, jogada_em=dia, duracao_min=minutos,
                                  origem=origem))
        db.session.commit()


def test_clb_05_jogo_do_mes_com_horas_dos_membros(clube):
    ana = cliente(clube, "ana")
    assert cliente(clube, "carla").post("/clubes/roguelikes/jogo-do-mes", data={"jogo": "hades", "mes": "2026-11"}).status_code == 403
    ana.post("/clubes/roguelikes/jogo-do-mes", data={"jogo": "hades", "mes": "2026-11"})
    sessao(clube, "carla", "hades", date(2026, 11, 3), 90)
    sessao(clube, "bia", "hades", date(2026, 11, 4), 30)
    sessao(clube, "eva", "hades", date(2026, 11, 4), 600)  # não é membro
    sessao(clube, "bia", "hades", date(2026, 10, 31), 600)  # outro mês
    html = ana.get("/clubes/roguelikes").get_data(as_text=True)
    assert "<strong>Hades</strong>" in html and "Os membros somam 2h nele este mês" in html


def meta(c, **campos):
    dados = {"tipo": "horas_total", "alvo": "2", "inicio": "2026-11-01", "fim": "2026-11-30", **campos}
    return c.post("/clubes/roguelikes/metas", data=dados, follow_redirects=True)


def test_clb_06_validacao_das_metas(clube):
    ana = cliente(clube, "ana")
    assert "maior ou igual a 1" in meta(ana, alvo="0").get_data(as_text=True)
    assert "depois do início" in meta(ana, fim="2026-10-01").get_data(as_text=True)
    assert "precisa de um jogo" in meta(ana, tipo="horas_jogo").get_data(as_text=True)
    assert cliente(clube, "carla").post("/clubes/roguelikes/metas", data={}).status_code == 403
    with clube.app_context():
        assert MetaClube.query.count() == 0


def test_clb_07_progresso_so_desde_a_entrada_e_top_5(clube):
    ana = cliente(clube, "ana")
    meta(ana, tipo="horas_jogo", jogo="hades", alvo="100")
    with clube.app_context():
        for m in MembroClube.query:
            m.entrou_em = date(2026, 11, 1)
        MembroClube.query.filter_by(user_id=uid(clube, "carla")).one().entrou_em = date(2026, 11, 10)
        User.query.filter_by(username="duda").one().perfil_privado = True
        db.session.commit()
    sessao(clube, "carla", "hades", date(2026, 11, 5), 600)  # antes da entrada: não conta
    sessao(clube, "carla", "hades", date(2026, 11, 12), 120)
    sessao(clube, "duda", "hades", date(2026, 11, 12), 60)
    sessao(clube, "bia", "celeste", date(2026, 11, 12), 600)  # outro jogo
    sessao(clube, "bia", "hades", date(2026, 11, 12), 9000, origem="steam")  # saldo da Steam fica de fora
    with clube.app_context():
        m = MetaClube.query.one()
        total, top = clubes.progresso(m)
        assert total == 3 and [u for u, _ in top] == [uid(clube, "carla"), uid(clube, "duda")]
    html = cliente(clube, "carla").get("/clubes/roguelikes").get_data(as_text=True)
    assert "3 de 100" in html and "membro do clube" in html


def test_clb_07_orcamento_por_meta(clube):
    ana = cliente(clube, "ana")
    meta(ana, alvo="100")
    consultas = []

    def conta(*a):
        consultas.append(1)
    with clube.app_context():
        m = MetaClube.query.one()
        event.listen(db.engine, "before_cursor_execute", conta)
        clubes.progresso(m)
        event.remove(db.engine, "before_cursor_execute", conta)
    assert len(consultas) <= 4


@pytest.mark.parametrize("tipo,extra", [("horas_total", {}), ("horas_jogo", {"jogo": "hades"})])
def test_clb_08_meta_de_horas_atingida_uma_vez(clube, tipo, extra):
    ana = cliente(clube, "ana")
    meta(ana, tipo=tipo, alvo="2", **extra)
    carla = cliente(clube, "carla")
    carla.post("/jogos/hades/sessoes", data={"jogada_em": "2026-11-15", "duracao": "600"})  # antes de entrar
    carla.post("/jogos/hades/sessoes", data={"jogada_em": "2026-11-16", "duracao": "90"})
    with clube.app_context():
        assert MetaClube.query.one().atingida_em is None
    carla.post("/jogos/hades/sessoes", data={"jogada_em": "2026-11-16", "duracao": "30"})
    with clube.app_context():
        m = MetaClube.query.one()
        assert m.atingida_em is not None
        assert clubes.avaliar_meta(m, db.session.get(Clube, m.clube_id)) is False
        assert Notificacao.query.filter_by(tipo="clube").count() == 4  # um aviso por membro ativo
        assert Activity.query.filter_by(kind="clube").count() == 1
        c = Conquista.query.filter_by(codigo="meta-clube-1").one()
        ganhou = {uc.user_id for uc in UsuarioConquista.query.filter(UsuarioConquista.conquista_id == c.id,
                                                                     UsuarioConquista.desbloqueada_em.isnot(None))}
        assert ganhou == {uid(clube, "carla")}


def test_clb_08_meta_de_zerados_em_clube_privado_sem_atividade(base):
    ana = cliente(base, "ana")
    criar(ana, visibilidade="privado")
    for n in ("bia", "carla"):
        cliente(base, n).post("/clubes/roguelikes/entrar")
        ana.post(f"/clubes/roguelikes/pedidos/{uid(base, n)}/aprovar")
    meta(ana, tipo="zerados_jogo", jogo="celeste", alvo="2")
    for n in ("bia", "carla"):
        cliente(base, n).post("/jogos/celeste/sessoes", data={"jogada_em": "2026-11-16", "duracao": "60", "zerou": "on"})
    with base.app_context():
        assert MetaClube.query.one().atingida_em is not None
        assert Activity.query.filter_by(kind="clube").count() == 0


# ---------- CLB-09 ----------
def test_clb_09_ranking_do_clube(clube):
    sessao(clube, "carla", "hades", date(2026, 11, 3), 120)
    sessao(clube, "eva", "hades", date(2026, 11, 3), 999)  # fora do clube
    html = cliente(clube, "eva").get("/rankings?escopo=clube&clube=roguelikes&categoria=horas").get_data(as_text=True)
    assert ">carla</a>" in html and ">eva</a>" not in html
    assert "1. carla · 2h" in cliente(clube, "ana").get("/clubes/roguelikes").get_data(as_text=True)
    ana = cliente(clube, "ana")
    criar(ana, "Segredo", visibilidade="privado")
    assert cliente(clube, "eva").get("/rankings?escopo=clube&clube=segredo").status_code == 404


# ---------- CLB-10, CLB-11 ----------
def test_clb_10_mural(clube):
    carla = cliente(clube, "carla")
    assert "1 a 1.000" in carla.post("/clubes/roguelikes/mural", data={"texto": " "}, follow_redirects=True).get_data(as_text=True)
    for i in range(22):
        carla.post("/clubes/roguelikes/mural", data={"texto": f"post {i:02d}"})
    visitante = cliente(clube)
    html = visitante.get("/clubes/roguelikes").get_data(as_text=True)
    assert "post 21" in html and "post 01" not in html
    assert "post 01" in visitante.get("/clubes/roguelikes?page=2").get_data(as_text=True)
    assert cliente(clube, "eva").post("/clubes/roguelikes/mural", data={"texto": "x"}).status_code == 403
    with clube.app_context():
        pid = PostClube.query.order_by(PostClube.id).first().id
    assert cliente(clube, "duda").post(f"/clubes/roguelikes/mural/{pid}/apagar").status_code == 403
    cliente(clube, "bia").post(f"/clubes/roguelikes/mural/{pid}/ocultar", data={"motivo": "fora do tema"})
    with clube.app_context():
        assert db.session.get(PostClube, pid).oculto
    carla.post(f"/clubes/roguelikes/mural/{pid}/apagar")
    with clube.app_context():
        assert db.session.get(PostClube, pid) is None


def test_clb_11_denuncia_uma_vez_e_ocultacao_automatica(clube):
    cliente(clube, "carla").post("/clubes/roguelikes/mural", data={"texto": "ofensa"})
    with clube.app_context():
        pid = PostClube.query.one().id
    cliente(clube, "eva").post("/clubes/roguelikes/entrar")
    for n in ("duda", "duda", "eva"):
        cliente(clube, n).post(f"/clubes/roguelikes/mural/{pid}/denunciar", data={"reason": "ofensivo"})
    with clube.app_context():
        assert Report.query.filter_by(alvo_tipo="post_clube", alvo_id=pid).count() == 2
        assert not db.session.get(PostClube, pid).oculto
    r = cliente(clube, "carla").post(f"/clubes/roguelikes/mural/{pid}/denunciar", data={"reason": "spam"},
                                     follow_redirects=True)
    assert "próprio post" in r.get_data(as_text=True)
    cliente(clube, "bia").post(f"/clubes/roguelikes/mural/{pid}/denunciar", data={"reason": "spam"})
    with clube.app_context():
        assert db.session.get(PostClube, pid).oculto
    assert "ofensa" in cliente(clube, "bia").get("/clubes/roguelikes/moderacao").get_data(as_text=True)
    assert cliente(clube, "carla").get("/clubes/roguelikes/moderacao").status_code == 403
    cliente(clube, "bia").post("/clubes/roguelikes/moderacao", data={"post": pid, "decisao": "manter"})
    with clube.app_context():
        assert not db.session.get(PostClube, pid).oculto
        decididas = Report.query.filter_by(alvo_tipo="post_clube").all()
        assert {r.status for r in decididas} == {"rejeitada"} and all(r.decided_by == uid(clube, "bia") for r in decididas)
    assert "ofensa" not in cliente(clube, "chefe").get("/admin/denuncias").get_data(as_text=True)


def test_clb_10_autor_privado_no_mural_sem_nome_nem_iniciais(clube):
    carla = cliente(clube, "carla")
    carla.post("/u/carla/editar", data={"display_name": "Zuleica", "bio": "", "perfil_privado": "on"})
    carla.post("/clubes/roguelikes/mural", data={"texto": "segredo"})

    def autor(nome=None):
        html = cliente(clube, nome).get("/clubes/roguelikes").get_data(as_text=True)
        return html[html.index('class="post-clube"'):html.index("segredo")]
    assert "membro do clube" in autor() and "anonimo" in autor() and "ZU" not in autor("ana")
    assert "Zuleica" in autor("chefe") and "ZU" in autor("chefe")  # admin vê o perfil privado (DIA-12)


def test_clb_11_moderacao_com_post_invalido_responde_404(clube):
    assert cliente(clube, "bia").post("/clubes/roguelikes/moderacao", data={"post": "abc"}).status_code == 404


# ---------- CLB-12, CLB-13 ----------
def test_clb_12_menor_protegido_barrado(clube, monkeypatch):
    monkeypatch.setitem(politica.ACOES, "entrar_clube", lambda user, alvo=None: user.username != "eva")
    monkeypatch.setitem(politica.ACOES, "publicar_clube", lambda user, alvo=None: user.username != "duda")
    r = cliente(clube, "eva").post("/clubes/roguelikes/entrar", follow_redirects=True)
    assert "Este clube não está disponível para a sua conta" in r.get_data(as_text=True)
    assert membro(clube, "eva") is None
    r = cliente(clube, "duda").post("/clubes/roguelikes/mural", data={"texto": "oi"}, follow_redirects=True)
    assert "não está disponível" in r.get_data(as_text=True)
    with clube.app_context():
        assert PostClube.query.count() == 0


def test_clb_13_admin_remove_e_restaura_com_auditoria(clube):
    chefe = cliente(clube, "chefe")
    with clube.app_context():
        cid = Clube.query.one().id
    assert cliente(clube, "ana").post(f"/admin/clubes/{cid}/remover").status_code == 403
    chefe.post(f"/admin/clubes/{cid}/remover")
    assert cliente(clube, "ana").get("/clubes/roguelikes").status_code == 404
    with clube.app_context():
        assert MembroClube.query.count() == 4  # nada apagado
    chefe.post(f"/admin/clubes/{cid}/restaurar")
    assert cliente(clube, "ana").get("/clubes/roguelikes").status_code == 200
    assert "Roguelikes" in chefe.get("/admin/clubes").get_data(as_text=True)
    with clube.app_context():
        assert [a.action for a in AuditLog.query.order_by(AuditLog.id)] == ["clube.remover", "clube.restaurar"]
