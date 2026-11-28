# Plano — CLS: classificação indicativa (2026-11-27)

Spec: `SPEC/2026-11-15-classificacao-indicativa.md` (CLS-01..10), já na main. Branch `feat/classificacao-indicativa`,
a partir da `main` (não depende de OFR, RSP, DIR nem DEN).

## O que já existe e é reaproveitado
- `visible_games()` é o ponto único das listagens de jogos (catálogo, busca, gêneros, recomendações, listas,
  estatísticas, API v1 e v2); o filtro por idade entra nele, como o `removed_at` (CAT-07).
- IDA: `faixa_etaria` e `idade`; sem data ou com idade contestada, a faixa mais protetiva (IDA-03, IDA-11).
- Conectores: `steam._jogo` já lê o `appdetails` (tem `required_age`); a busca e o detalhe da RAWG trazem
  `esrb_rating`. Importação (`fontes.importar_item`) e sincronização (`sync._sincronizar_item`) recebem o dict `j`.
- Snapshot: `export-games` grava o `j` da Steam; `enriquecer-snapshot` já consulta a busca da RAWG por jogo.

## Camadas
1. **model** — `Game.classificacao` e `Game.classificacao_origem`, validados (`ValueError`). Colunas novas em `game`:
   `./dev.sh reset`.
2. **serviço** — `src/app/menores/classificacao.py`: tabelas Steam e ESRB, faixa mais restritiva, `aplicar` (nunca
   sobrescreve o admin), idade-limite do usuário e o filtro SQL usado por `visible_games`.
3. **fontes** — Steam e RAWG põem `classificacao` no `j`; importação e sincronização aplicam; o snapshot grava o
   campo e a origem, e o seed lê sem rede.
4. **telas e API** — selo na página do jogo, página reduzida para quem tem idade abaixo da faixa, `classificacao_max`
   em `/buscar` e `/api/jogos`, campo no formulário do admin com `AuditLog`, `classificacao` nos serializadores.
5. **testes** — `tests/test_classificacao.py`, `test_cls_NN_*`.
6. **docs** — README e registro da sessão.

## Premissas
- A idade-limite usa a data de nascimento; sem data ou com idade contestada vale 12 anos (a faixa mais protetiva
  em que a conta ainda funciona). Adulto e visitante não são filtrados (o bloqueio a visitantes está fora do escopo).
- O filtro vale onde a listagem passa por `visible_games()`; a página do jogo abre o jogo sem o filtro para mostrar o
  aviso (CLS-08).
- O snapshot guarda `classificacao_origem` junto, para o seed saber se a faixa veio da Steam ou da RAWG.
