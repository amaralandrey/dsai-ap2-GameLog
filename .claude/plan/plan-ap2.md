# Plano de trabalho — GameLog (AP2 / DSAI 2026.4)

Repositório: `amaralandrey/dsai-ap2-GameLog` (fork de `fenalk/dsai-ap1-GameLog`).
Dupla: Andrey Amaral & Fabrício Assunção · Cliente: Fernanda Borges (`@fenalk`) & Artur (`@arturgomesc`).
Atualizado: 2026-11-15.

> 📏 **Baseline AP1 (`cloc`):** 4.064 linhas úteis (Python, HTML, CSS, Shell, INI).
> **Meta AP2 (`cloc --diff`):** ≥ 100.000 linhas novas úteis (total alvo: ~104.064 linhas).

---

## ⚠️ Regras invioláveis (leitura obrigatória para agentes e humanos)

Qualquer violação abaixo **anula a AP2** ou **zera o critério** na auditoria automatizada:

1. **Proibido reescrever o projeto:** nunca troque Python 3.12+, Flask 3, SQLAlchemy/SQLite, Jinja2 ou `pytest` por outra linguagem/framework. Nunca apague módulos existentes (`auth.py`, `catalogo.py`, `avaliacoes.py`, etc.) para refazê-los do zero. Refatoração pontual só é permitida se justificada em uma *issue* e coberta por testes.
2. **Proibido codar sem spec prévia:** nunca crie ou altere código de funcionalidade antes de existir um arquivo datado `SPEC/AAAA-MM-DD-<nome>.md` **já commitado** no Git antes do primeiro commit de código que o referencia.
3. **Proibido inflar linhas artificialmente:** não gere código duplicado (limite `jscpd` ≤ 5%), código morto (*dead code*), comentários inúteis, *lockfiles* ou arquivos gerados para bater a meta de 100 mil linhas. Toda linha deve pertencer a funcionalidades reais, templates ou testes válidos.
4. **Proibido alterar histórico ou commitar direto na `main`:** nunca execute `git push --force`, `git commit --amend` após push ou `rebase` na `main`. 100% dos merges na `main` devem ocorrer via Pull Request aprovado pelo outro integrante da dupla com CI verde.
5. **Proibido vazar segredos:** nunca commite chaves de API, senhas, `.env` ou tokens dentro do código ou dos logs em `prompts/sessoes/` (auditoria via `gitleaks` exige zero ocorrências).
6. **Proibido encerrar o dia sem sessão e custo:** todo dia com commit de agente (`Agent:`) exige um arquivo datado em `prompts/sessoes/` contendo o registro explícito de tokens consumidos ou custo financeiro.

---

## 0. Checklist de conformidade da rubrica (50 pontos)

| Critério | Meta obrigatória | Estratégia no GameLog AP2 | Pts |
| :--- | :--- | :--- | :---: |
| **Produção + CI** | URL pública no ar servindo `HEAD` da `main` + CI verde | Deploy contínuo no Render + GitHub Actions (`pytest` + `cov` + `gitleaks`) | **6** |
| **Tamanho (`cloc`)** | ≥ 100.000 linhas novas (sem gerados/mortos) | Expansão real de conectores, diário de jogatina, gamificação, clubes, API v2, servidor MCP e testes por AC | **8** |
| **Duplicação (`jscpd`)** | ≤ 5% de código duplicado | Camadas de serviço reutilizáveis (`FontesBase`, `GamificacaoEngine`, `graficos_svg`, serializadores comuns da API v2) | **3** |
| **Specs (`SPEC/`)** | 100% das features com spec datada commitada **antes** do código | 1 commit exclusivo de `SPEC/AAAA-MM-DD-*.md` abrindo cada branch | **5** |
| **Trailers Git** | ≥ 80% dos commits com `Agent:` e `Spec:` | Hook local `commit-msg` validando os trailers antes de aceitar o commit | **3** |
| **Frequência** | ≥ 2 dias/semana; mediana < 400 linhas/commit | Commits atômicos por camada (`model` → `service` → `route` → `template` → `test`) | **3** |
| **Sessões + Custo** | 100% dos dias com agente registrados em `prompts/sessoes/` com tokens/custo | Fechamento diário obrigatório antes do PR usando o template padrão | **5** |
| **Autoria + Revisão** | ≥ 30% commits cada (Andrey e Fabrício) + 100% merges via PR aprovado | Branch protection na `main` + divisão de épicos paralelos entre a dupla | **5** |
| **Testes + Mutação** | ≥ 70% *diff coverage* + 5/5 testes falham ao reverter mudança | Testes unitários e de integração mapeados 1:1 com IDs de critérios de aceitação | **9** |
| **Segurança** | Zero segredos (`gitleaks`) e alertas críticos | `.env` fora do Git + job `gitleaks` bloqueante no CI | **3** |

---

## 1. Arquitetura herdada e padrão de spec do projeto

### 1.1. Estrutura atual (não quebrar)

- **Stack:** Python 3.12+, Flask 3, Flask-SQLAlchemy (SQLite), Jinja2, CSS puro (`static/style.css`), `pytest`, `gunicorn`, `render.yaml`.
- **Módulos existentes em `src/app/`:** `__init__.py`, `models.py`, `util.py`, `seed.py`, `auth.py`, `catalogo.py`, `busca.py`, `jogo.py`, `avaliacoes.py`, `listas.py`, `social.py`, `recomendacoes.py`, `admin.py`, `api.py`, `afinidade.py`, `perfil.py`, `cli.py` e `fontes/` (`steam.py`, `arquivo.py`).
- **Convenção de testes em `tests/`:** cada arquivo `tests/test_<spec>.py` nomeia os testes com o ID exato do critério de aceitação (ex.: `test_ava_01_exige_login`, `test_fnt_01_...`).

### 1.2. Padrão obrigatório das novas specs (`SPEC/AAAA-MM-DD-<nome>.md`)

Seguindo o padrão estabelecido no repositório (ex.: `SPEC/2026-10-01-avaliacoes.md`), toda nova spec deve conter exatamente estas seções:

```markdown
# Nome da Funcionalidade (AAAA-MM-DD)

## O quê e por quê
Descrição objetiva do problema de negócio (FDE) ou demanda do cliente e o valor entregue.

## Critérios de aceitação
- SIGLA-01: regra verificável 1 (ex: autenticação, validação de entrada)
- SIGLA-02: regra verificável 2 (ex: persistência, cálculo de domínio)
- SIGLA-03: regra verificável 3 (ex: permissão 403/404, atualização de UI)
...

## Fora do escopo
O que explicitamente não será implementado nesta spec para evitar aumento artificial de escopo.
```

---

## 2. Roadmap de expansão (alinhado ao cliente + meta 100k linhas)

Para atingir **100.000 linhas novas** com valor real de produto (duplicação ≤ 5% e cobertura ≥ 70%), o trabalho está
estruturado em **5 épicos funcionais** (~15 specs principais).

> **Origem das demandas.** Na conversa com a cliente (WhatsApp, 2026-11-10), o pedido explícito foi mais catálogos além da Steam (Épico 2). A frase "seria bom utilizar um mpc e kills" respondeu à pergunta sobre como chegar às 100 mil linhas; a dupla a interpreta como **MCP e skills** (ferramentas de agentes), ainda sem confirmação da cliente. Os épicos 3 e 4 e os clubes são propostas da dupla (FDE), não demandas da cliente.

### Épico 1: Kickoff, infraestrutura e governança 

**Entregas imediatas:**

- [x] Registro do repositório com justificativa de 2–3 linhas.
- [x] Issue *Primeiras impressões* aberta com evidências e entrevista da cliente (`@fenalk`).
- [ ] Proteção da branch `main` (PR obrigatório com 1 aprovação; `push --force` bloqueado).
- [ ] Pipeline `.github/workflows/ci.yml` rodando `pytest`, `pytest-cov`, `jscpd` e `gitleaks`.
- [ ] Novo serviço Web no Render apontando para `amaralandrey/dsai-ap2-GameLog` e URL configurada no *About* do GitHub.
- [ ] Hook Git local (`.githooks/commit-msg`) validando a presença dos trailers `Agent:` e `Spec:`.

### Épico 2: Multi-catálogo de jogos — Demanda #1 da cliente

Expansão da arquitetura `src/app/fontes/` (citada explicitamente pela cliente Fernanda). A Epic Games Store entra como loja vinculada, com links obtidos pela RAWG, e não como fonte de importação: a loja não tem API pública que responda a clientes HTTP comuns (decisão na issue #9). A refatoração da fonte Steam para a base comum é justificada na issue #10.

| Spec | Critérios | Entrega |
|---|---|---|
| SPEC-01 | FNT-01..12 | **Base comum das fontes e vínculos multi-loja** (`SPEC/2026-11-15-fontes-multiloja.md`; `src/app/fontes/base.py`) — HTTP comum com novas tentativas e User-Agent do GameLog, tabela `JogoFonte` (um jogo, várias lojas, incluindo a Epic), seletor de fonte e importação em lote em `/admin/importar`. |
| SPEC-02 | GOG-01..15 | **Conectores GOG e RAWG** (`SPEC/2026-11-15-conectores-gog-rawg.md`; `src/app/fontes/gog.py`, `src/app/fontes/rawg.py`) — importação da GOG sem chave, RAWG com `RAWG_API_KEY` e vínculos de loja (Steam, GOG, Epic), normalização de gêneros, plataformas e datas, `--fonte todas` e filtro por loja em `/buscar`. |
| SPEC-03 | MRG-01..15 | **Deduplicação e mesclagem de catálogo** (`SPEC/2026-11-15-deduplicacao-catalogo.md`; `src/app/catalogo_merge.py`) — reconhecimento do mesmo jogo vindo de várias fontes, fila de duplicatas para o admin, mesclagem sem perda de histórico (soft delete e 301) e bloco "Disponível em" na página do jogo. |
| SPEC-03b | SNP-01..09 | **Snapshot multi-loja do seed** (`SPEC/2026-11-15-snapshot-multiloja.md`; `src/app/snapshot.py`) — `flask enriquecer-snapshot` grava no `src/app/dados/jogos.json` os links de GOG e Epic e o id da RAWG de cada jogo (correspondência só pelo id da Steam), para a URL pública mostrar o multi-loja sem rede nem chave no servidor, já que o Render recria o banco a cada deploy. |

### Épico 3: Diário e acompanhamento pessoal — proposta da dupla

O GameLog guarda só o status atual de cada jogo, não quando nem quanto se jogou. O diário é o recurso central do Letterboxd, referência do produto, e é a base de dados do Épico 4 (XP, conquistas, rankings e clubes).

| Spec | Critérios | Entrega |
|---|---|---|
| SPEC-04 | DIA-01..14 | **Diário de jogatina e visibilidade do perfil** (`src/app/diario.py`) — registro de sessões por jogo com data, duração, plataforma, progresso e anotação; registrar sessão de jogo "planejado" muda o status para "jogando"; sessões no feed (`Activity`), horas no perfil e na avaliação ("12h registradas"); perfil público ou privado, que o Épico 4 respeita. |
| SPEC-05 | IMP-01..12 | **Importação de horas e do diário** (`src/app/diario_import.py`) — horas jogadas da Steam pelo SteamID (`GetOwnedGames`, com `STEAM_API_KEY` e perfil público), reaproveitando o conector do Épico 2; CSV do diário com prévia antes de gravar e idempotência por linha; consistência: duração até 24h, sem sobreposição de horários do mesmo usuário e sem datas futuras. |
| SPEC-06 | RET-01..12 | **Retrospectiva e gráficos SVG** (`src/app/graficos_svg.py`, `src/app/retrospectiva.py`) — módulo de gráficos SVG server-side (linha, barra, sparkline, sem JS de build), reutilizado no Épico 4; retrospectiva pessoal com horas por mês, gêneros mais jogados e jogos zerados no ano; comparação de horas e gêneros com quem o usuário segue. |
| SPEC-07 | LSA-01..12 | **Listas inteligentes** (`src/app/listas_inteligentes.py`) — listas por regra ("comecei e larguei há 30 dias", "wishlist disponível na GOG", "zerados este ano"), barra de progresso por status e horas do diário, exportação com os serializadores da API v2 e arrastar e soltar sobre a reordenação que já existe (LIS-07). |

### Épico 4: Engajamento e comunidade — proposta da dupla

Preenche a lacuna de gamificação apontada na AP1 e evolui as conquistas atuais (CON-01..06). Depende do diário (Épico 3). As specs seguem a ordem da tabela: cada uma usa as anteriores.

| Spec | Critérios | Entrega |
|---|---|---|
| SPEC-08 | NTF-01..12 | **Notificações** (`src/app/notificacoes.py`) — avisos de curtida, comentário, novo seguidor, conquista, nível, sequência semanal prestes a quebrar e amigo que zerou um jogo da sua wishlist; preferências por tipo e marcar como lida. |
| SPEC-09 | GAM-01..15 | **Motor de conquistas orientado a eventos** (`src/app/gamificacao.py`) — catálogo de +40 conquistas (crítica, coleção, explorador multi-loja, social, diário: maratonista, 100h registradas, zerou 10 no ano), progressivas, secretas e idempotentes, avaliadas após as ações do usuário e avisadas pela NTF. |
| SPEC-10 | PRG-01..12 | **Níveis, XP e sequências semanais** (`src/app/progressao.py`) — XP por avaliações, sessões do diário e jogos zerados, com teto diário anti-farming; sequência **semanal** (registrar algo na semana) com congelamento; extrato de XP no perfil. |
| SPEC-11 | RNK-01..12 | **Rankings e temporadas** (`src/app/rankings.py`) — jogos zerados, horas registradas, avaliações mais curtidas e XP; escopos "Seguindo" e "Clube" em destaque, global só para perfis públicos; temporadas trimestrais com hall da fama e desclassificação pelo admin. |
| SPEC-12 | CLB-01..14 | **Clubes de jogos** (`src/app/clubes.py`) — grupos temáticos com jogo do mês e meta coletiva medida pelo diário ("membros somam 100h", "5 membros zeram"), ranking do clube (escopo do RNK), conquistas de clube e mural moderado. |

### Épico 5: API v2, MCP e portabilidade

| Spec | Critérios | Entrega |
|---|---|---|
| SPEC-13 | AP2-01..14 | **Tokens pessoais e API REST v2** (`src/app/tokens.py`, `src/app/api_v2.py`) — tokens com escopo (leitura, escrita), guardados só como hash, com expiração, revogação no perfil, rate-limit e auditoria; endpoints paginados de catálogo multi-loja, diário, listas, conquistas e rankings. |
| SPEC-14 | MCP-01..12 | **Servidor MCP do GameLog** (`src/app/mcp/`) — ferramentas `buscar_jogos`, `detalhes_jogo`, `minhas_listas`, `marcar_status`, `avaliar`, `registrar_sessao`, `recomendacoes`, `comparar_gosto` e `disponivel_em` sobre a API v2 autenticada por token, via `flask mcp` (stdio), mais skills curtas de uso (`skills/`). Clubes e listas inteligentes entram quando existirem. |
| SPEC-15 | BKP-01..12 | **Backup e restore portável do banco** (`src/app/backup.py`) — exportação/importação completa do estado do SQLite em JSON assinado via CLI e painel admin (solucionando a volatilidade do banco no plano gratuito do Render), com os serializadores da API v2. |

**Ordem de dependências:** Épico 3 (SPEC-04 primeiro) → Épico 4 (NTF → GAM → PRG → RNK → Clubes). O Épico 5 começa pelos tokens e pela API v2 em paralelo ao Épico 4; o MCP e o backup vêm depois da API v2.

> Os itens cortados da AP1 (REC-07, LIS-07, ADM-09, CAD-04, SOC-09) já estão implementados e testados (`test_rec07_*`, `test_lis07_*`, `test_adm09_*`, `test_cad04_*`, `test_soc09_*`) e não entram em nenhum épico.

---

## 3. Divisão de trabalho entre a dupla (meta ≥ 30% cada)

Para garantir equilíbrio na autoria de commits e revisão cruzada constante de PRs:

| Quem | Épicos | Revisão |
|---|---|---|
| **Andrey Amaral** | Épico 1 (CI/CD, Render, hooks) · Épico 2 (base comum e vínculos multi-loja, conectores GOG/RAWG, deduplicação e snapshot) · Épico 4: conquistas, XP/sequências e rankings (SPEC-09..11) | Aprova todos os PRs do Fabrício |
| **Fabrício Assunção** | Épico 3 (diário, importação, retrospectiva SVG e listas inteligentes) · Épico 4: notificações e clubes (SPEC-08 e SPEC-12) · Épico 5 (tokens e API v2, servidor MCP, backup/restore) | Aprova todos os PRs do Andrey |

---

## 4. Distribuição estimada das 100.000 linhas novas

Para atingir **≥ 100.000 linhas novas** no `cloc --diff` sem código duplicado ou morto:

> Dados gerados (`src/app/dados/jogos.json`, produzido por `export-games` e `enriquecer-snapshot`) não contam para a meta: medir com `cloc --git --diff main HEAD --exclude-dir=dados`.

| Camada | Onde | Estimativa | Conteúdo |
|---|---|---|---|
| **Código de aplicação** | `src/app/` | ~45.000 linhas Python | Separação limpa em modelos de domínio, camadas de serviço (`services/`), validadores de entrada (`validators/`), blueprints Flask e comandos CLI, com *type hints* completos e tratamento explícito de regras de negócio. |
| **Suíte de testes** | `tests/` | ~40.000 linhas Python | Cada critério de aceitação (`SIGLA-01..NN`) coberto por múltiplos cenários (`pytest.mark.parametrize` para casos válidos, limites de borda, permissões 401/403/404 e testes que falham se a lógica for revertida). |
| **Templates e estilos** | `src/app/templates/` e `static/` | ~15.000 linhas HTML/CSS | Interfaces completas e responsivas para diário e retrospectiva, vitrine de conquistas, disponibilidade multi-loja (Steam/GOG/Epic), clubes, rankings e relatórios administrativos. |

---

## 5. Rito diário obrigatório (checklist de execução)

1. **Criar branch:**
   ```bash
   git checkout main && git pull && git checkout -b feat/<spec>
   ```
2. **Commitar a spec primeiro:** criar `SPEC/AAAA-MM-DD-<spec>.md` e commitar isoladamente **antes de qualquer código**.
3. **Codar em commits pequenos (< 400 linhas):** dividir por camada (`model` → `service` → `route` → `template` → `test`)
   em pelo menos 2 dias distintos por semana, sempre incluindo os trailers no final da mensagem:
   ```text
   feat(modulo): descricao objetiva da alteracao

   Agent: <ferramenta/modelo>
   Spec: SPEC/AAAA-MM-DD-<spec>.md
   ```
4. **Validar qualidade localmente:** rodar `./dev.sh test` (garantindo cobertura do diff ≥ 70%), verificar duplicação
   (`jscpd` ≤ 5%) e ausência de segredos.
5. **Registrar sessão e custo no mesmo dia:** criar `prompts/sessoes/AAAA-MM-DD-<andrey|fabricio>-<tema>.md` com o
   resumo dos prompts e o total de **tokens / custo consumido**.
6. **Integrar via PR:** abrir Pull Request para a `main`, aguardar CI verde e aprovação do parceiro de dupla.
