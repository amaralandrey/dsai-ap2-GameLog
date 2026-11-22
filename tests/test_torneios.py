"""Torneios da comunidade (TRN-01..14), sem rede."""
from contextlib import contextmanager
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import event, update

from app import chaveamento, gamificacao, jobs, tempo
from app import torneios_servico as svc
from app.conquistas_catalogo import sincronizar
from app.models import (
    Activity, AuditLog, Bloqueio, Clube, Conquista, ExtratoXP, InscricaoTorneio, MembroClube, Notificacao,
    PartidaTorneio, PreferenciaNotificacao, RegistroTorneio, ReporteResultado, Torneio, User, UsuarioConquista, db,
)
from app.notificacoes import notificar
from conftest import login, make_game, make_user

AGORA = datetime(2026, 11, 16, 12, 0)
NOMES = ("ana", "bia", "carla", "duda", "eva", "fabio", "gil", "hugo", "iris")


@pytest.fixture(autouse=True)
def relogio(monkeypatch):
    """Relógio fixo em Belém (GLOBAL-09); `relogio(horas)` adianta."""
    atual = [AGORA]
    monkeypatch.setattr(tempo, "agora", lambda: atual[0].replace(tzinfo=tempo.FUSO))

    def adiantar(horas):
        atual[0] += timedelta(hours=horas)
    return adiantar


@pytest.fixture
def base(app):
    with app.app_context():
        sincronizar()
    for n in NOMES:
        make_user(app, n)
    make_user(app, "chefe", role="admin")
    make_game(app, "Hades")
    make_game(app, "Celeste")
    return app


def uid(app, nome):
    with app.app_context():
        return User.query.filter_by(username=nome).one().id


def cliente(app, nome=None):
    c = app.test_client()
    if nome:
        login(c, nome)
    return c


FORM = {"nome": "Copa Hades", "jogo": "hades", "formato": "eliminacao_simples", "limite": "8",
        "prazo_inscricao": "2026-11-20T18:00", "sementes": "ranking", "regras": "Melhor de 3."}


def criar(c, **extra):
    return c.post("/torneios/novo", data={**FORM, **extra})


def torneio(app, slug="copa-hades"):
    with app.app_context():
        return Torneio.query.filter_by(slug=slug).one()


def inscrever(app, slug, *nomes):
    for n in nomes:
        cliente(app, n).post(f"/torneios/{slug}/inscrever")


def iniciado(app, nomes, formato="eliminacao_simples", sementes="ranking", **extra):
    """Torneio da ana com `nomes` inscritos, iniciado depois do prazo; devolve o slug."""
    ana = cliente(app, "ana")
    criar(ana, formato=formato, sementes=sementes, limite=str(max(len(nomes), 2)), **extra)
    inscrever(app, "copa-hades", *nomes)
    with app.app_context():
        t = Torneio.query.filter_by(slug="copa-hades").one()
        t.prazo_inscricao = tempo.agora_utc() - timedelta(minutes=1)
        db.session.commit()
    assert ana.post("/torneios/copa-hades/iniciar").status_code == 302
    assert torneio(app).situacao == "andamento"
    return "copa-hades"


def partidas(app, slug="copa-hades"):
    with app.app_context():
        t = Torneio.query.filter_by(slug=slug).one()
        return {p.codigo: (p.id, p.a_id, p.b_id, p.situacao, p.vencedor_id)
                for p in PartidaTorneio.query.filter_by(torneio_id=t.id)}


def nome_de(app, user_id):
    with app.app_context():
        return db.session.get(User, user_id).username


def informar(app, codigo, vencedor_lado="a", quem="ambos", placar=None, slug="copa-hades"):
    """Os lados informam que `vencedor_lado` venceu a partida `codigo`."""
    pid, a, b, _, _ = partidas(app, slug)[codigo]
    vencedor = {"a": a, "b": b}.get(vencedor_lado, vencedor_lado)
    dados = {"vencedor": vencedor, **({"placar_a": placar[0], "placar_b": placar[1]} if placar else {})}
    lados = {"ambos": (a, b), "a": (a,), "b": (b,)}[quem]
    for lado in lados:
        cliente(app, nome_de(app, lado)).post(f"/torneios/{slug}/partidas/{pid}", data=dados)
    return pid


def textos(app, nome):
    with app.app_context():
        return [n.texto for n in Notificacao.query.filter_by(user_id=uid(app, nome), tipo="torneio")]


@contextmanager
def consultas(app):
    """Conta as consultas ao banco no bloco (TRN-13)."""
    feitas = []

    def conta(conn, cursor, sql, *a):
        feitas.append(sql)
    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", conta)
        try:
            yield feitas
        finally:
            event.remove(db.engine, "before_cursor_execute", conta)


# ---------- TRN-01 ----------
def test_trn_01_cria_torneio_e_quem_cria_organiza(base):
    r = criar(cliente(base, "ana"))
    assert r.status_code == 302 and r.headers["Location"].endswith("/torneios/copa-hades")
    t = torneio(base)
    assert (t.nome, t.formato, t.limite, t.sementes, t.regras) == ("Copa Hades", "eliminacao_simples", 8, "ranking",
                                                                  "Melhor de 3.")
    assert t.organizador_id == uid(base, "ana") and t.aberto_a_menores is False and t.situacao == "aberto"
    assert t.prazo_inscricao == datetime(2026, 11, 20, 21, 0)  # 18h em Belém, gravado em UTC
    assert cliente(base).get("/torneios/novo").status_code == 302  # GLOBAL-02: visitante vai ao login


@pytest.mark.parametrize("campo, valor", [
    ("nome", "x" * 81), ("nome", ""), ("jogo", "nao-existe"), ("formato", "mata-mata"), ("limite", "1"),
    ("limite", "65"), ("limite", "oito"), ("prazo_inscricao", "2026-11-15T10:00"), ("prazo_inscricao", "amanhã"),
    ("sementes", "idade"), ("regras", "r" * 2001), ("prazo_rodada_dias", "0"),
])
def test_trn_01_validacao_no_servidor(base, campo, valor):
    r = criar(cliente(base, "ana"), **{campo: valor})
    assert r.status_code == 400
    with base.app_context():
        assert Torneio.query.count() == 0


def test_trn_01_marca_aberto_a_menores(base):
    criar(cliente(base, "ana"), aberto_a_menores="1")
    assert torneio(base).aberto_a_menores is True


# ---------- TRN-02, TRN-03: clube ----------
@pytest.fixture
def clube(base):
    """Clube da ana com a bia moderadora e a carla membro; a duda não é membro."""
    ana = cliente(base, "ana")
    ana.post("/clubes/novo", data={"nome": "Roguelikes", "descricao": "d"})
    for n in ("bia", "carla"):
        cliente(base, n).post("/clubes/roguelikes/entrar")
    ana.post(f"/clubes/roguelikes/membros/{uid(base, 'bia')}/promover")
    return base


def test_trn_02_torneio_de_clube_so_para_membros_e_moderacao_organiza(clube):
    assert criar(cliente(clube, "carla"), clube="roguelikes").status_code == 400  # membro comum não cria
    assert criar(cliente(clube, "ana"), clube="roguelikes").status_code == 302
    t = torneio(clube)
    assert t.clube_id is not None
    inscrever(clube, "copa-hades", "carla", "duda")
    with clube.app_context():
        inscritos = {i.user_id for i in InscricaoTorneio.query.filter_by(torneio_id=t.id)}
    assert inscritos == {uid(clube, "carla")}
    bia = cliente(clube, "bia")  # a moderação do clube também organiza
    assert bia.get("/torneios/copa-hades/editar").status_code == 200
    assert cliente(clube, "carla").get("/torneios/copa-hades/editar").status_code == 403
    pagina = cliente(clube, "duda").get("/clubes/roguelikes").get_data(as_text=True)
    assert "/torneios/copa-hades" in pagina


def test_trn_03_torneio_de_clube_privado_responde_404_a_quem_nao_e_membro(clube):
    with clube.app_context():
        Clube.query.filter_by(slug="roguelikes").one().publico = False
        db.session.commit()
    criar(cliente(clube, "ana"), clube="roguelikes")
    assert cliente(clube, "carla").get("/torneios/copa-hades").status_code == 200
    assert cliente(clube, "duda").get("/torneios/copa-hades").status_code == 404
    assert cliente(clube).get("/torneios/copa-hades").status_code == 404
    assert cliente(clube, "duda").post("/torneios/copa-hades/inscrever").status_code == 404
    assert "Copa Hades" not in cliente(clube, "duda").get("/torneios").get_data(as_text=True)
    assert "Copa Hades" in cliente(clube, "carla").get("/torneios").get_data(as_text=True)


# ---------- TRN-03: lista e página ----------
def test_trn_03_lista_paginada_e_filtrada_por_jogo_e_situacao(base):
    ana = cliente(base, "ana")
    for i in range(21):
        criar(ana, nome=f"Copa {i:02d}")
    criar(ana, nome="Taça Celeste", jogo="celeste")
    with base.app_context():
        Torneio.query.filter_by(slug="copa-00").one().situacao = "encerrado"
        Torneio.query.filter_by(slug="copa-01").one().situacao = "cancelado"
        db.session.commit()
    visitante = cliente(base)
    assert "Taça Celeste" in visitante.get("/torneios?jogo=celeste").get_data(as_text=True)
    assert "Copa 05" not in visitante.get("/torneios?jogo=celeste").get_data(as_text=True)
    encerrados = visitante.get("/torneios?situacao=encerrado").get_data(as_text=True)
    assert "Copa 00" in encerrados and "Copa 05" not in encerrados
    paginas = visitante.get("/torneios").get_data(as_text=True) + visitante.get("/torneios?page=2").get_data(as_text=True)
    assert "Copa 01" not in paginas  # cancelado fica fora
    assert all(f"Copa {i:02d}" in paginas for i in range(2, 21))
    assert "Copa 00" in visitante.get("/torneios?page=99").get_data(as_text=True)  # GLOBAL-04: cai na última


def test_trn_03_pagina_mostra_regras_inscritos_chave_e_classificacao(base):
    iniciado(base, ("bia", "carla", "duda"))
    html = cliente(base).get("/torneios/copa-hades").get_data(as_text=True)
    assert "Melhor de 3." in html and 'role="img"' in html and "<svg" in html
    assert "Classificação" in html and "bia" in html and "duda" in html


# ---------- TRN-04 ----------
def test_trn_04_vagas_prazo_e_inscricao_duplicada(base, relogio):
    criar(cliente(base, "ana"), limite="2")
    bia = cliente(base, "bia")
    bia.post("/torneios/copa-hades/inscrever")
    bia.post("/torneios/copa-hades/inscrever")  # de novo: não duplica
    inscrever(base, "copa-hades", "carla", "duda")
    t = torneio(base)
    with base.app_context():
        assert InscricaoTorneio.query.filter_by(torneio_id=t.id).count() == 2  # duda ficou sem vaga
        assert InscricaoTorneio.query.filter_by(torneio_id=t.id, user_id=uid(base, "bia")).count() == 1
    r = cliente(base, "duda").post("/torneios/copa-hades/inscrever", follow_redirects=True)
    assert "Não há mais vagas" in r.get_data(as_text=True)
    cliente(base, "carla").post("/torneios/copa-hades/desistir")  # desiste até o fechamento e libera a vaga
    relogio(24 * 5)  # passou do prazo
    r = cliente(base, "duda").post("/torneios/copa-hades/inscrever", follow_redirects=True)
    assert "inscrições deste torneio estão encerradas" in r.get_data(as_text=True)


def test_trn_04_organizador_remove_com_motivo_notificado(base):
    criar(cliente(base, "ana"))
    inscrever(base, "copa-hades", "bia", "carla")
    ana = cliente(base, "ana")
    ana.post(f"/torneios/copa-hades/inscritos/{uid(base, 'bia')}/remover", data={"motivo": ""})
    with base.app_context():
        assert InscricaoTorneio.query.filter_by(user_id=uid(base, "bia")).one().situacao == "inscrito"
    ana.post(f"/torneios/copa-hades/inscritos/{uid(base, 'bia')}/remover", data={"motivo": "nick ofensivo"})
    with base.app_context():
        assert InscricaoTorneio.query.filter_by(user_id=uid(base, "bia")).one().situacao == "removido"
    assert any("nick ofensivo" in t for t in textos(base, "bia"))
    cliente(base, "bia").post("/torneios/copa-hades/inscrever")  # removida não volta sozinha
    with base.app_context():
        assert InscricaoTorneio.query.filter_by(user_id=uid(base, "bia")).one().situacao == "removido"
    assert cliente(base, "carla").post(f"/torneios/copa-hades/inscritos/{uid(base, 'carla')}/remover",
                                       data={"motivo": "x"}).status_code == 403


def menor(app, nome, anos):
    with app.app_context():
        u = User.query.filter_by(username=nome).one()
        u.nascimento = date(2026 - anos, 1, 1)
        db.session.commit()


def test_trn_04_adolescente_barrado_em_torneio_aberto_e_liberado_no_aberto_a_menores(base):
    menor(base, "bia", 17)
    criar(cliente(base, "ana"))
    criar(cliente(base, "ana"), nome="Copa Teen", aberto_a_menores="1")
    inscrever(base, "copa-hades", "bia")
    inscrever(base, "copa-teen", "bia")
    with base.app_context():
        inscricoes = {i.torneio_id: i.situacao for i in InscricaoTorneio.query}
    assert inscricoes == {torneio(base, "copa-teen").id: "inscrito"}  # de 16 a 17 não espera responsável


def test_trn_04_menor_de_12_a_15_no_clube_espera_o_responsavel_sem_ocupar_vaga(clube):
    menor(clube, "carla", 14)  # membro do clube, já vinculado ao responsável
    criar(cliente(clube, "ana"), limite="2")
    criar(cliente(clube, "ana"), nome="Copa Clube", clube="roguelikes", limite="2")
    inscrever(clube, "copa-hades", "carla")  # torneio aberto sem a marca: barrada (PMN-11)
    inscrever(clube, "copa-clube", "carla", "bia", "ana")
    t = torneio(clube, "copa-clube")
    with clube.app_context():
        assert InscricaoTorneio.query.filter_by(torneio_id=torneio(clube).id).count() == 0
        insc = InscricaoTorneio.query.filter_by(torneio_id=t.id, user_id=uid(clube, "carla")).one()
        assert insc.situacao == "pendente_responsavel"
        assert svc.vagas_ocupadas(t) == 2  # bia e ana: a pendente não ocupa vaga
        assert svc.autorizar_inscricao(insc) is False  # RSP-08 sem vaga
        InscricaoTorneio.query.filter_by(torneio_id=t.id, user_id=uid(clube, "ana")).one().situacao = "desistiu"
        assert svc.autorizar_inscricao(insc) is True and insc.situacao == "inscrito"  # com a autorização
        db.session.commit()


def test_trn_04_pendente_e_descartada_se_o_torneio_comecar_antes(clube):
    menor(clube, "carla", 13)
    criar(cliente(clube, "ana"), clube="roguelikes", limite="2")
    inscrever(clube, "copa-hades", "carla", "bia", "ana")
    cliente(clube, "ana").post("/torneios/copa-hades/iniciar")  # vagas preenchidas
    with clube.app_context():
        assert InscricaoTorneio.query.filter_by(user_id=uid(clube, "carla")).one().situacao == "descartada"


# ---------- TRN-05 ----------
def test_trn_05_inicio_exige_dois_inscritos_e_prazo_ou_vagas(base, relogio):
    criar(cliente(base, "ana"), limite="3")
    ana = cliente(base, "ana")
    inscrever(base, "copa-hades", "bia")
    relogio(24 * 5)
    assert "ao menos 2" in ana.post("/torneios/copa-hades/iniciar", follow_redirects=True).get_data(as_text=True)
    relogio(-24 * 5)
    inscrever(base, "copa-hades", "carla")
    r = ana.post("/torneios/copa-hades/iniciar", follow_redirects=True).get_data(as_text=True)
    assert "depois do prazo" in r and torneio(base).situacao == "aberto"
    inscrever(base, "copa-hades", "duda")  # vagas preenchidas
    ana.post("/torneios/copa-hades/iniciar")
    assert torneio(base).situacao == "andamento"
    assert any("começou" in t for t in textos(base, "bia"))
    inscrever(base, "copa-hades", "eva")  # congelado
    with base.app_context():
        assert InscricaoTorneio.query.filter_by(user_id=uid(base, "eva")).count() == 0


def dar_xp(app, **pontos):
    with app.app_context():
        for nome, xp in pontos.items():
            db.session.add(ExtratoXP(user_id=uid(app, nome), origem_tipo="teste", origem_id=0, xp=xp, motivo="t",
                                     created_at=datetime(2026, 11, 10)))
        db.session.commit()


def sementes(app):
    with app.app_context():
        return [nome_de(app, i.user_id) for i in InscricaoTorneio.query.filter(
            InscricaoTorneio.semente.isnot(None)).order_by(InscricaoTorneio.semente)]


def test_trn_05_sementes_pelo_xp_da_temporada(base):
    dar_xp(base, duda=300, bia=100, carla=200)
    iniciado(base, ("bia", "carla", "duda", "eva"))
    assert sementes(base) == ["duda", "carla", "bia", "eva"]  # eva sem XP fica no fim
    p = partidas(base)
    assert {nome_de(base, p["E1-1"][1]), nome_de(base, p["E1-1"][2])} == {"duda", "eva"}  # 1 × 4


def test_trn_05_sorteio_reprodutivel_pela_semente_guardada(base):
    iniciado(base, ("bia", "carla", "duda", "eva", "fabio"), sementes="sorteio")
    t = torneio(base)
    assert t.semente_sorteio is not None
    ids = sorted(uid(base, n) for n in ("bia", "carla", "duda", "eva", "fabio"))
    esperado = [nome_de(base, i) for i in chaveamento.sementes(ids, {}, aleatorio=t.semente_sorteio)]
    assert sementes(base) == esperado


def bloquear(app, a, b):
    with app.app_context():
        db.session.add(Bloqueio(bloqueador_id=uid(app, a), bloqueado_id=uid(app, b)))
        db.session.commit()


def confrontos(app, rodada=1):
    return {frozenset((nome_de(app, a), nome_de(app, b))) for c, (_, a, b, _, _) in partidas(app).items()
            if int(c[1:].split("-")[0]) == rodada}


@pytest.mark.parametrize("formato", ["eliminacao_simples", "eliminacao_dupla", "pontos_corridos"])
def test_trn_05_bloqueio_afasta_o_par_na_primeira_rodada(base, formato):
    dar_xp(base, bia=400, carla=300, duda=200, eva=100)
    bloquear(base, "eva", "bia")  # 1 × 4 pelas sementes
    iniciado(base, ("bia", "carla", "duda", "eva"), formato=formato)
    assert frozenset(("bia", "eva")) not in confrontos(base)
    assert sementes(base) == ["bia", "carla", "eva", "duda"]  # eva trocou com a semente vizinha


def test_trn_05_bloqueio_afasta_o_par_em_cada_rodada_do_suico(base):
    dar_xp(base, bia=400, carla=300, duda=200, eva=100)
    bloquear(base, "bia", "carla")  # 1 × 2 no suíço
    iniciado(base, ("bia", "carla", "duda", "eva"), formato="suico")
    assert frozenset(("bia", "carla")) not in confrontos(base)
    for codigo in ("S1-1", "S1-2"):
        informar(base, codigo)
    assert torneio(base).situacao == "andamento"
    assert frozenset(("bia", "carla")) not in confrontos(base, 2)


# ---------- TRN-06 ----------
def test_trn_06_consenso_confirma_e_avanca_a_chave(base):
    dar_xp(base, bia=400, carla=300, duda=200, eva=100)
    iniciado(base, ("bia", "carla", "duda", "eva"))
    informar(base, "E1-1", "a", quem="a", placar=(2, 1))
    assert partidas(base)["E1-1"][3] == "aguardando"
    informar(base, "E1-1", "a", quem="b")  # sem placar: vale o do outro lado
    pid, _, _, situacao, vencedor = partidas(base)["E1-1"]
    assert situacao == "confirmada" and vencedor == uid(base, "bia")
    with base.app_context():
        assert (db.session.get(PartidaTorneio, pid).placar_a, db.session.get(PartidaTorneio, pid).placar_b) == (2, 1)
    assert "E2-1" not in partidas(base)
    informar(base, "E1-2", "b")
    final = partidas(base)["E2-1"]
    assert {final[1], final[2]} == {uid(base, "bia"), uid(base, "duda")}


def test_trn_06_resultado_invalido_e_de_quem_nao_joga(base):
    iniciado(base, ("bia", "carla"))
    pid, a, b, _, _ = partidas(base)["E1-1"]
    joga = cliente(base, nome_de(base, a))
    for dados in ({"vencedor": "empate"}, {"vencedor": a, "placar_a": "1", "placar_b": "3"},
                  {"vencedor": a, "placar_a": "1"}, {"vencedor": uid(base, "eva")}, {"vencedor": "x"}):
        joga.post(f"/torneios/copa-hades/partidas/{pid}", data=dados)
    with base.app_context():
        assert ReporteResultado.query.count() == 0
    assert cliente(base, "eva").post(f"/torneios/copa-hades/partidas/{pid}", data={"vencedor": a}).status_code == 403


def test_trn_06_um_lado_so_confirma_depois_de_48_horas_pelo_job(base, relogio):
    iniciado(base, ("bia", "carla"))
    informar(base, "E1-1", "b", quem="a")
    relogio(47)
    with base.app_context():
        assert svc.confirmar_vencidas() == 0
    relogio(2)
    with base.app_context():
        job = jobs.enfileirar("torneios")
        db.session.commit()
        assert jobs.executar(job.id) == "ok"
    assert partidas(base)["E1-1"][3] == "confirmada"
    t = torneio(base)
    assert t.situacao == "encerrado"  # final de 2: o job encerra e despacha os eventos de GAM
    with base.app_context():
        campeao = InscricaoTorneio.query.filter_by(posicao_final=1).one().user_id
        assert campeao == partidas(base)["E1-1"][2]
        assert desbloqueadas(base, campeao) >= {"campeao-torneio", "podio-torneio", "primeiro-torneio"}


# ---------- TRN-07 ----------
def test_trn_07_contestacao_decidida_com_justificativa_e_auditoria(base):
    iniciado(base, ("bia", "carla", "duda", "eva"))
    pid = informar(base, "E1-1", "a", quem="a")
    informar(base, "E1-1", "b", quem="b")
    assert partidas(base)["E1-1"][3] == "contestada"
    assert any("contestado" in t for t in textos(base, "ana"))
    ana = cliente(base, "ana")
    with base.app_context():
        rid = ReporteResultado.query.filter_by(partida_id=pid).order_by(ReporteResultado.id).first().id
    ana.post(f"/torneios/copa-hades/partidas/{pid}/decidir", data={"opcao": f"reporte-{rid}", "justificativa": ""})
    assert partidas(base)["E1-1"][3] == "contestada"  # sem justificativa não decide
    assert cliente(base, "bia").post(f"/torneios/copa-hades/partidas/{pid}/decidir",
                                     data={"opcao": f"reporte-{rid}", "justificativa": "x"}).status_code == 403
    ana.post(f"/torneios/copa-hades/partidas/{pid}/decidir",
             data={"opcao": f"reporte-{rid}", "justificativa": "print do placar"})
    _, a, _, situacao, vencedor = partidas(base)["E1-1"]
    assert situacao == "confirmada" and vencedor == a
    html = cliente(base).get("/torneios/copa-hades").get_data(as_text=True)
    assert "print do placar" in html and "Decisão" in html and "por ana" in html


@pytest.mark.parametrize("opcao", ["outro", "wo"])
def test_trn_07_decisao_por_outro_resultado_ou_wo(base, opcao):
    iniciado(base, ("bia", "carla"))
    pid = informar(base, "E1-1", "a", quem="a", placar=(2, 0))
    informar(base, "E1-1", "a", quem="b", placar=(3, 0))  # placares diferentes: contestada
    assert partidas(base)["E1-1"][3] == "contestada"
    _, a, b, _, _ = partidas(base)["E1-1"]
    cliente(base, "ana").post(f"/torneios/copa-hades/partidas/{pid}/decidir",
                              data={"opcao": opcao, "vencedor": b, "justificativa": "vídeo"})
    with base.app_context():
        p = db.session.get(PartidaTorneio, pid)
        assert p.vencedor_id == b and p.wo is (opcao == "wo")


# ---------- TRN-08 ----------
def test_trn_08_correcao_bloqueada_lista_as_partidas_seguintes(base):
    nomes = ("bia", "carla", "duda", "eva", "fabio", "gil", "hugo", "iris")
    iniciado(base, nomes)
    for codigo in ("E1-1", "E1-2", "E1-3", "E1-4"):
        informar(base, codigo, "a")
    informar(base, "E2-1", "a")
    pid = partidas(base)["E1-1"][0]
    _, a, b, _, _ = partidas(base)["E1-1"]
    r = cliente(base, "ana").post(f"/torneios/copa-hades/partidas/{pid}/corrigir",
                                  data={"vencedor": b, "justificativa": "erro de digitação"}, follow_redirects=True)
    assert "E2-1" in r.get_data(as_text=True) and partidas(base)["E1-1"][4] == a
    # E1-3 → E2-2 ainda não jogada: a correção vale e troca o lado da partida seguinte
    pid3, a3, b3, _, _ = partidas(base)["E1-3"]
    cliente(base, "ana").post(f"/torneios/copa-hades/partidas/{pid3}/corrigir",
                              data={"vencedor": b3, "justificativa": ""})
    assert partidas(base)["E1-3"][4] == a3  # sem justificativa, nada muda
    cliente(base, "ana").post(f"/torneios/copa-hades/partidas/{pid3}/corrigir",
                              data={"vencedor": b3, "justificativa": "placar trocado"})
    assert partidas(base)["E1-3"][4] == b3
    assert partidas(base)["E2-2"][1] == b3
    with base.app_context():
        assert RegistroTorneio.query.filter_by(acao="correcao").count() == 1


def test_trn_08_correcao_no_suico_e_na_liga(base):
    iniciado(base, ("bia", "carla", "duda", "eva"), formato="suico")
    informar(base, "S1-1", "a")
    informar(base, "S1-2", "a")
    informar(base, "S2-1", "a")
    pid = partidas(base)["S1-1"][0]
    b = partidas(base)["S1-1"][2]
    r = cliente(base, "ana").post(f"/torneios/copa-hades/partidas/{pid}/corrigir",
                                  data={"vencedor": b, "justificativa": "x"}, follow_redirects=True)
    assert "S2-1" in r.get_data(as_text=True)
    pid2, a2, b2, _, _ = partidas(base)["S1-2"]
    with base.app_context():  # S2-1 está jogada; desfaz para a correção da rodada 1 valer
        p = PartidaTorneio.query.filter_by(codigo="S2-1").one()
        p.situacao, p.vencedor_id = "aguardando", None
        db.session.commit()
    cliente(base, "ana").post(f"/torneios/copa-hades/partidas/{pid2}/corrigir", data={"vencedor": "empate",
                                                                                     "justificativa": "x"})
    assert partidas(base)["S1-2"][4] is None and partidas(base)["S1-2"][3] == "confirmada"


# ---------- TRN-09 ----------
def test_trn_09_wo_depois_do_prazo_da_rodada(base, relogio):
    iniciado(base, ("bia", "carla", "duda", "eva"), prazo_rodada_dias="2")
    pid, a, b, _, _ = partidas(base)["E1-1"]
    ana = cliente(base, "ana")
    ana.post(f"/torneios/copa-hades/partidas/{pid}/wo", data={"vencedor": a, "justificativa": "sumiu"})
    assert partidas(base)["E1-1"][3] == "aguardando"  # antes do prazo
    pid2 = informar(base, "E1-2", "a", quem="a")
    relogio(24 * 2 + 1)
    _, a2, b2, _, _ = partidas(base)["E1-2"]
    ana.post(f"/torneios/copa-hades/partidas/{pid2}/wo", data={"vencedor": b2, "justificativa": "x"})
    assert partidas(base)["E1-2"][3] == "aguardando"  # quem informou não recebe W.O.
    ana.post(f"/torneios/copa-hades/partidas/{pid}/wo", data={"vencedor": a, "justificativa": "não apareceu"})
    with base.app_context():
        p = db.session.get(PartidaTorneio, pid)
        assert p.situacao == "confirmada" and p.wo and p.vencedor_id == a
        assert RegistroTorneio.query.filter_by(acao="wo", partida_id=pid).one().justificativa == "não apareceu"


def test_trn_09_desistencia_durante_o_torneio_perde_o_resto_por_wo(base):
    iniciado(base, ("bia", "carla", "duda"), formato="pontos_corridos")
    cliente(base, "duda").post("/torneios/copa-hades/desistir")
    with base.app_context():
        insc = InscricaoTorneio.query.filter_by(user_id=uid(base, "duda")).one()
        assert insc.situacao == "desistiu" and insc.semente is not None  # histórico preservado
        da_duda = PartidaTorneio.query.filter((PartidaTorneio.a_id == insc.user_id)
                                              | (PartidaTorneio.b_id == insc.user_id)).all()
        assert da_duda and all(p.wo and p.vencedor_id != insc.user_id for p in da_duda)
    html = cliente(base).get("/torneios/copa-hades").get_data(as_text=True)
    assert "desistiu" in html


def test_trn_09_desistente_perde_por_wo_as_partidas_novas_da_eliminacao(base):
    dar_xp(base, bia=400, carla=300, duda=200, eva=100)
    iniciado(base, ("bia", "carla", "duda", "eva"))
    cliente(base, "bia").post("/torneios/copa-hades/desistir")
    assert partidas(base)["E1-1"][4] == uid(base, "eva")
    informar(base, "E1-2", "a")
    assert partidas(base)["E2-1"][3] == "aguardando"
    cliente(base, "eva").post("/torneios/copa-hades/desistir")  # a final vira W.O.
    final = partidas(base)["E2-1"]
    assert final[3] == "confirmada" and final[4] == uid(base, "carla")
    assert torneio(base).situacao == "encerrado"


# ---------- TRN-10, TRN-11 ----------
def desbloqueadas(app, user_id):
    with app.app_context():
        return {c.codigo for c in Conquista.query.join(UsuarioConquista).filter(
            UsuarioConquista.user_id == user_id, UsuarioConquista.desbloqueada_em.isnot(None))}


def test_trn_10_notificacoes_do_torneio(base):
    dar_xp(base, bia=400, carla=300, duda=200, eva=100)
    iniciado(base, ("bia", "carla", "duda", "eva"))
    informar(base, "E1-1", "a", quem="b")
    pid = informar(base, "E1-2", "a", quem="a")
    informar(base, "E1-2", "b", quem="b")
    cliente(base, "ana").post(f"/torneios/copa-hades/partidas/{pid}/decidir",
                              data={"opcao": "outro", "vencedor": partidas(base)["E1-2"][1], "justificativa": "j"})
    informar(base, "E1-1", "a", quem="a")
    informar(base, "E2-1", "a")
    bia, carla = " | ".join(textos(base, "bia")), " | ".join(textos(base, "carla"))
    assert "começou" in bia and "Nova partida" in bia and "terminou" in bia
    assert "adversário informou" in bia
    assert "contestado" in carla and "decidiu" in carla
    with base.app_context():  # NTF-09: a preferência desligada cala o tipo
        db.session.add(PreferenciaNotificacao(user_id=uid(base, "eva"), tipo="torneio", site=False, email=False))
        db.session.commit()
        assert notificar(uid(base, "eva"), "torneio", texto="x") is None


def test_trn_11_encerramento_classificacao_conquistas_e_feed(base):
    dar_xp(base, bia=400, carla=300, duda=200, eva=100)
    iniciado(base, ("bia", "carla", "duda", "eva"))
    informar(base, "E1-1", "a")
    informar(base, "E1-2", "b")  # duda elimina carla
    informar(base, "E2-1", "b")  # duda campeã
    t = torneio(base)
    assert t.situacao == "encerrado" and t.encerrado_em is not None
    with base.app_context():
        posicoes = {nome_de(base, i.user_id): i.posicao_final for i in InscricaoTorneio.query}
    assert posicoes["duda"] == 1 and posicoes["bia"] == 2 and {posicoes["carla"], posicoes["eva"]} == {3, 4}
    assert desbloqueadas(base, uid(base, "duda")) >= {"campeao-torneio", "podio-torneio", "primeiro-torneio"}
    assert "campeao-torneio" not in desbloqueadas(base, uid(base, "bia"))
    assert "podio-torneio" in desbloqueadas(base, uid(base, "bia"))
    quarto = next(n for n, p in posicoes.items() if p == 4)
    assert desbloqueadas(base, uid(base, quarto)) & {"podio-torneio", "primeiro-torneio"} == {"primeiro-torneio"}
    with base.app_context():  # idempotente: o evento de novo não duplica
        antes = UsuarioConquista.query.count()
        gamificacao.emitir_evento(uid(base, "duda"), "torneio", posicao=1)
        assert UsuarioConquista.query.count() == antes
        atividade = Activity.query.filter_by(kind="torneio").one()
        assert atividade.user_id == uid(base, "duda") and "Copa Hades" in atividade.texto
    eva = cliente(base, "eva")
    eva.post("/u/duda/seguir")
    assert "venceu o torneio Copa Hades" in eva.get("/feed").get_data(as_text=True)
    with base.app_context():  # DIA-13: perfil privado some do feed de quem não pode vê-lo
        User.query.filter_by(username="duda").one().perfil_privado = True
        db.session.commit()
    assert "venceu o torneio" not in eva.get("/feed").get_data(as_text=True)


@pytest.mark.parametrize("formato, nomes", [("pontos_corridos", ("bia", "carla", "duda")),
                                            ("suico", ("bia", "carla", "duda", "eva", "fabio")),
                                            ("eliminacao_dupla", ("bia", "carla", "duda"))])
def test_trn_11_liga_suico_e_dupla_encerram(base, formato, nomes):
    iniciado(base, nomes, formato=formato)
    for _ in range(40):
        abertas = [c for c, p in partidas(base).items() if p[3] == "aguardando"]
        if not abertas:
            break
        informar(base, abertas[0], "a")
    assert torneio(base).situacao == "encerrado"
    with base.app_context():
        assert sorted(i.posicao_final for i in InscricaoTorneio.query) == list(range(1, len(nomes) + 1))
    assert "Classificação final" in cliente(base).get("/torneios/copa-hades").get_data(as_text=True)


def test_trn_11_torneio_de_clube_privado_nao_vai_ao_feed(clube):
    with clube.app_context():
        Clube.query.filter_by(slug="roguelikes").one().publico = False
        db.session.commit()
    criar(cliente(clube, "ana"), clube="roguelikes", limite="2")
    inscrever(clube, "copa-hades", "bia", "carla")
    cliente(clube, "ana").post("/torneios/copa-hades/iniciar")
    informar(clube, "E1-1", "a")
    assert torneio(clube).situacao == "encerrado"
    with clube.app_context():
        assert Activity.query.filter_by(kind="torneio").count() == 0


# ---------- TRN-12 ----------
def test_trn_12_so_o_organizador_edita_antes_do_inicio(base):
    criar(cliente(base, "ana"))
    novo = {"nome": "Copa Nova", "regras": "Nova regra", "prazo_inscricao": "2026-11-25T10:00", "prazo_rodada_dias": "3"}
    assert cliente(base, "bia").post("/torneios/copa-hades/editar", data=novo).status_code == 403
    assert cliente(base, "bia").post("/torneios/copa-hades/iniciar").status_code == 403
    assert cliente(base, "bia").post("/torneios/copa-hades/cancelar", data={"motivo": "x"}).status_code == 403
    ana = cliente(base, "ana")
    assert ana.get("/torneios/copa-hades/editar").status_code == 200
    assert ana.post("/torneios/copa-hades/editar", data={**novo, "nome": ""}).status_code == 400
    ana.post("/torneios/copa-hades/editar", data=novo)
    t = torneio(base)
    assert (t.nome, t.regras, t.prazo_rodada_dias) == ("Copa Nova", "Nova regra", 3)
    inscrever(base, "copa-hades", "bia", "carla")
    with base.app_context():
        Torneio.query.filter_by(slug="copa-hades").one().prazo_inscricao = datetime(2026, 11, 1)
        db.session.commit()
    ana.post("/torneios/copa-hades/iniciar")
    ana.post("/torneios/copa-hades/editar", data={**novo, "nome": "Depois do início"})
    assert torneio(base).nome == "Copa Nova"


def test_trn_12_cancelamento_com_motivo_e_pelo_admin(base):
    criar(cliente(base, "ana"))
    criar(cliente(base, "bia"), nome="Copa da Bia")
    inscrever(base, "copa-hades", "carla")
    ana = cliente(base, "ana")
    ana.post("/torneios/copa-hades/cancelar", data={"motivo": ""})
    assert torneio(base).situacao == "aberto"
    ana.post("/torneios/copa-hades/cancelar", data={"motivo": "sem quórum"})
    t = torneio(base)
    assert t.situacao == "cancelado" and t.motivo_cancelamento == "sem quórum"
    assert any("sem quórum" in x for x in textos(base, "carla"))
    assert cliente(base, "ana").get("/admin/torneios").status_code == 403
    admin = cliente(base, "chefe")
    assert "Copa da Bia" in admin.get("/admin/torneios").get_data(as_text=True)
    bia_t = torneio(base, "copa-da-bia")
    admin.post(f"/admin/torneios/{bia_t.id}/cancelar", data={"motivo": "spam"})
    assert torneio(base, "copa-da-bia").situacao == "cancelado"
    with base.app_context():
        assert AuditLog.query.filter_by(action="torneio.cancelar", detail="copa-da-bia").count() == 1
    admin.post(f"/admin/torneios/{bia_t.id}/cancelar", data={"motivo": "de novo"})
    with base.app_context():  # já cancelado: sem segundo registro
        assert AuditLog.query.filter_by(action="torneio.cancelar").count() == 1


# ---------- TRN-13 ----------
def _jogadores(app, n):
    """n usuários adultos de uma vez, sem o hash de senha lento do make_user."""
    with app.app_context():
        for i in range(n):
            db.session.add(User(username=f"j{i}", username_lower=f"j{i}", email=f"j{i}@ex.com", password_hash="x",
                                display_name=f"Jogador {i}", nascimento=date(1990, 1, 1)))
        db.session.commit()
        return [u.id for u in User.query.filter(User.username.like("j%"))]


def _torneio_com(app, ids, formato):
    with app.app_context():
        t = Torneio(slug=f"t-{len(ids)}-{formato}", nome="T", game_id=1, formato=formato, limite=len(ids),
                    prazo_inscricao=datetime(2026, 11, 1), organizador_id=ids[0])
        db.session.add(t)
        db.session.flush()
        for i in ids:
            db.session.add(InscricaoTorneio(torneio_id=t.id, user_id=i))
        svc.iniciar(t, db.session.get(User, ids[0]))
        db.session.commit()
        return t.slug


@pytest.mark.parametrize("formato", ["eliminacao_dupla", "suico", "pontos_corridos"])
def test_trn_13_pagina_com_numero_fixo_de_consultas(base, formato):
    ids = _jogadores(base, 64)
    grande, pequeno = _torneio_com(base, ids, formato), _torneio_com(base, ids[:4], formato)
    c = cliente(base, "bia")
    contagem = {}
    for slug in (grande, pequeno):
        with consultas(base) as feitas:
            assert c.get(f"/torneios/{slug}").status_code == 200
        contagem[slug] = len(feitas)
    assert contagem[grande] == contagem[pequeno] <= 8


# ---------- partida e serviço ----------
def test_trn_06_pagina_da_partida_mostra_os_lados_e_esconde_o_relato_de_terceiros(base):
    iniciado(base, ("bia", "carla"))
    pid = informar(base, "E1-1", "a", quem="a", placar=(2, 1))
    a = nome_de(base, partidas(base)["E1-1"][1])
    assert f"{a} informou" in cliente(base, a).get(f"/torneios/copa-hades/partidas/{pid}").get_data(as_text=True)
    assert "informou" not in cliente(base, "eva").get(f"/torneios/copa-hades/partidas/{pid}").get_data(as_text=True)
    assert cliente(base).get("/torneios/copa-hades/partidas/9999").status_code == 404
    assert cliente(base, "ana").post(f"/torneios/copa-hades/partidas/{pid}/apagar").status_code == 404


def test_trn_04_membros_do_clube_entram_no_menor_inscrito_por_clube(clube):
    """Menor de 16 a 17 membro do clube se inscreve no torneio do clube sem a marca (PMN-11)."""
    menor(clube, "carla", 16)
    criar(cliente(clube, "ana"), clube="roguelikes")
    inscrever(clube, "copa-hades", "carla")
    with clube.app_context():
        assert InscricaoTorneio.query.filter_by(user_id=uid(clube, "carla")).one().situacao == "inscrito"
        assert MembroClube.query.filter_by(user_id=uid(clube, "carla")).one().situacao == "ativo"


# ---------- revisão do PR ----------
def test_trn_05_bloqueio_resolve_todos_os_conflitos_mesmo_se_o_primeiro_nao_melhora():
    ordem = [1, 2, 3, 4, 5, 6, 7, 8]
    pares = {frozenset(p) for p in ((1, 8), (2, 8), (4, 5))}
    nova = svc.afastar_bloqueados("eliminacao_simples", ordem, pares)
    assert frozenset((4, 5)) not in svc._conflitos("eliminacao_simples", nova, pares)

