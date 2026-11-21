# Sessão Claude Code: PR do mapa de dados e bloquear e silenciar (2026-11-21)

**Ferramenta:** Claude Code CLI (Claude Opus 5.5, `claude-opus-5-5`), sem subagentes
**Quem conduziu:** Andrey Amaral
**Horário:** a partir das 13:19 (horário de Belém). Continua a sessão registrada em `2026-11-21-1302-andrey-claude-code.md`, que fica na branch do mapa de dados.
**Ponto de partida:** MAP commitado em `feat/mapa-de-dados`, ainda sem PR

## Custo

Medido no log da sessão do Claude Code (`~/.claude/projects/.../c0fdc479-....jsonl`), só a partir do prompt 1 abaixo. A parte anterior está no registro das 13:02. Cada resposta do modelo conta uma vez só, com o maior `usage` registrado para o mesmo `message.id`. Os preços são os da API do Claude Opus 5.5.

| Tipo de token | Tokens | Preço (US$/1M) | Custo (US$) |
|---|---:|---:|---:|
| Entrada | 94 | 4,00 | 0,00 |
| Saída | 34.789 | 20,00 | 0,70 |
| Leitura de cache | 7.113.644 | 0,20 | 1,42 |
| Escrita de cache (TTL 1h) | 69.317 | 8,00 | 0,55 |
| **Total** | **7.217.844** | | **≈ 2,67** |

São 43 requisições ao modelo. A conta foi feita antes deste registro, então ficam de fora a escrita deste arquivo, o commit e o push.

## Prompts

1. yes, keep the watch out note for future addressing.
2. yeah (pedir a revisão do Fbc00 no #28)
3. are they alocated to me?
4. yes, start BLQ from main

## Como foi feito

1. **PR #28 (MAP).** Push, PR para a `main` com a seção "Atenção: #24 e #25 depois deste merge" e pedido de revisão ao Fbc00. O pedido foi feito pela API REST, porque o `gh pr edit` falhou com o aviso de descontinuação do Projects (classic).
2. **Divisão de trabalho.** Pela seção 3 do `plan-ap2.md`, o BLQ é do Andrey (Épico 8) e a API v2 é do Fabrício (Épico 5). O Fabrício não tem nenhum commit no repositório, e o critério "Autoria + Revisão" pede pelo menos 30% de cada um.
3. **BLQ a partir da `main`** (`feat/bloquear-silenciar`). O bloqueio entrou nos dois pontos únicos de visibilidade:
   - `pode_ver_perfil` chama `bloqueado_entre`;
   - `ocultos_para` une a subconsulta de bloqueio à do perfil privado.

   Assim, a página do jogo, o feed, os rankings, a afinidade e a retrospectiva passaram a respeitar o bloqueio sem mudança própria. Os pontos que não passavam por essas funções foram ajustados um a um: a página do perfil, o seguir, as sugestões, o comentar, o curtir, os comentários no macro e o mural do clube.
4. **Dois tropeços.**
   - O SQLite recusou o UNION aninhado (perfil privado ∪ bloqueio, que já era um UNION). A subconsulta de bloqueio virou um SELECT só, com `CASE`.
   - O teste de orçamento da retrospectiva (RET-11) pegou uma consulta a mais no perfil do próprio dono. `pode_ver_perfil` agora devolve verdadeiro para o dono antes de olhar o bloqueio.
5. **Testes.** 15 testes novos em `tests/test_bloqueios.py`. A suíte completa passou (815), e o jscpd ficou em 0,8%.

## Artefatos

| Item | Onde |
|---|---|
| PR do mapa de dados | #28 |
| Modelos `Bloqueio` e `Silencio` | `src/app/models.py` |
| Regra, filtro de notificação, rotas e `/conta/bloqueios` (BLQ-01..08) | `src/app/bloqueios.py`, `templates/bloqueios/lista.html` |
| Bloqueio na visibilidade (BLQ-03, BLQ-04) | `src/app/diario.py` (`pode_ver_perfil`, `ocultos_para`) |
| Ajustes pontuais (BLQ-02, 04..07) | `auth.py`, `social.py`, `comentarios.py`, `avaliacoes.py`, `jogo.py`, `clubes.py`, `rankings.py`, `_macros.html`, perfis |
| Testes (BLQ-10) | `tests/test_bloqueios.py` |
| Commit | `96cb344` |

## Decisões

- **`com_bloqueio(user_id)` guarda o conjunto em `g`.** Assim, `pode_ver_perfil` chamado em laço (mural, notificações) faz uma consulta por usuário e requisição. Bloquear e desbloquear limpam esse cache.
- **Admin.** O bloqueio vale também para o admin nas telas públicas. A moderação continua pelas telas `/admin`.
- **Silenciar.** Quem tem bloqueio não consegue silenciar o outro (404). Silenciar a si mesmo não faz nada.
- **Feed (BLQ-09).** O filtro de silenciados entra na consulta do feed. O único custo novo é a consulta de `com_bloqueio` para esconder comentários, conferida no teste com no máximo 2 consultas a mais.

## Pontos de atenção

- **MAP (#28):** `bloqueio.*` e `silencio.*` não estão anotados no mapa, porque o MAP ainda não está na `main`. Quem entrar por último precisa anotá-los (`categoria="atividade"`, finalidade `perfil_social`, `apagar_linha`), ou o teste do mapa falha.
- **Fora deste PR**, porque as specs ainda não estão na `main`:
  - torneios (TRN): não juntar dois usuários com bloqueio na mesma partida;
  - denúncia de risco a menores (DEN-03);
  - o bloqueio feito pelo responsável (RSP-09). O modelo e a regra do 403 já existem, falta a tela do responsável.
