"""Direitos do titular (DIR-01..14): acesso, portabilidade, correção, exclusão, oposição e a fila de pedidos.

O mapa de dados decide o que sai na exportação (`campos_exportaveis`) e como cada coluna é anonimizada
(`anonimizar_conta`); nada disso tem lista própria aqui. O ZIP é montado em memória.
"""
import csv
import io
import json
import os
import secrets
import zipfile
from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, session
from sqlalchemy import select

from .. import tempo
from ..models import (Comment, Consentimento, Follow, Game, GameList, PedidoTitular, PostClube, Review, SessaoJogo, User,
                      db, now)
from ..util import admin_required, current_user, login_required, paginate
from . import mapa
from .anonimizar import anonimizar_conta

bp = Blueprint("direitos", __name__)

TIPOS = {"acesso": "Declaração completa dos dados (acesso)", "portabilidade": "Portabilidade",
         "correcao": "Correção", "exclusao": "Exclusão", "oposicao": "Oposição",
         "informacao": "Com quem compartilhamos", "contato": "Contato"}
PELO_FORMULARIO = ("acesso", "correcao", "informacao", "oposicao")
STATUS = {"aguardando_confirmacao": "Aguardando confirmação", "aberto": "Aberto", "concluido": "Concluído",
          "recusado": "Recusado"}
PRAZO_PADRAO = 15
VENCE_LOGO = timedelta(days=3)
VALIDADE_CONFIRMACAO = timedelta(hours=48)
SAL_CONFIRMACAO = "pedido-titular"
VERSAO_FORMATO = 1
ARQUIVOS = {"review": "avaliacoes", "sessao_jogo": "sessoes", "game_list": "listas", "status": "status",
            "consentimento": "consentimentos", "comment": "comentarios", "follow.follower_id": "seguindo",
            "follow.followed_id": "seguidores"}
RENOMEAR = {"hidden": "oculta"}
MAX_DETALHE = 2000


# ---------- pedidos e prazo (DIR-01, DIR-11) ----------
def prazo_dias():
    try:
        return max(1, int(os.environ.get("PRAZO_TITULAR_DIAS") or PRAZO_PADRAO))
    except ValueError:
        return PRAZO_PADRAO


def abrir(pedido, momento=None):
    """O prazo começa a contar quando o pedido fica aberto."""
    pedido.status, pedido.prazo_em = "aberto", (momento or now()) + timedelta(days=prazo_dias())


def novo_pedido(user, tipo, assunto, detalhe, email, confirmado=True):
    pedido = PedidoTitular(protocolo=f"{tempo.hoje():%Y%m%d}-{secrets.token_hex(3).upper()}", tipo=tipo,
                           user_id=user.id if user else None, email_contato=email, assunto=assunto[:120],
                           detalhe=detalhe[:MAX_DETALHE], status="aguardando_confirmacao", criado_em=now())
    if confirmado:
        abrir(pedido, pedido.criado_em)
    db.session.add(pedido)
    db.session.flush()
    return pedido


def pedir_confirmacao(pedido):
    """O pedido sem login só vale depois do clique no link enviado ao e-mail de contato (DIR-11)."""
    from ..conta import serializador
    from ..emails import enviar_email, url_publica

    token = serializador(SAL_CONFIRMACAO).dumps(pedido.id)
    enviar_email(None, "confirmar_pedido", para=pedido.email_contato, pedido=pedido,
                 link=url_publica(f"/privacidade/pedidos/confirmar/{token}"), horas=48)


@bp.route("/privacidade/pedidos/confirmar/<token>")
def confirmar(token):
    from ..conta import ler_link

    pid = ler_link(token, SAL_CONFIRMACAO, VALIDADE_CONFIRMACAO)
    pedido = db.session.get(PedidoTitular, pid) if isinstance(pid, int) else None
    if pedido is None:
        abort(400)
    if pedido.status == "aguardando_confirmacao":
        abrir(pedido)
        db.session.commit()
    flash(f"Pedido {pedido.protocolo} confirmado. Respondemos até {tempo.dia_de(pedido.prazo_em):%d/%m/%Y}.", "ok")
    return redirect("/privacidade/contato")


# ---------- acesso simplificado (DIR-02) ----------
def resumo(user):
    contagens = [
        ("Avaliações", Review.query.filter_by(user_id=user.id).count()),
        ("Sessões do diário", SessaoJogo.query.filter_by(user_id=user.id).count()),
        ("Listas", GameList.query.filter_by(user_id=user.id).count()),
        ("Seguidores", Follow.query.filter(Follow.followed_id == user.id, Follow.aprovado).count()),
        ("Seguindo", Follow.query.filter(Follow.follower_id == user.id).count()),
        ("Registros de consentimento", Consentimento.query.filter_by(user_id=user.id).count()),
    ]
    cadastro = [("Nome de usuário", user.username), ("Nome de exibição", user.display_name), ("E-mail", user.email),
                ("Conta criada em", tempo.dia_de(user.created_at).strftime("%d/%m/%Y"))]
    return cadastro, contagens


@bp.route("/conta/privacidade/direitos")
@login_required
def pagina():
    me = current_user()
    cadastro, contagens = resumo(me)
    pedidos = PedidoTitular.query.filter_by(user_id=me.id).order_by(PedidoTitular.id.desc()).all()
    return render_template("privacidade/direitos.html", cadastro=cadastro, contagens=contagens, pedidos=pedidos,
                           tipos=TIPOS, status=STATUS, pelo_formulario=PELO_FORMULARIO,
                           suboperadores=mapa.suboperadores(), prazo=prazo_dias(), me=me)


@bp.route("/conta/privacidade/pedidos", methods=["POST"])
@login_required
def pedir():
    """Correção dos demais dados, declaração completa, informação e oposição vão para a fila (DIR-05, DIR-10)."""
    me = current_user()
    tipo, detalhe = request.form.get("tipo"), (request.form.get("detalhe") or "").strip()
    if tipo not in PELO_FORMULARIO or not detalhe:
        flash("Escolha o pedido e descreva o que você quer.", "erro")
        return redirect("/conta/privacidade/direitos")
    pedido = novo_pedido(me, tipo, TIPOS[tipo], detalhe, me.email)
    db.session.commit()
    flash(f"Pedido {pedido.protocolo} registrado. Respondemos até {tempo.dia_de(pedido.prazo_em):%d/%m/%Y}.", "ok")
    return redirect("/conta/privacidade/direitos")


@bp.route("/conta/privacidade/oposicao", methods=["POST"])
@login_required
def oposicao():
    """Tira (ou devolve) as notas da pessoa do cálculo de vizinhos de todo mundo (DIR-09, COL-01)."""
    me = current_user()
    me.oposicao_recomendacao = bool(request.form.get("ligado"))
    db.session.commit()
    flash("Suas notas não entram mais nas recomendações de outras pessoas." if me.oposicao_recomendacao
          else "Suas notas voltam a ajudar nas recomendações de outras pessoas.", "ok")
    return redirect(request.form.get("voltar") == "direitos" and "/conta/privacidade/direitos"
                    or "/conta/privacidade")


# ---------- confirmação com senha e 2FA (DIR-03, DIR-06, DIR-12) ----------
def conferir(quem, form):
    """None se a senha (e o 2FA, se ativo) de `quem` confere; senão (mensagem, status)."""
    from ..conta import checar_senha, dois_fatores, segundo_fator

    if erro := checar_senha(quem, form.get("senha")):
        return erro
    if dois_fatores(quem) and not segundo_fator(quem, form.get("codigo")):
        return "Código de verificação incorreto.", 400
    return None


# ---------- portabilidade (DIR-03, DIR-04, DIR-13) ----------
def _referencia(coluna, tabela):
    return any(fk.column.table.name == tabela for fk in coluna.foreign_keys)


def fontes():
    """(arquivo, tabela, coluna dona): a coluna que aponta para a pessoa anotada como dona no mapa, mais o follow."""
    saida = []
    for tabela in db.metadata.sorted_tables:
        if tabela.name == "user" or not mapa.campos_exportaveis(tabela.name):
            continue
        for coluna in tabela.columns:
            chave = f"{tabela.name}.{coluna.name}"
            campo = mapa.CAMPOS.get(chave)
            dono = campo is not None and campo.categoria == "identificacao" and not campo.exportar
            if _referencia(coluna, "user") and (dono or chave in ARQUIVOS):
                arquivo = ARQUIVOS.get(chave) or ARQUIVOS.get(tabela.name) or tabela.name
                if any(a == arquivo for a, _, _ in saida):
                    arquivo = f"{arquivo}-{coluna.name}"
                saida.append((arquivo, tabela, coluna.name))
    return saida


def _valor(valor):
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    return valor


def exportar(user):
    """{arquivo: (colunas, linhas)} da pessoa e o `dados.json`; uma consulta por tabela e duas no fim."""
    tabelas, pessoas, jogos = {}, set(), set()
    usuario = {c: _valor(getattr(user, c)) for c in mapa.campos_exportaveis("user")}
    for arquivo, tabela, dono in fontes():
        colunas = [c for c in mapa.campos_exportaveis(tabela.name) if c != dono]
        linhas = [list(r) for r in db.session.execute(select(*(tabela.c[c] for c in colunas))
                                                      .where(tabela.c[dono] == user.id))]
        for i, c in enumerate(colunas):
            alvo = pessoas if _referencia(tabela.c[c], "user") else jogos if _referencia(tabela.c[c], "game") else None
            if alvo is not None:
                alvo.update(linha[i] for linha in linhas if linha[i] is not None)
        tabelas[arquivo] = (colunas, linhas, tabela)
    nomes = dict(db.session.execute(select(User.id, User.username).where(User.id.in_(pessoas))).all())
    slugs = dict(db.session.execute(select(Game.id, Game.slug).where(Game.id.in_(jogos))).all())
    arquivos = {}
    for arquivo, (colunas, linhas, tabela) in tabelas.items():
        trocas = [nomes if _referencia(tabela.c[c], "user") else slugs if _referencia(tabela.c[c], "game") else None
                  for c in colunas]
        saida = [[_valor(t.get(v, v) if t is not None and v is not None else v) for v, t in zip(linha, trocas)]
                 for linha in linhas]
        arquivos[arquivo] = ([RENOMEAR.get(c, c) for c in colunas], saida)
    dados = {"versao_formato": VERSAO_FORMATO, "gerado_em": tempo.agora_utc().isoformat(), "usuario": usuario,
             "tabelas": {a: [dict(zip(cab, linha)) for linha in linhas] for a, (cab, linhas) in arquivos.items()}}
    return dados, arquivos


def zip_dos_dados(user):
    dados, arquivos = exportar(user)
    memoria = io.BytesIO()
    with zipfile.ZipFile(memoria, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("dados.json", json.dumps(dados, ensure_ascii=False, indent=2))
        for arquivo, (cabecalho, linhas) in arquivos.items():
            texto = io.StringIO()
            escritor = csv.writer(texto)
            escritor.writerow(cabecalho)
            escritor.writerows(linhas)
            z.writestr(f"{arquivo}.csv", texto.getvalue())
    memoria.seek(0)
    return memoria


def entregar_zip(titular, quem_pediu):
    memoria = zip_dos_dados(titular)
    detalhe = "Baixado pelo titular." if quem_pediu.id == titular.id else f"Baixado pelo responsável @{quem_pediu.username}."
    pedido = novo_pedido(titular, "portabilidade", TIPOS["portabilidade"], detalhe, titular.email)
    pedido.status, pedido.concluido_em = "concluido", now()
    db.session.commit()
    return send_file(memoria, mimetype="application/zip", as_attachment=True,
                     download_name=f"gamelog-{titular.username}.zip")


@bp.route("/conta/privacidade/exportar", methods=["POST"])
@login_required
def baixar():
    me = current_user()
    if erro := conferir(me, request.form):
        flash(erro[0], "erro")
        return redirect("/conta/privacidade/direitos")
    return entregar_zip(me, me)


# ---------- exclusão (DIR-06..08) ----------
def excluir_conta(user, quem_pediu):
    """Avisa o endereço antigo, anonimiza pelo mapa e registra o pedido concluído; quem chama faz o commit.

    Conta preservada por denúncia (DEN-09) só é desligada: sessões e tokens caem, o conteúdo some, e o pedido fica
    aberto até o expurgo anonimizar a conta depois da preservação (DIR-07).
    """
    from ..emails import enviar_email

    uid = user.id
    detalhe = "Pedida pelo titular." if quem_pediu.id == uid else "Pedida pelo responsável."
    enviar_email(None, "conta_excluida", para=user.email)
    if user.preservar_ate is not None and user.preservar_ate > now():
        desligar(user)
        novo_pedido(user, "exclusao", TIPOS["exclusao"], f"{detalhe} Conta preservada por denúncia até "
                    f"{tempo.dia_de(user.preservar_ate):%d/%m/%Y}.", user.email)
        return
    anonimizar_conta(user)
    anonimo = db.session.get(User, uid)
    pedido = novo_pedido(anonimo, "exclusao", TIPOS["exclusao"], detalhe, anonimo.email)
    pedido.status, pedido.concluido_em = "concluido", now()


def desligar(user):
    from ..tokens import revogar_todos

    user.suspensa_em, user.session_version = user.suspensa_em or now(), user.session_version + 1
    revogar_todos(user.id)
    Review.query.filter_by(user_id=user.id).update({Review.hidden: True})
    Comment.query.filter_by(user_id=user.id).update({Comment.oculto: True})
    PostClube.query.filter_by(user_id=user.id).update({PostClube.oculto: True})


def exclusoes_adiadas(agora):
    """Contas com exclusão pedida cuja preservação venceu: o expurgo as anonimiza (DIR-07, DEN-09)."""
    return (User.query.join(PedidoTitular, PedidoTitular.user_id == User.id)
            .filter(PedidoTitular.tipo == "exclusao", PedidoTitular.status == "aberto",
                    User.preservar_ate.isnot(None), User.preservar_ate <= agora).order_by(User.id).all())


def concluir_exclusao(user):
    pedidos = PedidoTitular.query.filter_by(user_id=user.id, tipo="exclusao", status="aberto").all()
    anonimizar_conta(user)
    for pedido in pedidos:
        pedido = db.session.get(PedidoTitular, pedido.id)
        pedido.status, pedido.concluido_em = "concluido", now()


@bp.route("/conta/excluir", methods=["GET", "POST"])
@login_required
def excluir():
    me = current_user()
    if request.method == "GET":
        return render_template("conta/excluir.html", erro=None, dois_fatores=me.totp_ativado_em is not None)
    erro = conferir(me, request.form)
    if erro is None and (request.form.get("username") or "").strip().lower() != me.username_lower:
        erro = ("Digite o seu nome de usuário para confirmar.", 400)
    if erro:
        return render_template("conta/excluir.html", erro=erro[0],
                               dois_fatores=me.totp_ativado_em is not None), erro[1]
    excluir_conta(me, me)
    db.session.commit()
    session.clear()
    flash("Sua conta foi excluída. Enviamos a confirmação para o e-mail que estava cadastrado.", "ok")
    return redirect("/")


# ---------- pelo responsável (DIR-12) ----------
def menor_do_titular(menor_id):
    """Só o responsável com vínculo ativo, e só enquanto o menor tem de 12 a 15 anos; o resto é 403."""
    from ..menores.idade import VINCULADO, faixa_etaria
    from ..menores.responsavel import ativos

    me = current_user()
    vinculo = ativos(menor_id, me.id).first()
    if vinculo is None or faixa_etaria(vinculo.menor) != VINCULADO:
        abort(403)
    return me, vinculo.menor


@bp.route("/responsavel/<int:menor_id>/dados", methods=["POST"])
@login_required
def baixar_do_menor(menor_id):
    me, menor = menor_do_titular(menor_id)
    if erro := conferir(me, request.form):
        flash(erro[0], "erro")
        return redirect(f"/responsavel/{menor.id}")
    return entregar_zip(menor, me)


@bp.route("/responsavel/<int:menor_id>/excluir", methods=["POST"])
@login_required
def excluir_do_menor(menor_id):
    me, menor = menor_do_titular(menor_id)
    if erro := conferir(me, request.form):
        flash(erro[0], "erro")
        return redirect(f"/responsavel/{menor.id}")
    excluir_conta(menor, me)
    db.session.commit()
    flash("A conta foi excluída e o vínculo, encerrado.", "ok")
    return redirect("/responsavel")


# ---------- fila do admin (DIR-10) ----------
@bp.route("/admin/privacidade/pedidos")
@admin_required
def fila():
    q = PedidoTitular.query.filter_by(status="aberto").order_by(PedidoTitular.prazo_em, PedidoTitular.id)
    agora = now()
    return render_template("admin/pedidos_titular.html", page=paginate(q, request.args.get("page")), tipos=TIPOS,
                           agora=agora, logo=agora + VENCE_LOGO, belem=tempo.dia_de)


@bp.route("/admin/privacidade/pedidos/<int:pid>", methods=["POST"])
@admin_required
def decidir(pid):
    """Concluir ou recusar com resposta obrigatória; a pessoa vê e recebe por e-mail (DIR-10, GLOBAL-17)."""
    from ..admin import audit
    from ..emails import enviar_email

    pedido = db.get_or_404(PedidoTitular, pid)
    decisao, resposta = request.form.get("decisao"), (request.form.get("resposta") or "").strip()
    if pedido.status != "aberto" or decisao not in ("concluido", "recusado"):
        abort(400)
    if not resposta:
        flash("Escreva a resposta ao titular.", "erro")
        return redirect("/admin/privacidade/pedidos")
    pedido.status, pedido.resposta, pedido.concluido_em = decisao, resposta[:MAX_DETALHE], now()
    pedido.decidido_por = current_user().id
    audit("privacidade.pedido", f"{pedido.protocolo} ({pedido.tipo}): {STATUS[decisao].lower()}")
    titular = db.session.get(User, pedido.user_id) if pedido.user_id else None
    enviar_email(titular, "pedido_respondido", para=None if titular else pedido.email_contato, pedido=pedido,
                 situacao=STATUS[decisao], tipo_pedido=TIPOS[pedido.tipo])
    db.session.commit()
    flash(f"Pedido {pedido.protocolo} {STATUS[decisao].lower()}.", "ok")
    return redirect("/admin/privacidade/pedidos")
