# Plano — DEN: denúncia de risco a menores (2026-11-27)

Spec: `SPEC/2026-11-15-denuncias-menores.md` (DEN-01..12), já na main. Branch `feat/denuncias-menores`, empilhada
sobre `feat/direitos-do-titular` (PR #64), porque o DEN-09 muda a exclusão de conta do DIR.

## O que já existe e é reaproveitado
- `Report` já tem `alvo_tipo` e `alvo_id` (CLB-11 usa `post_clube`); a fila do admin é `admin.reports`/`decide`.
- IDA: `User.suspensa_em` derruba a sessão (`current_user`) e o login; contestação de idade (IDA-11).
- RTN: `nao_preservada` já filtra `preservar_ate` em qualquer tabela que ganhar a coluna.
- DIR: `excluir_conta`, `anonimizar_conta`, `PedidoTitular`. NTF tipo `sistema`. `audit()`.

## Camadas
1. **model** — `Report`: `reporter_id` opcional (notificação pública), `subcategoria`, `prazo_em`, `cautelar`,
   `decisao`, `fundamentacao`, `primeiro_decisor`, `recurso_em`, `recurso_texto` e os dados do notificante
   (`notificante_nome`, `notificante_email`, `qualidade`, `link`). `preservar_ate` em `user`, `review`, `comment` e
   `post_clube`; `Comment.oculto`. Tabelas novas `EventoDenuncia` (só inserção) e `ComunicacaoAutoridade`. Mapa e
   backup. Muda tabela existente: `./dev.sh reset`.
2. **serviço** — `src/app/denuncias.py`: alvo e autor de cada tipo, criar denúncia (validação, cautelar, preservação
   provisória), decidir (fundamentação, efeitos, notificação, recurso por outro admin), recurso, comunicação, trilha.
3. **rotas** — `POST /denunciar/<alvo_tipo>/<id>`; `/denunciar-risco-a-menor` (pública, identificada);
   `/admin/denuncias` com a aba "Risco a menores" primeiro; `/admin/denuncias/<id>` (trilha, decisão, comunicação);
   `/denuncias/<id>/recurso` do autor. As rotas antigas de denúncia de avaliação e de post de clube passam a chamar o
   serviço; `ocultar`/`rejeitar` do admin exigem fundamentação.
4. **preservação (DEN-09)** — apagar avaliação, comentário ou post preservado só oculta; a exclusão de conta
   preservada desliga a conta (suspensa, sessões e tokens caem, conteúdo oculto) e deixa o pedido aberto; o expurgo
   anonimiza quando a preservação vence; nem a inatividade nem a pendência apagam conta preservada.
5. **templates** — botão "Denunciar" (com a categoria de risco) em comentários, perfis e posts; página pública;
   fila, detalhe e recurso.
6. **testes** — `tests/test_denuncias.py`, `test_den_NN_*`; ajustes nos testes de ADM/AVA/CLB que decidiam sem
   fundamentação ou esperavam a denúncia apagada com a avaliação.
7. **docs** — README e registro da sessão.

## Premissas
- A denúncia de perfil é decidida com "suspender" ou "rejeitar"; "ocultar" vale para avaliação, comentário e post.
- A notificação pública aponta o conteúdo pelo link; quando o link é de uma avaliação, comentário, perfil ou post do
  GameLog, a denúncia fica ligada a ele (e a cautelar vale); senão, o alvo é só o link.
- A trilha registra eventos de toda denúncia; a denúncia não é apagada com o conteúdo (o alvo fica sem linha).
- A preservação provisória começa na criação da denúncia grave e é recontada a partir da decisão.
