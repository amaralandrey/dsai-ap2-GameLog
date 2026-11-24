"""Incidentes de segurança (INC-01..08): registro, avaliação de risco, prazos e comunicações.

Prazos da Res. CD/ANPD 15/2024: 3 dias úteis para comunicar à ANPD e aos titulares (arts. 6 e 9), em dobro para
agente de pequeno porte (Res. CD/ANPD 2/2022, art. 14), e 72 horas corridas da GDPR (art. 33) com `GDPR_ATIVO=1`.
Incidente encerrado não muda mais; correções entram como anotações (INC-08).
"""
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from flask import Blueprint, Response, current_app, flash, redirect, render_template, request
from sqlalchemy import event, inspect, or_, select

from .. import tempo
from ..admin import audit
from ..emails import enviar_email
from ..jobs import agenda_padrao, tarefa
from ..models import AnotacaoIncidente, Incidente, User, db
from ..notificacoes import notificar
from ..util import admin_required, current_user
from .feriados import dias_uteis_ate, somar_dias_uteis
from .mapa import CATEGORIAS, RETENCAO_INCIDENTE_DIAS

bp = Blueprint("incidentes", __name__)

CATEGORIAS_AFETAVEIS = tuple(c for c in CATEGORIAS if c != "nao_pessoal")
ROTULOS_CATEGORIA = {
    "identificacao": "identificação (nome de usuário, nome de exibição)",
    "contato": "contato (e-mail)",
    "autenticacao": "autenticação (hash de senha, sessão)",
    "perfil": "perfil (bio, foto)",
    "conteudo": "conteúdo publicado (avaliações, comentários, listas)",
    "atividade": "atividade (diário, status, seguidores)",
    "preferencia": "preferências",
    "idade": "idade (data de nascimento, verificação)",
    "tecnico": "dados técnicos",
}
ROTULOS_RISCO = {"relevante": "relevante", "a_avaliar": "a avaliar", "descartado": "descartado"}
ROTULOS_SITUACAO = {"aberto": "aberto", "comunicado_anpd": "comunicado à ANPD", "encerrado": "encerrado"}
LARGA_ESCALA = 1000
PRAZO_GDPR = timedelta(hours=72)
MARCADOR = "[PREENCHER"
MAX_TEXTO = 5000


class ErroIncidente(Exception):
    pass


# ---------- configuração (INC-03, INC-04) ----------
def _config(nome, padrao):
    valor = current_app.config.get(nome)
    return os.environ.get(nome, padrao) if valor is None else valor


def _ligado(nome):
    return str(_config(nome, "0")) in ("1", "True")


def prazo_dias_uteis():
    base = int(_config("INCIDENTE_PRAZO_DIAS_UTEIS", 3))
    return base * 2 if _ligado("AGENTE_PEQUENO_PORTE") else base


def gdpr_ativo():
    return _ligado("GDPR_ATIVO")


# ---------- risco (INC-02) ----------
def motivos_de_risco(categorias, titulares, menores):
    """Por que o risco é relevante; lista vazia quando ele fica "a avaliar"."""
    motivos = []
    if "autenticacao" in categorias:
        motivos.append("dados de autenticação")
    if menores:
        motivos.append("dados de crianças e adolescentes")
    if titulares >= LARGA_ESCALA:
        motivos.append(f"larga escala (≥ {LARGA_ESCALA:,} titulares)".replace(",", "."))
    return motivos


def motivos(inc):
    return motivos_de_risco(inc.lista_categorias, inc.titulares_estimados, inc.envolve_menores)


# ---------- prazos (INC-03, INC-04) ----------
@dataclass
class Prazo:
    nome: str
    limite: datetime  # com fuso de Belém
    cumprido: bool
    vencido: bool
    faltam: str
    dias_uteis: int | None = None
    destaque: bool = False


def _faltam_dias(n, vencido):
    if vencido:
        return "vencido"
    if n == 0:
        return "vence hoje"
    return "falta 1 dia útil" if n == 1 else f"faltam {n} dias úteis"


def prazos(inc):
    """Prazos da ANPD e dos titulares (e o da GDPR, se ativo); o mais próximo ainda aberto fica em destaque."""
    agora, ciencia = tempo.agora(), tempo.para_belem(inc.ciencia_em)
    final = somar_dias_uteis(ciencia.date(), prazo_dias_uteis())
    limite = datetime.combine(final, time(23, 59), tzinfo=tempo.FUSO)
    n, vencido = dias_uteis_ate(agora.date(), final), agora.date() > final
    lista = [Prazo(nome, limite, cumprido, vencido, _faltam_dias(n, vencido), n)
             for nome, cumprido in (("ANPD", inc.anpd_enviado_em is not None),
                                    ("Titulares", inc.titulares_avisados_em is not None))]
    if gdpr_ativo():
        fim = ciencia + PRAZO_GDPR
        horas = -(-(fim - agora) // timedelta(hours=1))
        lista.append(Prazo("GDPR (72 horas)", fim, inc.anpd_enviado_em is not None, fim < agora,
                           "vencido" if fim < agora else f"faltam {horas} hora{'s' if horas != 1 else ''}"))
    abertos = [p for p in lista if not p.cumprido]
    if abertos:
        min(abertos, key=lambda p: p.limite).destaque = True
    return lista


# ---------- registro (INC-01) ----------
def _momento(texto):
    """`AAAA-MM-DDTHH:MM` em Belém, devolvido em UTC ingênuo como o banco guarda; None se inválido."""
    try:
        local = datetime.strptime((texto or "").strip(), "%Y-%m-%dT%H:%M")
    except ValueError:
        return None
    return local.replace(tzinfo=tempo.FUSO).astimezone(timezone.utc).replace(tzinfo=None)


def _dia(texto):
    try:
        return date.fromisoformat((texto or "").strip())
    except ValueError:
        return None


def _texto(form, campo, erros, rotulo, minimo=1):
    valor = (form.get(campo) or "").strip()
    if not minimo <= len(valor) <= MAX_TEXTO:
        erros[campo] = f"{rotulo} deve ter de {minimo} a {MAX_TEXTO:,} caracteres.".replace(",", ".")
    return valor


def validar(form):
    """(dados, erros) do formulário de registro, validado no servidor (GLOBAL-03)."""
    erros = {}
    dados = {"descricao": _texto(form, "descricao", erros, "A descrição", 10),
             "medidas": _texto(form, "medidas", erros, "As medidas tomadas")}
    dados["ciencia_em"] = _momento(form.get("ciencia_em"))
    if dados["ciencia_em"] is None or dados["ciencia_em"] > tempo.agora_utc():
        erros["ciencia_em"] = "Informe quando o GameLog tomou ciência, sem data no futuro."
    dados["ocorrencia_em"] = None
    if (form.get("ocorrencia_em") or "").strip():
        dados["ocorrencia_em"] = _dia(form.get("ocorrencia_em"))
        if dados["ocorrencia_em"] is None or dados["ocorrencia_em"] > tempo.hoje():
            erros["ocorrencia_em"] = "Informe uma data válida, sem data no futuro, ou deixe em branco."
        elif dados["ciencia_em"] and dados["ocorrencia_em"] > tempo.dia_de(dados["ciencia_em"]):
            erros["ocorrencia_em"] = "A ocorrência não pode ser depois da ciência."
    categorias = form.getlist("categorias")
    if not categorias or any(c not in CATEGORIAS_AFETAVEIS for c in categorias):
        erros["categorias"] = "Escolha ao menos uma categoria de dados afetados."
    dados["categorias"] = [c for c in CATEGORIAS_AFETAVEIS if c in categorias]
    try:
        dados["titulares_estimados"] = int(form.get("titulares_estimados") or "")
    except ValueError:
        dados["titulares_estimados"] = None
    if dados["titulares_estimados"] is None or dados["titulares_estimados"] < 1:
        erros["titulares_estimados"] = "Informe o número estimado de titulares (1 ou mais)."
    dados["envolve_menores"] = bool(form.get("envolve_menores"))
    return dados, erros


def registrar(dados, admin):
    """Cria o incidente com o risco já avaliado; quem chama faz o commit (INC-01, INC-02)."""
    razoes = motivos_de_risco(dados["categorias"], dados["titulares_estimados"], dados["envolve_menores"])
    inc = Incidente(**{**dados, "categorias": ",".join(dados["categorias"])},
                    risco="relevante" if razoes else "a_avaliar", registrado_por=admin.id,
                    created_at=tempo.agora_utc())
    db.session.add(inc)
    db.session.flush()
    audit("incidente.registrar", f"incidente {inc.id}")
    if razoes:
        audit("incidente.risco", f"incidente {inc.id}: relevante automático ({', '.join(razoes)})")
    return inc


def _aberto(inc):
    if inc.encerrado_em is not None:
        raise ErroIncidente("Incidente encerrado não muda mais; registre uma anotação.")


def decidir_risco(inc, decisao, justificativa):
    """Confirma (relevante) ou descarta o risco, com justificativa obrigatória (INC-02)."""
    _aberto(inc)
    justificativa = (justificativa or "").strip()
    if decisao not in ("confirmar", "descartar"):
        raise ErroIncidente("Escolha confirmar ou descartar o risco.")
    if not justificativa:
        raise ErroIncidente("A justificativa é obrigatória.")
    if decisao == "descartar" and motivos(inc):
        raise ErroIncidente("O risco é relevante pelos critérios da Res. 15/2024 e não pode ser descartado.")
    inc.risco = "relevante" if decisao == "confirmar" else "descartado"
    inc.justificativa_risco = justificativa[:MAX_TEXTO]
    audit("incidente.risco", f"incidente {inc.id}: {ROTULOS_RISCO[inc.risco]}")


# ---------- comunicações (INC-05, INC-06) ----------
def _contexto(inc):
    lista = prazos(inc)
    return {"inc": inc, "motivos": motivos(inc), "rotulos": ROTULOS_CATEGORIA, "prazos": lista,
            "atrasado": lista[0].vencido, "pequeno_porte": _ligado("AGENTE_PEQUENO_PORTE"),
            "dias_prazo": prazo_dias_uteis(), "riscos": ROTULOS_RISCO, "belem": tempo.para_belem}


def texto_anpd(inc):
    """Modelo de comunicação à ANPD com o conteúdo mínimo do art. 6 §2 da Res. 15/2024."""
    return render_template("admin/incidentes/anpd.txt", **_contexto(inc))


def texto_titulares(inc):
    """Comunicação aos titulares em linguagem simples, com o conteúdo mínimo do art. 9 da Res. 15/2024."""
    return render_template("admin/incidentes/titulares.txt", **_contexto(inc))


def registrar_envio_anpd(inc, dia):
    _aberto(inc)
    if dia is None or dia > tempo.hoje() or dia < tempo.dia_de(inc.ciencia_em):
        raise ErroIncidente("Informe a data do envio, entre a ciência e hoje.")
    inc.anpd_enviado_em, inc.situacao = dia, "comunicado_anpd"
    audit("incidente.anpd", f"incidente {inc.id}: enviado em {dia.isoformat()}")


def destinatarios(destino, nomes):
    """(usuários, erro); `todos` ou a lista de nomes de usuário colada, separada por espaço, vírgula ou linha."""
    if destino == "todos":
        return User.query.order_by(User.id).all(), None
    pedidos = list(dict.fromkeys(n.lstrip("@").lower() for n in re.split(r"[\s,;]+", nomes or "") if n.strip("@")))
    if not pedidos:
        return [], "Cole ao menos um nome de usuário ou escolha todos."
    achados = User.query.filter(User.username_lower.in_(pedidos)).order_by(User.id).all()
    faltando = sorted(set(pedidos) - {u.username_lower for u in achados})
    if faltando:
        return [], "Usuários não encontrados: " + ", ".join(faltando)
    return achados, None


def avisar_titulares(inc, usuarios, mensagem):
    """E-mail (EML) e notificação `sistema` (NTF) para cada titular; quem chama faz o commit (INC-06)."""
    _aberto(inc)
    for u in usuarios:
        enviar_email(u, "incidente", mensagem=mensagem)
        notificar(u.id, "sistema", texto="Comunicado do GameLog sobre um incidente de segurança. "
                                         "Enviamos os detalhes para o seu e-mail.")
    inc.titulares_avisados = (inc.titulares_avisados or 0) + len(usuarios)
    inc.titulares_avisados_em, inc.texto_titulares = tempo.agora_utc(), mensagem
    audit("incidente.titulares", f"incidente {inc.id}: {len(usuarios)} titulares avisados")


# ---------- encerramento e anotações (INC-08) ----------
def encerrar(inc):
    _aberto(inc)
    if inc.risco == "a_avaliar":
        raise ErroIncidente("Confirme ou descarte o risco antes de encerrar.")
    inc.encerrado_em, inc.situacao = tempo.agora_utc(), "encerrado"
    audit("incidente.encerrar", f"incidente {inc.id}")


def anotar(inc, autor, texto):
    texto = (texto or "").strip()
    if not 1 <= len(texto) <= MAX_TEXTO:
        raise ErroIncidente(f"A anotação deve ter de 1 a {MAX_TEXTO:,} caracteres.".replace(",", "."))
    db.session.add(AnotacaoIncidente(incidente_id=inc.id, autor_id=autor.id, texto=texto,
                                     created_at=tempo.agora_utc()))
    audit("incidente.anotar", f"incidente {inc.id}")


@event.listens_for(Incidente, "before_update")
def _imutavel_depois_de_encerrado(mapper, conexao, inc):
    """Lê o encerramento gravado no banco: o objeto pode estar expirado, sem o valor anterior na memória."""
    encerrado = conexao.execute(select(Incidente.encerrado_em).where(Incidente.id == inc.id)).scalar()
    estado = inspect(inc)
    if encerrado is not None and any(estado.attrs[c.key].history.has_changes() for c in mapper.column_attrs):
        raise ErroIncidente("Incidente encerrado não é editado; registre uma anotação.")


@event.listens_for(Incidente, "before_delete")
def _so_expurgo_apaga(mapper, conexao, inc):
    """Só o expurgo (RTN) apaga, e só incidente encerrado há mais de 5 anos."""
    limite = tempo.agora_utc() - timedelta(days=RETENCAO_INCIDENTE_DIAS)
    if inc.encerrado_em is None or inc.encerrado_em > limite:
        raise ErroIncidente("Incidente só é apagado 5 anos depois de encerrado.")


# ---------- lembrete de prazo (INC-07) ----------
def pendentes_de_comunicacao():
    return (Incidente.query.filter(Incidente.risco == "relevante", Incidente.encerrado_em.is_(None),
                                   or_(Incidente.anpd_enviado_em.is_(None), Incidente.titulares_avisados_em.is_(None)))
            .order_by(Incidente.id).all())


@tarefa("incidentes_prazos")
def avisar_prazos():
    """Avisa os admins quando falta 1 dia útil ou menos; um aviso por incidente por dia (sem usuário em `args`)."""
    hoje = tempo.hoje()
    admins = [uid for (uid,) in db.session.query(User.id).filter(User.role == "admin").order_by(User.id)]
    for inc in pendentes_de_comunicacao():
        abertos = [p for p in prazos(inc) if not p.cumprido and p.dias_uteis is not None and p.dias_uteis <= 1]
        if inc.lembrete_em == hoje or not abertos:
            continue
        quais = " e ".join(p.nome for p in abertos)
        for uid in admins:
            notificar(uid, "sistema", alvo_url=f"/admin/incidentes/{inc.id}",
                      texto=f"Incidente #{inc.id}: prazo de comunicação ({quais}) {abertos[0].faltam}.")
        inc.lembrete_em = hoje
    db.session.commit()


agenda_padrao("incidentes_prazos", "0 8 * * *", "incidentes_prazos")


# ---------- rotas do admin (INC-01..08) ----------
def _incidente(iid):
    return db.get_or_404(Incidente, iid)


def _acao(iid, funcao, sucesso):
    """Executa `funcao(inc)`, grava e volta ao detalhe; ErroIncidente vira flash de erro."""
    inc = _incidente(iid)
    try:
        funcao(inc)
    except ErroIncidente as exc:
        db.session.rollback()
        flash(str(exc), "erro")
    else:
        db.session.commit()
        flash(sucesso, "ok")
    return redirect(f"/admin/incidentes/{iid}")


@bp.route("/admin/incidentes")
@admin_required
def lista():
    incidentes = Incidente.query.order_by(Incidente.ciencia_em.desc(), Incidente.id.desc()).all()
    return render_template("admin/incidentes/lista.html", incidentes=incidentes, prazos=prazos,
                           riscos=ROTULOS_RISCO, situacoes=ROTULOS_SITUACAO, belem=tempo.para_belem)


def _formulario(codigo=200, **ctx):
    return render_template("admin/incidentes/novo.html", categorias=CATEGORIAS_AFETAVEIS, rotulos=ROTULOS_CATEGORIA,
                           larga_escala=LARGA_ESCALA, **ctx), codigo


@bp.route("/admin/incidentes/novo", methods=["GET", "POST"])
@admin_required
def novo():
    if request.method == "GET":
        return _formulario(erros={}, form={}, marcadas=[])
    dados, erros = validar(request.form)
    if erros:
        return _formulario(400, erros=erros, form=request.form, marcadas=request.form.getlist("categorias"))
    inc = registrar(dados, current_user())
    db.session.commit()
    razoes = motivos(inc)
    flash(f"Incidente #{inc.id} registrado. Risco: "
          + (f"relevante ({', '.join(razoes)})." if razoes else "a avaliar; confirme ou descarte com justificativa."),
          "ok")
    return redirect(f"/admin/incidentes/{inc.id}")


@bp.route("/admin/incidentes/<int:iid>")
@admin_required
def detalhe(iid):
    inc = _incidente(iid)
    anotacoes = AnotacaoIncidente.query.filter_by(incidente_id=inc.id).order_by(AnotacaoIncidente.id).all()
    return render_template("admin/incidentes/detalhe.html", anotacoes=anotacoes, situacoes=ROTULOS_SITUACAO,
                           **_contexto(inc))


@bp.route("/admin/incidentes/<int:iid>/risco", methods=["POST"])
@admin_required
def risco(iid):
    return _acao(iid, lambda inc: decidir_risco(inc, request.form.get("decisao"), request.form.get("justificativa")),
                 "Avaliação de risco registrada.")


@bp.route("/admin/incidentes/<int:iid>/anpd.txt")
@admin_required
def anpd_txt(iid):
    inc = _incidente(iid)
    return Response(texto_anpd(inc), mimetype="text/plain",
                    headers={"Content-Disposition": f'attachment; filename="incidente-{inc.id}-anpd.txt"'})


@bp.route("/admin/incidentes/<int:iid>/anpd", methods=["POST"])
@admin_required
def anpd(iid):
    return _acao(iid, lambda inc: registrar_envio_anpd(inc, _dia(request.form.get("enviado_em"))),
                 "Envio à ANPD registrado.")


@bp.route("/admin/incidentes/<int:iid>/titulares", methods=["GET", "POST"])
@admin_required
def titulares(iid):
    inc = _incidente(iid)
    if inc.encerrado_em is not None:
        flash("Incidente encerrado não muda mais; registre uma anotação.", "erro")
        return redirect(f"/admin/incidentes/{iid}")
    if request.method == "GET":
        form = {"mensagem": texto_titulares(inc), "destino": "lista"}
        return render_template("admin/incidentes/titulares.html", inc=inc, form=form, erros={})
    form, erros = request.form, {}
    mensagem = (form.get("mensagem") or "").replace("\r\n", "\n").strip()
    if not mensagem or len(mensagem) > MAX_TEXTO:
        erros["mensagem"] = f"Escreva a mensagem (até {MAX_TEXTO:,} caracteres).".replace(",", ".")
    elif MARCADOR in mensagem:
        erros["mensagem"] = "Preencha os campos marcados com [PREENCHER] antes de enviar."
    usuarios, erro = destinatarios(form.get("destino"), form.get("nomes"))
    if erro:
        erros["nomes"] = erro
    if erros:
        return render_template("admin/incidentes/titulares.html", inc=inc, form=form, erros=erros), 400
    avisar_titulares(inc, usuarios, mensagem)
    db.session.commit()
    flash(f"Comunicação enviada a {len(usuarios)} titular{'es' if len(usuarios) != 1 else ''}.", "ok")
    return redirect(f"/admin/incidentes/{iid}")


@bp.route("/admin/incidentes/<int:iid>/encerrar", methods=["POST"])
@admin_required
def encerrar_rota(iid):
    return _acao(iid, encerrar, "Incidente encerrado.")


@bp.route("/admin/incidentes/<int:iid>/anotacoes", methods=["POST"])
@admin_required
def anotacoes(iid):
    return _acao(iid, lambda inc: anotar(inc, current_user(), request.form.get("texto")), "Anotação registrada.")
