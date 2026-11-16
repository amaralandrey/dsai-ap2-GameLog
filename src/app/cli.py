import os

import click
from werkzeug.security import generate_password_hash

from .models import User, db


def create_admin(email, password, username="admin"):
    """ADM-10: credenciais vêm de variável de ambiente, nunca do código."""
    if User.query.filter_by(email=email.lower()).first():
        return False
    db.session.add(
        User(
            username=username,
            username_lower=username.lower(),
            email=email.lower(),
            password_hash=generate_password_hash(password),
            display_name="Administrador",
            role="admin",
        )
    )
    db.session.commit()
    return True


def _nomes_das_fontes(fonte):
    """`todas` roda cada fonte registrada, menos o snapshot `arquivo` (GOG-10)."""
    from .fontes import FONTES

    if fonte == "todas":
        return [n for n in FONTES if n != "arquivo"]
    if fonte not in FONTES:
        raise click.UsageError(f"fonte desconhecida; use uma de {sorted(FONTES)} ou todas")
    return [fonte]


def _rodar_uma(nome, executar, campos):
    """Linha de resumo de uma fonte e se ela falhou; a chave da RAWG nunca chega aqui (GOG-05)."""
    from .fontes import FONTES, Interrompido, indisponivel

    if motivo := indisponivel(FONTES[nome]):
        return f"erro={motivo}", True
    extra = ""
    try:
        contagens = executar(nome)
    except Interrompido as exc:  # 429 persistente: fica o que já foi gravado (GOG-11)
        contagens, extra = exc.parcial, f" interrompido={exc.motivo}"
    except Exception as exc:  # fonte fora do ar ou resposta inesperada: sem traceback
        return f"erro=fonte indisponivel: {str(exc).strip() or exc.__class__.__name__}", True
    return " ".join(f"{c}={n}" for c, n in zip(campos, contagens)) + extra, False


def rodar(fonte, executar, campos):
    """Roda uma fonte ou todas, em sequência; a falha de uma não impede as seguintes (GOG-04, GOG-10).

    Com uma fonte só, falha ou falta de chave termina com código 1; em `todas`, a fonte sem chave fica
    desligada sem erro (GOG-07) e só uma falha de verdade muda o código de saída.
    """
    from .fontes import FONTES, indisponivel

    nomes, todas, falhou = _nomes_das_fontes(fonte), fonte == "todas", False
    for nome in nomes:
        linha, erro = _rodar_uma(nome, executar, campos)
        click.echo(f"{nome}: {linha}" if todas else linha)
        falhou = falhou or (erro and not (todas and indisponivel(FONTES[nome])))
    if falhou:
        raise click.exceptions.Exit(1)


def register(app):
    @app.cli.command("seed")
    @click.option("--seed", default=42)
    def seed_cmd(seed):
        """Popula o banco com dados fictícios reproduzíveis."""
        from .seed import run

        run(seed, real=True)
        click.echo("seed ok")
        email, pw = os.environ.get("ADMIN_EMAIL"), os.environ.get("ADMIN_PASSWORD")
        if email and pw and create_admin(email, pw):
            click.echo("admin criado")

    @app.cli.command("create-admin")
    def create_admin_cmd():
        email, pw = os.environ.get("ADMIN_EMAIL"), os.environ.get("ADMIN_PASSWORD")
        if not (email and pw):
            raise click.UsageError("defina ADMIN_EMAIL e ADMIN_PASSWORD")
        click.echo("admin criado" if create_admin(email, pw) else "admin já existe")

    @app.cli.command("import-games")
    @click.option("--fonte", default="steam", help="uma fonte de FONTES ou `todas`")
    @click.option("--limite", default=40)
    @click.option("--termo", default=None)
    def import_games_cmd(fonte, limite, termo):
        """Importa jogos reais de uma fonte externa (padrão: Steam, sem chave)."""
        from .fontes import importar

        rodar(fonte, lambda nome: importar(nome, limite, termo), ("importados", "ignorados", "falhas"))

    @app.cli.command("sync-games")
    @click.option("--fonte", default="steam", help="uma fonte de FONTES ou `todas`")
    @click.option("--limite", default=40)
    @click.option("--termo", default=None)
    def sync_games_cmd(fonte, limite, termo):
        """Sincroniza o catálogo com uma fonte externa (idempotente; pensado para cron)."""
        from .sync import sincronizar

        rodar(fonte, lambda nome: sincronizar(nome, limite, termo), ("novos", "atualizados", "ignorados", "falhas"))

    @app.cli.command("sugerir-duplicatas")
    def sugerir_duplicatas_cmd():
        """Registra pendências de mesclagem entre jogos parecidos do catálogo, sem mesclar nada (MRG-04)."""
        from .catalogo_merge import sugerir_duplicatas

        click.echo(f"pendencias={sugerir_duplicatas()}")

    @app.cli.command("export-games")
    @click.option("--termos", required=True, help="nomes separados por ;, um jogo por nome")
    @click.option("--saida", required=True)
    def export_games_cmd(termos, saida):
        """Gera o snapshot de jogos reais (fonte `arquivo`) a partir da Steam."""
        import json

        from .fontes import steam

        jogos, faltaram = {}, 0
        for t in filter(None, (x.strip() for x in termos.split(";"))):
            j = next((x for x in steam.listar(3, t) if "erro" not in x and x["release_date"]), None)
            if j:
                jogos[j["external_id"]] = {**j, "release_date": j["release_date"].isoformat()}
            else:
                faltaram += 1
                click.echo(f"sem resultado: {t}")
        with open(saida, "w", encoding="utf-8") as f:
            json.dump(list(jogos.values()), f, ensure_ascii=False, indent=1)
        click.echo(f"jogos={len(jogos)} sem_resultado={faltaram}")
