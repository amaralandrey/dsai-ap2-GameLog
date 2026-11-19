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


# ---------- JOB-07, JOB-08 ----------
@pytest.mark.parametrize("expressao", ["* * * * *", "*/15 4 1,15 1-6 1-5", "0 0 29 2 *", "30 4 * * 7",
                                       "1-30/5 * * * 0"])
def test_job_07_cron_valido(expressao):
    from app.cron import validar

    assert validar(expressao) is None


@pytest.mark.parametrize("expressao,trecho", [
    ("* * * *", "5 campos"), ("60 * * * *", "minuto: 60 fora de 0 a 59"), ("* 24 * * *", "hora: 24"),
    ("* * 0 * *", "dia do mês: 0"), ("* * * 13 *", "mês: 13"), ("* * * * 8", "dia da semana: 8"),
    ("*/0 * * * *", "minuto: 0 fora de 1"), ("5-1 * * * *", "invertido"), ("a * * * *", "não é um número"),
    ("0 0 31 2 *", "nunca acontece"), ("", "5 campos"),
])
def test_job_07_cron_invalido_com_mensagem_do_campo(expressao, trecho):
    from app.cron import validar

    assert trecho in validar(expressao)


@pytest.mark.parametrize("expressao,depois_de,esperado", [
    ("*/15 * * * *", (2026, 11, 16, 10, 59, 30), (2026, 11, 16, 11, 0)),  # virada de hora
    ("0 4 * * *", (2026, 11, 16, 4, 0), (2026, 11, 17, 4, 0)),  # estritamente depois
    ("30 23 * * *", (2026, 11, 30, 23, 45), (2026, 12, 1, 23, 30)),  # virada de dia e de mês
    ("0 0 1 1 *", (2026, 12, 31, 23, 59), (2027, 1, 1, 0, 0)),  # virada de ano
    ("0 0 29 2 *", (2026, 3, 1, 0, 0), (2028, 2, 29, 0, 0)),  # só em ano bissexto
    ("0 9 * * 1", (2026, 11, 16, 9, 0), (2026, 11, 23, 9, 0)),  # segunda-feira
    ("0 9 13 * 5", (2026, 11, 1, 0, 0), (2026, 11, 6, 9, 0)),  # dia 13 OU sexta, como no cron do Unix
])
def test_job_07_proxima_execucao_nas_viradas(expressao, depois_de, esperado):
    from app.cron import proxima_execucao

    assert proxima_execucao(expressao, datetime(*depois_de)) == datetime(*esperado)
    com_fuso = proxima_execucao(expressao, datetime(*depois_de, tzinfo=tempo.FUSO))
    assert com_fuso == datetime(*esperado, tzinfo=tempo.FUSO)


def test_job_07_agendamento_com_cron_invalido_nao_grava(app):
    from app.cron import CronInvalido
    from app.models import Agendamento

    with app.app_context(), pytest.raises(CronInvalido, match="cron: hora: 25"):
        Agendamento(nome="x", cron="0 25 * * *", tipo="anota")


def agendamento(app, cron="0 4 * * *", ativo=True, tipo="anota"):
    from app.models import Agendamento

    with app.app_context():
        db.session.add(Agendamento(nome="diario", cron=cron, tipo=tipo, args='{"n": 7}', ativo=ativo))
        db.session.commit()


def test_job_08_agendamento_vencido_vira_um_job_sem_duplicar(app, tarefas, relogio):
    agendamento(app)
    with app.app_context():
        assert jobs.enfileirar_agendados() == 0  # recém-criado: conta a partir de agora (16/11 12:00)
        relogio.andar(hours=16)  # 17/11 04:00 em Belém
        assert jobs.enfileirar_agendados() == 1 and jobs.enfileirar_agendados() == 0
        j = Job.query.one()
        assert j.chave_unica == "agenda:diario:2026-11-17T04:00:00-03:00" and j.args == '{"n": 7}'
        relogio.andar(days=3)  # três execuções perdidas viram uma só
        assert jobs.ciclo()["executados"] == 2 and Job.query.count() == 2
        assert tarefas == [{"n": 7}, {"n": 7}]


def test_job_08_agendamento_desligado_nao_enfileira(app, tarefas, relogio):
    agendamento(app, ativo=False)
    with app.app_context():
        relogio.andar(days=2)
        jobs.ciclo()
        assert Job.query.count() == 0


def test_job_08_dois_executores_marcam_o_agendamento_uma_vez(app, tarefas, relogio):
    from app.models import Agendamento

    agendamento(app)
    with app.app_context():
        jobs.enfileirar_agendados()
        antes = Agendamento.query.one().ultima_execucao
        relogio.andar(days=1)
        ag = Agendamento.query.one()
        assert jobs._marcar(ag, antes) and not jobs._marcar(ag, antes)


# ---------- JOB-04 (comando), JOB-09 ----------
def test_job_04_rodar_uma_vez_imprime_contagem(app, tarefas):
    with app.app_context():
        jobs.enfileirar("anota")
        jobs.enfileirar("permanente")
    r = app.test_cli_runner().invoke(args=["jobs", "rodar", "--uma-vez", "--limite", "5"])
    assert r.exit_code == 0 and r.output.strip() == "executados=2 ok=1 falhas=0 mortos=1"


def test_job_04_rodar_em_laco_para_no_sinal_depois_do_ciclo(app, tarefas, monkeypatch):
    import signal

    tratadores, ciclos = {}, []
    monkeypatch.setattr(signal, "signal", lambda s, f: tratadores.__setitem__(s, f))

    def ciclo(limite):
        ciclos.append(limite)
        if len(ciclos) == 2:
            tratadores[signal.SIGTERM](signal.SIGTERM, None)  # chega no meio do ciclo: ele termina antes
        return {"executados": 0, "ok": 0, "falhas": 0, "mortos": 0}
    monkeypatch.setattr(jobs, "ciclo", ciclo)
    r = app.test_cli_runner().invoke(args=["jobs", "rodar", "--intervalo", "0"])
    assert r.exit_code == 0 and ciclos == [50, 50] and set(tratadores) == {signal.SIGINT, signal.SIGTERM}


def test_job_09_agendar_e_idempotente_e_respeita_o_admin(app):
    from app.models import Agendamento

    r = app.test_cli_runner().invoke(args=["jobs", "agendar"])
    assert r.output.strip() == f"criados={len(jobs.AGENDAS_PADRAO)} atualizados=0"
    with app.app_context():
        ags = {a.nome: a for a in Agendamento.query}
        assert (ags["sync_games"].cron, ags["sync_games"].ativo) == ("0 4 * * *", True)
        assert ags["enriquecer_snapshot"].ativo is False
        ags["sync_games"].ativo, ags["sync_games"].cron = False, "0 3 * * *"
        db.session.commit()
    assert app.test_cli_runner().invoke(args=["jobs", "agendar"]).output.strip() == "criados=0 atualizados=1"
    assert app.test_cli_runner().invoke(args=["jobs", "agendar"]).output.strip() == "criados=0 atualizados=0"
    with app.app_context():
        a = Agendamento.query.filter_by(nome="sync_games").one()
        assert (a.cron, a.ativo) == ("0 4 * * *", False)


def test_job_09_sync_games_reaproveita_a_funcao_do_comando(app, monkeypatch):
    from app import sync

    chamadas = []
    monkeypatch.setattr(sync, "sincronizar", lambda fonte, limite: chamadas.append((fonte, limite)))
    with app.app_context():
        jobs.enfileirar("sync_games", limite=10)
        assert jobs.ciclo()["ok"] == 1 and chamadas == [("steam", 10)]


def test_job_09_tarefa_sem_chave_da_rawg_morre_com_mensagem_clara(app, monkeypatch):
    monkeypatch.delenv("RAWG_API_KEY", raising=False)
    with app.app_context():
        jid = jobs.enfileirar("enriquecer_snapshot").id
        jobs.ciclo()
        j = db.session.get(Job, jid)
        assert j.status == "morto" and "RAWG_API_KEY não configurada" in j.ultimo_erro
