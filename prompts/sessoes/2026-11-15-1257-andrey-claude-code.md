# Sessão Claude Code — Implementação da spec deduplicacao-catalogo (2026-11-15)

**Ferramenta:** Claude Code CLI (Claude Opus 5.5, `claude-opus-5-5`)
**Quem conduziu:** Andrey Amaral
**Horário:** 12:57–13:25 (horário de Belém)
**Spec:** `SPEC/2026-11-15-deduplicacao-catalogo.md` (MRG-01..15), sobre `SPEC/2026-11-15-conectores-gog-rawg.md` (PR #13) e `SPEC/2026-11-15-fontes-multiloja.md` (PR #12)

## Custo

Medido no log da sessão do Claude Code (`usage` de cada resposta do modelo, sem repetir a mesma resposta), com o preço de API do Claude Opus 5.5.

| Tipo de token | Tokens | Preço (US$/1M) | Custo (US$) |
|---|---:|---:|---:|
| Entrada | 104 | 4,00 | 0,00 |
| Saída | 75.914 | 20,00 | 1,52 |
| Leitura de cache | 7.412.677 | 0,20 | 1,48 |
| Escrita de cache (TTL 1h) | 174.592 | 8,00 | 1,40 |
| **Total** | **7.663.287** | | **≈ 4,40** |

51 requisições ao modelo até a escrita deste registro; as respostas seguintes (commit deste arquivo, push e PR) ficam de fora.

## Prompts (em ordem)

1. implemente a spec deduplicação, atende especialmente ao plan e aos requisitos avaliativos, assim como a documentação de artefatos e rastreabilidade

## O que foi feito

Branch `feat/deduplicacao-catalogo`, criada a partir de `feat/conectores-gog-rawg` (PR #13, ainda sem aprovação), porque a spec depende dos vínculos `JogoFonte` e dos links de loja da RAWG. A spec já estava na `main` (PR #11) antes do primeiro commit de código.

| Commit | Critérios | Conteúdo |
|---|---|---|
| `feat(catalogo): semelhança, reconhecimento na importação e pendências de mesclagem` | MRG-01..03, 06..09, 14 | `src/app/catalogo_merge.py` (título normalizado com lista fixa de sufixos de edição, semelhança 70/20/10/10 com `difflib`, corte por `quick_ratio`, reconhecimento por link de loja e por semelhança, `PendenciaMerge` única por par, mesclagem); `Game.mesclado_em_id` e `PendenciaMerge` em `models.py`; `importar_item` e `sync` usam o reconhecimento; dois testes antigos ajustados porque IMP-04 mudou. |
| `feat(cli): sugerir-duplicatas ...` | MRG-04 | `flask --app app sugerir-duplicatas` imprime `pendencias=N` (pendências novas desta execução). |
| `feat(admin): fila de duplicatas, mesclar e ignorar com auditoria` | MRG-02, 05, 06, 08, 10, 11 | `/admin/catalogo/duplicatas` (20 por página, mais antiga primeiro, jogos lado a lado), `POST /admin/catalogo/mesclar` e `/ignorar-merge`, auditoria `catalogo.mesclar`/`catalogo.ignorar`, restaurar jogo mesclado recusado, item vinculado pelo painel conta como importado. |
| `feat(jogo): 301 do jogo mesclado, "Disponível em" e fontes na API` | MRG-08, 12, 13 | 301 na página e na API mantendo a query string; bloco de lojas com link só `http`/`https` e `rel="noopener"`; `fontes` no detalhe da API. |
| 2 × `fix(catalogo): ...` | MRG-07, 14 | Atividade acompanha a avaliação trocada; vínculo GOG assumido mantém o link da RAWG se a GOG não mandar URL (achados pelos testes). |
| 3 × `test(...)` | MRG-15 | `tests/test_merge_semelhanca.py`, `tests/test_merge_mesclagem.py`, `tests/test_merge_pagina.py`: 89 testes nomeados pelo ID do critério, sem rede. |
| `docs(readme): ...` | — | Funcionamento da deduplicação, comando, fila do admin, recriação do banco e linha da spec na tabela specs → código → testes. |

Validações locais: **515 testes passando** (eram 426); cobertura total 98%, `catalogo_merge.py` 100%; **cobertura do diff 100%** (305 linhas) contra `feat/conectores-gog-rawg`; `jscpd` 0,37% de duplicação. Mutação manual: 10 regras revertidas uma a uma (sufixos de edição, reconhecimento por link, limiar 90, mais recente no principal, par único, 301, vínculo da mesma loja no secundário, restaurar mesclado, `rel="noopener"`, só a fonte de origem atualiza metadados), e cada mudança derrubou ao menos um teste (10/10).

Teste de fumaça num banco temporário com o seed real (153 jogos): o seed continua com 153 jogos e nenhuma pendência; `sugerir-duplicatas` roda em menos de 1 s e não acha falsos pares no snapshot; a "The Witcher 3: Wild Hunt - Complete Edition" da GOG (id 1207664663) ficou com semelhança 86 com a "The Witcher 3: Wild Hunt — Remastered" do snapshot e virou pendência para o admin, como pede MRG-03.

## Decisões e pontos de atenção

- **Troca de registros em conflito (MRG-07):** a unicidade (usuário, jogo) do SQLite é conferida linha a linha, então a troca entre principal e secundário passa por um id de jogo provisório com `PRAGMA defer_foreign_keys` dentro da transação; nada é apagado e a chave estrangeira é conferida no commit.
- **Fonte de origem é dona dos metadados:** depois que a GOG é ligada a um jogo da Steam, a sincronização da GOG só toca no vínculo; senão as duas fontes trocariam título e sinopse a cada execução. O snapshot (`arquivo`) continua sendo corrigido pela Steam, a loja que ele copia.
- **Jogo que já tem outro vínculo da mesma fonte:** semelhança ≥ 90 com um jogo que já tem um vínculo da fonte com outro id (duas entradas da mesma loja) cria o jogo com pendência, porque um jogo só tem um vínculo por fonte (FNT-04); o vínculo provisório da GOG criado pela RAWG é a exceção (MRG-14).
- **Inconsistência da spec:** com mais de 1 ano de diferença a semelhança máxima é 80, então "≥ 90 com diferença de até 2 anos" na prática vale para até 1 ano, e "≥ 90 com mais de 2 anos" nunca acontece; o código segue o texto da spec e os testes documentam o caso.
- **`sugerir-duplicatas` sugere todo par ≥ 70**, inclusive ≥ 90, porque a mesclagem de jogos que já existem é sempre decisão do admin (fora do escopo da spec mesclar automaticamente).
- **Pendência com jogo já mesclado em outra** é recusada com mensagem; desfazer mesclagem continua fora do escopo.
- **Banco local:** a spec acrescenta `game.mesclado_em_id` e a tabela `pendencia_merge`; rode `./dev.sh reset` (no Render o banco é recriado a cada deploy).
- **Base do PR:** `feat/conectores-gog-rawg` (PR #13), para mostrar só o diff desta spec; a base vira `main` quando os PRs #12 e #13 forem mesclados.
- **Plano:** o Épico 2 do `.claude/plan/plan-ap2.md` já descreve esta spec (SPEC-03, MRG-01..15, `catalogo_merge.py`) e não precisa de mudança.
