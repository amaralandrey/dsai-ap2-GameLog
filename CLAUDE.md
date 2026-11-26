# CLAUDE.md

> Para agentes de IA que escrevem código neste repo. Porta de entrada: produto, mapa, políticas.
> Regras invioláveis da AP2 e rubrica em [`.claude/plan/plan-ap2.md`](.claude/plan/plan-ap2.md) (seção "Regras invioláveis"). Violar qualquer uma anula a AP2.
> Regras globais do produto: [visão geral AP1](SPEC/2026-10-01-visao-geral.md) (GLOBAL-01..08) · [visão geral AP2](SPEC/2026-11-15-visao-geral-ap2.md) (GLOBAL-09..18). Critérios de cada parte: a spec datada em [`SPEC/`](SPEC/).

---

## Produto

GameLog: catálogo de jogos com avaliações, listas e status, diário de jogatina, feed social, recomendações,
gamificação (conquistas, XP, rankings, clubes) e painel de moderação. Trabalho da AP2 de DSAI 2026,
feito em dupla (Andrey e Fabrício) com Spec-Driven Development. Cliente: Fernanda Borges (`fenalk`).
Produção: <https://gamelog-92zh.onrender.com/> (Render gratuito, banco recriado a cada deploy).

Stack fixa: Python 3.12+, Flask 3, Flask-SQLAlchemy (SQLite), Jinja2, CSS puro, pytest, gunicorn.
Sem JS de build. Trocar qualquer peça da stack ou reescrever módulo do zero anula a AP2.

## Mapa

```
README.md          como rodar, comandos flask, variáveis, tabela spec → código → testes
SPEC/              uma spec datada por parte (AAAA-MM-DD-<nome>.md), com sigla dos critérios (DIA-01, JOB-07…)
.claude/plan/      plan-ap1.md · plan-ap2.md (regras invioláveis, épicos, divisão da dupla, rito diário)
prompts/sessoes/   registro de cada sessão de IA com custo (tokens e US$), um arquivo por sessão; modelo em prompts/CLAUDE.md
loop/              PROMPT.md e ESTADO.md do exercício de loop (sync do catálogo); histórico, não mexe
.githooks/         commit-msg: exige trailers Agent:/Spec: e spec commitada sozinha antes do código
.github/workflows/ ci.yml (PR e push na main): pytest + cobertura, diff-cover ≥ 70%, jscpd ≤ 5%, gitleaks
.github/           PULL_REQUEST_TEMPLATE.md: o card do PR (modelo do corpo)
render.yaml        deploy: seed + jobs agendar + gunicorn; segredos com sync: false
dev.sh             ./dev.sh (sobe) · ./dev.sh test (suíte toda) · ./dev.sh reset (recria o banco)
src/app/           aplicação Flask: um módulo por domínio, models.py único, templates/, static/, dados/
tests/             um test_<parte>.py por spec; conftest.py com app em tmp_path e fontes falsas
```

Um domínio = `src/app/<dominio>.py` (blueprint `bp`, registrado em `routes.py`) + `src/app/templates/<dominio>/`
+ `tests/test_<dominio>.py` + `SPEC/AAAA-MM-DD-<dominio>.md`. Subpacotes: `fontes/` (Steam, GOG, RAWG),
`menores/` (idade, política) e `privacidade/` (mapa de dados). Todos os modelos ficam em `models.py`.

Papéis: visitante, jogador, admin (e responsável, moderador de clube, agente por token). Regra de acesso (GLOBAL-18):
visitante vai ao login (`login_required`), não admin recebe 403 (`admin_required`), recurso privado de outro responde 404.
O servidor nega. O template só esconde o botão.

## Rodar

```bash
./dev.sh                                     # venv + seed + http://127.0.0.1:5000 (admin local no dev.sh)
./dev.sh reset                               # recria o banco: obrigatório depois de mudar coluna de tabela existente
.venv/bin/python -m pytest -q tests/test_diario.py           # teste de uma parte
.venv/bin/python -m pytest -q tests/test_diario.py -k dia_03 # um critério
cd src && ../.venv/bin/flask --app app <comando>             # seed, import-games, sync-games, jobs rodar…
```

Sem ferramenta de migração: `db.create_all()` cria tabela nova, mas não altera tabela existente.
Mudou coluna → `./dev.sh reset` e diga isso no handoff.

## Mentalidade

Você é **engenheiro sênior**. Entende o sistema **antes de mexer**.

- **Sênior, direto.** Resposta certa > resposta longa.
- **Olha antes de agir.** Reutilizar > Evoluir > Criar. Repo pequeno: ler o módulo inteiro é barato, leia.
- **Premissa externa** (API da Steam/GOG/RAWG, payload, comportamento do Flask) → confirma na fonte real. Memória é pista, não verdade.
- **Simplicidade.** Sem abstração até a Regra do Três. Apagar código > escrever código.
- **Reproduz antes de consertar.** Bug: teste que falha primeiro, depois o fix.
- **Aprende com correção.** Usuário corrigiu → internaliza, não repete.
- **Qualidade não negociável.** "Conserto depois" sem registro (issue ou pendência na sessão) = não entrega.

## Antes de codar

1. Lê nesta ordem: este arquivo → spec da parte → specs citadas no "Depende de" → visão geral AP2 se a mudança
   toca usuário, efeito colateral ou admin → arquivo inteiro que vai editar → testes da parte.
2. **Sem spec commitada, sem código.** Funcionalidade nova ou mudança de comportamento exige
   `SPEC/AAAA-MM-DD-<nome>.md` já commitada, sozinha, antes do primeiro commit de código. Formato no plan-ap2 §1.2
   (O quê e por quê · Critérios de aceitação com sigla · Fora do escopo). Mudou critério → muda a spec primeiro.
3. **Tarefa subespecificada?** Não dispara cascata de pergunta. Escolhe o caminho mais provável, declara
   no máximo 2-3 premissas explícitas e segue. Só pergunta antes se premissa errada joga o trabalho fora.

## Git policy

- **Agent não comita, agent não pusha, agent não mergeia.** Dev revisa o diff e assina o commit.
- Agent FAZ: `git status`, `git diff`, `git log`, `git add`. Sugere a mensagem completa. Dev comita.
- **Sem `Co-Authored-By` de LLM.** A autoria do agente vai no trailer `Agent:`, que o hook exige.
- Conventional Commits com escopo do domínio, terminando nos trailers:

  ```text
  feat(diario): sessão com início e fim

  Agent: claude-code/claude-opus-5-5
  Spec: SPEC/2026-11-15-diario-de-jogatina.md
  ```

  `Spec: none` só para infra/docs sem funcionalidade. `Agent: none` só para trabalho manual.
- Commits pequenos (mediana < 400 linhas), por camada: spec → model → serviço → rota → template → teste.
- Branch a partir da `main` atualizada. Nunca commit direto na `main`, `push --force`, `amend` depois do push
  ou `rebase` da `main`. Merge só por PR aprovado pelo outro integrante da dupla, com CI verde.
- Hook ativo no clone: `git config core.hooksPath .githooks` (o `./dev.sh` já faz).

## Secrets policy

- Agent **não lê** `.env` (só `.env.example`), `*.pem`, `*.key`, `id_rsa*`, `.ssh/`, `.aws/`, `credentials.*`, `secrets.*`.
- Segredos do projeto: `SECRET_KEY`, `ADMIN_PASSWORD`, `RAWG_API_KEY`, `STEAM_API_KEY`, `JOBS_TOKEN`. Vêm só de
  variável de ambiente, declarada no `render.yaml` com `sync: false` (GLOBAL-16). `.env.example` só tem placeholder.
- Precisa de variável? Pergunta nome e uso, nunca valor. Comando que exige chave real: dev roda no shell dele.
- Segredo nunca entra em código, teste, log, saída de comando, resposta HTTP, export, nem em `prompts/sessoes/`.
  O CI roda `gitleaks` no histórico inteiro: um vazamento zera o critério e não sai com commit novo.

## Segurança (mínimo, não negociável)

CSRF por token de sessão em todo POST de formulário (`csrf_token` no template) · senha com
`werkzeug.security` (hash, nunca texto puro) · bloqueio de login por `LoginAttempt` · cookie `Secure` e
`SECRET_KEY` obrigatória com `GAMELOG_PRODUCAO=1` · redirecionamento só por `safe_next` · só ORM ou bind
params (f-string em SQL = bug) · Jinja com autoescape: `|safe` só em conteúdo gerado pelo servidor (SVG de
`graficos_svg`) · token e código guardados só como hash · ação de admin grava `AuditLog` `<area>.<acao>` (GLOBAL-17).

## Regras de produto que atravessam specs

- **Visibilidade (GLOBAL-13):** quem mostra usuário ou fala com ele consulta `pode_ver_perfil`, `bloqueado_entre`
  e `pode` (política de menores). Não reimplementa a regra.
- **Um ponto de entrada por efeito (GLOBAL-14):** `notificar()`, `enfileirar()`, `emitir_evento()`, serializadores,
  `graficos_svg` e `enviar_email()` quando a spec de e-mail entrar. Rota HTML e API chamam o mesmo serviço.
- **Jobs (GLOBAL-15):** o processo web não roda agendador nem thread. Trabalho fora da requisição vai para a fila.
- **Tempo (GLOBAL-09):** dia, semana e mês no fuso `America/Belem` (`tempo.py`). Testes fixam o relógio.
- **Mapa de dados (GLOBAL-12):** coluna nova em `models.py` → anota em `privacidade/mapa.py`, ou o teste do MAP falha.
- **Rede (GLOBAL-10):** HTTP externo só por `fontes/base.py`. Testes nunca usam rede.

## Workflow

1. **Plan first.** Multiarquivo ou 3+ passos → planeja e mostra o plano. Desviou? Para e replaneja.
2. **TDD.** Teste falhando → implementação mínima → verde. Cada critério tem ao menos um teste, com a sigla no
   nome (`test_dia_03_...`). O teste precisa falhar quando a mudança é revertida (a auditoria roda mutação).
3. **Testa só o nosso código.** Nada de teste do Flask, do SQLAlchemy ou do Jinja.
4. **Roda só o teste da parte.** Suíte inteira (`./dev.sh test`) só no CI ou se pedirem.
5. **Self-verify antes de "done".** Teste da parte verde, rota conferida no navegador com `./dev.sh` quando
   muda tela, e a spec batendo com o que foi feito. Não reporta done com vermelho.
6. **Sessão com custo.** Toda sessão de agente com commit gera registro (passo 6 de "Nova implementação").
   Sem esse arquivo, o dia zera o critério de sessões.

## Nova implementação (do pedido ao PR)

Vale para feature, fix, teste e docs. O agente prepara tudo; o dev comita, pusha e abre o PR.

1. **Branch** a partir da `origin/main` recém-buscada: `git fetch origin && git switch -c <tipo>/<slug> origin/main`.
   `<tipo>` é o do commit principal; `<slug>` em kebab-case e em português, com o número da issue quando houver
   (`feat/diario-de-jogatina`, `fix/backup-issue-56`, `docs/sessao-2026-11-25`).
2. **Spec primeiro** se muda comportamento: commit `docs(spec): <o que muda>`, sozinho (ver "Antes de codar").
3. **Plano** se a tarefa é multiarquivo: `.claude/plan/AAAA-MM-DD-<slug>.md`, commitado junto com a sessão.
4. **Commits por camada** (spec → model → serviço → rota → template → teste → docs), no padrão do histórico:
   - assunto `<tipo>(<escopo>): <descrição>` em português, minúsculo, sem ponto final, até ~72 caracteres;
   - `feat` comportamento novo · `fix` correção · `test` só teste · `docs` README, spec, plano e sessão ·
     `refactor` sem mudar comportamento · `perf` desempenho;
   - escopo = domínio (`diario`, `backup`, `api-v2`), `spec` para a spec e `sessoes` para o registro;
   - corpo opcional com o porquê; sempre os trailers `Agent:` e `Spec:` (ver "Git policy").
5. **Card do PR.** O corpo do PR é o card, no modelo de `.github/PULL_REQUEST_TEMPLATE.md`:
   - fix: Problema (impacto para quem usa, sem nome de função) · Causa · Solução · Testes · Revisar;
   - feature: Contexto · Solução · Testes · Revisar;
   - `Fixes #N` quando há issue, a spec com as siglas e o caminho do registro da sessão;
   - título: épico `Épico N: <nome> (SIGLA)`; o resto, igual ao assunto do commit principal.
6. **Sessão salva, sempre.** Toda sessão de agente que gera commit vira
   `prompts/sessoes/AAAA-MM-DD-HHMM-<autor>-<ferramenta>.md`, no modelo de `prompts/CLAUDE.md`, commitada no
   mesmo PR como `docs(sessoes): <tema>` com `Spec: none`. Sem o registro, o PR não está pronto.
7. **Entrega.** Teste da parte verde e `diff-cover` ≥ 70%. O agente grava o card num arquivo fora do repo e entrega
   **um único comando pronto para colar**: `git add` + `git commit` por camada, `git push -u origin <branch>` e
   `gh pr create --title ... --body-file <card>`. O dev confere o diff e roda.
8. **Depois do PR.** CI verde e aprovação do parceiro. Cada comentário de review é avaliado; o que for corrigido
   entra em commit novo pelo mesmo caminho, sem `amend` depois do push, e o registro da sessão é atualizado.

## Convenções

- **Sem comentários novos.** Existente fica intacto. O "porquê" vai na mensagem de commit e no PR.
- Segue o estilo do arquivo vizinho (PEP 8, 4 espaços). Sem anotação de tipo em massa no código existente.
- Domínio em português (`jogo`, `diario`, `clube`, `sessao`), técnico em inglês (`bp`, `query`, `client`).
- **Sem dependência nova** (GLOBAL-11): `requirements.txt` não cresce sem issue aprovada. Stdlib e Flask resolvem.
- **Sem inflar linhas:** nada de código duplicado (jscpd ≤ 5% em `src` e `tests`), código morto ou arquivo gerado.
  `src/app/dados/jogos.json` só muda pelos comandos `export-games` e `enriquecer-snapshot`.
- Refatoração fora da spec só com issue que justifique e com teste cobrindo.
- README e tabela spec → código → testes acompanham a mudança no mesmo PR.

## Limite

**200 linhas** por `CLAUDE.md`. Passou? Move detalhe para um `CLAUDE.md` na pasta correspondente e deixa índice aqui.
