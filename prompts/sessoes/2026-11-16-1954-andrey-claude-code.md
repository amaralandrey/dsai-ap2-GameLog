# Sessão Claude Code: implementação dos épicos 3 e 4 (2026-11-16)

**Ferramenta:** Claude Code CLI (Claude Opus 5.5, `claude-opus-5-5`), sem subagentes
**Quem conduziu:** Andrey Amaral
**Horário:** 19:54–20:50 (horário de Belém)
**Plano:** `.claude/plan/plan-ap2.md` como está na `main` depois do PR #20 (épicos 3 e 4, SPEC-04..12)

## Custo

Medido no log da sessão do Claude Code (`~/.claude/projects/.../33da37ba-....jsonl`). Cada resposta do modelo conta uma vez só, com o maior `usage` registrado para o mesmo `message.id`. Os preços são os da API do Claude Opus 5.5.

| Tipo de token | Tokens | Preço (US$/1M) | Custo (US$) |
|---|---:|---:|---:|
| Entrada | 252 | 4,00 | 0,00 |
| Saída | 249.899 | 20,00 | 5,00 |
| Leitura de cache | 32.398.046 | 0,20 | 6,48 |
| Escrita de cache (TTL 1h) | 407.748 | 8,00 | 3,26 |
| **Total** | **33.055.945** | | **≈ 14,74** |

São 126 requisições ao modelo. A conta foi feita antes de escrever este registro, então ficam de fora as últimas respostas: este arquivo, o push e a abertura dos PRs.

## Prompt

1. implemente todas as specs relacionadas aos epicos 3 e 4 do plan, atente aos requisitos do projeto presentes na pasta trabalho gustavo em downloads e no plan ap 2, especialmenta quanto aos artefatos de rastreabilidade e de sessão.

## Como foi feito

1. **Contexto.** Li as regras da AP2 (`REGRAS_AP2.md` e o manual da pasta "trabalho gustavo"), o plano, a visão geral da AP2 (GLOBAL-09..18), as 9 specs dos épicos 3 e 4 e os critérios das specs vizinhas que elas chamam: PMN-01 (`pode`), BLQ-01 (`bloqueado_entre`), JOB-03 (`@tarefa`), DEN-01 (`Report.alvo_tipo`) e MAP-01..03.
2. **Base do trabalho.** O `main` só tinha o PR #12 do Épico 2. Os PRs #13, #14 e #16 foram mergeados nas branches empilhadas, e não no `main` (veja "Pontos de atenção"). Os épicos 3 e 4 dependem deles (`JogoFonte`, lojas, `fontes/base.py`), então a branch do Épico 3 parte de `feat/snapshot-multiloja` com o `main` mesclado.
3. **Implementação.** Uma spec por vez, na ordem de dependência do plano:
   - Épico 3: DIA → HRS → RET → LSA;
   - Épico 4: NTF → GAM → PRG → RNK → CLB.

   Cada spec foi dividida em commits por camada (modelo e serviço, rotas e telas, testes), e os testes levam o ID do critério (`test_dia_01_...`). Toda spec já estava na `main` antes do primeiro commit de código que a cita (regra 2), e todo commit traz `Agent:` e `Spec:`. O hook `commit-msg` aceitou todos.
4. **Verificação.** A suíte inteira rodou ao fim de cada épico, mais a cobertura do diff, o `jscpd` e o `pyflakes` nos arquivos novos.

## Artefatos

| Épico | Spec | Critérios | Código | Testes |
|---|---|---|---|---|
| 3 | diario-de-jogatina | DIA-01..14 | `tempo.py`, `diario.py` | `tests/test_diario.py` |
| 3 | importacao-horas-diario | HRS-01..12 | `diario_import.py` | `tests/test_importacao_horas.py` |
| 3 | retrospectiva-graficos | RET-01..12 | `graficos_svg.py`, `retrospectiva.py` | `tests/test_retrospectiva.py` |
| 3 | listas-inteligentes | LSA-01..12 | `listas_inteligentes.py`, `serializadores.py`, `static/ordem.js` | `tests/test_listas_inteligentes.py` |
| 4 | notificacoes | NTF-01..12 | `notificacoes.py` | `tests/test_notificacoes.py` |
| 4 | motor-conquistas | GAM-01..15 | `gamificacao.py`, `conquistas_catalogo.py`, `conquistas.py`, `menores/politica.py` | `tests/test_gamificacao.py`, `tests/test_perfil.py` |
| 4 | niveis-xp-sequencias | PRG-01..12 | `progressao.py` | `tests/test_progressao.py` |
| 4 | rankings-temporadas | RNK-01..12 | `rankings.py` | `tests/test_rankings.py` |
| 4 | clubes | CLB-01..14 | `clubes.py` | `tests/test_clubes.py` |

- **Branches e PRs:**
  - `feat/epico-3-diario` → PR #22;
  - `feat/epico-4-engajamento` → PR #23;
  - PR #21 leva ao `main` o restante do Épico 2 (já revisado nos PRs #13, #14 e #16).
- **Commits:** 36 desta sessão, todos com `Agent: claude-code/claude-opus-5-5` e `Spec:`. Os de código citam a spec; os dois do README levam `Spec: none`. A mediana é de 229 linhas por commit e o maior tem 534. São cerca de 7.600 linhas novas em `src/` e `tests/`.
- **Testes:** 767 passando na branch do Épico 4 (eram 527 no Épico 2). A cobertura do diff é de 96% no Épico 3 e de 95% no Épico 4. O `jscpd src tests` mede 0,76% de duplicação.
- **README:** um parágrafo por épico, a chave `STEAM_API_KEY`, os comandos novos e as linhas da tabela specs → código → testes.
- **`render.yaml` e `.env.example`:** `STEAM_API_KEY` com `sync: false`.

## Decisões

- **Relógio:** `src/app/tempo.py` concentra o fuso `America/Belem` (GLOBAL-09). Os testes fixam o relógio trocando `tempo.agora`. O XP e a curtida gravam o instante por esse relógio, para o teto diário e as janelas de ranking baterem com o relógio dos testes.
- **Perfil privado:** `diario.pode_ver_perfil` é a regra única (DIA-12). `diario.ocultos_para` é a mesma regra em SQL, usada no feed, na página do jogo, na API v1, nos rankings e na média dos seguidos da retrospectiva, sem uma consulta por linha.
- **Pontos de extensão para specs que ainda não existem:**
  - `menores/politica.py` traz `pode(user, acao, alvo)` com a tabela `ACOES` de PMN-01. Por enquanto libera tudo, porque não há faixa etária (IDA), e levanta `ValueError` para ação desconhecida. Os testes de menor protegido trocam a regra da ação.
  - `rankings.bloqueados_com` devolve vazio até a spec de bloqueios (BLQ).
  - O aviso semanal de sequência roda por `flask --app app progressao avisar-sequencias`, já que a fila de jobs (JOB) ainda não existe. Quando ela entrar, só falta o `@tarefa("aviso_sequencia")`.
  - O `Report` ganhou `alvo_tipo` e `alvo_id` (parte de DEN-01) para a denúncia de post de clube (CLB-11). A fila do admin continua só com avaliações.
- **Eventos:** `gamificacao.emitir_evento` é o ponto único de reação (GLOBAL-14).
  - PRG e CLB entram como ouvintes (`gamificacao.ouvir`) e registram métricas próprias (`semanas_seguidas`, `nivel`, `metas_clube`), sem que o motor importe esses módulos.
  - Uma falha no motor vai para o log e não muda a resposta da ação.
- **Conquistas da AP1:** viraram itens do catálogo, com os mesmos códigos. O `flask seed` roda `conquistas sincronizar`, `conquistas recalcular` e `rankings abrir-temporada` a cada subida. Como o Render recria o banco a cada deploy, as conquistas antigas reaparecem sem aviso.
- **Recálculo sem aviso:** a data estimada sai da métrica quando ela sabe datar os registros (avaliações e sessões). Quando a data é desconhecida, a conquista é tratada como antiga, sem aviso nem atividade.
- **Teto diário:** um lançamento que passaria de 150 XP é gravado com 0, como diz PRG-05, em vez de completar até 150.
- **Página do jogo:** passou a carregar o autor da avaliação junto (`joinedload`). Sem isso, o selo "Xh registradas" (DIA-09) faria o número de consultas crescer com o número de resenhas.
- **Orçamento do perfil (PRF-09):** subiu de 15 para 20 consultas, como a DIA altera.

## Pontos de atenção

- **PRs empilhados do Épico 2:** os PRs #13, #14 e #16 foram aprovados e mergeados em `feat/fontes-multiloja`, `feat/conectores-gog-rawg` e `feat/deduplicacao-catalogo`. Depois do merge do #12, o GitHub não reapontou a base deles para o `main`, e esses commits estão fora da `main` e da produção. O PR #21 corrige isso.
  - Ordem de merge: #21, depois #22, depois #23.
  - Os PRs #22 e #23 apontam para o `main`. Até os anteriores entrarem, o diff deles inclui os commits dos anteriores.
- **Autoria (≥ 30% por integrante):** todos os commits desta sessão são do Andrey, e o plano atribuía o Épico 3, as notificações e os clubes ao Fabrício. A rubrica cobra 30% dos commits de cada um.
- **Revisão:** os PRs precisam da aprovação do Fabrício antes do merge (critério Revisão).
- **Mapa de dados (GLOBAL-12):** as colunas novas ainda não estão anotadas, porque a spec MAP não foi implementada. O teste de MAP-03 vai listar todas quando ela entrar.
- **Menores:** "lista inteligente de menor nasce privada" (LSA), o aviso noturno e as regras por faixa só valem quando IDA e PMN preencherem `politica.ACOES`.
