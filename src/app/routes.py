import importlib

MODULES = ["auth", "catalogo", "busca", "jogo", "avaliacoes", "comentarios", "listas", "social", "recomendacoes", "afinidade", "api", "estatisticas", "admin", "diario", "diario_import", "retrospectiva", "listas_inteligentes", "notificacoes", "conquistas", "progressao"]


def register(app):
    for name in MODULES:
        try:
            mod = importlib.import_module(f"{__package__}.{name}")
        except ModuleNotFoundError as e:
            if e.name != f"{__package__}.{name}":
                raise
            continue  # parte ainda não implementada
        app.register_blueprint(mod.bp)
