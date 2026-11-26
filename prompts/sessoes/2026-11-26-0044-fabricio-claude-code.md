# Sessão Claude Code: roteiro de nova implementação no CLAUDE.md, card do PR e modelo de sessão (2026-11-26)

**Ferramenta:** Claude Code CLI (Claude Opus 5.5, `claude-opus-5-5`), sem subagentes
**Quem conduziu:** Fabrício Assunção
**Ponto de partida:** `fix/backup-issue-56` limpa, com o PR #59 aberto; branch `docs/claude-md-nova-implementacao` a partir da `origin/main`

## Custo

Medido no log da sessão do Claude Code (`~/.claude/projects/.../cf072221-....jsonl`), só a partir do prompt deste pedido (03:44 UTC). A mesma conversa já tinha feito o plano e o fix da issue #56, registrados em `2026-11-26-0015-fabricio-claude-code.md` no PR #59. Cada resposta do modelo conta uma vez só, com o maior `usage` registrado para o mesmo `message.id`. Os preços são os da API do Claude Opus 5.5, os mesmos dos registros anteriores.

| Tipo de token | Tokens | Preço (US$/1M) | Custo (US$) |
|---|---:|---:|---:|
| Entrada | 22 | 4,00 | 0,00 |
| Saída | 15.079 | 20,00 | 0,30 |
| Leitura de cache | 2.412.354 | 0,20 | 0,48 |
| Escrita de cache (TTL 5 min) | 0 | 5,00 | 0,00 |
| Escrita de cache (TTL 1h) | 26.894 | 8,00 | 0,22 |
| **Total** | **2.454.349** | | **≈ 1,00** |

São 10 requisições ao modelo, todas da sessão principal. A conta não inclui as requisições que escreveram este registro.

## Prompts

1. Em uma nova branch, adicione no CLAUDE.md as orientações para uma nova implementação. Precisa ter descrição de card (pode usar o git-pr como base), necessariamente salva a sessão, seguir o padrão que já existe no código de commits e outras orientações

## Como foi feito

1. **Levantamento.** Li o `CLAUDE.md` da `origin/main` e o rito diário do `plan-ap2.md` (§5 e regra 6). Contei os tipos de commit nos últimos 300 da `main`: `fix` 100, `feat` 71, `docs` 37, `test` 18, `perf` 11 e `refactor` 8. Também li os títulos das 20 PRs mais recentes: épico como `Épico N: <nome> (SIGLA)`, e o resto no formato do commit.
2. **Card.** O projeto não usa Notion, então o card é o corpo da PR. O modelo segue a estrutura da skill `git-pr` (Problema, Causa, Solução, Revisar), com as partes que as PRs do repo já trazem: mudança na spec em commit separado, testes e o caminho do registro da sessão. Ele foi para `.github/PULL_REQUEST_TEMPLATE.md`, que o GitHub usa para preencher a PR dos dois integrantes, e a skill `git-pr` preenche quando existe.
3. **Sessão.** O modelo do registro e a regra de medida de custo foram para `prompts/CLAUDE.md`, a partir dos registros de 2026-11-22, 2026-11-23 e 2026-11-26. Ficaram fora de `prompts/sessoes/` para essa pasta conter só registros.
4. **`CLAUDE.md`.** Nova seção "Nova implementação (do pedido ao PR)", com 8 passos: branch, spec, plano, commits por camada, card, sessão, entrega e depois do PR. O item 6 do Workflow aponta para ela, e o mapa cita os dois modelos. O arquivo ficou com 181 linhas, dentro do limite de 200.

## Artefatos

| Item | Conteúdo | Situação |
|---|---|---|
| `CLAUDE.md` | seção "Nova implementação", mapa e Workflow | a commitar |
| `.github/PULL_REQUEST_TEMPLATE.md` | modelo do card do PR | a commitar |
| `prompts/CLAUDE.md` | modelo do registro de sessão e como medir o custo | a commitar |
| este arquivo | registro da sessão | a commitar |

## Decisões

- **Card = corpo da PR:** sem board externo, a PR é o registro que o parceiro aprova e que fecha a issue (`Fixes #N`). Não há linha `Card:`.
- **Sessão por PR:** sessão que continua no mesmo PR atualiza o arquivo; outro PR na mesma conversa ganha arquivo próprio. É o caso deste registro, porque o de 00:15 está no PR #59, ainda aberto.
- **Registro obrigatório em toda sessão com commit**, mais estrito que o "todo dia" da regra 6 do plan-ap2, como foi pedido.

## Pontos de atenção

- **Nome do registro:** o `plan-ap2.md` §5 ainda cita `AAAA-MM-DD-<autor>-<tema>.md`, mas os registros e o `CLAUDE.md` usam `AAAA-MM-DD-HHMM-<autor>-<ferramenta>.md`. Não mexi no plano, porque ele está fora do escopo desta mudança.
- **PR template:** vale para PRs novas; o texto-guia de cada seção é substituído por quem abre a PR.
