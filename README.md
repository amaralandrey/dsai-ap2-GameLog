# GameLog — AP2 DSAI 2026

Catálogo de jogos com avaliações, listas/status (wishlist = "planejado"), feed social, recomendações e painel de moderação.
Desenvolvido com **Spec-Driven Development**: cada parte tem uma spec datada em [`SPEC/`](SPEC/), commitada antes do código.

- **Produção:** <https://gamelog-92zh.onrender.com/> (Render, plano gratuito: depois de um tempo parado, a primeira visita pode levar ~1 min para acordar o serviço).
- **Stack:** Python 3.12+, Flask 3, Flask-SQLAlchemy (SQLite), Jinja2, pytest, gunicorn. Sem JS de build; as fontes externas (Steam, GOG, RAWG) só são chamadas pelos comandos de importação e pelo painel do admin.

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
cd .. && pytest             # 526 testes, um ou mais por critério de aceitação
```

O catálogo vem de `src/app/dados/jogos.json` (gerado com `flask --app app export-games --termos "Nome 1;Nome 2" --saida src/app/dados/jogos.json`). API pública somente leitura em `/api` (jogos, detalhe e avaliações, em JSON). Pelo painel, o admin escolhe a fonte (Steam, GOG ou RAWG), busca e importa jogos em `/admin/importar`, um a um ou em lote (até 10 por envio). Pela linha de comando: `flask --app app import-games [--fonte steam|gog|rawg|todas] [--limite 40] [--termo nome]` (Steam e GOG por APIs públicas, sem chave; a RAWG exige chave, veja abaixo). Para manter o catálogo atualizado, `flask --app app sync-games [--fonte steam|gog|rawg|todas] [--limite 40] [--termo nome]` cria jogos novos e corrige os metadados dos existentes sem duplicar (saída `novos=N atualizados=M ignorados=I falhas=F`); `--fonte todas` roda cada fonte em sequência (menos o snapshot `arquivo`), com uma linha por fonte (`gog: novos=...`), e a falha de uma não impede as seguintes. Se a fonte responder HTTP 429 mesmo depois das novas tentativas, o lote para, o que já foi gravado fica e a linha ganha `interrompido=rate-limit`. O agendamento é externo ao processo web (cron do host ou cron job do Render, uma vez por dia), nunca em thread/processo dentro do Flask. Para somar outra fonte (IGDB, por exemplo), crie `src/app/fontes/<nome>.py` com `listar(limite, termo)` (e `buscar`/`detalhe` para aparecer no painel), faça o HTTP por `fontes/base.py` (novas tentativas, User-Agent do GameLog, timeout em `FONTES_TIMEOUT`, padrão 15 s) e registre em `FONTES`. Gêneros, plataformas e datas de todas as fontes passam por `fontes/normalizar.py` ("Role-playing" vira "RPG", "Win" e "PC" viram "Windows"; jogo com só o ano de lançamento mostra só o ano). Cada jogo guarda em quais fontes e lojas está na tabela `JogoFonte` (Steam, GOG e Epic; a Epic entra só como loja vinculada, com os links que a RAWG informa, issue #9), e `/buscar?loja=gog` (ou `/api/jogos?loja=gog`) filtra por loja.

**Jogo repetido entre fontes:** ao importar ou sincronizar, um item cujo link de loja já pertence a um jogo, ou muito parecido com um jogo do catálogo (semelhança ≥ 90 de 100, pelo título sem acentos nem sufixos como "GOTY" e "Definitive Edition", pelo ano e pela desenvolvedora), não vira jogo novo: ganha o vínculo da fonte no jogo existente (`src/app/catalogo_merge.py`). Só a fonte de origem do jogo atualiza os metadados dele; a fonte ligada depois não troca título nem sinopse. Quando a semelhança fica entre 70 e 89, o jogo é criado e uma pendência vai para `/admin/catalogo/duplicatas`, onde o admin escolhe o jogo principal e mescla, ou marca o par como diferente (ele não volta). Para comparar o catálogo que já existe: `flask --app app sugerir-duplicatas` (saída `pendencias=N`, sem mesclar nada). A mesclagem leva para o principal avaliações (com curtidas, comentários e denúncias), status, itens de lista, atividades, dispensas de recomendação e lojas; quando o mesmo usuário tem registro nos dois, o mais recente fica no principal e o outro continua no jogo mesclado, que sai do catálogo por soft delete. Nada é apagado, e o endereço antigo (`/jogos/<slug>` e `/api/jogos/<slug>`) responde 301 para o principal. A página do jogo mostra "Disponível em" com o link de cada loja, e `/api/jogos/<slug>` traz `fontes`.

**Lojas no snapshot do seed:** o `seed` cria os vínculos de GOG, Epic e RAWG gravados no próprio `src/app/dados/jogos.json` (campos opcionais `lojas` e `origens`), então a URL pública mostra as três lojas mesmo sem `RAWG_API_KEY` no servidor. Para regenerar esses campos, rode localmente, com a chave no `.env`, `flask --app app enriquecer-snapshot`. O comando procura cada jogo na RAWG e só aceita o resultado cujo link da Steam tem o mesmo id; a saída é `jogos=N vinculados=M sem_correspondencia=K falhas=F`. Depois disso o snapshot volta a ser só um arquivo de dados versionado.

**Chave da RAWG:** a RAWG é a única fonte com chave (gratuita, em <https://rawg.io/apidocs>). Localmente, coloque `RAWG_API_KEY=<sua chave>` no arquivo `.env` na raiz do repositório (copie de `.env.example`; o `.env` está no `.gitignore` e nunca é commitado). O `./dev.sh` carrega o `.env` sozinho; no fluxo manual, rode `set -a; . ./.env; set +a` na raiz, antes do `cd src`. No Render, a variável `RAWG_API_KEY` é declarada no `render.yaml` com `sync: false` e o valor é preenchido só no painel do serviço. Sem a chave, a RAWG fica desligada sem erro (some do painel; `--fonte rawg` avisa `RAWG_API_KEY não configurada` e sai com código 1; em `--fonte todas` ela é pulada). A chave nunca vai para o banco, telas ou saídas dos comandos. Os jogos com dados da RAWG mostram "Dados fornecidos por RAWG" com link para o site, como pedem os termos da API.

**Banco local e mudanças de esquema:** o projeto não tem ferramenta de migração (`db.create_all()` cria tabelas novas, mas não altera as existentes). Quando uma spec do Épico 2 mudar colunas, recrie o banco local com `./dev.sh reset` (a deduplicação acrescentou `game.mesclado_em_id` e a tabela `pendencia_merge`). No Render o disco é efêmero e o banco é recriado a cada deploy.

Deploy: `render.yaml` em <https://gamelog-92zh.onrender.com/> (Render, plano gratuito; o seed reproduzível recria o banco a cada start).
Variáveis: `SECRET_KEY` (gerada), `ADMIN_EMAIL`, `ADMIN_PASSWORD`; opcionais `FONTES_TIMEOUT` (segundos por tentativa nas fontes, padrão 15) e `RAWG_API_KEY` (liga a fonte RAWG).

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
| conectores-gog-rawg (GOG-01..15) | `fontes/gog.py`, `fontes/rawg.py`, `fontes/normalizar.py`, `fontes/__init__.py`, `sync.py`, `cli.py`, `busca.py`, `templates/jogo/pagina.html`, `templates/busca/buscar.html` | `tests/test_conectores_gog.py`, `tests/test_conectores_rawg.py`, `tests/test_conectores_normalizar.py`, `tests/test_conectores_lojas.py` |
| deduplicacao-catalogo (MRG-01..15) | `catalogo_merge.py`, `models.py` (`PendenciaMerge`, `Game.mesclado_em_id`), `fontes/__init__.py`, `sync.py`, `cli.py`, `admin.py`, `jogo.py`, `api.py`, `templates/admin/duplicatas.html`, `templates/jogo/pagina.html` | `tests/test_merge_semelhanca.py`, `tests/test_merge_mesclagem.py`, `tests/test_merge_pagina.py` |
| snapshot-multiloja (SNP-01..09) | `snapshot.py`, `fontes/__init__.py` (`origens`), `cli.py`, `dados/jogos.json` | `tests/test_snapshot_multiloja.py` |
