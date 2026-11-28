"""Transparência (PRV-01..12): política e termos versionados, aceite, canal do titular e suboperadores.

As seções que descrevem os dados saem do mapa (`CAMPOS`, `suboperadores`), então a política acompanha o código
(PRV-03). O aceite é só de inserção (PRV-11). Quem ainda não aceitou uma versão com `exige_novo_aceite` vai para
`/termos/aceitar`, depois das pendências de idade (PRV-05).
"""
import os
import time
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy import func

from .. import tempo
from ..admin import audit
from ..menores.idade import ADULTO, faixa_etaria
from ..models import AceiteDocumento, AvisoDocumentoFechado, DocumentoLegal, User, db
from ..util import (LIVRES_DO_ACEITE, _destino, _pendencia_de_idade, admin_required, current_user, login_required,
                    rota_livre, safe_next)
from . import consentimentos, mapa

bp = Blueprint("transparencia", __name__)

TIPOS = {"politica": "Política de privacidade", "termos": "Termos de uso"}
TEXTOS = Path(__file__).parent / "textos"
AVISO_MUDANCA = {"politica": "A política de privacidade mudou em", "termos": "Os termos de uso mudaram em"}
MAX_MENSAGEM, MAX_ASSUNTO = 2000, 120
VALIDADE_S = 60
_ULTIMO = {}  # token da instância do banco -> (válido até, id do documento mais recente)
RECUSA = "Para criar a conta, aceite os termos de uso e a política de privacidade."
# "Não aceito" leva à exclusão de conta (DIR); até a DIR existir, o pedido vai pelo canal do titular
DESTINO_NAO_ACEITO = "/privacidade/contato?assunto=exclusao"
ASSUNTOS = {"exclusao": "Excluir minha conta"}

COOKIES = [  # PRV-09: todo cookie que o GameLog grava; um cookie novo entra aqui ou o teste falha
    {"nome": "session", "finalidade": "Manter você conectado e proteger os formulários (token CSRF)",
     "duracao": "Até fechar o navegador", "tipo": "Estritamente necessário"},
]

CATEGORIAS = {"identificacao": "identificação da conta", "contato": "e-mail de contato",
              "autenticacao": "senha (só como hash)", "perfil": "perfil público", "conteudo": "o que você publica",
              "atividade": "atividade no site", "preferencia": "suas escolhas e configurações",
              "idade": "data de nascimento e faixa etária", "tecnico": "dados técnicos"}
BASES = {"consentimento": "consentimento", "obrigacao_legal": "obrigação legal", "contrato": "execução do contrato",
         "exercicio_direitos": "exercício de direitos", "legitimo_interesse": "legítimo interesse"}


# ---------- documentos (PRV-01, PRV-02) ----------
def atual(tipo):
    return DocumentoLegal.query.filter_by(tipo=tipo).order_by(DocumentoLegal.versao.desc()).first()


def publicar(tipo, texto, resumo_mudancas="", exige_novo_aceite=False):
    """Nova versão do tipo, uma acima da última; quem chama faz o commit."""
    texto = (texto or "").strip()
    if tipo not in TIPOS or not texto:
        raise ValueError("Escolha o documento e escreva o texto.")
    ultima = db.session.query(func.max(DocumentoLegal.versao)).filter_by(tipo=tipo).scalar() or 0
    doc = DocumentoLegal(tipo=tipo, versao=ultima + 1, texto=texto, resumo_mudancas=(resumo_mudancas or "").strip(),
                         exige_novo_aceite=bool(exige_novo_aceite))
    db.session.add(doc)
    db.session.flush()
    _ULTIMO.clear()
    return doc


def ultimo_documento():
    """Id do documento mais recente, guardado por VALIDADE_S em cada processo (o outro worker vê a versão nova
    em até um minuto): sem documento novo, quem já está em dia não custa consulta a cada página."""
    chave = current_app.config["INSTANCIA"]
    validade, ultimo = _ULTIMO.get(chave, (0, None))
    if time.monotonic() >= validade:
        ultimo = db.session.query(func.max(DocumentoLegal.id)).scalar()
        _ULTIMO[chave] = (time.monotonic() + VALIDADE_S, ultimo)
    return ultimo


def publicar_iniciais():
    """Versão 1 da política e dos termos a partir de `textos/`, só para o tipo que ainda não tem versão (seed)."""
    for tipo in TIPOS:
        if atual(tipo) is None:
            publicar(tipo, (TEXTOS / f"{tipo}.txt").read_text(encoding="utf-8"), "Primeira versão.", True)
    db.session.commit()


# ---------- aceite (PRV-04, PRV-05, PRV-06, PRV-11) ----------
def aceitar_atuais(user):
    """Grava o aceite da versão atual de cada documento que a pessoa ainda não aceitou; quem chama faz o commit."""
    ja = {a.documento_id for a in AceiteDocumento.query.filter_by(user_id=user.id)}
    for tipo in TIPOS:
        doc = atual(tipo)
        if doc is not None and doc.id not in ja:
            db.session.add(AceiteDocumento(user_id=user.id, documento_id=doc.id))


def _maiores_aceitas(user):
    linhas = (db.session.query(DocumentoLegal.tipo, func.max(DocumentoLegal.versao))
              .join(AceiteDocumento, AceiteDocumento.documento_id == DocumentoLegal.id)
              .filter(AceiteDocumento.user_id == user.id).group_by(DocumentoLegal.tipo))
    return dict(linhas.all())


def pendentes_de_aceite(user):
    """Versões com `exige_novo_aceite` mais novas que a última versão aceita do mesmo tipo."""
    aceitas = _maiores_aceitas(user)
    exigidas = (db.session.query(DocumentoLegal.tipo, func.max(DocumentoLegal.versao))
                .filter(DocumentoLegal.exige_novo_aceite).group_by(DocumentoLegal.tipo).all())
    pendentes = [atual(tipo) for tipo, versao in exigidas if aceitas.get(tipo, 0) < versao]
    return sorted(pendentes, key=lambda d: d.tipo)


def avisos(user):
    """Versões atuais sem novo aceite que a pessoa não aceitou nem fechou: a faixa do topo (PRV-06)."""
    aceitas = _maiores_aceitas(user)
    fechados = {a.documento_id for a in AvisoDocumentoFechado.query.filter_by(user_id=user.id)}
    docs = [atual(tipo) for tipo in TIPOS]
    return [d for d in docs if d is not None and not d.exige_novo_aceite and aceitas.get(d.tipo, 0) < d.versao
            and d.id not in fechados]


def contas_por_versao():
    """{documento_id: contas ativas que aceitaram}, sem lista individual (PRV-11)."""
    linhas = (db.session.query(AceiteDocumento.documento_id, func.count(func.distinct(AceiteDocumento.user_id)))
              .join(User, User.id == AceiteDocumento.user_id).filter(User.suspensa_em.is_(None))
              .group_by(AceiteDocumento.documento_id))
    return dict(linhas.all())


def validar_aceite(form):
    """Parte do formulário de cadastro (PRV-04): (erros, gravar(user))."""
    if not form.get("aceite"):
        return {"aceite": RECUSA}, None
    return {}, aceitar_atuais


# ---------- conteúdo gerado do mapa (PRV-03, PRV-07, PRV-08, PRV-09) ----------
def encarregado():
    nome, email = os.environ.get("ENCARREGADO_NOME"), os.environ.get("ENCARREGADO_EMAIL")
    return {"nome": nome, "email": email} if nome and email else None


def secoes_do_mapa():
    """Uma seção por finalidade com dado pessoal: categorias, bases legais e retenção, em linguagem simples."""
    secoes = []
    for linha in mapa.registro_operacoes():
        categorias = [CATEGORIAS[c] for c in linha["categorias"] if c in CATEGORIAS]
        if not categorias:
            continue
        secoes.append({"descricao": linha["descricao"], "categorias": categorias,
                       "bases": [BASES[b] for b in linha["bases_legais"] if b in BASES],
                       "retencoes": [r for r in linha["retencoes"] if r != "não se aplica"]})
    return secoes


def resumo():
    """O começo da política, em linguagem simples (PRV-03, PRV-10): no máximo 10 itens."""
    secoes = secoes_do_mapa()
    categorias = sorted({c for s in secoes for c in s["categorias"]})
    prazos = sorted({r for s in secoes for r in s["retencoes"] if r.endswith("dias")}, key=lambda r: int(r.split()[0]))
    contato = encarregado()
    return [
        ("O que coletamos", "Só o que o site precisa: " + ", ".join(categorias) + "."),
        ("Para quê", "Para sua conta funcionar, mostrar o que você publica, recomendar jogos, proteger a conta e "
                     "cumprir a lei. Não vendemos seus dados nem mostramos anúncios."),
        ("Por quanto tempo", "Enquanto sua conta existir. Alguns registros somem antes: "
                             + ", ".join(prazos) + "." if prazos else "Enquanto sua conta existir."),
        ("Com quem compartilhamos", "Com quem nos ajuda a manter o site: "
                                    + ", ".join(s.nome for s in mapa.suboperadores()) + "."),
        ("Cookies", "Só um, o de sessão, que mantém você conectado. Sem cookies de propaganda."),
        ("Menores de idade", "Quem tem menos de 12 anos não pode ter conta. De 12 a 17 anos, a conta tem regras "
                             "mais protetivas."),
        ("Seus direitos", "Você pode ver, corrigir, baixar e apagar seus dados, e mudar de ideia sobre o que "
                          "autorizou."),
        ("Como falar conosco", f"Escreva para {contato['nome']} ({contato['email']})." if contato
         else "Pelo formulário \"Fale com a gente sobre seus dados\", no fim desta página."),
    ]


# ---------- redirecionamento e faixa (PRV-05, PRV-06) ----------
def em_dia():
    """A sessão já conferiu o documento mais recente: nada pendente nem faixa, sem consultar o banco."""
    ultimo = ultimo_documento()
    return ultimo is None or session.get("prv") == ultimo


def marcar_em_dia(user):
    """No login e no cadastro: quem não tem nada pendente não volta a ser conferido até o próximo documento."""
    if not em_dia() and not pendentes_de_aceite(user) and not avisos(user):
        session["prv"] = ultimo_documento()


def estado(me):
    """(pendentes de aceite, avisos) da requisição."""
    if "prv" not in g:
        g.prv = ([], []) if em_dia() else (pendentes_de_aceite(me), avisos(me))
        if g.prv == ([], []) and not em_dia():
            session["prv"] = ultimo_documento()
    return g.prv


@bp.before_app_request
def _exigir_novo_aceite():
    if request.endpoint in (None, "static") or request.path.startswith(("/api/", "/capa/")) or request.path == "/api":
        return None
    if em_dia():
        return None
    me = current_user()
    if me is None or rota_livre(LIVRES_DO_ACEITE) or _pendencia_de_idade(me) is not None:
        return None  # data de nascimento (IDA-05) e responsável (IDA-04) vêm antes do novo aceite
    if estado(me)[0]:
        return redirect(url_for("transparencia.aceitar", next=_destino()))
    return None


@bp.app_context_processor
def _faixa_de_mudanca():
    me = current_user()
    if me is None or request.path.startswith("/static"):
        return {"avisos_legais": []}
    return {"avisos_legais": estado(me)[1], "aviso_mudanca": AVISO_MUDANCA}


# ---------- páginas públicas (PRV-02, PRV-03, PRV-08, PRV-09, PRV-10) ----------
def _versao_pedida(tipo):
    n = request.args.get("versao")
    if n is None:
        return atual(tipo)
    if not n.isdigit():
        abort(404)
    return DocumentoLegal.query.filter_by(tipo=tipo, versao=int(n)).first_or_404()


def _versoes(tipo):
    return DocumentoLegal.query.filter_by(tipo=tipo).order_by(DocumentoLegal.versao.desc()).all()


@bp.route("/privacidade")
def politica():
    me = current_user()
    return render_template("privacidade/politica.html", doc=_versao_pedida("politica"), versoes=_versoes("politica"),
                           resumo=resumo(), secoes=secoes_do_mapa(), suboperadores=mapa.suboperadores(),
                           cookies=COOKIES, encarregado=encarregado(),
                           menor=me is not None and faixa_etaria(me) != ADULTO)


@bp.route("/termos")
def termos():
    return render_template("privacidade/termos.html", doc=_versao_pedida("termos"), versoes=_versoes("termos"))


@bp.route("/privacidade/suboperadores")
def suboperadores():
    return render_template("privacidade/suboperadores.html", suboperadores=mapa.suboperadores())


# ---------- canal do titular (PRV-07) ----------
def criar_contato(user, assunto, mensagem, email):
    """Pedido `contato` com protocolo, ou ValueError com {campo: mensagem}; quem chama faz o commit."""
    erros = {}
    assunto, mensagem, email = (assunto or "").strip(), (mensagem or "").strip(), (email or "").strip().lower()
    if not assunto or len(assunto) > MAX_ASSUNTO:
        erros["assunto"] = f"Escreva o assunto em até {MAX_ASSUNTO} caracteres."
    if not mensagem or len(mensagem) > MAX_MENSAGEM:
        erros["mensagem"] = f"Escreva a mensagem em até {MAX_MENSAGEM} caracteres."
    email = email or (user.email if user else "")
    from ..auth import EMAIL_RE  # auth importa este módulo para o cadastro

    if not EMAIL_RE.match(email):
        erros["email"] = "Informe um e-mail para a resposta."
    if erros:
        raise ValueError(erros)
    from .direitos import novo_pedido, pedir_confirmacao

    pedido = novo_pedido(user, "contato", assunto, mensagem, email, confirmado=user is not None)
    if user is None:
        pedir_confirmacao(pedido)
    return pedido


@bp.route("/privacidade/contato", methods=["GET", "POST"])
def contato():
    me = current_user()
    if request.method == "GET":
        assunto = ASSUNTOS.get(request.args.get("assunto"), "")
        return render_template("privacidade/contato.html", erros={}, form={"assunto": assunto},
                               encarregado=encarregado())
    try:
        pedido = criar_contato(me, request.form.get("assunto"), request.form.get("mensagem"), request.form.get("email"))
    except ValueError as exc:
        return render_template("privacidade/contato.html", erros=exc.args[0], form=request.form,
                               encarregado=encarregado()), 400
    db.session.commit()
    if pedido.status == "aguardando_confirmacao":
        flash(f"Protocolo {pedido.protocolo}. Enviamos um link ao seu e-mail: o pedido vale depois da confirmação, "
              "em até 48 horas.", "ok")
    else:
        flash(f"Recebemos sua mensagem. Protocolo {pedido.protocolo}.", "ok")
    return redirect("/privacidade/contato")


# ---------- novo aceite (PRV-05) ----------
@bp.route("/termos/aceitar", methods=["GET", "POST"])
@login_required
def aceitar():
    me = current_user()
    pendentes = pendentes_de_aceite(me)
    destino = safe_next(request.args.get("next"))
    if not pendentes:
        return redirect(destino)
    if request.method == "POST":
        aceitar_atuais(me)
        db.session.commit()
        flash("Obrigado! Aceite registrado.", "ok")
        return redirect(destino)
    return render_template("privacidade/aceitar.html", pendentes=pendentes, nomes=TIPOS,
                           nao_aceito=DESTINO_NAO_ACEITO)


@bp.route("/privacidade/aviso/<int:doc_id>/fechar", methods=["POST"])
@login_required
def fechar_aviso(doc_id):
    me = current_user()
    db.get_or_404(DocumentoLegal, doc_id)
    if not AvisoDocumentoFechado.query.filter_by(user_id=me.id, documento_id=doc_id).first():
        db.session.add(AvisoDocumentoFechado(user_id=me.id, documento_id=doc_id))
        db.session.commit()
    return redirect(safe_next(request.form.get("next")))


@bp.route("/conta/privacidade")
@login_required
def minha_privacidade():
    """Versões aceitas e quando (PRV-11) e os consentimentos (CNS-03, CNS-08); a DIR acrescenta os direitos."""
    me = current_user()
    aceites = (AceiteDocumento.query.filter_by(user_id=me.id)
               .order_by(AceiteDocumento.aceito_em.desc(), AceiteDocumento.id.desc()).all())
    return render_template("privacidade/conta.html", aceites=aceites, nomes=TIPOS,
                           consentimentos=consentimentos.painel(me), historico=consentimentos.historico(me),
                           rotulos={f: c.rotulo for f, c in consentimentos.FINALIDADES.items()})


# ---------- admin (PRV-01, PRV-11) ----------
@bp.route("/admin/privacidade/documentos", methods=["GET", "POST"])
@admin_required
def documentos():
    erro = None
    if request.method == "POST":
        f = request.form
        try:
            doc = publicar(f.get("tipo"), f.get("texto"), f.get("resumo_mudancas"), f.get("exige_novo_aceite"))
        except ValueError as exc:
            erro = str(exc)
        else:
            audit("privacidade.publicar", f"{doc.tipo} v{doc.versao}" + (" (novo aceite)" if doc.exige_novo_aceite else ""))
            db.session.commit()
            flash(f"{TIPOS[doc.tipo]} versão {doc.versao} publicada.", "ok")
            return redirect("/admin/privacidade/documentos")
    docs = DocumentoLegal.query.order_by(DocumentoLegal.tipo, DocumentoLegal.versao.desc()).all()
    return render_template("admin/documentos.html", docs=docs, contas=contas_por_versao(), nomes=TIPOS, erro=erro,
                           form=request.form if erro else {}), 400 if erro else 200
