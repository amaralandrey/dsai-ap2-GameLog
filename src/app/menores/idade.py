"""Verificação de idade (IDA-01..12): faixa etária, adaptadores de verificação e histórico.

Quem consulta usa só `faixa_etaria(user)`; a data de nascimento nunca sai do servidor (IDA-12). A
autodeclaração é o primeiro sinal, não a prova: um adaptador `confiavel` (responsável ou provedor) pode
corrigir a data, e entre sinais de mesma confiança vale o mais protetivo (IDA-08).
"""
import os
from collections import namedtuple
from datetime import date, datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, session

from .. import tempo
from ..admin import audit
from ..models import User, VerificacaoIdade, db, now
from ..util import admin_required, current_user, login_required, paginate, safe_next

bp = Blueprint("idade", __name__)

IDADE_MINIMA, IDADE_MAXIMA = 12, 120
ADULTO, ADOLESCENTE, VINCULADO = "adulto", "adolescente", "adolescente_vinculado"
MENOR_DE_12 = "O GameLog é para pessoas a partir de 12 anos"

Resultado = namedtuple("Resultado", "nascimento metodo confiavel")


class IdadeInvalida(ValueError):
    """Mensagem para o campo de data de nascimento."""


def idade(nascimento, hoje=None):
    hoje = hoje or tempo.hoje()
    return hoje.year - nascimento.year - ((hoje.month, hoje.day) < (nascimento.month, nascimento.day))


def faixa_de(nascimento):
    anos = idade(nascimento)
    return ADULTO if anos >= 18 else ADOLESCENTE if anos >= 16 else VINCULADO


def faixa_etaria(user):
    """Calculada a cada chamada; sem data ou com idade contestada, a faixa mais protetiva (IDA-03, IDA-11)."""
    if user.nascimento is None or user.idade_contestada_em is not None:
        return VINCULADO
    return faixa_de(user.nascimento)


def aguarda_responsavel(user):
    """A conta espera o vínculo enquanto a faixa for a de 12 a 15 anos: o aniversário de 16 anos a libera sozinho.

    A coluna `aguardando_responsavel` diz que a conta nasceu (ou foi contestada) nessa espera; a faixa diz se
    ela ainda vale (IDA-03, IDA-04, IDA-11).
    """
    return user.aguardando_responsavel and faixa_etaria(user) == VINCULADO


def ler_nascimento(texto):
    """Data válida e plausível (IDA-01) ou IdadeInvalida; menor de 12 é checado à parte (IDA-02)."""
    texto = (texto or "").strip()
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            nascimento = datetime.strptime(texto, formato).date()
            break
        except ValueError:
            continue
    else:
        raise IdadeInvalida("Informe uma data de nascimento válida (dia, mês e ano).")
    if nascimento > tempo.hoje():
        raise IdadeInvalida("A data de nascimento não pode estar no futuro.")
    hoje = tempo.hoje()
    limite = hoje.replace(year=hoje.year - IDADE_MAXIMA, day=28 if (hoje.month, hoje.day) == (2, 29) else hoje.day)
    if nascimento < limite:
        raise IdadeInvalida(f"A data de nascimento não pode ser de mais de {IDADE_MAXIMA} anos atrás.")
    return nascimento


# ---------- adaptadores (IDA-07) ----------
class Autodeclaracao:
    nome, rotulo, confiavel = "autodeclaracao", "Informar a data de novo", False

    def disponivel(self):
        return True

    def verificar(self, user, dados):
        return Resultado(ler_nascimento(dados.get("nascimento")), self.nome, self.confiavel)


class Responsavel:
    """Confirmação no aceite do vínculo (RSP-02); não tem tela própria aqui."""
    nome, rotulo, confiavel = "responsavel", "Confirmação pelo responsável", True

    def disponivel(self):
        return False

    def verificar(self, user, dados):
        return Resultado(ler_nascimento(dados.get("nascimento")), self.nome, self.confiavel)


class ProvedorSimulado:
    """Resultados fixos por código de teste, sem rede; só com VERIFICACAO_PROVEDOR=simulado."""
    nome, rotulo, confiavel = "provedor_simulado", "Provedor de verificação (simulado)", True
    CODIGOS = {"ADULTO": 30, "ADOLESCENTE": 17, "VINCULADO": 14, "CRIANCA": 10}

    def disponivel(self):
        return os.environ.get("VERIFICACAO_PROVEDOR") == "simulado"

    def verificar(self, user, dados):
        anos = self.CODIGOS.get((dados.get("codigo") or "").strip().upper())
        if anos is None:
            raise IdadeInvalida("Código de verificação não reconhecido.")
        hoje = tempo.hoje()
        nascimento = date(hoje.year - anos, hoje.month, 1)
        return Resultado(nascimento, self.nome, self.confiavel)


ADAPTADORES = {a.nome: a for a in (Autodeclaracao(), Responsavel(), ProvedorSimulado())}


# ---------- aplicação de uma verificação (IDA-06, IDA-08, IDA-10) ----------
def _vale(user, resultado):
    """A data que fica: um sinal confiável corrige um não confiável; fora isso, a mais protetiva (a mais nova)."""
    if user.nascimento is None or (resultado.confiavel and not user.nascimento_confiavel):
        return resultado.nascimento, resultado.confiavel
    if resultado.nascimento > user.nascimento:
        return resultado.nascimento, user.nascimento_confiavel and resultado.confiavel
    return user.nascimento, user.nascimento_confiavel


def registrar(user, resultado):
    """Aplica o resultado, grava o histórico e devolve a faixa; quem chama faz o commit.

    Menor de 12 tem a conta suspensa (IDA-05). Uma verificação confiável encerra a contestação do admin
    (IDA-11). A conta fica aguardando responsável enquanto a faixa for a de 12 a 15 anos (IDA-04).
    """
    divergente = user.nascimento is not None and faixa_de(user.nascimento) != faixa_de(resultado.nascimento)
    user.nascimento, user.nascimento_confiavel = _vale(user, resultado)
    if resultado.confiavel:
        user.idade_contestada_em = None
    if idade(user.nascimento) < IDADE_MINIMA:
        user.suspensa_em = user.suspensa_em or now()
    faixa = faixa_etaria(user)
    user.aguardando_responsavel = faixa == VINCULADO
    db.session.add(VerificacaoIdade(user_id=user.id, metodo=resultado.metodo, faixa_resultante=faixa,
                                    confiavel=resultado.confiavel, divergente=divergente))
    return faixa


# ---------- telas da conta (IDA-04, IDA-05, IDA-10) ----------
NOMES_DAS_FAIXAS = {ADULTO: "18 anos ou mais", ADOLESCENTE: "16 ou 17 anos", VINCULADO: "12 a 15 anos"}


def _suspender_sessao(me):
    me.session_version += 1
    db.session.commit()
    session.clear()
    flash(f"Conta suspensa. {MENOR_DE_12}.", "erro")
    return redirect("/")


def _seguir(user):
    return redirect("/conta/pendente" if aguarda_responsavel(user) else safe_next(request.args.get("next")))


@bp.route("/conta/nascimento", methods=["GET", "POST"])
@login_required
def nascimento():
    """Conta antiga sem data de nascimento informa a data antes de qualquer outra rota (IDA-05)."""
    me = current_user()
    if me.nascimento is not None:
        return redirect("/")
    if request.method == "GET":
        return render_template("conta/nascimento.html", erro=None, valor="")
    try:
        resultado = ADAPTADORES["autodeclaracao"].verificar(me, {"nascimento": request.form.get("nascimento")})
    except IdadeInvalida as exc:
        return render_template("conta/nascimento.html", erro=str(exc), valor=request.form.get("nascimento", "")), 400
    registrar(me, resultado)
    if me.suspensa_em is not None:  # menor de 12: suspensa, sai e aparece para o admin
        return _suspender_sessao(me)
    db.session.commit()
    return _seguir(me)


@bp.route("/conta/pendente")
@login_required
def pendente():
    """Conta de 12 a 15 anos, ou com idade contestada, espera o vínculo com um responsável (IDA-04, IDA-11)."""
    me = current_user()
    if not aguarda_responsavel(me):
        return redirect("/")
    confiavel = any(a.confiavel and a.disponivel() for a in ADAPTADORES.values())
    return render_template("conta/pendente.html", contestada=me.idade_contestada_em is not None,
                           confiavel=confiavel, revisao_pedida=me.revisao_idade_pedida_em is not None)


@bp.route("/conta/pendente/revisao", methods=["POST"])
@login_required
def pedir_revisao():
    """Data errada sem verificação confiável disponível: a moderação revê e pede nova data (IDA-04, IDA-11)."""
    me = current_user()
    if not aguarda_responsavel(me):
        return redirect("/")
    if me.revisao_idade_pedida_em is None:
        me.revisao_idade_pedida_em = now()
        db.session.commit()
    flash("Pedido de revisão enviado à moderação.", "ok")
    return redirect("/conta/pendente")


def _tela_idade(me, erro=None):
    disponiveis = [a for a in ADAPTADORES.values() if a.disponivel()]
    return render_template("conta/idade.html", faixa=NOMES_DAS_FAIXAS[faixa_etaria(me)], adaptadores=disponiveis,
                           contestada=me.idade_contestada_em is not None, erro=erro)


@bp.route("/conta/idade")
@login_required
def contestar():
    """Contestar o resultado e pedir nova verificação com um adaptador disponível (IDA-10)."""
    return _tela_idade(current_user())


@bp.route("/conta/idade/<metodo>", methods=["POST"])
@login_required
def verificar_de_novo(metodo):
    """Só um adaptador confiável deixa a faixa menos protetiva; o provedor simulado sem a variável é 404."""
    me, adaptador = current_user(), ADAPTADORES.get(metodo)
    if adaptador is None or not adaptador.disponivel():
        abort(404)
    try:
        resultado = adaptador.verificar(me, request.form)
    except IdadeInvalida as exc:
        return _tela_idade(me, str(exc)), 400
    faixa = registrar(me, resultado)
    if me.suspensa_em is not None:
        return _suspender_sessao(me)
    db.session.commit()
    flash(f"Verificação registrada. Faixa da conta: {NOMES_DAS_FAIXAS[faixa]}.", "ok")
    return redirect("/conta/pendente" if aguarda_responsavel(me) else "/conta/idade")


# ---------- moderação (IDA-11) ----------
@bp.route("/admin/usuarios")
@admin_required
def usuarios():
    """Contas com a faixa (nunca a data): suspensas, com revisão pedida e contestadas primeiro."""
    q = User.query.order_by(User.suspensa_em.is_(None), User.revisao_idade_pedida_em.is_(None),
                            User.idade_contestada_em.is_(None), User.username_lower)
    page = paginate(q, request.args.get("page"))
    return render_template("admin/usuarios.html", page=page, faixa=lambda u: NOMES_DAS_FAIXAS[faixa_etaria(u)],
                           aguarda=aguarda_responsavel)


def _alvo(uid):
    """A conta que a moderação vai mudar, ou None: o admin nunca age sobre a própria idade (IDA-11)."""
    alvo = db.get_or_404(User, uid)
    if alvo.id == current_user().id:
        flash("A idade da sua conta é revisada por outro admin.", "erro")
        return None
    return alvo


def _auditar(acao, alvo):
    audit(acao, f"@{alvo.username} (id {alvo.id})")
    db.session.commit()


@bp.route("/admin/usuarios/<int:uid>/contestar-idade", methods=["POST"])
@admin_required
def contestar_idade(uid):
    """Indício de idade menor: a conta vira de 12 a 15 anos, à espera do responsável, até verificação confiável."""
    alvo = _alvo(uid)
    if alvo is not None and alvo.idade_contestada_em is None:
        alvo.idade_contestada_em, alvo.aguardando_responsavel = now(), True
        _auditar("idade.contestar", alvo)
        flash(f"Idade de @{alvo.username} marcada como contestada.", "ok")
    return redirect("/admin/usuarios")


@bp.route("/admin/usuarios/<int:uid>/retirar-contestacao", methods=["POST"])
@admin_required
def retirar_contestacao(uid):
    """Desfaz uma contestação por engano: a conta volta à faixa da data que tem."""
    alvo = _alvo(uid)
    if alvo is not None and alvo.idade_contestada_em is not None:
        alvo.idade_contestada_em = None
        alvo.aguardando_responsavel = alvo.nascimento is not None and faixa_de(alvo.nascimento) == VINCULADO
        _auditar("idade.retirar_contestacao", alvo)
        flash(f"Contestação da idade de @{alvo.username} retirada.", "ok")
    return redirect("/admin/usuarios")


@bp.route("/admin/usuarios/<int:uid>/nova-data", methods=["POST"])
@admin_required
def nova_data(uid):
    """Data autodeclarada errada (até a que suspendeu a conta): apaga a data, e a conta a informa de novo (IDA-05)."""
    alvo = _alvo(uid)
    if alvo is not None and alvo.nascimento_confiavel:
        flash("A data desta conta veio de uma verificação confiável e não é apagada.", "erro")
    elif alvo is not None:
        alvo.nascimento = alvo.suspensa_em = alvo.revisao_idade_pedida_em = None
        alvo.aguardando_responsavel = alvo.idade_contestada_em is not None  # a contestação continua valendo
        _auditar("idade.nova_data", alvo)
        flash(f"@{alvo.username} informa a data de nascimento de novo no próximo acesso.", "ok")
    return redirect("/admin/usuarios")
