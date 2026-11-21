"""Ponto único das regras de menores: `pode(user, acao, alvo=None)` (PMN-01, GLOBAL-13).

As specs do Épico 4 já consultam esta função. Enquanto a verificação de idade (IDA) e a política de
menores (PMN) não entram, nenhuma conta tem faixa etária e toda ação da tabela é liberada; ação fora da
tabela levanta ValueError, para nunca liberar por omissão.
"""


def _liberado(user, alvo=None):
    return True


ACOES = {
    acao: _liberado
    for acao in ("seguir", "comentar", "perfil_publico", "ranking_global", "conquista_por_tempo",
                 "aviso_engajamento", "email_engajamento", "ver_ofertas", "alerta_preco", "entrar_clube",
                 "publicar_clube", "torneio_inscrever")
}


def pode(user, acao, alvo=None):
    if acao not in ACOES:
        raise ValueError(f"ação desconhecida na política de menores: {acao}")
    return bool(ACOES[acao](user, alvo))
