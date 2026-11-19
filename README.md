# GameLog — AP2 DSAI 2026

Catálogo de jogos com avaliações, listas/status (wishlist = "planejado"), feed social, recomendações e painel de moderação.
Desenvolvido com **Spec-Driven Development**: cada parte tem uma spec datada em [`SPEC/`](SPEC/), commitada antes do código.

- **Produção:** <https://gamelog-92zh.onrender.com/> (Render, plano gratuito: depois de um tempo parado, a primeira visita pode levar ~1 min para acordar o serviço).
- **Stack:** Python 3.12+, Flask 3, Flask-SQLAlchemy (SQLite), Jinja2, pytest, gunicorn. Sem JS de build, sem serviços externos.

## Como rodar

Atalho: `./dev.sh` (cria venv, instala, faz seed e sobe em http://127.0.0.1:5000; admin `admin@gamelog.local` / `admin12345`, só local). `./dev.sh test` roda os testes; `./dev.sh reset` recria o banco.

Manual:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cd src
export ADMIN_EMAIL=admin@example.com ADMIN_PASSWORD=<senha>   # ver .env.example
flask --app app seed        # 153 jogos reais (snapshot da Steam), 30 usuários fictícios com avaliações em ~30 jogos (senha: senha1234)
flask --app app run
cd .. && pytest             # 270 testes, um ou mais por critério de aceitação
```

O catálogo vem de `src/app/dados/jogos.json` (gerado com `flask --app app export-games --termos "Nome 1;Nome 2" --saida src/app/dados/jogos.json`). API pública somente leitura em `/api` (jogos, detalhe e avaliações, em JSON). Pelo painel, o admin escolhe a fonte, busca e importa jogos em `/admin/importar`, um a um ou em lote (até 10 por envio). Pela linha de comando: `flask --app app import-games [--fonte steam] [--limite 40] [--termo nome]` (API pública da Steam, sem chave). Para manter o catálogo atualizado, `flask --app app sync-games [--fonte steam] [--limite 40] [--termo nome]` cria jogos novos e corrige os metadados dos existentes sem duplicar (saída `novos=N atualizados=M ignorados=I falhas=F`); o agendamento é externo ao processo web (cron do host ou cron job do Render, uma vez por dia), nunca em thread/processo dentro do Flask. Para somar outra fonte (RAWG, IGDB), crie `src/app/fontes/<nome>.py` com `listar(limite, termo)` (e `buscar`/`detalhe` para aparecer no painel), faça o HTTP por `fontes/base.py` (novas tentativas, User-Agent do GameLog, timeout em `FONTES_TIMEOUT`, padrão 15 s) e registre em `FONTES`. Cada jogo guarda em quais fontes e lojas está na tabela `JogoFonte` (Steam, GOG e Epic; a Epic entra só como loja vinculada, issue #9).

**Idade (ECA Digital):** o cadastro pede a data de nascimento e recusa menores de 12 anos. A conta de 12 a 15 anos fica em `/conta/pendente` até o vínculo com um responsável, e a conta antiga sem data informa a data em `/conta/nascimento` no próximo login, inclusive a de admin. A data nunca aparece no site nem na API: o código consulta só `menores.idade.faixa_etaria(user)`. Em `/conta/idade`, o usuário pede nova verificação. Com `VERIFICACAO_PROVEDOR=simulado` (só para teste), aparece um provedor confiável simulado, que aceita os códigos `ADULTO`, `ADOLESCENTE`, `VINCULADO` e `CRIANCA`. Em `/admin/usuarios`, o admin vê a faixa de cada conta e contesta a idade.

**Banco local e mudanças de esquema:** o projeto não tem ferramenta de migração (`db.create_all()` cria tabelas novas, mas não altera as existentes). Quando uma spec mudar colunas de uma tabela que já existe, recrie o banco local com `./dev.sh reset` (a verificação de idade acrescentou colunas em `user`). No Render o disco é efêmero e o banco é recriado a cada deploy.

Deploy: `render.yaml` em <https://gamelog-92zh.onrender.com/> (Render, plano gratuito; o seed reproduzível recria o banco a cada start).
Variáveis: `SECRET_KEY` (gerada), `ADMIN_EMAIL`, `ADMIN_PASSWORD`; opcional `FONTES_TIMEOUT` (segundos por tentativa nas fontes, padrão 15).

## Commits

Todo commit precisa terminar com os trailers `Agent:` e `Spec:` (use `none` para trabalho manual ou infra/docs):

```text
feat(comentarios): resposta a comentários

Agent: claude-code/claude-opus-5-5
Spec: SPEC/2026-11-07-comentarios.md
```

O hook `.githooks/commit-msg` recusa commits sem eles e exige que uma spec nova seja commitada sozinha, antes do código.
O `./dev.sh` ativa o hook; sem ele, rode `git config core.hooksPath .githooks` uma vez no clone.

## Specs → código → testes

| Spec | Código | Testes |
|---|---|---|
| cadastro-e-login (CAD-01..10) | `src/app/auth.py` | `tests/test_auth.py` |
| catalogo (CAT-01..09) | `models.py`, `seed.py`, `catalogo.py` | `tests/test_catalogo.py` |
| busca-e-filtros (BUS-01..09) | `busca.py` | `tests/test_busca.py` |
| pagina-do-jogo (PAG-01..10) | `jogo.py` | `tests/test_jogo.py` |
| avaliacoes (AVA-01..11) | `avaliacoes.py` | `tests/test_avaliacoes.py` |
| listas (LIS-01..11) | `listas.py` | `tests/test_listas.py` |
| social (SOC-01..10) | `social.py` | `tests/test_social.py` |
| recomendacoes (REC-01..09) | `recomendacoes.py` | `tests/test_recomendacoes.py` |
| painel-admin (ADM-01..10) | `admin.py` | `tests/test_admin.py` |
| importacao-steam (IMP-01..09) | `fontes/`, `cli.py` | `tests/test_importacao.py` |
| botões (BTN-01..11) | `static/style.css`, `templates/base.html` | sem testes automatizados (spec só visual) |
| api-json (API-01..10) | `api.py` | `tests/test_api.py` |
| capas-verticais (CAP-01..05) | `fontes/steam.py`, `dados/jogos.json` | `tests/test_capas.py` |
| recomendacoes-colaborativas (COL-01..08) | `recomendacoes.py` | `tests/test_recomendacoes_colab.py` |
| comparar-gosto (AFI-01..10) | `afinidade.py`, `templates/afinidade/` | `tests/test_afinidade.py` |
| comentarios (COM-01..06) | `comentarios.py`, `models.py` (`Comment`), `templates/_macros.html` | `tests/test_comentarios.py` |
| estatisticas (EST-01..06) | `estatisticas.py`, `templates/estatisticas/` | `tests/test_estatisticas.py` |
| perfil-rico (PRF-01..09) | `perfil.py`, `auth.py`, `templates/auth/perfil.html` | `tests/test_perfil.py` |
| importar-pelo-admin (IAD-01..09) | `admin.py`, `fontes/steam.py`, `templates/admin/importar.html` | `tests/test_importar_admin.py` |
| catalogo-real (CRE-01..09) | `seed.py`, `fontes/arquivo.py`, `dados/jogos.json` | `tests/test_catalogo_real.py` |
| fontes-multiloja (FNT-01..12) | `fontes/base.py`, `fontes/__init__.py`, `models.py` (`JogoFonte`), `admin.py`, `templates/admin/importar.html` | `tests/test_fontes_base.py`, `tests/test_fontes_multiloja.py`, `tests/test_fontes_admin.py` |
