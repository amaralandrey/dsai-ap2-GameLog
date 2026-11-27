import json
from datetime import date
from types import SimpleNamespace

import pytest

from app.fontes import FONTES, arquivo, importar, normalizar_url_loja
from app.models import Game, JogoFonte, db
from app.sync import sincronizar
from conftest import http
from test_conectores_rawg import CHAVE, EPIC, GOG, PLAYSTATION, STEAM, chave, lojas  # noqa: F401 (chave é fixture)

HADES = {"external_id": "1145360", "title": "Hades", "synopsis": "s", "release_date": "2020-09-17",
         "developer": "Supergiant Games", "genres": ["Ação"], "platforms": ["Windows"], "cover_url": None}
CELESTE = {**HADES, "external_id": "504230", "title": "Celeste", "developer": "Maddy Makes Games"}
OUTRO = {**HADES, "external_id": "999", "title": "Outro", "developer": "Outra"}


def steam(app_id):
    return (1, f"https://store.steampowered.com/app/{app_id}/")


def busca(*ids):
    return {"count": len(ids), "next": None, "results": [{"id": i, "name": f"R{i}"} for i in ids]}


@pytest.fixture
def snapshot(tmp_path):
    caminho = tmp_path / "jogos.json"
    caminho.write_text(json.dumps([HADES, CELESTE, OUTRO], ensure_ascii=False, indent=1), encoding="utf-8")
    return caminho


@pytest.fixture
def rawg_falsa(api, chave):  # noqa: F811
    """Hades bate no 1º resultado, Celeste no 3º; Outro só no 4º, que não é conferido."""
    api.rotas["search=Hades&"] = busca(1)
    api.rotas["search=Celeste&"] = busca(20, 21, 2)
    api.rotas["search=Outro&"] = busca(30, 31, 32, 3)
    api.rotas["api.rawg.io/api/games/1/stores"] = lojas(1, STEAM, GOG, EPIC, PLAYSTATION)
    api.rotas["api.rawg.io/api/games/2/stores"] = lojas(2, steam("504230"), (11, "https://store.epicgames.com/p/celeste"))
    for i, app_id in ((20, "1"), (21, "2"), (30, "3"), (31, "4"), (32, "5")):
        api.rotas[f"api.rawg.io/api/games/{i}/stores"] = lojas(i, steam(app_id))
    api.rotas["api.rawg.io/api/games/3/stores"] = lojas(3, steam("999"), GOG)
    return api


def enriquecer(app, caminho):
    return app.test_cli_runner().invoke(args=["enriquecer-snapshot", "--arquivo", str(caminho)])


def ler(caminho):
    return json.loads(caminho.read_text(encoding="utf-8"))


# ---------- SNP-01..03 ----------
def test_snp01_aceita_so_o_resultado_com_o_mesmo_id_da_steam(app, snapshot, rawg_falsa):
    r = enriquecer(app, snapshot)
    assert r.exit_code == 0 and r.output == "jogos=3 vinculados=2 sem_correspondencia=1 falhas=0\n"
    assert not any("/games/3/stores" in u for u in rawg_falsa.urls())  # só os 3 primeiros resultados
    assert all("page_size=3" in u for u in rawg_falsa.urls() if "search=" in u)


def test_snp02_grava_lojas_gog_e_epic_e_o_id_da_rawg_sem_mexer_no_resto(app, snapshot, rawg_falsa):
    enriquecer(app, snapshot)
    hades, celeste, outro = ler(snapshot)
    assert hades == {**HADES, "lojas": [["gog", "https://www.gog.com/game/hades"], ["epic", "https://store.epicgames.com/p/hades"]],
                     "origens": [["rawg", "1"]], "classificacao": None}  # Steam e PlayStation ficam de fora; Epic no formato de FNT-07
    assert list(hades)[:8] == list(HADES)
    assert celeste["lojas"] == [["epic", "https://store.epicgames.com/p/celeste"]] and celeste["origens"] == [["rawg", "2"]]
    assert outro == {**OUTRO, "classificacao": None}  # sem correspondência: só ganha o campo da classificação (CLS-05)


def test_snp02_link_fora_de_fnt07_nao_e_gravado(app, snapshot, rawg_falsa):
    rawg_falsa.rotas["api.rawg.io/api/games/1/stores"] = lojas(1, STEAM, (5, "http://www.gog.com/game/hades"))
    enriquecer(app, snapshot)
    assert ler(snapshot)[0]["lojas"] == []


def test_snp03_idempotente_e_sem_a_chave(app, snapshot, rawg_falsa):
    r1 = enriquecer(app, snapshot)
    primeira = snapshot.read_text(encoding="utf-8")
    r2 = enriquecer(app, snapshot)
    assert snapshot.read_text(encoding="utf-8") == primeira and r1.output == r2.output
    assert CHAVE not in primeira and CHAVE not in r1.output


# ---------- SNP-04, SNP-05 ----------
def test_snp04_sem_chave_avisa_e_nao_altera(app, snapshot, api):
    antes = snapshot.read_bytes()
    r = enriquecer(app, snapshot)
    assert r.exit_code == 1 and r.output == "erro=RAWG_API_KEY não configurada\n"
    assert snapshot.read_bytes() == antes and api.pedidos == []


def test_snp05_falha_num_jogo_conta_e_nao_muda_o_jogo(app, snapshot, rawg_falsa):
    rawg_falsa.rotas["search=Celeste&"] = http(500)
    r = enriquecer(app, snapshot)
    assert r.output == "jogos=3 vinculados=1 sem_correspondencia=1 falhas=1\n"
    assert ler(snapshot)[1] == CELESTE and "lojas" in ler(snapshot)[0]


def test_snp05_429_persistente_para_e_grava_o_que_ja_foi_feito(app, snapshot, rawg_falsa):
    rawg_falsa.rotas["search=Celeste&"] = http(429)
    r = enriquecer(app, snapshot)
    assert r.exit_code == 0 and r.output == "jogos=3 vinculados=1 sem_correspondencia=0 falhas=0 interrompido=rate-limit\n"
    hades, celeste, outro = ler(snapshot)
    assert hades["origens"] == [["rawg", "1"]] and (celeste, outro) == (CELESTE, OUTRO)
    assert not any("search=Outro" in u for u in rawg_falsa.urls())


def test_snp05_falha_ao_gravar_nao_corrompe_o_snapshot(app, snapshot, rawg_falsa, monkeypatch):
    from app import snapshot as modulo

    def dump_quebrado(dados, f, **kw):
        f.write("[{")
        raise OSError("disco cheio")

    antes = snapshot.read_bytes()
    monkeypatch.setattr(modulo.json, "dump", dump_quebrado)
    with pytest.raises(OSError):
        modulo.enriquecer(str(snapshot))
    assert snapshot.read_bytes() == antes and not list(snapshot.parent.glob("jogos.json.*"))


# ---------- SNP-06, SNP-07 ----------
def vinculos(game):
    return sorted(v.fonte for v in game.fontes)


def test_snp06_seed_cria_os_vinculos_do_snapshot_sem_rede_nem_chave(app, snapshot, rawg_falsa, monkeypatch):
    enriquecer(app, snapshot)
    monkeypatch.delenv("RAWG_API_KEY")
    rawg_falsa.rotas.clear()  # qualquer acesso à rede agora quebraria
    monkeypatch.setattr(arquivo, "CAMINHO", snapshot)
    with app.app_context():
        assert importar("arquivo", limite=10) == (3, 0, 0)
        jogos = {g.title: g for g in Game.query}
        assert vinculos(jogos["Hades"]) == ["arquivo", "epic", "gog", "rawg", "steam"]
        assert vinculos(jogos["Outro"]) == ["arquivo", "steam"]  # SNP-07: sem lojas nem origens, como antes
    html = app.test_client().get("/jogos/hades").get_data(as_text=True)
    assert all(f">{n}</a>" in html for n in ("Steam", "GOG", "Epic Games Store")) and "Dados fornecidos por" in html


def test_snp06_origens_valem_tambem_na_sincronizacao(app):
    item = {**HADES, "release_date": date(2020, 9, 17), "origens": [["rawg", "7"], ["desconhecida", "1"]]}
    with app.app_context():
        assert sincronizar("steam", modulo=SimpleNamespace(listar=lambda limite, termo=None: [item])) == (1, 0, 0, 0)
        assert vinculos(Game.query.one()) == ["rawg", "steam"]  # fonte fora de FONTES é ignorada


def test_snp07_origem_com_id_de_outro_jogo_nao_vira_vinculo(app, monkeypatch, tmp_path):
    caminho = tmp_path / "jogos.json"
    caminho.write_text(json.dumps([{**HADES, "origens": [["rawg", "1"]]}, {**OUTRO, "origens": [["rawg", "1"]]}]), encoding="utf-8")
    monkeypatch.setattr(arquivo, "CAMINHO", caminho)
    with app.app_context():
        importar("arquivo", limite=10)
        assert [(v.game.title, v.external_id) for v in JogoFonte.query.filter_by(fonte="rawg")] == [("Hades", "1")]
        assert vinculos(Game.query.filter_by(title="Outro").one()) == ["arquivo", "steam"]


def test_snp07_sincronizacao_nao_toca_no_vinculo_do_outro_jogo(app):
    hades = {**HADES, "release_date": date(2020, 9, 17), "origens": [["rawg", "1"]]}
    outro = {**OUTRO, "release_date": date(2001, 1, 1), "origens": [["rawg", "1"]]}
    with app.app_context():
        sincronizar("steam", modulo=SimpleNamespace(listar=lambda limite, termo=None: [hades]))
        v = JogoFonte.query.filter_by(fonte="rawg").one()
        v.ultima_sincronizacao = None
        db.session.commit()
        sincronizar("steam", modulo=SimpleNamespace(listar=lambda limite, termo=None: [outro]))
        assert JogoFonte.query.filter_by(fonte="rawg").one().ultima_sincronizacao is None


# ---------- SNP-08: o snapshot versionado ----------
def test_snp08_snapshot_versionado_tem_links_validos_e_o_seed_mantem_os_jogos(app):
    jogos = json.loads(arquivo.CAMINHO.read_text(encoding="utf-8"))
    enriquecidos = [j for j in jogos if "origens" in j]
    assert len(enriquecidos) >= 100
    for j in enriquecidos:
        assert [f for f, _ in j["origens"]] == ["rawg"] and j["origens"][0][1].isdigit()
        assert all(loja in ("gog", "epic") and normalizar_url_loja(loja, url) == url for loja, url in j["lojas"])
    with app.app_context():
        importar("arquivo", limite=10**6)
        assert Game.query.count() == len(jogos)  # nenhum jogo do snapshot vira vínculo de outro (MRG-02)
        com_loja = {(v.fonte, v.game.external_id) for v in JogoFonte.query.filter(JogoFonte.fonte.in_(["gog", "epic"]))}
        assert com_loja == {(loja, j["external_id"]) for j in enriquecidos for loja, _ in j["lojas"]}
        assert JogoFonte.query.filter_by(fonte="rawg").count() == len(enriquecidos)
    assert "arquivo" in FONTES
