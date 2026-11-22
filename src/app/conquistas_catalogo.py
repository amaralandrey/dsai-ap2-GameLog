"""Catálogo inicial de conquistas, gravado por `flask conquistas sincronizar` e pelo seed (GAM-02).

Cada linha: (código, nome, descrição, categoria, raridade, ícone, métrica, meta, secreta, por_tempo).
As três primeiras são as da AP1 (CON-01), com os mesmos códigos e regras.
"""
CATALOGO = [
    ("critico", "Crítico", "Escreveu pelo menos 5 avaliações", "critica", "comum", "✍️", "avaliacoes_visiveis", 5, False, False),
    ("colecionador", "Colecionador", "Tem pelo menos 20 jogos em suas listas", "colecao", "rara", "📚", "jogos_em_listas", 20, False, False),
    ("pioneiro", "Pioneiro", "Foi o primeiro a avaliar um jogo", "critica", "rara", "🚩", "primeira_avaliacao", 1, False, False),
    # crítica
    ("primeira-avaliacao", "Primeira impressão", "Avaliou o primeiro jogo", "critica", "comum", "⭐", "avaliacoes_visiveis", 1, False, False),
    ("critico-25", "Crítico assíduo", "Escreveu 25 avaliações", "critica", "rara", "📝", "avaliacoes_visiveis", 25, False, False),
    ("critico-100", "Crítico de carreira", "Escreveu 100 avaliações", "critica", "epica", "🏛️", "avaliacoes_visiveis", 100, False, False),
    ("resenhista", "Resenhista", "Escreveu 5 resenhas com 140 caracteres ou mais", "critica", "comum", "📜", "resenhas_longas", 5, False, False),
    ("ensaista", "Ensaísta", "Escreveu 30 resenhas com 140 caracteres ou mais", "critica", "epica", "🖋️", "resenhas_longas", 30, False, False),
    ("nota-maxima", "Obra-prima", "Deu nota 10 a um jogo", "critica", "comum", "💯", "notas_dez", 1, False, False),
    ("pioneiro-10", "Desbravador", "Foi o primeiro a avaliar 10 jogos", "critica", "lendaria", "🧭", "primeira_avaliacao", 10, True, False),
    ("querido", "Voz ouvida", "Recebeu 10 curtidas nas avaliações", "critica", "rara", "❤️", "curtidas_recebidas", 10, False, False),
    ("influente", "Influente", "Recebeu 100 curtidas nas avaliações", "critica", "lendaria", "🔥", "curtidas_recebidas", 100, False, False),
    # coleção
    ("primeira-lista", "Organizador", "Criou a primeira lista", "colecao", "comum", "🗂️", "listas_criadas", 1, False, False),
    ("curador", "Curador", "Criou 10 listas", "colecao", "rara", "🖼️", "listas_criadas", 10, False, False),
    ("acervo-50", "Acervo", "Tem status em 50 jogos", "colecao", "rara", "🎮", "jogos_com_status", 50, False, False),
    ("acervo-200", "Biblioteca viva", "Tem status em 200 jogos", "colecao", "epica", "🏰", "jogos_com_status", 200, False, False),
    ("sonhador", "Sonhador", "Tem 25 jogos planejados", "colecao", "comum", "💭", "planejados", 25, False, False),
    ("desapego", "Desapego", "Abandonou 5 jogos", "colecao", "comum", "🍂", "abandonados", 5, True, False),
    # explorador
    ("explorador-multiloja", "Explorador multi-loja", "Tem jogos com vínculo em 3 lojas", "explorador", "rara", "🛒", "lojas_distintas", 3, False, False),
    ("generos-5", "Eclético", "Jogou 5 gêneros diferentes", "explorador", "comum", "🎲", "generos_distintos", 5, False, False),
    ("generos-12", "Onívoro", "Jogou 12 gêneros diferentes", "explorador", "epica", "🌈", "generos_distintos", 12, False, False),
    ("plataformas-3", "Multiplataforma", "Tem jogos em 3 plataformas", "explorador", "comum", "🖥️", "plataformas_distintas", 3, False, False),
    ("estudios-20", "Caçador de estúdios", "Tem jogos de 20 desenvolvedoras", "explorador", "rara", "🏭", "desenvolvedoras_distintas", 20, False, False),
    # diário
    ("primeira-sessao", "Diário aberto", "Registrou a primeira sessão no diário", "diario", "comum", "📔", "sessoes", 1, False, False),
    ("sessoes-50", "Disciplinado", "Registrou 50 sessões no diário", "diario", "rara", "📅", "sessoes", 50, False, False),
    ("dias-30", "Presença", "Registrou sessões em 30 dias diferentes", "diario", "rara", "🗓️", "dias_com_sessao", 30, False, False),
    ("maratonista", "Maratonista", "Registrou uma sessão de 6 horas ou mais", "diario", "rara", "🏃", "maior_sessao_min", 360, False, True),
    ("horas-10", "Aquecendo", "Registrou 10 horas no diário", "diario", "comum", "⏱️", "horas_registradas", 10, False, True),
    ("horas-100", "100 horas registradas", "Registrou 100 horas no diário", "diario", "epica", "⌛", "horas_registradas", 100, False, True),
    ("zerou-1", "Créditos finais", "Zerou o primeiro jogo", "diario", "comum", "🏁", "zerados_total", 1, False, False),
    ("zerou-10-ano", "Zerou 10 no ano", "Zerou 10 jogos no mesmo ano", "diario", "epica", "🏆", "zerados_no_ano", 10, False, False),
    ("zerou-50", "Finalizador", "Zerou 50 jogos", "diario", "lendaria", "👑", "zerados_total", 50, False, False),
    ("semanas-4", "Constância", "Manteve 4 semanas seguidas de registro", "diario", "rara", "🔗", "semanas_seguidas", 4, False, False),
    ("semanas-12", "Temporada inteira", "Manteve 12 semanas seguidas de registro", "diario", "lendaria", "⛓️", "semanas_seguidas", 12, True, False),
    # social
    ("primeiro-seguidor", "Primeiro fã", "Ganhou o primeiro seguidor", "social", "comum", "👋", "seguidores", 1, False, False),
    ("seguidores-25", "Popular", "Tem 25 seguidores", "social", "epica", "🌟", "seguidores", 25, False, False),
    ("seguindo-10", "Antenado", "Segue 10 pessoas", "social", "comum", "📡", "seguindo", 10, False, False),
    ("conversador", "Conversador", "Fez 20 comentários", "social", "rara", "💬", "comentarios_feitos", 20, False, False),
    ("generoso", "Generoso", "Curtiu 50 avaliações", "social", "rara", "👍", "curtidas_dadas", 50, False, False),
    # comunidade
    ("nivel-5", "Veterano", "Chegou ao nível 5", "comunidade", "rara", "🎖️", "nivel", 5, False, False),
    ("nivel-10", "Lenda do GameLog", "Chegou ao nível 10", "comunidade", "lendaria", "🐉", "nivel", 10, False, False),
    ("colecao-conquistas", "Caçador de conquistas", "Desbloqueou 15 conquistas", "comunidade", "epica", "🎯", "conquistas_desbloqueadas", 15, False, False),
    ("meta-clube-1", "Espírito de equipe", "Ajudou um clube a atingir uma meta", "comunidade", "rara", "🤝", "metas_clube", 1, False, False),
    ("meta-clube-5", "Pilar do clube", "Ajudou clubes a atingir 5 metas", "comunidade", "epica", "🏟️", "metas_clube", 5, False, False),
    ("podio-zerados", "Pódio de zerados", "Ficou entre os 3 que mais zeraram numa temporada", "comunidade", "lendaria", "🥇", "concedida", 1, False, False),
    ("podio-horas", "Pódio de horas", "Ficou entre os 3 com mais horas numa temporada", "comunidade", "lendaria", "🥈", "concedida", 1, False, True),
    ("podio-curtidas", "Pódio de curtidas", "Ficou entre os 3 mais curtidos numa temporada", "comunidade", "lendaria", "🥉", "concedida", 1, False, False),
    ("podio-xp", "Pódio de XP", "Ficou entre os 3 com mais XP numa temporada", "comunidade", "lendaria", "🏅", "concedida", 1, False, False),
    # torneios (TRN-11): métricas registradas por `torneios_servico`
    ("primeiro-torneio", "Primeiro torneio", "Terminou o primeiro torneio", "comunidade", "comum", "🎟️", "torneios_jogados", 1, False, False),
    ("podio-torneio", "Pódio", "Ficou entre os 3 primeiros de um torneio", "comunidade", "rara", "🎖️", "torneios_podio", 1, False, False),
    ("campeao-torneio", "Campeão", "Venceu um torneio", "comunidade", "epica", "🏆", "torneios_vencidos", 1, False, False),
]
CAMPOS = ("codigo", "nome", "descricao", "categoria", "raridade", "icone", "metrica", "meta", "secreta", "por_tempo")


def sincronizar():
    """Grava ou atualiza o catálogo pelo código, sem duplicar nem tocar nas conquistas do admin (GAM-02)."""
    from .models import Conquista, db

    existentes = {c.codigo: c for c in Conquista.query}
    criadas = atualizadas = 0
    for linha in CATALOGO:
        dados = dict(zip(CAMPOS, linha))
        c = existentes.get(dados["codigo"])
        if c is None:
            db.session.add(Conquista(**dados))
            criadas += 1
        elif not c.do_admin:
            for campo, valor in dados.items():
                setattr(c, campo, valor)
            atualizadas += 1
    db.session.commit()
    return criadas, atualizadas
