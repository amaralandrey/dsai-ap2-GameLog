# Sessão Claude Code: plano e correção da issue #55 (escopo `ler` e auditoria no MCP) (2026-11-26)

**Ferramenta:** Claude Code CLI (Claude Opus 5.5, `claude-opus-5-5`), skills `superpowers:writing-plans`, `superpowers:executing-plans` e `superpowers:test-driven-development`, 1 subagente revisor (Opus)
**Quem conduziu:** Fabrício Assunção
**Ponto de partida:** `main` limpa em `c7502e7`, igual à `origin/main`. Branch `fix/mcp-issue-55` a partir da `origin/main`.

## Custo

Medido no log da sessão do Claude Code (`~/.claude/projects/.../72f84e05-....jsonl`) e no log do subagente revisor (`subagents/agent-*.jsonl`), desde o primeiro prompt (04:12 UTC). Cada resposta do modelo conta uma vez só, com o maior `usage` registrado para o mesmo `message.id`. Os preços são os da API do Claude Opus 5.5, os mesmos dos registros anteriores.

| Tipo de token | Tokens | Preço (US$/1M) | Custo (US$) |
|---|---:|---:|---:|
| Entrada | 152 | 4,00 | 0,00 |
| Saída | 68.753 | 20,00 | 1,38 |
| Leitura de cache | 10.473.669 | 0,20 | 2,09 |
| Escrita de cache (TTL 5 min) | 96.855 | 5,00 | 0,48 |
| Escrita de cache (TTL 1h) | 176.202 | 8,00 | 1,41 |
| **Total** | **10.815.631** | | **≈ 5,36** |

São 72 requisições ao modelo: 60 da sessão principal e 12 do subagente revisor. A conta não inclui as requisições que escreveram este registro.

## Prompts

1. `/superpowers:writing-skills` Escreva uma plano para a solução do problema que a issue https://github.com/amaralandrey/dsai-ap2-GameLog/issues/55 mostra. Siga claramente as orientações do projeto para implementação de features
2. `/superpowers:executing-plans` .claude/plan/2026-11-26-mcp-issue-55.md

## Como foi feito

1. **Skill.** O primeiro prompt chamou a `writing-skills`, que serve para escrever skills. O pedido era um plano, por isso usei a `writing-plans`.
2. **Levantamento.** Li a issue #55 pela API do GitHub. O `gh issue view` falha por causa do Projects (classic). Também li a spec MCP, `mcp/ferramentas.py`, `mcp/protocolo.py`, `tests/test_mcp.py`, `api_v2.py`, `api.py` (`jogo_ou_404`, `mesclado_ou_404`), `catalogo_merge.principal_do_mesclado`, `tokens.py` e AP2-03..07. Na API v2, o slug mesclado responde 301 nas leituras e 404 nas escritas.
3. **Log no stderr.** Rodei um teste descartável pela CLI, e o stderr veio vazio. Nada liga o logger do MCP ao stderr: em produção o traceback chega lá só pelo `logging.lastResort`, e sob pytest o handler de captura no root o engole. Por isso o item 4 da issue virou um fix em `iniciar`, e não só um teste.
4. **Plano.** Salvei em `.claude/plan/2026-11-26-mcp-issue-55.md`. Ele tem 3 premissas, 6 tasks (spec → escopo → slug mesclado → auditoria → log → entrega) e 5 pontos de Review Focus.
5. **Execução inline com TDD.** Cada teste novo falhou pelo motivo esperado antes do fix e passou depois. A suíte do MCP passou de 19 para 23 testes, todos verdes.
6. **Commits por camada sem o agente comitar.** O agente só faz `git add`. Gerei um patch cru por task com `git diff-tree -p` entre árvores temporárias, montadas com o `hash-object` dos estados intermediários. A cadeia de patches reproduz o index real. Numa worktree descartável, cada estágio passou (20, 21, 22 e 23 testes). O teste das Tasks 2 e 3 falha sobre o código da task anterior.
7. **Verificação.** `tests/test_mcp.py` e `tests/test_api_v2_tokens.py`: 40 testes passaram. `diff-cover` contra a `origin/main`: 100% (29 linhas).
8. **Revisão final.** Um subagente Opus revisou o diff inteiro e não achou nenhum Critical. O único Important era este registro. Os Minor estão em "Pontos de atenção".

## Artefatos

| Item | Conteúdo | Situação |
|---|---|---|
| `SPEC/2026-11-15-servidor-mcp.md` | MCP-05, 07, 08, 10 e 12 | a commitar, sozinho e antes do código |
| `src/app/mcp/ferramentas.py` | `escopo` por ferramenta, `jogo_ou_principal`, `_executar` com o código da auditoria | a commitar |
| `src/app/mcp/protocolo.py` | `iniciar` liga o logger ao `erros` | a commitar |
| `tests/test_mcp.py` | 4 testes novos (MCP-01, 05, 07/08, 10) | a commitar |
| `README.md` | escopo e auditoria do MCP | a commitar |
| `.claude/plan/2026-11-26-mcp-issue-55.md` | plano | a commitar com este registro |
| PR | `fix(mcp): ferramentas de leitura exigem o escopo ler`, `Fixes #55` | a abrir |

## Decisões

- **Catálogo exige `ler`:** a issue pede, e uma regra só para as 9 ferramentas é mais simples de ler e testar. A API v2 serve `/jogos` sem token, mas o MCP sempre roda com um token.
- **Slug mesclado:** a leitura segue o principal, como o 301 da API. A escrita recusa o slug, como o 404 da API. O MCP não tem redirecionamento, por isso replica o efeito.
- **Código na auditoria:** toda chamada de escrita com token válido grava 200, 403, 404, 422 ou 429, na ordem do `before_request` da API (limite, escopo, validação). A leitura não grava, e o token revogado também não, como o 401 da API.
- **Log:** `iniciar` usa um `StreamHandler(erros)` com `propagate = False` durante o laço e restaura os dois num `finally`.

## Pontos de atenção

- **Escrita que lança exceção** não entra na auditoria. A API v2 gravaria 500, e o MCP-10 diz "toda chamada". Ficou como pendência menor, apontada pelo revisor.
- **Código "que a API v2 gravaria" não é idêntico em 3 casos:**
  - avaliação ou sessão nova grava 201 na API e 200 no MCP;
  - `registrar_sessao` com slug inexistente grava 422 na API e 404 no MCP;
  - argumento inválido junto com slug inexistente grava 404 na API e 422 no MCP.

  Para resolver, basta trocar o texto da spec e do README para "código equivalente".
- **O código 404 da auditoria depende do texto** `JOGO_NAO_ENCONTRADO`. Se a mensagem mudar, a auditoria passa a gravar 422 em silêncio.
- **O teste de recusa por regra usa a data futura**, não a regra das 24 h no dia. As duas regras dão 422.
- **`propagate` volta fixo em `True`**, e não no valor anterior. Hoje dá no mesmo.
- Não muda nenhuma coluna, então o `./dev.sh reset` não é necessário.
