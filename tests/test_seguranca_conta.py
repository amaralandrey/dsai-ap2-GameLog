"""Segurança da conta (CTA-01..12), sem rede e com o relógio fixo."""
import base64
import re
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app import conta, qr, tempo
from app.models import CodigoRecuperacao, EmailSaida, EventoSeguranca, LoginAttempt, Notificacao, User, db

from conftest import login, make_user

CODIGO_RE = re.compile(r"<code>([A-Z2-7]{4}(?:-[A-Z2-7]{4}){3})</code>")


@pytest.fixture(autouse=True)
def relogio(monkeypatch):
    r = SimpleNamespace(momento=datetime(2026, 11, 16, 12, 0, 5, tzinfo=tempo.FUSO))
    monkeypatch.setattr(tempo, "agora", lambda: r.momento)
    r.avancar = lambda **kw: setattr(r, "momento", r.momento + timedelta(**kw))
    return r


def usuario(app, client=None, nome="ana"):
    uid = make_user(app, nome)
    if client is not None:
        login(client, nome)
    return uid


def ler(app, uid):
    with app.app_context():
        return db.session.get(User, uid)


def codigo_agora(app, uid, desvio=0):
    return conta.codigo_totp(ler(app, uid).totp_segredo, conta.passo_atual() + desvio)


def ligar_2fa(app, client, uid, relogio):
    """Liga o 2FA pela tela e devolve os códigos de recuperação; o relógio anda um passo depois."""
    client.post("/conta/seguranca/2fa/ativar")
    r = client.post("/conta/seguranca/2fa/confirmar", data={"codigo": codigo_agora(app, uid)})
    relogio.avancar(seconds=30)
    return CODIGO_RE.findall(r.get_data(as_text=True))


def senha(client, atual="senha1234", nova="novasenha99", confirmacao=None):
    return client.post("/conta/seguranca/senha", data={"atual": atual, "nova": nova,
                                                       "confirmacao": confirmacao or nova})


def eventos(app, uid):
    with app.app_context():
        return [e.tipo for e in EventoSeguranca.query.filter_by(user_id=uid).order_by(EventoSeguranca.id)]


def tentativas(app, email="ana@ex.com"):
    with app.app_context():
        return LoginAttempt.query.filter_by(email=email).count()


# ---------- CTA-01 ----------
def test_cta01_exige_login_e_mostra_os_quatro_blocos(app, client):
    r = client.get("/conta/seguranca")
    assert r.status_code == 302 and "/entrar" in r.location
    usuario(app, client)
    usuario(app, nome="bia")
    html = client.get("/conta/seguranca").get_data(as_text=True)
    for bloco in ("Senha", "E-mail", "Sessões", "Dois fatores"):
        assert f"<h2>{bloco}</h2>" in html
    assert "ana@ex.com" in html and "bia@ex.com" not in html


# ---------- CTA-02 ----------
def test_cta02_troca_de_senha_derruba_a_outra_sessao(app, client):
    uid = usuario(app, client)
    outro = app.test_client()
    login(outro)
    r = senha(client)
    assert r.status_code == 302
    assert client.get("/conta/seguranca").status_code == 200  # a sessão atual foi recriada
    assert outro.get("/conta/seguranca").status_code == 302  # a outra caiu
    assert ler(app, uid).session_version == 1
    assert login(app.test_client(), password="novasenha99").status_code == 302


def test_cta02_senha_atual_errada_responde_400_e_conta_tentativa(app, client):
    uid = usuario(app, client)
    r = senha(client, atual="errada123")
    assert r.status_code == 400 and "Senha atual incorreta" in r.get_data(as_text=True)
    assert tentativas(app) == 1 and ler(app, uid).session_version == 0


def test_cta02_senha_errada_repetida_cai_no_bloqueio_do_login(app, client):
    usuario(app, client)
    for _ in range(5):
        senha(client, atual="errada123")
    assert senha(client).status_code == 429
    assert login(app.test_client()).status_code == 429  # CAD-04


@pytest.mark.parametrize("nova, confirmacao", [("curta12", "curta12"), ("novasenha99", "outrasenha99"),
                                               ("senha1234", "senha1234")])
def test_cta02_regras_da_nova_senha(app, client, nova, confirmacao):
    uid = usuario(app, client)
    assert senha(client, nova=nova, confirmacao=confirmacao).status_code == 400
    assert ler(app, uid).session_version == 0 and eventos(app, uid) == []


# ---------- CTA-03, CTA-04 ----------
def pedir_troca(client, email="nova@ex.com", senha_atual="senha1234"):
    return client.post("/conta/seguranca/email", data={"email": email, "senha": senha_atual})


def link_de_confirmacao(app):
    with app.app_context():
        e = EmailSaida.query.filter_by(modelo="confirmar_email").order_by(EmailSaida.id.desc()).first()
        return e.para, re.search(r"/conta/email/confirmar/\S+", e.corpo_texto).group()


def test_cta03_link_valido_troca_o_email_e_o_antigo_vale_ate_la(app, client):
    uid = usuario(app, client)
    assert pedir_troca(client).status_code == 302
    para, link = link_de_confirmacao(app)
    assert para == "nova@ex.com" and ler(app, uid).email == "ana@ex.com"
    assert ler(app, uid).email_pendente == "nova@ex.com"
    assert login(app.test_client()).status_code == 302  # o endereço antigo continua valendo
    r = app.test_client().get(link)  # abre em outro navegador, sem login
    assert r.status_code == 200 and "nova@ex.com" in r.get_data(as_text=True)
    u = ler(app, uid)
    assert (u.email, u.email_pendente) == ("nova@ex.com", None)
    assert "email" in eventos(app, uid)


def test_cta03_pedido_exige_senha_e_email_valido(app, client):
    uid = usuario(app, client)
    usuario(app, nome="bia")
    assert pedir_troca(client, senha_atual="errada123").status_code == 400
    assert pedir_troca(client, email="sem-arroba").status_code == 400
    assert pedir_troca(client, email="bia@ex.com").status_code == 400
    with app.app_context():
        assert EmailSaida.query.count() == 0 and db.session.get(User, uid).email_pendente is None


def test_cta04_link_vencido_reutilizado_ou_adulterado_responde_400(app, client, relogio):
    uid = usuario(app, client)
    pedir_troca(client)
    _, link = link_de_confirmacao(app)
    assert client.get(link[:-2] + ("AA" if not link.endswith("AA") else "BB")).status_code == 400
    relogio.avancar(hours=24, seconds=1)
    assert client.get(link).status_code == 400
    assert ler(app, uid).email == "ana@ex.com"
    relogio.avancar(hours=-24, seconds=-1)
    assert client.get(link).status_code == 200
    assert client.get(link).status_code == 400  # serve uma vez só


def test_cta04_aviso_ao_email_antigo(app, client):
    usuario(app, client)
    pedir_troca(client)
    client.get(link_de_confirmacao(app)[1])
    with app.app_context():
        aviso = EmailSaida.query.filter_by(modelo="email_alterado").one()
        assert aviso.para == "ana@ex.com"
        assert "16/11/2026 às 12:00" in aviso.corpo_texto and "/privacidade" in aviso.corpo_texto
        assert "nova@ex.com" not in aviso.corpo_texto  # o novo vai mascarado


def test_cta04_email_tomado_no_meio_tempo_recusa_a_troca(app, client):
    uid = usuario(app, client)
    pedir_troca(client)
    usuario(app, nome="nova")  # cadastra nova@ex.com antes da confirmação
    assert client.get(link_de_confirmacao(app)[1]).status_code == 400
    u = ler(app, uid)
    assert (u.email, u.email_pendente) == ("ana@ex.com", None)


# ---------- CTA-05 ----------
def test_cta05_sair_dos_outros_aparelhos(app, client):
    uid = usuario(app, client)
    outro = app.test_client()
    login(outro)
    assert client.post("/conta/seguranca/sessoes").status_code == 302
    assert client.get("/conta/seguranca").status_code == 200
    assert outro.get("/conta/seguranca").status_code == 302
    assert ler(app, uid).session_version == 1 and eventos(app, uid) == ["sessoes"]


# ---------- CTA-06 ----------
def test_cta06_ativacao_mostra_uri_e_qr_e_so_liga_com_codigo_valido(app, client):
    uid = usuario(app, client)
    assert client.post("/conta/seguranca/2fa/ativar").location.endswith("/conta/seguranca/2fa")
    segredo = ler(app, uid).totp_segredo
    assert len(base64.b32decode(segredo)) == 20  # 160 bits
    r = client.get("/conta/seguranca/2fa")
    html = r.get_data(as_text=True)
    assert f"otpauth://totp/GameLog:ana?secret={segredo}&amp;issuer=GameLog" in html
    svg = html.split("<svg", 1)[1].split("</svg>")[0]
    assert "<path" in svg and "href" not in svg  # gerado aqui, sem serviço externo
    assert r.headers["Cache-Control"] == "no-store"
    assert client.post("/conta/seguranca/2fa/confirmar", data={"codigo": "000000"}).status_code == 400
    assert ler(app, uid).totp_ativado_em is None
    r = client.post("/conta/seguranca/2fa/confirmar", data={"codigo": codigo_agora(app, uid)})
    assert r.status_code == 200 and ler(app, uid).totp_ativado_em is not None


def test_cta06_segredo_ausente_das_respostas_depois_da_ativacao(app, client, relogio):
    uid = usuario(app, client)
    ligar_2fa(app, client, uid, relogio)
    segredo = ler(app, uid).totp_segredo
    assert client.get("/conta/seguranca/2fa").status_code == 302
    for caminho in ("/conta/seguranca", "/notificacoes", "/u/ana"):
        assert segredo not in client.get(caminho).get_data(as_text=True)
    with app.app_context():
        assert all(segredo not in e.corpo_texto for e in EmailSaida.query)


def test_cta06_qr_em_svg_segue_a_norma():
    # Reed-Solomon do exemplo "HELLO WORLD" 1-M, formato M com máscara 0 e versão 7 (ISO/IEC 18004)
    dados = [32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236, 17, 236, 17]
    assert qr.correcao(dados, qr.divisor(10)) == [196, 35, 39, 119, 235, 215, 231, 226, 93, 23]
    assert format(qr.informacao_formato(0), "015b") == "101010000010010"
    assert format(qr.informacao_versao(7), "018b") == "000111110010010100"
    assert qr.posicoes_alinhamento(7) == [6, 22, 38]
    texto = "otpauth://totp/GameLog:um_nome_bem_longo?secret=" + "A" * 32 + "&issuer=GameLog"
    assert ler_qr(qr.matriz(texto)) == texto
    with pytest.raises(qr.TextoLongo):
        qr.matriz("x" * 300)


def ler_qr(m):
    """Decodificador mínimo: formato, máscara, zigue-zague, blocos e modo byte."""
    lado = len(m)
    versao = (lado - 17) // 4
    formato = sum(m[8][lado - 1 - i] << i for i in range(8)) | sum(m[lado - 15 + i][8] << i for i in range(8, 15))
    mascara = next(k for k in range(8) if qr.informacao_formato(k) == formato)
    fixo = qr.Matriz(versao)
    fixo.padroes()
    bits = []
    direita = lado - 1
    while direita >= 1:
        direita = 5 if direita == 6 else direita
        for vert in range(lado):
            for j in range(2):
                x = direita - j
                y = lado - 1 - vert if (direita + 1) & 2 == 0 else vert
                if not fixo.fixo[y][x]:
                    bits.append(m[y][x] ^ qr.MASCARAS[mascara](x, y))
        direita -= 2
    palavras = [int("".join(map(str, map(int, bits[i:i + 8]))), 2) for i in range(0, len(bits) // 8 * 8, 8)]
    ecc, nblocos = qr.BLOCOS_M[versao]
    total = qr.modulos_de_dados(versao) // 8
    curtos, curto = nblocos - total % nblocos, total // nblocos - ecc
    blocos, k = [[] for _ in range(nblocos)], 0
    for i in range(curto + 1):
        for b in range(nblocos):
            if i < curto or b >= curtos:
                blocos[b].append(palavras[k])
                k += 1
    dados = [p for b in blocos for p in b]
    fluxo = "".join(format(p, "08b") for p in dados)
    assert fluxo[:4] == "0100"
    n = int(fluxo[4:12], 2)
    return bytes(int(fluxo[12 + 8 * i:20 + 8 * i], 2) for i in range(n)).decode()


# ---------- CTA-07 ----------
@pytest.mark.parametrize("instante, esperado", [(59, "94287082"), (1111111109, "07081804"),
                                                (1111111111, "14050471"), (1234567890, "89005924"),
                                                (2000000000, "69279037"), (20000000000, "65353130")])
def test_cta07_vetores_da_rfc_6238_sha1(instante, esperado):
    segredo = base64.b32encode(b"12345678901234567890").decode()
    assert conta.codigo_totp(segredo, instante // 30, digitos=8) == esperado


def test_cta07_aceita_um_passo_de_cada_lado(app):
    uid = make_user(app)
    with app.app_context():
        u = db.session.get(User, uid)
        u.totp_segredo = conta.gerar_segredo()
        db.session.commit()
        p = conta.passo_atual()
        assert not conta.verificar_totp(u, conta.codigo_totp(u.totp_segredo, p - 2))
        assert not conta.verificar_totp(u, conta.codigo_totp(u.totp_segredo, p + 2))
        assert conta.verificar_totp(u, conta.codigo_totp(u.totp_segredo, p - 1))
        assert conta.verificar_totp(u, conta.codigo_totp(u.totp_segredo, p + 1))


def test_cta07_codigo_usado_nao_vale_de_novo(app):
    uid = make_user(app)
    with app.app_context():
        u = db.session.get(User, uid)
        u.totp_segredo = conta.gerar_segredo()
        db.session.commit()
        codigo = conta.codigo_totp(u.totp_segredo, conta.passo_atual())
        assert conta.verificar_totp(u, codigo)
        assert not conta.verificar_totp(u, codigo)
        assert not conta.verificar_totp(u, conta.codigo_totp(u.totp_segredo, conta.passo_atual() - 1))


# ---------- CTA-08 ----------
def test_cta08_login_em_dois_passos(app, client, relogio):
    uid = usuario(app, client)
    ligar_2fa(app, client, uid, relogio)
    novo = app.test_client()
    novo.post("/entrar", data={"email": "ana@ex.com", "password": "errada123"})
    r = novo.post("/entrar?next=/diario", data={"email": "ana@ex.com", "password": "senha1234"})
    assert r.status_code == 302 and r.location.endswith("/entrar/2fa")
    assert novo.get("/conta/seguranca").status_code == 302  # ainda sem sessão
    assert tentativas(app) == 1  # senha certa sem o segundo passo não zera
    r = novo.post("/entrar/2fa", data={"codigo": codigo_agora(app, uid)})
    assert r.status_code == 302 and r.location.endswith("/diario")
    assert novo.get("/conta/seguranca").status_code == 200 and tentativas(app) == 0


def test_cta08_cinco_codigos_errados_voltam_ao_login_e_contam(app, client, relogio):
    uid = usuario(app, client)
    ligar_2fa(app, client, uid, relogio)
    novo = app.test_client()
    login(novo)
    for _ in range(4):
        assert novo.post("/entrar/2fa", data={"codigo": "000000"}).status_code == 401
    r = novo.post("/entrar/2fa", data={"codigo": "000000"}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert r.request.path == "/entrar" and "E-mail, senha ou código incorretos" in html
    assert tentativas(app) == 5
    assert novo.post("/entrar/2fa", data={"codigo": codigo_agora(app, uid)}).location.endswith("/entrar")
    assert login(novo).status_code == 429  # CAD-04


def test_cta08_passo_intermediario_expira_em_5_minutos(app, client, relogio):
    uid = usuario(app, client)
    ligar_2fa(app, client, uid, relogio)
    novo = app.test_client()
    login(novo)
    relogio.avancar(minutes=5, seconds=1)
    r = novo.post("/entrar/2fa", data={"codigo": codigo_agora(app, uid)})
    assert r.status_code == 302 and r.location.endswith("/entrar")
    assert novo.get("/conta/seguranca").status_code == 302


# ---------- CTA-09 ----------
def test_cta09_dez_codigos_guardados_so_como_hash_e_de_uso_unico(app, client, relogio):
    uid = usuario(app, client)
    codigos = ligar_2fa(app, client, uid, relogio)
    assert len(set(codigos)) == 10
    with app.app_context():
        hashes = {c.codigo_hash for c in CodigoRecuperacao.query.filter_by(user_id=uid)}
    assert len(hashes) == 10 and not any(c.replace("-", "") in h for c in codigos for h in hashes)
    assert client.get("/conta/seguranca").get_data(as_text=True).count(codigos[0]) == 0
    novo = app.test_client()
    login(novo)
    assert novo.post("/entrar/2fa", data={"codigo": codigos[0].lower()}).status_code == 302
    novo.post("/sair")
    login(novo)
    assert novo.post("/entrar/2fa", data={"codigo": codigos[0]}).status_code == 401


def test_cta09_regenerar_exige_senha_e_invalida_os_anteriores(app, client, relogio):
    uid = usuario(app, client)
    antigos = ligar_2fa(app, client, uid, relogio)
    assert client.post("/conta/seguranca/2fa/codigos", data={"senha": "errada123"}).status_code == 400
    r = client.post("/conta/seguranca/2fa/codigos", data={"senha": "senha1234"})
    novos = CODIGO_RE.findall(r.get_data(as_text=True))
    assert len(novos) == 10 and not set(novos) & set(antigos)
    with app.app_context():
        u = db.session.get(User, uid)
        assert not conta.usar_codigo(u, antigos[0]) and conta.usar_codigo(u, novos[0])
    assert eventos(app, uid)[-1] == "codigos"


# ---------- CTA-10 ----------
def test_cta10_desligar_exige_senha_e_codigo(app, client, relogio):
    uid = usuario(app, client)
    codigos = ligar_2fa(app, client, uid, relogio)
    outro = app.test_client()
    login(outro)
    outro.post("/entrar/2fa", data={"codigo": codigos[1]})
    assert client.post("/conta/seguranca/2fa/desligar", data={"senha": "senha1234", "codigo": "000000"}).status_code == 400
    assert client.post("/conta/seguranca/2fa/desligar",
                       data={"senha": "errada123", "codigo": codigo_agora(app, uid)}).status_code == 400
    sv = ler(app, uid).session_version
    r = client.post("/conta/seguranca/2fa/desligar", data={"senha": "senha1234", "codigo": codigos[0]})
    assert r.status_code == 302
    u = ler(app, uid)
    assert (u.totp_segredo, u.totp_ativado_em, u.session_version) == (None, None, sv + 1)
    with app.app_context():
        assert CodigoRecuperacao.query.filter_by(user_id=uid).count() == 0
    assert outro.get("/conta/seguranca").status_code == 302
    assert login(app.test_client()).location.endswith("/")  # sem segundo passo


# ---------- CTA-11 ----------
def test_cta11_cada_mudanca_notifica_e_entra_no_historico(app, client, relogio):
    uid = usuario(app, client)
    senha(client)
    client.post("/conta/seguranca/sessoes")
    codigos = ligar_2fa(app, client, uid, relogio)
    client.post("/conta/seguranca/2fa/codigos", data={"senha": "novasenha99"})
    client.post("/conta/seguranca/2fa/desligar", data={"senha": "novasenha99", "codigo": codigo_agora(app, uid)})
    pedir_troca(client, senha_atual="novasenha99")
    client.get(link_de_confirmacao(app)[1])
    tipos = ["senha", "sessoes", "2fa_ligado", "codigos", "2fa_desligado", "email"]
    assert eventos(app, uid) == tipos and codigos
    with app.app_context():
        avisos = Notificacao.query.filter_by(user_id=uid, tipo="sistema").all()
        assert len(avisos) == 6 and all(n.alvo_url == "/conta/seguranca" for n in avisos)
    html = client.get("/conta/seguranca").get_data(as_text=True)
    assert "16/11/2026" in html and all(conta.EVENTOS[t] in html for t in tipos)


def test_cta11_tokens_so_revogados_quando_pedido(app, client, monkeypatch):
    usuario(app, client)
    revogados = []
    monkeypatch.setattr(conta, "revogar_tokens", revogados.append)
    assert "revogar_tokens" not in client.get("/conta/seguranca").get_data(as_text=True)  # sem AP2, sem opção
    client.post("/conta/seguranca/sessoes")
    assert revogados == []
    client.post("/conta/seguranca/sessoes", data={"revogar_tokens": "1"})
    assert len(revogados) == 1
    monkeypatch.setattr(conta, "tem_tokens", lambda u: True)
    assert "revogar_tokens" in client.get("/conta/seguranca").get_data(as_text=True)
