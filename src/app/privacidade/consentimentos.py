from collections import namedtuple

from flask import Blueprint, abort, flash, redirect, render_template
from sqlalchemy import func

from .. import tempo
from ..menores.idade import VINCULADO, faixa_etaria
from ..menores.politica import e_menor
from ..models import Consentimento, User, db
from ..util import admin_required, current_user, login_required

bp = Blueprint("consentimentos", __name__)

Texto = namedtuple("Texto", "versao texto exige_renovar")
Finalidade = namedtuple("Finalidade", "rotulo textos")
FINALIDADES = {
    "email_resumo": Finalidade("Resumo semanal por e-mail", (
        Texto(1, "Usamos seu e-mail e a atividade de quem você segue para mandar, às segundas, um resumo da semana "
                 "anterior. Você pode desligar quando quiser, aqui ou pelo link no próprio e-mail.", False),
    )),
    "ofertas": Finalidade("Avisos de ofertas", (
        Texto(1, "Usamos sua wishlist e seus alertas de preço para avisar, no site e por e-mail, quando um jogo entra "
                 "em promoção ou chega ao preço que você definiu. Só para maiores de 18 anos.", False),
    )),
}
ORIGENS = ("cadastro", "configuracoes", "contexto", "responsavel", "descadastro", "seed")
SO_ADULTOS = ("ofertas",)
CAMPO = "consentir_{}"

Pedido = namedtuple("Pedido", "finalidade rotulo texto marcado bloqueado")


class FinalidadeDesconhecida(ValueError):
    pass


class NaoPodeConceder(PermissionError):
    pass


def _finalidade(finalidade):
    if finalidade not in FINALIDADES:
        raise FinalidadeDesconhecida(f"finalidade de consentimento desconhecida: {finalidade}")
    return FINALIDADES[finalidade]


def texto_atual(finalidade):
    return _finalidade(finalidade).textos[-1]


def versao_minima(finalidade):
    return max((t.versao for t in _finalidade(finalidade).textos if t.exige_renovar), default=0)


def ultima(user, finalidade):
    _finalidade(finalidade)
    return (Consentimento.query.filter_by(user_id=user.id, finalidade=finalidade)
            .order_by(Consentimento.id.desc()).first())


def _vale(linha, user):
    if linha is None or not linha.concedido or linha.versao_texto < versao_minima(linha.finalidade):
        return False
    if linha.finalidade in SO_ADULTOS and e_menor(user):
        return False
    return linha.origem == "responsavel" or faixa_etaria(user) != VINCULADO


def tem_consentimento(user, finalidade):
    _finalidade(finalidade)
    return user is not None and _vale(ultima(user, finalidade), user)


def precisa_renovar(user, finalidade):
    linha = ultima(user, finalidade)
    return linha is not None and linha.concedido and linha.versao_texto < versao_minima(finalidade)


def visiveis(user):
    return [f for f in FINALIDADES if not (f in SO_ADULTOS and e_menor(user))]


def pode_conceder(user, finalidade, responsavel=None):
    _finalidade(finalidade)
    if finalidade in SO_ADULTOS and e_menor(user):
        return False
    if responsavel is not None:
        return e_responsavel_vinculado(responsavel, user)
    return faixa_etaria(user) != VINCULADO


def e_responsavel_vinculado(responsavel, menor):
    from ..menores.responsavel import e_responsavel

    return e_responsavel(responsavel, menor)


def _gravar(user, finalidade, concedido, origem):
    if origem not in ORIGENS:
        raise ValueError(f"origem de consentimento desconhecida: {origem}")
    linha = Consentimento(user_id=user.id, finalidade=finalidade, concedido=concedido, origem=origem,
                          versao_texto=texto_atual(finalidade).versao, created_at=tempo.agora_utc())
    db.session.add(linha)
    return linha


def conceder(user, finalidade, origem):
    if not pode_conceder(user, finalidade):
        raise NaoPodeConceder(finalidade)
    return _gravar(user, finalidade, True, origem)


def revogar(user, finalidade, origem):
    _finalidade(finalidade)
    return _gravar(user, finalidade, False, origem)


def conceder_pelo_responsavel(responsavel, menor, finalidade):
    if not pode_conceder(menor, finalidade, responsavel):
        raise NaoPodeConceder(finalidade)
    return _gravar(menor, finalidade, True, "responsavel")


def revogar_pelo_responsavel(responsavel, menor, finalidade):
    _finalidade(finalidade)
    if not e_responsavel_vinculado(responsavel, menor):
        raise NaoPodeConceder(finalidade)
    return _gravar(menor, finalidade, False, "responsavel")


def aplicar_interruptor(user, finalidade, ligado):
    atual = tem_consentimento(user, finalidade)
    if ligado and not atual and pode_conceder(user, finalidade):
        return conceder(user, finalidade, "contexto")
    if not ligado and atual:
        return revogar(user, finalidade, "configuracoes")
    return None


def pedido(user, finalidade, marcado=None):
    if marcado is None:
        marcado = user is not None and tem_consentimento(user, finalidade)
    bloqueado = user is not None and not marcado and not pode_conceder(user, finalidade)
    f = _finalidade(finalidade)
    return Pedido(finalidade, f.rotulo, f.textos[-1].texto, marcado, bloqueado)


def marcado_no_form(form, finalidade):
    return bool(form.get(CAMPO.format(finalidade)))


def historico(user):
    return Consentimento.query.filter_by(user_id=user.id).order_by(Consentimento.id.desc()).all()


def validar_cadastro(form):
    marcadas = [f for f in FINALIDADES if marcado_no_form(form, f)]

    def gravar(user):
        for finalidade in marcadas:
            if pode_conceder(user, finalidade):
                conceder(user, finalidade, "cadastro")

    return {}, gravar


def pedidos_do_cadastro(form):
    return [pedido(None, f, marcado_no_form(form, f)) for f in FINALIDADES]


def painel(user):
    return [{"finalidade": f, "rotulo": FINALIDADES[f].rotulo, "texto": texto_atual(f),
             "ativo": tem_consentimento(user, f), "renovar": precisa_renovar(user, f),
             "pode_permitir": pode_conceder(user, f)} for f in visiveis(user)]


def _da_lista_ou_400(finalidade):
    if finalidade not in FINALIDADES:
        abort(400)
    return finalidade


@bp.route("/conta/privacidade/consentimentos/<finalidade>/permitir", methods=["POST"])
@login_required
def permitir(finalidade):
    me = current_user()
    finalidade = _da_lista_ou_400(finalidade)
    try:
        conceder(me, finalidade, "configuracoes")
    except NaoPodeConceder:
        abort(403)
    db.session.commit()
    flash(f"{FINALIDADES[finalidade].rotulo}: permitido.", "ok")
    return redirect("/conta/privacidade")


@bp.route("/conta/privacidade/consentimentos/<finalidade>/revogar", methods=["POST"])
@login_required
def revogar_rota(finalidade):
    me = current_user()
    revogar(me, _da_lista_ou_400(finalidade), "configuracoes")
    db.session.commit()
    flash(f"{FINALIDADES[finalidade].rotulo}: revogado.", "ok")
    return redirect("/conta/privacidade")


def contagem_por_finalidade():
    recentes = (db.session.query(func.max(Consentimento.id)).group_by(Consentimento.user_id, Consentimento.finalidade))
    linhas = (db.session.query(Consentimento, User).join(User, User.id == Consentimento.user_id)
              .filter(Consentimento.id.in_(recentes), User.suspensa_em.is_(None)))
    contagem = dict.fromkeys(FINALIDADES, 0)
    for linha, user in linhas:
        if linha.finalidade in contagem and _vale(linha, user):
            contagem[linha.finalidade] += 1
    return contagem


@bp.route("/admin/privacidade/consentimentos")
@admin_required
def admin():
    return render_template("admin/consentimentos.html", finalidades=FINALIDADES, contagem=contagem_por_finalidade(),
                           atual=texto_atual)
