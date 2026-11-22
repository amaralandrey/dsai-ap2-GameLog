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
flask --app app seed        # 153 jogos reais (snapshot da Steam), 30 usuários fictícios com avaliações em ~30 jogos (senha: senha1234; em produção, aleatória)
flask --app app run
cd .. && pytest             # 767 testes, um ou mais por critério de aceitação
```

O catálogo vem de `src/app/dados/jogos.json` (gerado com `flask --app app export-games --termos "Nome 1;Nome 2" --saida src/app/dados/jogos.json`). API pública somente leitura em `/api` (jogos, detalhe e avaliações, em JSON). Pelo painel, o admin escolhe a fonte (Steam, GOG ou RAWG), busca e importa jogos em `/admin/importar`, um a um ou em lote (até 10 por envio). Pela linha de comando: `flask --app app import-games [--fonte steam|gog|rawg|todas] [--limite 40] [--termo nome]` (Steam e GOG por APIs públicas, sem chave; a RAWG exige chave, veja abaixo). Para manter o catálogo atualizado, `flask --app app sync-games [--fonte steam|gog|rawg|todas] [--limite 40] [--termo nome]` cria jogos novos e corrige os metadados dos existentes sem duplicar (saída `novos=N atualizados=M ignorados=I falhas=F`); `--fonte todas` roda cada fonte em sequência (menos o snapshot `arquivo`), com uma linha por fonte (`gog: novos=...`), e a falha de uma não impede as seguintes. Se a fonte responder HTTP 429 mesmo depois das novas tentativas, o lote para, o que já foi gravado fica e a linha ganha `interrompido=rate-limit`. A execução diária é o agendamento `sync_games` da fila de jobs (abaixo), nunca uma thread ou processo dentro do Flask. Para somar outra fonte (IGDB, por exemplo), crie `src/app/fontes/<nome>.py` com `listar(limite, termo)` (e `buscar`/`detalhe` para aparecer no painel), faça o HTTP por `fontes/base.py` (novas tentativas, User-Agent do GameLog, timeout em `FONTES_TIMEOUT`, padrão 15 s) e registre em `FONTES`. Gêneros, plataformas e datas de todas as fontes passam por `fontes/normalizar.py` ("Role-playing" vira "RPG", "Win" e "PC" viram "Windows"; jogo com só o ano de lançamento mostra só o ano). Cada jogo guarda em quais fontes e lojas está na tabela `JogoFonte` (Steam, GOG e Epic; a Epic entra só como loja vinculada, com os links que a RAWG informa, issue #9), e `/buscar?loja=gog` (ou `/api/jogos?loja=gog`) filtra por loja.

**Jobs em segundo plano:** o trabalho fora da requisição (sincronização do catálogo e, nas próximas specs, preços, e-mail, expurgo e backup) vai para uma fila gravada no SQLite. `flask --app app jobs agendar` cria os agendamentos padrão (`sync_games` às 04:00 de Belém; `enriquecer_snapshot` semanal, desligado). `/admin/jobs` mostra a fila, tenta de novo, cancela e liga ou desliga agendamentos. O processo web não tem agendador; o ciclo é disparado de fora, de um destes dois jeitos:

- **Host próprio:** o cron do host chama `flask --app app jobs rodar --uma-vez` (ou deixe `flask --app app jobs rodar` rodando, que repete a cada 30 s e para no SIGTERM).
- **Render (plano gratuito):** o serviço `cron` do Render não enxerga o disco do serviço web. Configure `JOBS_TOKEN` no painel e um cron HTTP externo (ex.: cron-job.org) que faça, a cada 15 minutos, `POST https://<app>/jobs/ciclo` com o cabeçalho `Authorization: Bearer <JOBS_TOKEN>`. A resposta é `{"executados", "ok", "falhas", "mortos"}`. Sem `JOBS_TOKEN`, a rota responde 404. Cada chamada roda até 10 jobs, mas não pega um novo depois de 20 s; o resto fica para a chamada seguinte. Um job sozinho pode passar disso (o `sync_games` leva alguns minutos no pior caso), por isso o gunicorn sobe com `--timeout 600`. Se o cron HTTP desistir de esperar a resposta, o ciclo continua no servidor.

**E-mail:** todo e-mail passa por `enviar_email()` e vai para a caixa de saída (`EmailSaida`), enviada pelo job `enviar_email`. Com `SMTP_HOST`, o envio usa `smtplib` (STARTTLS, ou SSL na porta 465) com `SMTP_USER`/`SMTP_PASSWORD` e remetente `EMAIL_REMETENTE`; sem ele (dev, testes), o e-mail é escrito no log. `URL_PUBLICA` é a base dos links dos e-mails. Os modelos ficam em `templates/email/<modelo>.txt` e `.html`. O "por e-mail" de cada notificação é o de `/notificacoes/preferencias`; os e-mails não transacionais têm link de descadastro assinado (`/email/descadastro/<token>`) e `List-Unsubscribe`. O resumo semanal (segunda às 08:00) só sai com o consentimento `email_resumo`, que chega com a spec de consentimentos: até lá ninguém recebe. `/admin/emails` mostra a caixa de saída, e `flask --app app email teste --para <endereço>` valida a configuração (`backend=console|smtp status=...`).

**Jogo repetido entre fontes:** ao importar ou sincronizar, um item cujo link de loja já pertence a um jogo, ou muito parecido com um jogo do catálogo (semelhança ≥ 90 de 100, pelo título sem acentos nem sufixos como "GOTY" e "Definitive Edition", pelo ano e pela desenvolvedora), não vira jogo novo: ganha o vínculo da fonte no jogo existente (`src/app/catalogo_merge.py`). Só a fonte de origem do jogo atualiza os metadados dele; a fonte ligada depois não troca título nem sinopse. Quando a semelhança fica entre 70 e 89, o jogo é criado e uma pendência vai para `/admin/catalogo/duplicatas`, onde o admin escolhe o jogo principal e mescla, ou marca o par como diferente (ele não volta). Para comparar o catálogo que já existe: `flask --app app sugerir-duplicatas` (saída `pendencias=N`, sem mesclar nada). A mesclagem leva para o principal avaliações (com curtidas, comentários e denúncias), status, itens de lista, atividades, dispensas de recomendação e lojas; quando o mesmo usuário tem registro nos dois, o mais recente fica no principal e o outro continua no jogo mesclado, que sai do catálogo por soft delete. Nada é apagado, e o endereço antigo (`/jogos/<slug>` e `/api/jogos/<slug>`) responde 301 para o principal. A página do jogo mostra "Disponível em" com o link de cada loja, e `/api/jogos/<slug>` traz `fontes`.

**Lojas no snapshot do seed:** o `seed` cria os vínculos de GOG, Epic e RAWG gravados no próprio `src/app/dados/jogos.json` (campos opcionais `lojas` e `origens`), então a URL pública mostra as três lojas mesmo sem `RAWG_API_KEY` no servidor. Para regenerar esses campos, rode localmente, com a chave no `.env`, `flask --app app enriquecer-snapshot`. O comando procura cada jogo na RAWG e só aceita o resultado cujo link da Steam tem o mesmo id; a saída é `jogos=N vinculados=M sem_correspondencia=K falhas=F`. Depois disso o snapshot volta a ser só um arquivo de dados versionado.

**Chave da RAWG:** a RAWG é a única fonte com chave (gratuita, em <https://rawg.io/apidocs>). Localmente, coloque `RAWG_API_KEY=<sua chave>` no arquivo `.env` na raiz do repositório (copie de `.env.example`; o `.env` está no `.gitignore` e nunca é commitado). O `./dev.sh` carrega o `.env` sozinho; no fluxo manual, rode `set -a; . ./.env; set +a` na raiz, antes do `cd src`. No Render, a variável `RAWG_API_KEY` é declarada no `render.yaml` com `sync: false` e o valor é preenchido só no painel do serviço. Sem a chave, a RAWG fica desligada sem erro (some do painel; `--fonte rawg` avisa `RAWG_API_KEY não configurada` e sai com código 1; em `--fonte todas` ela é pulada). A chave nunca vai para o banco, telas ou saídas dos comandos. Os jogos com dados da RAWG mostram "Dados fornecidos por RAWG" com link para o site, como pedem os termos da API.

**Diário e acompanhamento (Épico 3):** na página de cada jogo, "Registrar sessão no diário" grava data, duração ("1h30", "90" ou "1:30"), início, plataforma, anotação e "zerei". O diário fica em `/diario` e em `/u/<usuario>/diario`. Registrar sessão de um jogo planejado o marca como "jogando", e "zerei" grava `Status.zerado_em` e muda o status para "jogado". Todo dia, semana e mês é contado no fuso de Belém (`src/app/tempo.py`). Na edição do perfil dá para torná-lo privado: só quem o dono segue (e os admins) vê diário, avaliações, listas, seguidores e retrospectiva. A regra única é `diario.pode_ver_perfil`. Em `/diario/importar` entram as horas da Steam (com a conta vinculada no perfil e `STEAM_API_KEY` no servidor) e um CSV (`data,jogo,duracao` e opcionais `inicio,plataforma,zerei,anotacao`), com prévia antes de gravar. `/diario/exportar.csv` baixa o diário no mesmo formato. `/u/<usuario>/retrospectiva/<ano>` mostra o ano em jogos com gráficos SVG gerados no servidor (`graficos_svg.py`, sem JavaScript), e o perfil ganha a linha das horas das últimas 12 semanas. Em `/listas/inteligentes/nova` entram listas calculadas por regra: largados, wishlist numa loja, zerados no ano, por gênero, mais jogados e jogados sem avaliação. Toda lista mostra a barra de progresso, exporta em JSON e CSV e, para o dono, aceita arrastar e soltar (`static/ordem.js`).

**Engajamento e comunidade (Épico 4):** o sino no cabeçalho leva a `/notificacoes`, com curtidas, comentários, novos seguidores, conquistas, subida de nível e "quem você segue zerou um jogo da sua wishlist". `/notificacoes/preferencias` liga e desliga cada tipo. As conquistas são um catálogo no banco (`flask --app app conquistas sincronizar` grava as 48 do catálogo inicial sem duplicar). O motor `gamificacao.emitir_evento` as avalia depois de cada ação e as desbloqueia uma única vez, e `flask --app app conquistas recalcular [--usuario nome]` reavalia o histórico. O `flask seed` roda os dois a cada subida, então as três conquistas da AP1 viram registros no primeiro deploy. Cada usuário escolhe até 3 conquistas para a estante do perfil, e `/conquistas` mostra a porcentagem de jogadores que têm cada uma. O XP vem de avaliações, sessões do diário, jogos zerados e conquistas, com teto de 150 XP por dia nas ações repetíveis. A sequência é semanal, com até 2 congelamentos, e o extrato fica em `/u/<usuario>/xp`. `flask --app app progressao avisar-sequencias` manda o aviso de domingo. Em `/rankings` ficam XP, zerados, horas e curtidas por temporada trimestral (escopos "quem eu sigo", "global" só com perfis públicos, e "clube"). `flask --app app rankings abrir-temporada` e `encerrar-temporada <slug>` abrem e fecham as temporadas, e o encerramento grava o retrato e premia o pódio. `/clubes` reúne clubes públicos e privados com jogo do mês, metas coletivas medidas pelo diário, ranking interno e mural moderado. No painel, o admin ajusta conquistas (`/admin/conquistas`), XP (`/admin/xp`), temporadas e desclassificações (`/admin/rankings`) e clubes (`/admin/clubes`), sempre com registro na auditoria.

**Chave da Steam (horas jogadas):** a importação de horas usa `IPlayerService/GetOwnedGames`, que exige uma chave da Web API (<https://steamcommunity.com/dev/apikey>). Coloque `STEAM_API_KEY=<sua chave>` no `.env`, como a da RAWG. No Render ela fica no `render.yaml` com `sync: false`. Sem a chave, o botão some e a rota responde "Importação da Steam indisponível neste servidor", sem chamar a rede.

**Idade (ECA Digital):** o cadastro pede a data de nascimento e recusa menores de 12 anos. A conta de 12 a 15 anos fica em `/conta/pendente` até o vínculo com um responsável, e a conta antiga sem data informa a data em `/conta/nascimento` no próximo login, inclusive a de admin. A data nunca aparece no site nem na API: o código consulta só `menores.idade.faixa_etaria(user)`. Em `/conta/idade`, o usuário pede nova verificação. Com `VERIFICACAO_PROVEDOR=simulado` (só para teste), aparece um provedor confiável simulado, que aceita os códigos `ADULTO`, `ADOLESCENTE`, `VINCULADO` e `CRIANCA`. Em `/admin/usuarios`, o admin vê a faixa de cada conta, contesta a idade e retira a contestação. Quando a conta pendente pede a revisão da data ("Pedir revisão da data"), ou quando a conta foi suspensa por uma data autodeclarada, o admin pode pedir nova data: a data é apagada, e o usuário a informa de novo. Nenhuma dessas ações vale para a própria conta do admin. A espera por responsável acaba sozinha no aniversário de 16 anos.

**Banco local e mudanças de esquema:** o projeto não tem ferramenta de migração (`db.create_all()` cria tabelas novas, mas não altera as existentes). Quando uma spec mudar colunas de uma tabela que já existe, recrie o banco local com `./dev.sh reset`; sem isso, o app quebra com "no such column". Exemplos: a deduplicação acrescentou `game.mesclado_em_id`; o Épico 4 acrescentou `like.created_at` e colunas em `report`, e passou `review` e `sessao_jogo` a usar `AUTOINCREMENT`; a verificação de idade acrescentou colunas em `user`; a política de menores acrescentou `follow.aprovado_em` (os follows existentes contam como aprovados). No Render o disco é efêmero e o banco é recriado a cada deploy.

Deploy: `render.yaml` em <https://gamelog-92zh.onrender.com/> (Render, plano gratuito; o seed reproduzível recria o banco a cada start).
Variáveis: `SECRET_KEY` (gerada), `GAMELOG_PRODUCAO=1` (contas do seed com senha aleatória, cookie de sessão `Secure` e boot recusado sem `SECRET_KEY`), `ADMIN_EMAIL`, `ADMIN_PASSWORD`; opcionais `FONTES_TIMEOUT` (segundos por tentativa nas fontes, padrão 15), `RAWG_API_KEY` (liga a fonte RAWG), `STEAM_API_KEY` (liga a importação de horas da Steam), `JOBS_TOKEN` (liga `POST /jobs/ciclo`), `URL_PUBLICA` (base dos links nos e-mails) e `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_REMETENTE` (envio de e-mail; sem `SMTP_HOST`, o e-mail vai para o log).

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
| diario-de-jogatina (DIA-01..14) | `tempo.py`, `diario.py`, `models.py` (`SessaoJogo`, `Status.zerado_em`, `User.perfil_privado`), `templates/diario/` | `tests/test_diario.py` |
| importacao-horas-diario (HRS-01..12) | `diario_import.py`, `diario.py` (`consistencia`), `auth.py` (`steam_id`) | `tests/test_importacao_horas.py` |
| retrospectiva-graficos (RET-01..12) | `graficos_svg.py`, `retrospectiva.py`, `templates/retrospectiva/` | `tests/test_retrospectiva.py` |
| listas-inteligentes (LSA-01..12) | `listas_inteligentes.py`, `serializadores.py`, `static/ordem.js`, `templates/listas/` | `tests/test_listas_inteligentes.py` |
| notificacoes (NTF-01..12) | `notificacoes.py`, `models.py` (`Notificacao`, `PreferenciaNotificacao`), `templates/notificacoes/` | `tests/test_notificacoes.py` |
| motor-conquistas (GAM-01..15) | `gamificacao.py`, `conquistas_catalogo.py`, `conquistas.py`, `menores/politica.py` (`pode`), `cli.py` | `tests/test_gamificacao.py`, `tests/test_perfil.py` (`test_con*`) |
| niveis-xp-sequencias (PRG-01..12) | `progressao.py`, `models.py` (`PerfilProgressao`, `ExtratoXP`), `templates/progressao/` | `tests/test_progressao.py` |
| rankings-temporadas (RNK-01..12) | `rankings.py`, `models.py` (`Temporada`, `SnapshotRanking`, `Desclassificacao`, `Like.created_at`), `templates/rankings/` | `tests/test_rankings.py` |
| clubes (CLB-01..14) | `clubes.py`, `models.py` (`Clube`, `MembroClube`, `MetaClube`, `PostClube`, `Report.alvo_tipo`), `templates/clubes/` | `tests/test_clubes.py` |
| fila-de-jobs (JOB-01..12) | `jobs.py`, `cron.py`, `models.py` (`Job`, `Agendamento`), `templates/admin/jobs.html` | `tests/test_jobs.py` |
| chaveamento (CHV-01..14) | `chaveamento.py`, `graficos_svg.py` (`chave`) | `tests/test_chaveamento.py` |
| politica-de-menores (PMN-01..14) | `menores/politica.py` (`pode`, `ACOES`), `menores/idade.py` (`sql_menor`), `models.py` (`Follow.aprovado_em`), `social.py` (pedidos), `diario.py`, `templates/privacidade/menores.html` | `tests/test_politica_menores.py` |
| email (EML-01..12; PMN-07 e PMN-08 na parte de e-mail) | `emails.py`, `models.py` (`EmailSaida`), `notificacoes.py` (`notificar`), `templates/email/`, `templates/admin/emails.html` | `tests/test_email.py` |
