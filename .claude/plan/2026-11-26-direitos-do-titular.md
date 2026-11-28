# Plano — DIR: direitos do titular (2026-11-26)

Spec: `SPEC/2026-11-15-direitos-do-titular.md` (DIR-01..14), já na main. Branch `feat/direitos-do-titular`, empilhada
sobre `feat/responsavel-supervisao` (PR #63), porque o DIR-12 usa o vínculo do responsável.

## O que já existe e é reaproveitado
- RTN: `anonimizar_conta` aplica `regra_anonimizacao` coluna por coluna e tabela por tabela (DIR-06/07).
- MAP: `campos_exportaveis` (MAP-09) sem campo de autenticação (MAP-06); `suboperadores` (MAP-07).
- EML: o modelo `conta_excluida` vai ao endereço antigo e o mascara depois do envio (DIR-08).
- PRV-07: `PedidoTitular` e o canal `/privacidade/contato`; as rotas `/conta/privacidade*` e `/conta/excluir` já
  são livres dos desvios de idade e de aceite.
- CTA: `checar_senha`, `segundo_fator`, `serializador`/`ler_link` (link assinado com o relógio do app).
- RSP: `vinculo_do_painel` e `e_responsavel` (DIR-12).

## Camadas
1. **model** — `PedidoTitular` ganha `prazo_em`, `concluido_em`, `resposta`, `decidido_por`; `User` ganha
   `oposicao_recomendacao`; anotações no mapa. Muda tabela existente: `./dev.sh reset`.
2. **serviço** — `src/app/privacidade/direitos.py`: prazo (`PRAZO_TITULAR_DIAS`, padrão 15), resumo do acesso
   simplificado, exportação pelo mapa (ZIP em memória, `dados.json` + um CSV por tabela), exclusão, decisão da fila.
3. **rotas** — `/conta/privacidade/direitos`, `/conta/privacidade/exportar`, `/conta/privacidade/pedidos`,
   `/conta/privacidade/oposicao`, `/conta/excluir`, `/admin/privacidade/pedidos`, confirmação do pedido sem login,
   e no painel do responsável `/responsavel/<id>/dados` e `/responsavel/<id>/excluir`.
4. **ganchos** — `vizinhos` (COL-01) sem quem se opôs; o canal sem login nasce `aguardando_confirmacao`; perfil
   anonimizado 404; avaliação e comentário de conta anonimizada sem link; `recuperacao.conta_anonimizada` usa a marca
   de `anonimizar.anonimizada`.
5. **templates** e e-mails `confirmar_pedido` e `pedido_respondido`.
6. **testes** — `tests/test_direitos.py`, `test_dir_NN_*`.
7. **docs** — README e registro da sessão.

## Premissas
- A exportação é genérica pelo mapa: entra toda tabela com coluna dona (`user_id` anotado como DONO ou a lista
  de `DONOS`), só com `campos_exportaveis`; colunas que apontam para outra pessoa viram o nome de usuário, e as que
  apontam para um jogo viram o slug. Datas em ISO 8601, como nos serializadores da API v2.
- A preservação por denúncia (DEN-09, `preservar_ate`) entra com a DEN: até lá, a exclusão anonimiza na hora.
- O responsável exclui a conta do menor direto (com a própria senha), sem fila, como o próprio titular.
