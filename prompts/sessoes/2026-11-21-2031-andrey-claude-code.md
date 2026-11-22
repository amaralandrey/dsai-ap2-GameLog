# Sessão Claude Code: merge dos PRs #24–#29 (2026-11-21)

**Ferramenta:** Claude Code CLI (Claude Opus 5.5, `claude-opus-5-5`), sem subagentes
**Quem conduziu:** Andrey Amaral
**Ponto de partida:** `main` em `1b614b8` (PRC mergeado), PRs #24 a #29 abertos

## Custo

Medido no log da sessão do Claude Code (`~/.claude/projects/.../232abe89-....jsonl`). Cada resposta do modelo conta uma vez só, com o maior `usage` registrado para o mesmo `message.id`. Os preços são os da API do Claude Opus 5.5.

| Tipo de token | Tokens | Preço (US$/1M) | Custo (US$) |
|---|---:|---:|---:|
| Entrada | 124 | 4,00 | 0,00 |
| Saída | 24.474 | 20,00 | 0,49 |
| Leitura de cache | 4.195.421 | 0,20 | 0,84 |
| Escrita de cache (TTL 1h) | 72.151 | 8,00 | 0,58 |
| **Total** | **4.292.170** | | **≈ 1,91** |

São 61 requisições ao modelo. Este registro, o push do #29 e o merge final ficam de fora da conta.

## Prompts

1. merge all the pending prs, resolve all conflicts
2. remember to generate the due artefacts

## Como foi feito

Merge sem review, a pedido do Andrey. Antes de cada merge: `git merge main` na branch, suíte local inteira e CI verde.

1. **#27 e #26.** Sem conflito. A suíte (965 testes) rodou sobre a `main` com os dois mesclados antes do merge.
2. **#28 (MAP).** Sem conflito de texto, mas o teste `test_toda_coluna_do_banco_esta_no_mapa` falhou com as tabelas que entraram na `main` depois do PR: `preco_loja` (catálogo, não pessoal) e `instancia` (segurança, não pessoal). A linha "seguranca" do registro das operações passou a listar `instancia` também, e o teste do CSV foi ajustado.
3. **#24 (JOB).** Conflitos em `README.md` (variáveis e tabela de specs) e `admin/index.html`, resolvidos mantendo os dois lados. `job` e `agendamento` entraram no mapa como não pessoais em "catalogo", porque as tarefas de hoje só sincronizam o catálogo.
4. **#25 (IDA).** Conflitos em `.env.example`, `auth.py`, `models.py`, `routes.py`, `admin/index.html` e `util.py`:
   - no cadastro, o `flush` e o `idade.registrar` foram para dentro do `try` do `IntegrityError` da `main`;
   - no login, a checagem de conta suspensa ficou depois da comparação de tempo constante (CAD-03);
   - `_destino()` herdou a regra da `main` de só voltar a um `referrer` do mesmo host.
   Nova finalidade `protecao_menores` no mapa, com base legal "obrigacao_legal", para as seis colunas de idade em `user` e para `verificacao_idade`. O teste de cadastro simultâneo falhava porque o `INSERT` cru da corrida não preenchia as colunas `NOT NULL` novas; o teste foi corrigido.
5. **#29 (BLQ).** Conflitos em `auth.py`, `clubes.py`, `jogo.py` e `routes.py`. O BLQ transformou `ocultos_para` numa `UNION` com os bloqueios, e a `main` chamava `.where()` sobre ela em `clubes.pagina`, o que quebrou 17 testes. Agora o filtro usa uma subconsulta. `bloqueio` e `silencio` entraram no mapa como preferência em "perfil_social".

## Artefatos

| PR | Conteúdo | Situação |
|---|---|---|
| #27 | savepoint do pysqlite | mergeado |
| #26 | chaveamento (SPEC-28) | mergeado |
| #28 | mapa de dados (SPEC-MAP) + anotação de `preco_loja` e `instancia` | mergeado |
| #24 | fila de jobs (SPEC-16) + anotação de `job` e `agendamento` | mergeado |
| #25 | verificação de idade (SPEC-30) + anotação das colunas de idade | mergeado |
| #29 | bloquear e silenciar (SPEC-27) + anotação de `bloqueio` e `silencio` | merge depois do CI |

## Decisões

- Tabela sem dado pessoal ganha `_nao_pessoal` na finalidade que a usa, mesmo quando isso acrescenta uma tabela à linha do registro das operações.
- O nascimento sai na exportação do próprio titular (`exportar=True`). A IDA-12 só proíbe a saída para terceiros.

## Pontos de atenção

- PRC-07 continua pendente agora que o JOB entrou: registrar `@tarefa("precos")` e o agendamento diário.
- Se uma tarefa de job passar a levar `user_id` em `args`, `job` deixa de ser não pessoal no mapa.
- Nenhum dos seis PRs teve aprovação do Fabrício (regra 4 do plano).
