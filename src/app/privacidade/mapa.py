"""Mapa de dados: cada coluna de cada tabela anotada uma vez (MAP-01, MAP-02).

O registro das operações (MAP-08), a exportação e a anonimização (DIR), o expurgo (RTN) e a política (PRV)
leem daqui por `campos_exportaveis`, `regra_anonimizacao` e `retencao` (MAP-09). Coluna nova sem anotação
faz `verificar_mapa()` e o teste da suíte falharem (MAP-03, GLOBAL-12).
"""
import os
from dataclasses import dataclass

CATEGORIAS = ("identificacao", "contato", "autenticacao", "perfil", "conteudo", "atividade", "preferencia",
              "idade", "tecnico", "nao_pessoal")
BASES_LEGAIS = ("consentimento", "obrigacao_legal", "contrato", "exercicio_direitos", "legitimo_interesse",
                "nao_se_aplica")  # LGPD art. 7, I, II, V, VI e IX
ANONIMIZACOES = ("apagar", "substituir", "manter", "apagar_linha")
FINALIDADES_CONSENTIMENTO = ("email_resumo", "ofertas")  # as de CNS
FINALIDADES = {  # agrupam o registro das operações (MAP-08)
    "conta": "Cadastro, login e manutenção da conta",
    "seguranca": "Segurança da conta e prevenção de abuso",
    "perfil_social": "Perfil, seguidores e feed",
    "conteudo": "Avaliações, comentários e listas publicados pelo jogador",
    "acompanhamento": "Diário, status e horas de jogo",
    "engajamento": "Notificações, conquistas, XP, rankings e clubes",
    "recomendacoes": "Recomendações de jogos",
    "moderacao": "Denúncias e moderação de conteúdo",
    "auditoria": "Registro das ações administrativas",
    "catalogo": "Catálogo de jogos, lojas e conquistas",
}


@dataclass(frozen=True)
class Campo:
    categoria: str
    finalidade: str
    base_legal: str
    retencao_dias: int | None  # None: enquanto a conta existir
    exportar: bool
    anonimizar: str
    avaliacao_li: str = ""  # teste de balanceamento, obrigatório com legitimo_interesse (MAP-04)
    finalidade_consentimento: str | None = None  # obrigatória com consentimento (MAP-05)


@dataclass(frozen=True)
class Suboperador:
    nome: str
    finalidade: str
    pais: str
    mecanismo: str  # transferência internacional (LGPD art. 33)
    finalidades: tuple  # chaves de FINALIDADES em que ele atua; vazio = todas


def _tabela(tabela, regra, *grupos):
    """Anota as colunas da tabela; cada grupo é (colunas separadas por espaço, mudanças sobre a regra)."""
    return {f"{tabela}.{c}": Campo(**{**regra, **mudancas}) for colunas, mudancas in grupos for c in colunas.split()}


def _nao_pessoal(tabela, finalidade):
    return {f"{tabela}.*": Campo("nao_pessoal", finalidade, "nao_se_aplica", None, False, "manter")}


TECNICO = {"categoria": "tecnico", "exportar": False}
DONO = {"categoria": "identificacao", "exportar": False}  # user_id: a própria pessoa, redundante na exportação
LI_SEGURANCA = ("Bloquear tentativas repetidas de senha protege a conta do próprio titular, que espera esse "
                "controle; guarda só o e-mail digitado e a hora, por 30 dias.")
LI_AUDITORIA = ("Responsabilizar as ações dos administradores sobre contas e conteúdo; só o administrador "
                "aparece como autor, o detalhe é curto e o registro é apagado em 2 anos.")
LI_MODERACAO = ("Manter a comunidade livre de abuso; a denúncia guarda só o motivo e o comentário de quem "
                "denuncia, visíveis apenas para administradores, e quem denuncia não é exposto ao denunciado.")

CONTA = dict(categoria="identificacao", finalidade="conta", base_legal="contrato", retencao_dias=None,
             exportar=True, anonimizar="manter")
SOCIAL = dict(CONTA, categoria="atividade", finalidade="perfil_social", anonimizar="apagar_linha")
CONTEUDO = dict(CONTA, categoria="conteudo", finalidade="conteudo")
CONTEUDO_PRIVADO = dict(CONTEUDO, anonimizar="apagar_linha")  # listas: some com a conta (DIR-07)
DIARIO = dict(CONTA, categoria="atividade", finalidade="acompanhamento", anonimizar="apagar_linha")
ENGAJAMENTO = dict(CONTA, categoria="atividade", finalidade="engajamento", anonimizar="apagar_linha")
MODERACAO = dict(CONTA, categoria="atividade", finalidade="moderacao", base_legal="legitimo_interesse",
                 avaliacao_li=LI_MODERACAO, exportar=False)
AUDITORIA = dict(CONTA, categoria="atividade", finalidade="auditoria", base_legal="legitimo_interesse",
                 avaliacao_li=LI_AUDITORIA, exportar=False)

CAMPOS = {
    # ---------- conta ----------
    **_tabela(
        "user", CONTA,
        ("id", {}),
        ("username", {"anonimizar": "substituir"}),
        ("username_lower", {**TECNICO, "anonimizar": "substituir"}),
        ("email", {"categoria": "contato", "anonimizar": "substituir"}),
        ("password_hash session_version", {"categoria": "autenticacao", "exportar": False, "anonimizar": "substituir"}),
        ("role", TECNICO),
        ("created_at", {"categoria": "atividade"}),
        ("display_name", {"categoria": "perfil", "finalidade": "perfil_social", "anonimizar": "substituir"}),
        ("bio", {"categoria": "perfil", "finalidade": "perfil_social", "anonimizar": "apagar"}),
        ("perfil_privado", {"categoria": "preferencia", "finalidade": "perfil_social"}),
        ("steam_id", {"finalidade": "acompanhamento", "anonimizar": "apagar"}),
    ),
    **_tabela(
        "login_attempt",
        dict(CONTA, categoria="contato", finalidade="seguranca", base_legal="legitimo_interesse",
             avaliacao_li=LI_SEGURANCA, retencao_dias=30, exportar=False, anonimizar="apagar_linha"),
        ("id", TECNICO), ("email", {}), ("at", {"categoria": "atividade"}),
    ),
    # ---------- conteúdo publicado ----------
    **_tabela("review", CONTEUDO, ("id", TECNICO), ("user_id", DONO),
              ("game_id score text spoiler edited hidden created_at", {})),
    **_tabela("comment", CONTEUDO, ("id", TECNICO), ("user_id", DONO), ("review_id text created_at", {})),
    **_tabela("like", dict(CONTEUDO, anonimizar="apagar_linha"), ("id", TECNICO), ("user_id", DONO),
              ("review_id created_at", {})),
    **_tabela("game_list", CONTEUDO_PRIVADO, ("id", TECNICO), ("user_id", DONO),
              ("name description public created_at", {})),
    **_tabela("list_item", CONTEUDO_PRIVADO, ("id list_id", TECNICO), ("game_id position", {})),
    **_tabela("lista_inteligente", CONTEUDO_PRIVADO, ("id", TECNICO), ("user_id", DONO),
              ("nome publica regra created_at", {})),
    **_tabela("post_clube", dict(CONTEUDO, finalidade="engajamento"), ("id", TECNICO), ("user_id", DONO),
              ("clube_id texto spoiler oculto created_at", {}),
              ("motivo_ocultacao oculto_por", {"finalidade": "moderacao", "exportar": False})),
    # ---------- perfil e feed ----------
    **_tabela("follow", SOCIAL, ("id", TECNICO), ("follower_id followed_id", {})),
    **_tabela("activity", SOCIAL, ("id", TECNICO), ("user_id", DONO),
              ("kind game_id review_id list_id dia valor texto created_at", {})),
    # ---------- diário e acompanhamento ----------
    **_tabela("status", DIARIO, ("id", TECNICO), ("user_id", DONO), ("game_id value updated_at zerado_em", {})),
    **_tabela("sessao_jogo", DIARIO, ("id", TECNICO), ("user_id", DONO),
              ("game_id jogada_em duracao_min inicio plataforma_id zerou anotacao spoiler origem created_at", {})),
    **_tabela("previa_importacao", dict(DIARIO, exportar=False), ("id", TECNICO), ("user_id", DONO),
              ("linhas created_at", {})),
    **_tabela("dismissed", dict(DIARIO, finalidade="recomendacoes"), ("id", TECNICO), ("user_id", DONO),
              ("game_id", {})),
    # ---------- engajamento ----------
    **_tabela("notificacao", dict(ENGAJAMENTO, retencao_dias=180), ("id", TECNICO), ("user_id", DONO),
              ("tipo ator_id game_id review_id texto alvo_url lida_em created_at", {})),
    **_tabela("preferencia_notificacao", dict(ENGAJAMENTO, categoria="preferencia"), ("id", TECNICO),
              ("user_id", DONO), ("tipo site email", {})),
    **_tabela("usuario_conquista", ENGAJAMENTO, ("id", TECNICO), ("user_id", DONO),
              ("conquista_id progresso desbloqueada_em fixada", {})),
    **_tabela("perfil_progressao", ENGAJAMENTO, ("id", TECNICO), ("user_id", DONO),
              ("xp_total nivel sequencia_semanas maior_sequencia ultima_semana_ativa congelamentos", {})),
    **_tabela("extrato_xp", ENGAJAMENTO, ("id", TECNICO), ("user_id", DONO),
              ("origem_tipo origem_id xp motivo created_at", {})),
    **_tabela("snapshot_ranking", dict(ENGAJAMENTO, anonimizar="manter"), ("id", TECNICO), ("user_id", DONO),
              ("temporada_id categoria posicao valor", {})),  # retrato imutável (RNK-09)
    **_tabela("membro_clube", ENGAJAMENTO, ("id", TECNICO), ("user_id", DONO),
              ("clube_id papel situacao entrou_em created_at", {})),
    **_tabela("contribuinte_meta", ENGAJAMENTO, ("id", TECNICO), ("user_id", DONO), ("meta_id", {})),
    # ---------- moderação e auditoria ----------
    **_tabela("report", MODERACAO, ("id", TECNICO), ("reporter_id", DONO),
              ("review_id alvo_tipo alvo_id reason comment created_at", {"exportar": True}),
              ("status decided_by decided_at", {})),
    **_tabela("desclassificacao", MODERACAO, ("id temporada_id", TECNICO), ("user_id", DONO),
              ("categoria motivo created_at revertida_em", {"exportar": True}), ("admin_id", {})),
    **_tabela("audit_log", dict(AUDITORIA, retencao_dias=730), ("id", TECNICO),
              ("admin_id action detail created_at", {})),
    **_tabela("pendencia_merge", dict(CONTA, categoria="nao_pessoal", finalidade="catalogo",
                                      base_legal="nao_se_aplica", exportar=False),
              ("id jogo_a_id jogo_b_id semelhanca status created_at decidida_em", {}),
              ("decidida_por", {k: v for k, v in AUDITORIA.items() if k != "anonimizar"})),
    # ---------- catálogo e clubes, sem dado pessoal ----------
    **_nao_pessoal("game", "catalogo"),
    **_nao_pessoal("game_genres", "catalogo"),
    **_nao_pessoal("game_platforms", "catalogo"),
    **_nao_pessoal("genre", "catalogo"),
    **_nao_pessoal("platform", "catalogo"),
    **_nao_pessoal("developer", "catalogo"),
    **_nao_pessoal("jogo_fonte", "catalogo"),
    **_nao_pessoal("conquista", "catalogo"),
    **_nao_pessoal("preco_loja", "catalogo"),
    **_nao_pessoal("instancia", "seguranca"),  # token do banco, não identifica ninguém
    **_nao_pessoal("temporada", "engajamento"),
    **_nao_pessoal("clube", "engajamento"),
    **_nao_pessoal("jogo_do_mes", "engajamento"),
    **_nao_pessoal("meta_clube", "engajamento"),
}

SUBOPERADORES = [
    Suboperador("Render", "Hospedagem da aplicação e do banco", "Estados Unidos",
                "Cláusulas-padrão contratuais (Res. CD/ANPD 19/2024)", ()),
    Suboperador("Valve (Steam)", "Catálogo e capas dos jogos; o navegador do visitante acessa a CDN da Steam "
                "enquanto o proxy de capas (RTN) não está ligado", "Estados Unidos",
                "Acesso direto do navegador do titular à CDN; nenhum dado é enviado pelo GameLog", ("catalogo",)),
    Suboperador("GOG", "Catálogo e preços; nenhum dado pessoal é enviado", "Polônia",
                "Não se aplica: nenhum dado pessoal é transferido", ("catalogo",)),
    Suboperador("RAWG", "Catálogo; nenhum dado pessoal é enviado", "Não informado",
                "Não se aplica: nenhum dado pessoal é transferido", ("catalogo",)),
]


def suboperadores():
    """SUBOPERADORES, com o provedor de e-mail só quando `SMTP_HOST` está configurado (MAP-07)."""
    host = os.environ.get("SMTP_HOST")
    if not host:
        return list(SUBOPERADORES)
    return [*SUBOPERADORES, Suboperador(f"Provedor de e-mail ({host})", "Envio de e-mails da conta e de avisos",
                                        "Conforme o provedor configurado",
                                        "Cláusulas-padrão contratuais (Res. CD/ANPD 19/2024)",
                                        ("conta", "seguranca", "engajamento"))]


# ---------- validação e verificação (MAP-03..06) ----------
def validar_campo(chave, campo):
    """Erros de uma anotação; lista vazia quando ela é válida."""
    erros = []
    if campo.categoria not in CATEGORIAS:
        erros.append(f"{chave}: categoria inválida {campo.categoria!r}")
    if campo.base_legal not in BASES_LEGAIS:
        erros.append(f"{chave}: base legal inválida {campo.base_legal!r}")
    if campo.anonimizar not in ANONIMIZACOES:
        erros.append(f"{chave}: anonimização inválida {campo.anonimizar!r}")
    if campo.finalidade not in FINALIDADES:
        erros.append(f"{chave}: finalidade desconhecida {campo.finalidade!r}")
    if campo.retencao_dias is not None and (type(campo.retencao_dias) is not int or campo.retencao_dias <= 0):
        erros.append(f"{chave}: retenção deve ser um inteiro positivo ou None")
    if type(campo.exportar) is not bool:
        erros.append(f"{chave}: exportar deve ser verdadeiro ou falso")
    if campo.base_legal == "legitimo_interesse" and not campo.avaliacao_li.strip():
        erros.append(f"{chave}: legítimo interesse exige a avaliação de balanceamento")
    if (campo.categoria == "nao_pessoal") != (campo.base_legal == "nao_se_aplica"):
        erros.append(f"{chave}: nao_pessoal e nao_se_aplica só andam juntos")
    if campo.categoria == "nao_pessoal" and campo.exportar:
        erros.append(f"{chave}: dado não pessoal não é exportável")
    if campo.base_legal == "consentimento" and campo.finalidade_consentimento not in FINALIDADES_CONSENTIMENTO:
        erros.append(f"{chave}: finalidade de consentimento desconhecida {campo.finalidade_consentimento!r}")
    if campo.categoria == "autenticacao" and campo.exportar:
        erros.append(f"{chave}: dado de autenticação nunca é exportável")
    return erros


def validar_mapa(campos=None):
    campos = CAMPOS if campos is None else campos
    erros = [e for chave, campo in campos.items() for e in validar_campo(chave, campo)]
    for chave in campos:
        tabela, coluna = chave.split(".", 1)
        if coluna != "*" and f"{tabela}.*" in campos:
            erros.append(f"{chave}: a tabela já está anotada inteira com {tabela}.*")
    return erros


def _colunas(metadata):
    return {f"{t.name}.{c.name}" for t in metadata.sorted_tables for c in t.columns}


def verificar_mapa(campos=None, metadata=None):
    """(colunas sem anotação, anotações sem coluna), ambas ordenadas (MAP-03)."""
    if metadata is None:
        from ..models import db

        metadata = db.metadata
    campos = CAMPOS if campos is None else campos
    colunas = _colunas(metadata)
    tabelas = {c.split(".", 1)[0] for c in colunas}
    inteiras = {k.split(".", 1)[0] for k in campos if k.endswith(".*")}
    faltando = sorted(c for c in colunas if c not in campos and c.split(".", 1)[0] not in inteiras)
    sobrando = sorted(k for k in campos if (k.split(".", 1)[0] not in tabelas if k.endswith(".*") else k not in colunas))
    return faltando, sobrando


# ---------- consultas para DIR, RTN e PRV (MAP-09) ----------
def _anotacoes(tabela):
    return {k.split(".", 1)[1]: v for k, v in CAMPOS.items() if k.split(".", 1)[0] == tabela}


def campos_exportaveis(tabela):
    """Colunas da tabela que entram na portabilidade, na ordem do mapa."""
    return [coluna for coluna, campo in _anotacoes(tabela).items() if coluna != "*" and campo.exportar]


def regra_anonimizacao(tabela, coluna):
    anotacoes = _anotacoes(tabela)
    campo = anotacoes.get(coluna) or anotacoes.get("*")
    if campo is None:
        raise KeyError(f"{tabela}.{coluna} não está no mapa de dados")
    return campo.anonimizar


def retencao(tabela):
    """Prazo em dias das linhas da tabela: o menor das colunas; None quando nenhuma tem prazo."""
    prazos = [c.retencao_dias for c in _anotacoes(tabela).values() if c.retencao_dias is not None]
    return min(prazos) if prazos else None


# ---------- registro das operações (MAP-08) ----------
def _texto_retencao(campo):
    if campo.categoria == "nao_pessoal":
        return "não se aplica"
    return "enquanto a conta existir" if campo.retencao_dias is None else f"{campo.retencao_dias} dias"


def registro_operacoes():
    """Uma linha por finalidade, na ordem de FINALIDADES, com o que o mapa diz sobre ela."""
    subs = suboperadores()
    linhas = []
    for finalidade, descricao in FINALIDADES.items():
        campos = {k: c for k, c in CAMPOS.items() if c.finalidade == finalidade}
        if not campos:
            continue
        exportados = {c.exportar for c in campos.values()}
        linhas.append({
            "finalidade": finalidade,
            "descricao": descricao,
            "categorias": sorted({c.categoria for c in campos.values()}),
            "tabelas": sorted({k.split(".", 1)[0] for k in campos}),
            "bases_legais": sorted({c.base_legal for c in campos.values()}),
            "retencoes": sorted({_texto_retencao(c) for c in campos.values()}),
            "exportado": "sim" if exportados == {True} else "não" if exportados == {False} else "parcial",
            "anonimizacao": sorted({c.anonimizar for c in campos.values()}),
            "suboperadores": [s.nome for s in subs if not s.finalidades or finalidade in s.finalidades],
        })
    return linhas
