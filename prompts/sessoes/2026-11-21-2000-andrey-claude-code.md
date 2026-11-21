# Sessão Claude Code: estado do projeto e rodapé da AP2 (2026-11-21)

**Ferramenta:** Claude Code CLI (Claude Opus 5.5, `claude-opus-5-5`), sem subagentes
**Quem conduziu:** Andrey Amaral
**Ponto de partida:** `main` em `df35225` e PRs #24 a #29 abertos, aguardando review

## Custo

Medido no log da sessão do Claude Code (`~/.claude/projects/.../5ab77bfe-....jsonl`). Cada resposta do modelo conta uma vez só, com o maior `usage` registrado para o mesmo `message.id`. Os preços são os da API do Claude Opus 5.5.

| Tipo de token | Tokens | Preço (US$/1M) | Custo (US$) |
|---|---:|---:|---:|
| Entrada | 34 | 4,00 | 0,00 |
| Saída | 5.762 | 20,00 | 0,12 |
| Leitura de cache | 803.418 | 0,20 | 0,16 |
| Escrita de cache (TTL 1h) | 40.584 | 8,00 | 0,32 |
| **Total** | **849.798** | | **≈ 0,60** |

São 15 requisições ao modelo. Este registro e o push ficam de fora da conta.

## Prompts

1. analise o estado do projeto e o que esta pendente
2. antes de prosseguir, quero mexer no front end, retirando qualquer menção à AP1, isso deve ser feito a partir de uma nova SPEC?
3. siga o exemplo proposto

## Como foi feito

1. **Estado do projeto.** Cruzei os PRs abertos (`gh pr list`, reviews e checks), as branches fora da `main`, o `git shortlog` e o cronograma do `plan-ap2.md`. Seis PRs esperam a aprovação do Fbc00, e as specs AP2, MCP, BKP, EML, PRC, OFR, PRV, CNS, DIR, RTN, INC, CTA, RSN, PMN, TRN, RSP, CLS e DEN ainda não têm código.
2. **Sem spec nova.** A única menção à AP1 visível no front era o rodapé de `base.html`. Trocar um texto não muda comportamento, então o commit aponta para a `SPEC/2026-11-15-visao-geral-ap2.md`, que descreve o produto da AP2 no lugar do da AP1. As duas docstrings que citam o CON-01 da AP1 ficaram como estão.
3. **Rodapé.** "dados fictícios gerados por seed. Projeto AP1 DSAI 2026." virou "Projeto AP2 DSAI 2026.", porque o catálogo vem das lojas desde o Épico 2. Nenhum teste dependia do texto, e a suíte inteira passou (800 testes).

## Artefatos

| Commit | O quê |
|---|---|
| `843ff64` | `fix(ui)`: rodapé cita a AP2 |
