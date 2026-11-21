"""Fila persistente de jobs (JOB-01..12), sem rede e com relógio injetável."""
import threading
from datetime import datetime, timedelta

import pytest

from app import jobs, tempo
from app.models import AuditLog, Job, User, db


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


def test_job_06_job_enfileirado_por_tarefa_que_falha_e_desfeito(app, tarefas):
    @jobs.tarefa("enfileira_e_falha")
    def enfileira_e_falha():
        jobs.enfileirar("anota", n=1)
        raise RuntimeError("quebrou depois de enfileirar")

    with app.app_context():
        jid = jobs.enfileirar("enfileira_e_falha").id
        db.session.commit()
        jobs.executar(jid)
        assert [j.tipo for j in Job.query] == ["enfileira_e_falha"]


def test_job_01_enfileirar_nao_esconde_o_erro_de_quem_chama(app, tarefas):
    from sqlalchemy.exc import IntegrityError

    with app.app_context():
        db.session.add(User(username="w", username_lower="w", password_hash="h", display_name="w"))  # sem e-mail
        with pytest.raises(IntegrityError):
            jobs.enfileirar("anota", chave_unica="k")


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


def test_job_06_erro_no_commit_e_falha_do_job_e_o_ciclo_continua(app, tarefas):
    @jobs.tarefa("quebra_no_commit")
    def quebra_no_commit():  # sem flush: o NOT NULL do e-mail só estoura no commit
        db.session.add(User(username="y", username_lower="y", password_hash="h", display_name="y"))

    with app.app_context():
        jid = jobs.enfileirar("quebra_no_commit").id
        jobs.enfileirar("anota", n=1)
        assert jobs.ciclo() == {"executados": 2, "ok": 1, "falhas": 1, "mortos": 0}
        j = db.session.get(Job, jid)
        assert (j.status, j.tentativas, j.travado_por) == ("pendente", 1, None)
        assert j.ultimo_erro.startswith("IntegrityError") and tarefas == [{"n": 1}]


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
        db.session.commit()
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


@pytest.mark.parametrize("falha", [False, True])
def test_job_05_execucao_que_perdeu_a_trava_nao_grava_o_fim(app, tarefas, relogio, falha):
    @jobs.tarefa("lenta")
    def lenta():  # enquanto roda, passa de 15 min, é liberada e outro executor a pega
        db.session.add(User(username="z", username_lower="z", email="z@ex.com", password_hash="h", display_name="z"))
        relogio.andar(minutes=16)
        assert jobs.liberar_abandonados() == 1 and jobs._travar(jid, "b:2")
        if falha:
            raise RuntimeError("quebrou depois de perder a trava")

    with app.app_context():
        jid = jobs.enfileirar("lenta").id
        assert jobs.executar(jid, "a:1") is None
        j = db.session.get(Job, jid)
        assert (j.status, j.travado_por, j.tentativas, j.ultimo_erro) == (
            "rodando", "b:2", 1, "abandonado: rodando há mais de 15 minutos")


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
        db.session.commit()
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


# ---------- JOB-10 ----------
@pytest.fixture
def chefe(app):
    from conftest import login, make_user

    make_user(app, "chefe", role="admin")
    make_user(app, "ana")
    c = app.test_client()
    login(c, "chefe")
    return c


def test_job_10_so_admin(app, chefe):
    from conftest import login

    assert app.test_client().get("/admin/jobs").status_code == 302
    ana = app.test_client()
    login(ana, "ana")
    assert ana.get("/admin/jobs").status_code == 403
    for rota in ("/admin/jobs/1/retentar", "/admin/jobs/1/cancelar", "/admin/jobs/agendamentos/1"):
        assert ana.post(rota).status_code == 403
    assert "/admin/jobs" in chefe.get("/admin/").get_data(as_text=True)


def test_job_10_lista_filtra_e_pagina_do_mais_recente(app, chefe, tarefas, relogio):
    with app.app_context():
        for i in range(22):
            jobs.enfileirar("anota", n=i)
            relogio.andar(seconds=1)
        jobs.enfileirar("permanente")
        jobs.ciclo(limite=1)  # o mais antigo (n=0) roda
    html = chefe.get("/admin/jobs").get_data(as_text=True)
    assert html.index("<td>23</td>") < html.index("<td>22</td>") and "<td>3</td>" not in html
    assert "<td>3</td>" in chefe.get("/admin/jobs?page=2").get_data(as_text=True)
    so_ok = chefe.get("/admin/jobs?status=ok").get_data(as_text=True)
    assert "<td>1</td>" in so_ok and "<td>2</td>" not in so_ok
    assert "<td>23</td>" in chefe.get("/admin/jobs?tipo=permanente").get_data(as_text=True)
    assert "<td>22</td>" not in chefe.get("/admin/jobs?tipo=permanente").get_data(as_text=True)


def test_job_10_acoes_com_auditoria(app, chefe, tarefas):
    from app.models import Agendamento

    agendamento(app)
    with app.app_context():
        morto = jobs.enfileirar("permanente").id
        jobs.ciclo()
        pendente = jobs.enfileirar("anota").id
        db.session.commit()
    r = chefe.post(f"/admin/jobs/{pendente}/retentar", follow_redirects=True)
    assert "Só um job morto ou que falhou" in r.get_data(as_text=True)
    chefe.post(f"/admin/jobs/{morto}/retentar")
    chefe.post(f"/admin/jobs/{pendente}/cancelar")
    assert "Só um job pendente" in chefe.post(f"/admin/jobs/{pendente}/cancelar", follow_redirects=True).get_data(as_text=True)
    with app.app_context():
        aid = Agendamento.query.one().id
    chefe.post(f"/admin/jobs/agendamentos/{aid}")
    with app.app_context():
        j = db.session.get(Job, morto)
        assert (j.status, j.tentativas) == ("pendente", 0)
        c = db.session.get(Job, pendente)
        assert c.status == "falhou" and c.ultimo_erro == "cancelado por @chefe"
        assert Agendamento.query.one().ativo is False
        assert [a.action for a in AuditLog.query.order_by(AuditLog.id)] == ["jobs.retentar", "jobs.cancelar",
                                                                            "jobs.agendamento"]
    assert chefe.post("/admin/jobs/999/retentar").status_code == 404
    assert "Ligar" in chefe.get("/admin/jobs").get_data(as_text=True)


def test_job_10_retentar_com_a_chave_ja_aberta_em_outro_job(app, chefe, tarefas):
    with app.app_context():
        morto = jobs.enfileirar("permanente", chave_unica="k").id
        jobs.ciclo()
        jobs.enfileirar("anota", chave_unica="k")
        db.session.commit()
    r = chefe.post(f"/admin/jobs/{morto}/retentar", follow_redirects=True)
    assert r.status_code == 200 and "Já existe um job aberto com a chave k" in r.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(Job, morto).status == "morto"


def test_job_10_cancelar_nao_passa_por_cima_de_quem_ja_travou(app, chefe, tarefas, monkeypatch):
    with app.app_context():
        jid = jobs.enfileirar("anota").id
        db.session.commit()
    original = jobs.db.get_or_404

    def executor():
        with app.app_context():  # outra sessão e outra conexão, como um executor de verdade
            jobs._travar(jid, "b:2")
            db.session.remove()

    def le_e_um_executor_trava(*args, **kwargs):  # o executor trava logo depois de a rota ler o job
        job = original(*args, **kwargs)
        t = threading.Thread(target=executor)
        t.start()
        t.join()
        return job
    monkeypatch.setattr(jobs.db, "get_or_404", le_e_um_executor_trava)
    r = chefe.post(f"/admin/jobs/{jid}/cancelar", follow_redirects=True)
    assert "Só um job pendente pode ser cancelado." in r.get_data(as_text=True)
    with app.app_context():
        j = db.session.get(Job, jid)
        assert (j.status, j.travado_por) == ("rodando", "b:2")
        assert AuditLog.query.count() == 0


# ---------- JOB-11 ----------
def test_job_11_criar_o_app_nao_inicia_agendador_nem_thread(tmp_path):
    import sys

    from app import create_app

    antes = threading.active_count()
    create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path}/x.db"})
    assert threading.active_count() == antes
    assert not {"apscheduler", "sched", "schedule", "celery", "rq"} & set(sys.modules)


def test_job_11_rota_ciclo_com_token(tmp_path, tarefas):
    from app import create_app

    app = create_app({"TESTING": True, "CSRF": True, "JOBS_TOKEN": "s3gredo",
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path}/c.db"})
    with app.app_context():
        for i in range(12):
            jobs.enfileirar("anota", n=i)
        db.session.commit()
    c = app.test_client()
    assert c.post("/jobs/ciclo").status_code == 401
    assert c.post("/jobs/ciclo", headers={"Authorization": "Bearer errado"}).status_code == 401
    r = c.post("/jobs/ciclo", headers={"Authorization": "Bearer s3gredo"})  # sem cookie nem csrf_token
    assert r.status_code == 200 and r.get_json() == {"executados": 10, "ok": 10, "falhas": 0, "mortos": 0}
    assert c.get("/jobs/ciclo").status_code == 405


def test_job_11_rota_ciclo_sem_token_configurado_e_404(app, monkeypatch):
    monkeypatch.delenv("JOBS_TOKEN", raising=False)
    assert app.test_client().post("/jobs/ciclo", headers={"Authorization": "Bearer x"}).status_code == 404
