# Tokens pessoais e API REST v2 (2026-11-15)

## O quê e por quê
A API da AP1 (`/api`, API-01..10) é pública, só de leitura e sem autenticação: não expõe nada do próprio usuário (diário, listas, status), não aceita escrita e não tem limite de taxa (está no "Fora do escopo" da spec api-json). O servidor MCP do Épico 5 (pedido da cliente interpretado como "MCP e skills"), a exportação das listas inteligentes, o backup e o direito de portabilidade precisam de um acesso autenticado por usuário e de um formato JSON único. Esta spec cria tokens pessoais com escopo, guardados só como hash, e a API v2 em `/api/v2`, com os serializadores comuns em `src/app/serializadores.py`, que as outras specs reaproveitam em vez de montar o próprio JSON.

Depende de: DIA (sessões do diário), GAM (conquistas), RNK (rankings); os endpoints dessas áreas entram quando a spec correspondente estiver na `main`. Altera: API-01 (o índice `/api` passa a listar também a versão 2). A v1 continua igual. Respeita: MAP (campos novos anotados no mapa de dados), PMN (dados de menores na API seguem a política de menores) e BLQ (bloqueios valem também na API).

## Critérios de aceitação
- AP2-01: em `/u/<username>/tokens` (só o dono; outro usuário recebe 403 e o visitante vai ao login, GLOBAL-02) o usuário cria um token com nome (1 a 60 caracteres), escopos `ler` e/ou `escrever` (ao menos um) e validade de 30, 90 ou 365 dias. O token aparece **uma única vez** no formato `glp_` + 40 caracteres aleatórios (`secrets`). O banco guarda só o SHA-256 do token, o prefixo visível (`glp_` + 6 primeiros caracteres), os escopos, `expira_em`, `criado_em`, `ultimo_uso_em` e `revogado_em`. Cada usuário tem no máximo 10 tokens ativos.
- AP2-02: a lista de tokens mostra nome, prefixo, escopos, criação, expiração e último uso, nunca o token. "Revogar" (POST) marca `revogado_em`, e o token deixa de funcionar na requisição seguinte. Encerrar as outras sessões ou trocar a senha (CTA) não revoga tokens, mas a exclusão da conta (DIR) revoga todos.
- AP2-03: toda rota sob `/api/v2/` aceita `Authorization: Bearer <token>`. Token ausente nas rotas que exigem conta, desconhecido, expirado ou revogado responde 401 em JSON `{"erro": "token inválido"}` com `WWW-Authenticate: Bearer`, sem dizer qual dos casos ocorreu. A sessão do navegador (cookie) **não** autentica na v2, o que dispensa o CSRF nessas rotas.
- AP2-04: rotas de leitura de dados do próprio usuário exigem o escopo `ler`. POST, PUT, PATCH e DELETE exigem `escrever`. Falta de escopo responde 403 `{"erro": "escopo insuficiente", "requer": "<escopo>"}`.
- AP2-05: limite de taxa por token de 60 requisições por minuto (janela fixa por minuto, contada no banco, porque os 2 workers do gunicorn não compartilham memória). Ao passar do limite, a resposta é 429 com `Retry-After` em segundos. Toda resposta autenticada traz `X-RateLimit-Limit` e `X-RateLimit-Remaining`. Os testes usam um relógio injetável.
- AP2-06: cada requisição autenticada grava `ultimo_uso_em` no token. Criar, revogar e usar um token para escrita gravam auditoria do usuário (token, método, rota, código HTTP e data), consultável pelo dono em `/u/<username>/tokens/<id>`, paginada com 20 por página (GLOBAL-04). O token e o corpo das requisições nunca entram no log.
- AP2-07: `src/app/serializadores.py` tem as funções `jogo`, `avaliacao`, `sessao`, `lista`, `usuario_publico` e `conquista`, que devolvem dicionários prontos para JSON (datas em ISO-8601, URLs absolutas). A v1 continua com o formato de API-04 sem mudar nenhum campo. A v2, a exportação de listas (LSA), o backup (BKP) e a portabilidade (DIR) usam só esses serializadores. `usuario_publico` nunca inclui e-mail, hash de senha, data de nascimento, `steam_id` nem dados de conta (API-07).
- AP2-08: a leitura pública sem token funciona em `GET /api/v2/jogos` (mesmos filtros de API-02, mais `loja` de GOG-13), `GET /api/v2/jogos/<slug>` (com `fontes`: lojas e links de `JogoFonte`, MRG-13) e `GET /api/v2/jogos/<slug>/avaliacoes`. Jogo removido responde 404, e jogo mesclado responde 301 para o principal (MRG-08).
- AP2-09: com o escopo `ler`, `GET /api/v2/eu` traz o perfil do dono do token. `GET /api/v2/eu/status` traz os status por jogo (filtro `status`), `GET /api/v2/eu/listas` e `/api/v2/eu/listas/<id>` trazem as listas, inclusive as privadas, `GET /api/v2/eu/diario` traz as sessões (filtros `de`, `ate`, `jogo`), e `GET /api/v2/eu/conquistas` traz as conquistas. Um id de lista de outra pessoa responde 404.
- AP2-10: com o escopo `escrever`:
  - `PUT /api/v2/eu/status/<slug>` aplica as mesmas regras de LIS-01/02 e AVA-11 da interface (`{"status": "jogado"}`; `null` remove o status);
  - `PUT /api/v2/eu/avaliacoes/<slug>` aplica AVA-01..03 (`{"nota": 8, "texto": "...", "spoiler": false}`);
  - `POST /api/v2/eu/diario` aplica DIA (inclusive a mudança de status de "planejado" para "jogando");
  - `POST /api/v2/eu/listas/<id>/jogos` aplica LIS-06/10.

  A API chama as mesmas funções de serviço das rotas HTML, sem duplicar validação. Entrada inválida responde 422 `{"erro": "...", "campos": {"<campo>": "<mensagem>"}}`, e as ações geram as mesmas atividades, eventos (`emitir_evento`) e notificações da interface.
- AP2-11: as listagens da v2 são paginadas por `page` e `per_page` (padrão 20, máximo 100, API-03) e respondem `{"items", "page", "pages", "total", "per_page", "proxima"}`. `proxima` é a URL absoluta da página seguinte ou `null`.
- AP2-12: `GET /api/v2/usuarios/<username>` e `/api/v2/usuarios/<username>/diario` devolvem o perfil público e o diário de outro usuário só se `diario.pode_ver_perfil(dono_do_token_ou_visitante, dono)` permitir (perfil privado, bloqueio ou política de menores). Quando não permite, a resposta é 404, igual a um usuário inexistente. `GET /api/v2/rankings/<categoria>` segue os escopos e exclusões de RNK.
- AP2-13: `GET /api/v2` devolve a versão, os endpoints com método e escopo exigido e o link para `/api/v2/docs`, uma página HTML curta com exemplos de `curl`. Todas as respostas JSON seguem API-01 (tipo, CORS `*`) e API-08 (405 e 404 em JSON). Respostas de dados do usuário levam `Cache-Control: no-store`, e as públicas, `public, max-age=60` (API-09).
- AP2-14: os testes rodam sem rede e cobrem:
  - criação com o token mostrado uma vez e só o hash no banco;
  - token expirado, revogado e desconhecido (401);
  - escopo insuficiente (403);
  - limite de taxa com relógio falso (429 e cabeçalhos);
  - leitura e escrita de status, avaliação, diário e lista, com a mesma validação da interface (422);
  - lista privada de outro usuário (404) e perfil privado (404);
  - formato dos serializadores, sem campos de conta;
  - paginação com `proxima`;
  - v1 inalterada (os testes de `test_api.py` continuam passando);
  - número de consultas de uma listagem de 20 itens, no máximo 8.

## Fora do escopo
OAuth2 e login de aplicativos de terceiros, tokens de aplicação sem usuário, webhooks, GraphQL, documentação OpenAPI gerada, escrita de comentários, curtidas e follows pela API, endpoints administrativos, limite de taxa por IP para a leitura pública e desligamento da v1.
