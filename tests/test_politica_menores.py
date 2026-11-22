"""Política de menores (PMN-01..14), sem rede e com o relógio de Belém fixo em 16/11/2026."""
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app import recomendacoes, tempo
from app.conquistas_catalogo import sincronizar
from app.diario import ocultos_para, pode_ver_perfil
from app.menores import politica
from app.menores.idade import faixa_etaria, sql_menor
from app.menores.politica import ACOES, pode
from app.models import (
    Activity, Clube, Conquista, Follow, Game, GameList, MembroClube, Notificacao, Review, Status, User, UsuarioConquista, db,
    now,
)
from app.notificacoes import notificar
from app.social import feed_query
from conftest import login, make_game, make_user

HOJE = date(2026, 11, 16)
PERFIS = ("adulto", "adolescente", "vinculado", "pendente", "sem_data")


@pytest.fixture(autouse=True)
def relogio(monkeypatch):
    monkeypatch.setattr(tempo, "agora", lambda: datetime(2026, 11, 16, 12, 0, tzinfo=tempo.FUSO))


def anos_atras(anos, dias=0):
    return date(HOJE.year - anos, HOJE.month, HOJE.day + dias)


def criar(app, nome, perfil="adulto"):
    """Conta no perfil pedido; a de 12 a 15 anos já vinculada só não está `pendente` (RSP fica para depois)."""
    uid = make_user(app, nome)
    with app.app_context():
        u = db.session.get(User, uid)
        u.nascimento = {"adulto": u.nascimento, "adolescente": anos_atras(17), "vinculado": anos_atras(14),
                        "pendente": anos_atras(14), "sem_data": None}[perfil]
        u.aguardando_responsavel = perfil == "pendente"
        u.perfil_privado = perfil != "adulto"
        db.session.commit()
    return uid


def usuario(nome):
    return User.query.filter_by(username=nome).one()


def cliente(app, nome):
    c = app.test_client()
    login(c, nome)
    return c


def seguir(app, quem, alvo):
    return cliente(app, quem).post(f"/u/{alvo}/seguir")


def follow(app, quem, alvo):
    with app.app_context():
        return Follow.query.filter_by(follower_id=usuario(quem).id, followed_id=usuario(alvo).id).first()


# ---------- PMN-01, PMN-14: matriz faixa × ação ----------
S, N = True, False
MATRIZ = {  # adulto, adolescente, vinculado, pendente, sem data (sem alvo)
    "seguir": (S, S, S, N, S),
    "aprovar_seguidor": (S, S, N, N, N),
    "comentar": (S, S, S, N, S),
    "perfil_publico": (S, S, N, N, N),
    "ranking_global": (S, N, N, N, N),
    "conquista_por_tempo": (S, N, N, N, N),
    "aviso_engajamento": (S, N, N, N, N),
    "email_engajamento": (S, S, S, S, S),  # a faixa de PMN-08 fica em emails.py (EML)
    "ver_ofertas": (S, N, N, N, N),
    "alerta_preco": (S, N, N, N, N),
    "historico_para_terceiros": (S, N, N, N, N),
    "entrar_clube": (S, S, S, N, S),
    "publicar_clube": (S, S, S, N, S),
    "torneio_inscrever": (S, N, N, N, N),
}


@pytest.mark.parametrize("acao", sorted(MATRIZ))
def test_pmn_14_matriz_faixa_por_acao(app, acao):
    for perfil in PERFIS:
        criar(app, perfil, perfil)
    with app.app_context():
        assert tuple(pode(usuario(p), acao) for p in PERFIS) == MATRIZ[acao]


def test_pmn_01_tabela_cobre_as_acoes_das_specs_e_a_matriz_cobre_a_tabela():
    exigidas = {"seguir", "comentar", "perfil_publico", "ranking_global", "conquista_por_tempo", "aviso_engajamento",
                "email_engajamento", "ver_ofertas", "alerta_preco", "entrar_clube", "publicar_clube",
                "torneio_inscrever"}
    assert exigidas <= set(ACOES) == set(MATRIZ)


def test_pmn_01_acao_desconhecida_levanta_valueerror(app):
    criar(app, "ana")
    with app.app_context(), pytest.raises(ValueError, match="desconhecida"):
        pode(usuario("ana"), "mensagem_direta")


def test_pmn_01_visitante_nunca_pode():
    assert not any(pode(None, acao) for acao in ACOES)


def test_pmn_01_sql_menor_igual_a_faixa_nos_limites(app):
    casos = {"faz18hoje": anos_atras(18), "faz18amanha": anos_atras(18, dias=1), "semdata": None,
             "contestada": date(1990, 1, 1)}
    for nome, nascimento in casos.items():
        uid = make_user(app, nome)
        with app.app_context():
            u = db.session.get(User, uid)
            u.nascimento = nascimento
            u.idade_contestada_em = now() if nome == "contestada" else None
            db.session.commit()
    with app.app_context():
        menores = {u.username for u in User.query.filter(sql_menor())}
        assert menores == {u.username for u in User.query if faixa_etaria(u) != "adulto"}
        assert menores == {"faz18amanha", "semdata", "contestada"}


# ---------- PMN-02 ----------
def test_pmn_02_cadastro_de_menor_nasce_privado(app, client):
    client.post("/cadastro", data={"username": "teen", "email": "teen@ex.com", "password": "senha1234",
                                   "nascimento": anos_atras(17).isoformat()})
    app.test_client().post("/cadastro", data={"username": "adulta", "email": "adulta@ex.com",
                                              "password": "senha1234", "nascimento": "1990-01-01"})
    with app.app_context():
        assert usuario("teen").perfil_privado and not usuario("adulta").perfil_privado


def test_pmn_02_lista_nova_de_menor_nasce_privada(app):
    criar(app, "teen", "adolescente")
    criar(app, "ana")
    for nome in ("teen", "ana"):
        c = cliente(app, nome)
        assert ('value="privada" selected' in c.get("/listas").get_data(as_text=True)) == (nome == "teen")
        c.post("/listas", data={"name": "Favoritos"})
    with app.app_context():
        assert {g.user.username: g.public for g in GameList.query} == {"teen": False, "ana": True}


def test_pmn_02_so_16_e_17_tornam_o_perfil_publico(app):
    criar(app, "teen", "adolescente")
    criar(app, "kid", "vinculado")
    dados = {"display_name": "x", "bio": ""}  # sem a caixa marcada: perfil público
    assert "só o responsável" in cliente(app, "kid").get("/u/kid/editar").get_data(as_text=True)
    assert cliente(app, "kid").post("/u/kid/editar", data=dados).status_code == 403
    assert cliente(app, "teen").post("/u/teen/editar", data=dados).status_code == 302
    with app.app_context():
        assert usuario("kid").perfil_privado and not usuario("teen").perfil_privado


# ---------- PMN-03 ----------
def test_pmn_03_contestar_idade_de_adulto_volta_seguidor_a_pedido_pendente(app):
    criar(app, "ana")
    criar(app, "bia")
    make_user(app, "chefe", role="admin")
    seguir(app, "bia", "ana")
    assert follow(app, "bia", "ana").aprovado_em is not None
    with app.app_context():
        aid = usuario("ana").id
    cliente(app, "chefe").post(f"/admin/usuarios/{aid}/contestar-idade")
    assert follow(app, "bia", "ana").aprovado_em is None
    assert "Pedido enviado" in cliente(app, "bia").get("/u/ana").get_data(as_text=True)


def test_pmn_03_verificacao_que_torna_adulto_menor_volta_seguidor_a_pedido_pendente(app, monkeypatch):
    monkeypatch.setenv("VERIFICACAO_PROVEDOR", "simulado")
    criar(app, "ana")
    criar(app, "bia")
    seguir(app, "bia", "ana")
    cliente(app, "ana").post("/conta/idade/provedor_simulado", data={"codigo": "VINCULADO"})
    assert follow(app, "bia", "ana").aprovado_em is None


def test_pmn_03_seguir_menor_e_pedido_pendente(app):
    criar(app, "ana")
    criar(app, "teen", "adolescente")
    with app.app_context():
        db.session.add(Activity(user_id=usuario("teen").id, kind="lista"))
        db.session.commit()
    r = seguir(app, "ana", "teen")
    assert r.status_code == 302
    assert follow(app, "ana", "teen").aprovado_em is None
    ana = cliente(app, "ana")
    perfil = ana.get("/u/teen").get_data(as_text=True)
    assert "Pedido enviado" in perfil and "perfil privado" in perfil  # sem acesso ao perfil
    assert "Ninguém por aqui" in cliente(app, "teen").get("/u/teen/seguidores").get_data(as_text=True)
    assert "<strong>0</strong> seguidores" in cliente(app, "teen").get("/u/teen").get_data(as_text=True)
    with app.app_context():
        assert feed_query(usuario("ana").id, usuario("ana")).count() == 0  # SOC-04: nem no feed
        n, = Notificacao.query.filter_by(user_id=usuario("teen").id, tipo="seguidor").all()
        assert (n.texto, n.alvo_url) == ("pediu para seguir você", "/conta/pedidos")
        assert not pode_ver_perfil(usuario("ana"), usuario("teen"))


def test_pmn_03_aprovado_ve_o_perfil_e_conta(app):
    criar(app, "ana")
    criar(app, "teen", "adolescente")
    with app.app_context():
        db.session.add(Activity(user_id=usuario("teen").id, kind="lista"))
        db.session.commit()
    seguir(app, "ana", "teen")
    teen = cliente(app, "teen")
    fid = follow(app, "ana", "teen").id
    assert 'href="/conta/pedidos"' in teen.get("/u/teen").get_data(as_text=True)
    assert "@ana" in teen.get("/conta/pedidos").get_data(as_text=True)
    assert teen.post(f"/conta/pedidos/{fid}/aprovar").status_code == 302
    assert teen.post(f"/conta/pedidos/{fid}/aprovar").status_code == 404  # já aprovado
    assert follow(app, "ana", "teen").aprovado_em is not None
    perfil = cliente(app, "ana").get("/u/teen").get_data(as_text=True)
    assert "Seguindo" in perfil and "<strong>1</strong> seguidores" in perfil
    with app.app_context():
        ana, dono = usuario("ana"), usuario("teen")
        assert pode_ver_perfil(ana, dono)
        assert feed_query(ana.id, ana).count() == 1
        assert dono.id not in set(db.session.scalars(ocultos_para(ana)))


def test_pmn_03_quem_tem_12_a_15_nao_aprova_mas_recusa(app):
    criar(app, "ana")
    criar(app, "kid", "vinculado")
    seguir(app, "ana", "kid")
    fid = follow(app, "ana", "kid").id
    kid = cliente(app, "kid")
    assert "responsável" in kid.get("/conta/pedidos").get_data(as_text=True)
    assert kid.post(f"/conta/pedidos/{fid}/aprovar").status_code == 403
    assert cliente(app, "ana").post(f"/conta/pedidos/{fid}/recusar").status_code == 404  # não é dela
    with app.app_context():
        assert Notificacao.query.count() == 0  # o pedido vai ao responsável (RSP-06)
    assert kid.post(f"/conta/pedidos/{fid}/recusar").status_code == 302
    assert follow(app, "ana", "kid") is None


def test_pmn_03_seguir_adulto_continua_aprovado_e_conta_pendente_nao_e_seguida(app):
    criar(app, "ana")
    criar(app, "bia")
    criar(app, "esp", "pendente")
    seguir(app, "ana", "bia")
    seguir(app, "ana", "esp")
    assert follow(app, "ana", "bia").aprovado_em is not None
    assert follow(app, "ana", "esp") is None


def test_pmn_03_amigo_zerou_so_para_seguidor_aprovado(app):
    criar(app, "teen", "adolescente")
    slug = make_game(app)
    with app.app_context():
        jogo = Game.query.one().id
    for nome, aprovado in (("ana", True), ("bia", False)):
        criar(app, nome)
        with app.app_context():
            u = usuario(nome)
            db.session.add(Follow(follower_id=u.id, followed_id=usuario("teen").id,
                                  aprovado_em=now() if aprovado else db.null()))
            db.session.add(Status(user_id=u.id, game_id=jogo, value="planejado"))
            db.session.commit()
    cliente(app, "teen").post(f"/jogos/{slug}/sessoes", data={"jogada_em": "2026-11-10", "duracao": "60", "zerou": "on"})
    with app.app_context():
        assert [n.user_id for n in Notificacao.query.filter_by(tipo="amigo_zerou")] == [usuario("ana").id]


def test_pmn_03_ocultos_para_concorda_com_pode_ver_perfil(app):
    criar(app, "ana")
    for nome, perfil in (("teen", "adolescente"), ("kid", "vinculado"), ("priv", "adulto")):
        criar(app, nome, perfil)
    with app.app_context():
        usuario("priv").perfil_privado = True
        ana = usuario("ana")
        db.session.add_all([Follow(follower_id=ana.id, followed_id=usuario("teen").id),
                            Follow(follower_id=ana.id, followed_id=usuario("priv").id),
                            Follow(follower_id=ana.id, followed_id=usuario("kid").id, aprovado_em=db.null())])
        db.session.commit()
        ocultos = set(db.session.scalars(ocultos_para(ana)))
        assert {u.username for u in User.query if not pode_ver_perfil(ana, u)} == {"kid", "priv"}
        assert {u.username for u in User.query if u.id in ocultos} == {"kid", "priv"}


# ---------- PMN-04 ----------
def test_pmn_04_ranking_global_sem_menores_e_seguindo_so_para_quem_ve(app):
    from app.models import ExtratoXP

    criar(app, "ana")
    criar(app, "bia")
    criar(app, "teen", "adolescente")
    with app.app_context():
        for nome in ("ana", "teen"):
            db.session.add(ExtratoXP(user_id=usuario(nome).id, origem_tipo="ajuste", origem_id="1", xp=50,
                                     motivo="t", created_at=datetime(2026, 11, 10)))
        db.session.commit()
    assert ">teen</a>" not in app.test_client().get("/rankings?escopo=global").get_data(as_text=True)
    seguir(app, "bia", "teen")
    bia = cliente(app, "bia")
    assert ">teen</a>" not in bia.get("/rankings?escopo=seguindo").get_data(as_text=True)  # pedido pendente
    with app.app_context():
        Follow.query.filter_by(follower_id=usuario("bia").id).one().aprovado_em = now()
        db.session.commit()
    assert ">teen</a>" in bia.get("/rankings?escopo=seguindo").get_data(as_text=True)


def test_pmn_04_hall_e_premiacao_do_podio_sem_menor_contestado(app):
    from app.conquistas_catalogo import sincronizar
    from app.models import SnapshotRanking, Temporada, UsuarioConquista
    from app.rankings import premiar_podio

    criar(app, "ana")
    criar(app, "bia")
    with app.app_context():
        sincronizar()
        t = Temporada(nome="2026-T3", slug="2026-T3", inicio=date(2026, 7, 1), fim=date(2026, 9, 30),
                      encerrada_em=now())
        db.session.add(t)
        db.session.flush()
        for pos, nome in enumerate(("ana", "bia"), start=1):
            db.session.add(SnapshotRanking(temporada_id=t.id, categoria="xp", posicao=pos,
                                           user_id=usuario(nome).id, valor=50 - pos))
        usuario("bia").idade_contestada_em = now()
        db.session.commit()
        premiar_podio(t)
        assert {u.username for u in User.query.join(UsuarioConquista, UsuarioConquista.user_id == User.id)
                .filter(UsuarioConquista.desbloqueada_em.isnot(None))} == {"ana"}
    hall = app.test_client().get("/rankings/hall-da-fama").get_data(as_text=True)
    assert "/u/ana" in hall and "/u/bia" not in hall


# ---------- PMN-05 ----------
def test_pmn_05_ofertas_e_alerta_de_preco_negados_a_menores(app):
    criar(app, "ana")
    criar(app, "teen", "adolescente")
    with app.app_context():
        for acao in ("ver_ofertas", "alerta_preco"):
            assert pode(usuario("ana"), acao) and not pode(usuario("teen"), acao)


# ---------- PMN-06 ----------
def test_pmn_06_historico_de_menor_fora_de_col_e_afi(app):
    criar(app, "ana")
    criar(app, "teen", "adolescente")
    with app.app_context():
        usuario("teen").perfil_privado = False
        db.session.commit()
    slugs = [make_game(app, f"Jogo {i}") for i in range(4)]
    with app.app_context():
        jogos = {g.slug: g.id for g in Game.query}
        for nome in ("ana", "teen"):
            for s in slugs:
                db.session.add(Review(user_id=usuario(nome).id, game_id=jogos[s], score=9, text=""))
        db.session.commit()
        assert recomendacoes.vizinhos(usuario("ana").id) == []
        ana_id = usuario("ana").id
    seguir(app, "ana", "teen")
    with app.app_context():
        Follow.query.filter_by(follower_id=ana_id).one().aprovado_em = now()
        db.session.commit()
    ana = cliente(app, "ana")
    assert ana.get("/u/teen/comparar").status_code == 404
    assert "@teen" not in ana.get("/comparar").get_data(as_text=True)
    assert "Afinidade" not in ana.get("/u/teen").get_data(as_text=True)


# ---------- PMN-07 ----------
@pytest.mark.parametrize("tipo", politica.SEM_ENGAJAMENTO)
def test_pmn_07_notificacao_de_engajamento_descartada_para_menor(app, tipo):
    criar(app, "ana")
    criar(app, "teen", "adolescente")
    criar(app, "nd", "sem_data")
    with app.app_context():
        assert notificar(usuario("teen").id, tipo, texto="x") is None
        assert notificar(usuario("nd").id, tipo, texto="x") is None
        assert notificar(usuario("ana").id, tipo, texto="x") is not None
        assert notificar(usuario("teen").id, "sistema", texto="x") is not None  # os demais tipos continuam


# ---------- PMN-09 ----------
def test_pmn_09_conquista_por_tempo_nao_concedida_nem_mostrada(app):
    with app.app_context():
        sincronizar()
    criar(app, "teen", "adolescente")
    slug = make_game(app)
    teen = cliente(app, "teen")
    teen.post(f"/jogos/{slug}/sessoes", data={"jogada_em": "2026-11-10", "duracao": "7h"})
    with app.app_context():
        ganhas = {c.codigo for c in Conquista.query.join(UsuarioConquista).filter(
            UsuarioConquista.desbloqueada_em.isnot(None))}
        assert "primeira-sessao" in ganhas and "maratonista" not in ganhas
        maratonista = Conquista.query.filter_by(codigo="maratonista").one()  # ganha quando ainda era adulto
        db.session.add(UsuarioConquista(user_id=usuario("teen").id, conquista_id=maratonista.id, desbloqueada_em=now()))
        db.session.commit()
    assert "Maratonista" not in teen.get("/u/teen").get_data(as_text=True)
    assert "Maratonista" not in teen.get("/u/teen/conquistas").get_data(as_text=True)


def test_pmn_09_sem_widget_de_sequencia_para_menor(app):
    criar(app, "ana")
    criar(app, "teen", "adolescente")
    assert "sequência de" in cliente(app, "ana").get("/u/ana").get_data(as_text=True)
    teen = cliente(app, "teen")
    assert "sequência de" not in teen.get("/u/teen").get_data(as_text=True)
    assert "Sequência:" not in teen.get("/u/teen/xp").get_data(as_text=True)


# ---------- PMN-10 ----------
def test_pmn_10_so_o_menor_e_seguidor_aprovado_comentam(app):
    criar(app, "ana")
    criar(app, "bia")
    criar(app, "teen", "adolescente")
    slug = make_game(app)
    with app.app_context():
        usuario("teen").perfil_privado = False  # perfil aberto: todos veem a avaliação
        db.session.commit()
    cliente(app, "teen").post(f"/jogos/{slug}/avaliar", data={"score": 8, "text": "bom"})
    with app.app_context():
        rid = Review.query.one().id
        db.session.add(Follow(follower_id=usuario("bia").id, followed_id=usuario("teen").id))
        db.session.commit()
    ana = cliente(app, "ana")
    assert "Adicionar comentário" not in ana.get(f"/jogos/{slug}").get_data(as_text=True)
    assert ana.post(f"/avaliacoes/{rid}/comentar", data={"text": "oi"}).status_code == 403
    for nome in ("bia", "teen"):
        c = cliente(app, nome)
        assert "Adicionar comentário" in c.get(f"/jogos/{slug}").get_data(as_text=True)
        assert c.post(f"/avaliacoes/{rid}/comentar", data={"text": "oi"}).status_code == 302
    with app.app_context():
        da_bia = SimpleNamespace(user=usuario("bia"), user_id=usuario("bia").id)
        assert pode(usuario("ana"), "comentar", da_bia)  # na de um adulto, como antes
        assert {c.user.username for c in Review.query.one().comments} == {"bia", "teen"}


# ---------- PMN-11 ----------
def test_pmn_11_torneio_so_do_proprio_clube_ou_aberto_a_menores(app):
    criar(app, "ana")
    criar(app, "teen", "adolescente")
    criar(app, "esp", "pendente")
    with app.app_context():
        clube = Clube(nome="Roguelikes", nome_lower="roguelikes", slug="roguelikes")
        db.session.add(clube)
        db.session.flush()
        db.session.add(MembroClube(clube_id=clube.id, user_id=usuario("teen").id))
        db.session.add(MembroClube(clube_id=clube.id, user_id=usuario("esp").id))
        db.session.commit()
        teen, ana, esp = usuario("teen"), usuario("ana"), usuario("esp")
        do_clube = SimpleNamespace(clube_id=clube.id, aberto_a_menores=False)
        outro = SimpleNamespace(clube_id=clube.id + 1, aberto_a_menores=False)
        aberto = SimpleNamespace(clube_id=None, aberto_a_menores=True)
        assert pode(teen, "torneio_inscrever", do_clube) and pode(teen, "torneio_inscrever", aberto)
        assert not pode(teen, "torneio_inscrever", outro) and not pode(teen, "torneio_inscrever")
        assert pode(ana, "torneio_inscrever", outro)
        assert not pode(esp, "torneio_inscrever", aberto)  # conta pendente: nada social
        MembroClube.query.filter_by(user_id=teen.id).one().situacao = "removido"
        assert not pode(teen, "torneio_inscrever", do_clube)


# ---------- PMN-12 ----------
def test_pmn_12_api_v1_sem_avaliacao_de_menor_e_nota_na_media(app, client):
    slug = make_game(app)
    for nome, perfil in (("ana", "adulto"), ("teen", "adolescente")):
        criar(app, nome, perfil)
        cliente(app, nome).post(f"/jogos/{slug}/avaliar", data={"score": 10 if nome == "ana" else 6, "text": "x"})
    d = client.get(f"/api/jogos/{slug}/avaliacoes").get_json()
    assert [a["usuario"] for a in d["items"]] == ["ana"]
    assert client.get(f"/api/jogos/{slug}").get_json()["nota_media"] == 8.0


# ---------- PMN-13 ----------
def test_pmn_13_pagina_gerada_da_tabela(client, monkeypatch):
    html = client.get("/privacidade/menores").get_data(as_text=True)
    assert all(r.descricao in html for r in ACOES.values()) and "16 ou 17 anos" in html
    monkeypatch.setitem(ACOES, "nova", politica.Regra("Ação nova de teste", politica.TODAS))
    assert "Ação nova de teste" in client.get("/privacidade/menores").get_data(as_text=True)
