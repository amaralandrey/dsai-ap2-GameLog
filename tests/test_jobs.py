"""Fila persistente de jobs (JOB-01..12), sem rede e com relógio injetável."""
import threading
from datetime import datetime, timedelta

import pytest

from app import jobs, tempo
from app.models import Job, User, db


class Relogio:
    def __init__(self):
        self.momento = datetime(2026, 11, 16, 12, 0, tzinfo=tempo.FUSO)

    def andar(self, **delta):
        self.momento += timedelta(**delta)


@pytest.fixture(autouse=True)
def relogio(monkeypatch):
    r = Relogio()
    monkeypatch.setattr(tempo, "agora", lambda: r.momento)
    return r


@pytest.fixture
def tarefas(monkeypatch):
    """Tarefas de teste num registro isolado; `chamadas` guarda os args de cada execução."""
    monkeypatch.setattr(jobs, "TAREFAS", dict(jobs.TAREFAS))
    chamadas = []

    @jobs.tarefa("anota")
    def anota(**args):
        chamadas.append(args)

    @jobs.tarefa("grava_e_falha")
    def grava_e_falha():
        db.session.add(User(username="x", username_lower="x", email="x@ex.com", password_hash="h", display_name="x"))
        db.session.flush()
        raise RuntimeError("quebrou")

    @jobs.tarefa("permanente")
    def permanente():
        raise jobs.ErroPermanente("sem chave da RAWG")

    return chamadas


def job(app, jid):
    with app.app_context():
        return db.session.get(Job, jid)


# ---------- JOB-01..03 ----------
def test_job_01_enfileirar_valida_tipo_e_args(app, tarefas):
    with app.app_context():
        j = jobs.enfileirar("anota", n=1)
        assert (j.status, j.tentativas, j.max_tentativas, j.args) == ("pendente", 0, 5, '{"n": 1}')
        assert j.executar_em == tempo.agora_utc() and j.criado_em == tempo.agora_utc()
        with pytest.raises(ValueError, match="desconhecida"):
            jobs.enfileirar("nao_existe")
        with pytest.raises(ValueError, match="JSON"):
            jobs.enfileirar("anota", quando=datetime(2026, 1, 1))
        assert Job.query.count() == 1


def test_job_02_chave_unica_aberta_nao_duplica(app, tarefas):
    with app.app_context():
        a = jobs.enfileirar("anota", chave_unica="email_resumo:1:2026-W47")
        b = jobs.enfileirar("anota", chave_unica="email_resumo:1:2026-W47")
        assert a.id == b.id and Job.query.count() == 1
        jobs.ciclo()
        c = jobs.enfileirar("anota", chave_unica="email_resumo:1:2026-W47")  # o anterior já terminou
        assert c.id != a.id and Job.query.count() == 2


def test_job_03_registrar_duas_vezes_levanta(tarefas):
    with pytest.raises(RuntimeError, match="duas vezes"):
        jobs.tarefa("anota")(lambda: None)


# ---------- JOB-04..06 ----------
def test_job_04_ciclo_executa_vencidos_do_mais_antigo_ao_mais_novo(app, tarefas, relogio):
    with app.app_context():
        jobs.enfileirar("anota", n=2, executar_em=tempo.agora_utc() - timedelta(minutes=1))
        jobs.enfileirar("anota", n=1, executar_em=tempo.agora_utc() - timedelta(minutes=5))
        jobs.enfileirar("anota", n=3, executar_em=tempo.agora_utc() + timedelta(minutes=5))
        assert jobs.ciclo() == {"executados": 2, "ok": 2, "falhas": 0, "mortos": 0}
        assert tarefas == [{"n": 1}, {"n": 2}]
        relogio.andar(minutes=5)
        assert jobs.ciclo(limite=1)["ok"] == 1 and tarefas[-1] == {"n": 3}
        assert {j.status for j in Job.query} == {"ok"} and all(j.concluido_em for j in Job.query)


def test_job_06_falha_desfaz_e_espera_cresce_ate_morrer(app, tarefas, relogio):
    with app.app_context():
        jid = jobs.enfileirar("grava_e_falha").id
        esperas = []
        for _ in range(5):
            antes = tempo.agora_utc()
            conta = jobs.ciclo()
            j = db.session.get(Job, jid)
            if j.status == "pendente":
                esperas.append(j.executar_em - antes)
                relogio.andar(hours=7)
        assert conta == {"executados": 1, "ok": 0, "falhas": 0, "mortos": 1}
        assert esperas == [timedelta(seconds=30), timedelta(seconds=60), timedelta(seconds=120),
                           timedelta(seconds=240)]
        assert (j.status, j.tentativas, j.ultimo_erro) == ("morto", 5, "RuntimeError: quebrou")
        assert User.query.count() == 0  # o que a tarefa gravou foi desfeito


def test_job_06_espera_tem_teto_de_6_horas():
    assert jobs.espera(1) == timedelta(seconds=30) and jobs.espera(10) == timedelta(hours=4, minutes=16)
    assert jobs.espera(11) == timedelta(hours=6) and jobs.espera(30) == timedelta(hours=6)


def test_job_06_erro_permanente_morre_sem_novas_tentativas(app, tarefas):
    with app.app_context():
        jid = jobs.enfileirar("permanente").id
        assert jobs.ciclo()["mortos"] == 1
        j = db.session.get(Job, jid)
        assert (j.status, j.tentativas, j.ultimo_erro) == ("morto", 1, "ErroPermanente: sem chave da RAWG")


def test_job_06_tarefa_que_sumiu_do_codigo_morre(app, tarefas, monkeypatch):
    with app.app_context():
        jid = jobs.enfileirar("anota").id
        monkeypatch.delitem(jobs.TAREFAS, "anota")
        jobs.ciclo()
        assert db.session.get(Job, jid).status == "morto"


def test_job_05_dois_executores_nunca_rodam_o_mesmo_job(app, tarefas):
    with app.app_context():
        ids = [jobs.enfileirar("anota", n=i).id for i in range(10)]
    barreira, travou = threading.Barrier(2), {}

    def executor(nome):
        with app.app_context():  # cada thread tem a própria sessão e a própria conexão
            barreira.wait()
            travou[nome] = [i for i in ids if jobs._travar(i, nome)]
            db.session.remove()
    threads = [threading.Thread(target=executor, args=(n,)) for n in ("a:1", "b:2")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(travou["a:1"] + travou["b:2"]) == ids  # cada job travado por exatamente um


def test_job_05_abandonado_volta_para_pendente_contando_tentativa(app, tarefas, relogio):
    with app.app_context():
        jid = jobs.enfileirar("anota").id
        assert jobs._travar(jid, "outro:1")
        relogio.andar(minutes=15)
        assert jobs.liberar_abandonados() == 0
        relogio.andar(seconds=1)
        assert jobs.ciclo()["ok"] == 1
        j = db.session.get(Job, jid)
        assert (j.status, j.tentativas, j.travado_por) == ("ok", 1, None)
