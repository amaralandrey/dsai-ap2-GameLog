Fixes #N · Spec: `SPEC/AAAA-MM-DD-<nome>.md` (SIGLA-01..NN)

## Problema

O que quebra ou falta, contado pelo impacto em quem usa (jogador, admin, deploy) e em que cenário acontece. Sem nome de função, arquivo ou símbolo. Numa feature, troque por `## Contexto` (o que a spec pede e por quê) e apague `## Causa`.

## Causa

A causa-raiz técnica: símbolo, arquivo e fluxo do dado que levam ao problema.

## Solução

**Mudança na spec, em commit separado e antes do código:** critérios alterados e o motivo. Apague se a spec não mudou.

- **<parte>** (`src/app/<arquivo>.py`): o que muda e por que essa abordagem, não um remendo.

## Testes

Testes novos em `tests/test_<parte>.py`, com a sigla no nome, e o que cada um cobre. O que rodou localmente, com o número de testes, e a cobertura do diff. Se a tela mudou, o que foi conferido no navegador com `./dev.sh`.

## Revisar

A premissa que o revisor precisa confirmar, o efeito colateral intencional ou o `./dev.sh reset` necessário. Apague se não houver.

Sessão: `prompts/sessoes/AAAA-MM-DD-HHMM-<autor>-<ferramenta>.md`
