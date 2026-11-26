# Servidor MCP do GameLog (2026-11-15)

## O quê e por quê
Na conversa de 2026-11-10, a cliente sugeriu "um mpc e kills", que a dupla interpreta como **MCP e skills**: deixar um agente de IA (Claude Code, Claude Desktop ou outro cliente MCP) consultar o catálogo e agir no GameLog em nome do usuário, por exemplo "o que tenho na wishlist que está na GOG?" ou "registra 2h de Hades hoje". O Model Context Protocol é JSON-RPC 2.0. Esta spec implementa o transporte stdio com a biblioteca padrão do Python, sem SDK nem dependência nova, sobre os serviços e os serializadores da API v2, autenticado pelo token pessoal. Também traz skills curtas que ensinam o agente a usar as ferramentas.

Depende de: AP2 (tokens, escopos, serializadores e funções de serviço), DIA (registrar sessão), REC/COL (recomendações), AFI (comparar gosto), MRG-12 (lojas do jogo). Respeita: AP2-04 (escopos), PMN e BLQ (as ferramentas devolvem o mesmo que a API devolveria ao dono do token).

## Critérios de aceitação
- MCP-01: `flask --app app mcp` lê mensagens JSON-RPC 2.0 delimitadas por linha no stdin e escreve as respostas no stdout, uma por linha. Logs e avisos vão só para o stderr, e o stdout carrega apenas JSON-RPC. O código fica em `src/app/mcp/` (`protocolo.py` para o transporte e o despacho, `ferramentas.py` para as ferramentas), só com a biblioteca padrão.
- MCP-02: `initialize` responde `protocolVersion` (a versão pedida pelo cliente, se o servidor a suportar, senão a mais recente suportada), `capabilities: {"tools": {}}` e `serverInfo` (`name: "gamelog"` e a versão). `notifications/initialized` não recebe resposta. `ping` responde `{}`. Uma chamada antes de `initialize` responde erro `-32002`.
- MCP-03: o servidor responde aos erros de protocolo com os códigos do JSON-RPC:
  - JSON inválido: `-32700`;
  - requisição sem `jsonrpc: "2.0"` ou sem `method`: `-32600`;
  - método desconhecido: `-32601`;
  - parâmetros inválidos: `-32602`.

  Uma notificação (mensagem sem `id`) nunca recebe resposta. Uma mensagem ruim não derruba o servidor.
- MCP-04: o token vem da variável `GAMELOG_TOKEN`, e nunca de argumento de linha de comando, para não aparecer na lista de processos. Ele é validado como na API v2 (AP2-03). Sem token, ou com token inválido, expirado ou revogado, o comando escreve `erro=token inválido` no stderr e sai com código 1 antes de ler o stdin. O token nunca aparece no stdout, no stderr nem nas respostas.
- MCP-05: `tools/list` devolve as ferramentas `buscar_jogos`, `detalhes_jogo`, `disponivel_em`, `minhas_listas`, `marcar_status`, `avaliar`, `registrar_sessao`, `recomendacoes` e `comparar_gosto`, cada uma com `name`, `description` em português e `inputSchema` (JSON Schema com `required` e `additionalProperties: false`). Cada ferramenta exige um escopo (AP2-04): as de leitura (`buscar_jogos`, `detalhes_jogo`, `disponivel_em`, `minhas_listas`, `recomendacoes`, `comparar_gosto`) exigem `ler`, e as de escrita (`marcar_status`, `avaliar`, `registrar_sessao`) exigem `escrever`. `tools/list` mostra só as ferramentas do escopo do token, e `tools/call` de uma ferramenta fora do escopo devolve `isError: true` com "escopo insuficiente", sem chamar o serviço.
- MCP-06: `tools/call` valida `arguments` contra o `inputSchema` antes de executar (tipos, obrigatórios, enum, mínimo e máximo, sem propriedades extras). Argumento inválido devolve resultado com `isError: true` e a mensagem do campo, sem chamar o serviço. Uma ferramenta desconhecida responde `-32602`.
- MCP-07: as ferramentas de leitura devolvem `content` com um bloco `text` (resumo legível em português) e `structuredContent` com o JSON dos serializadores da API v2:
  - `buscar_jogos(termo, genero?, loja?, limite≤20)` usa a busca de BUS e GOG-13;
  - `detalhes_jogo(slug)`;
  - `disponivel_em(slug)` traz as lojas e os links de `JogoFonte`, MRG-12;
  - `minhas_listas(status?)` traz status e listas, inclusive as privadas, do dono do token.

  Jogo inexistente ou removido devolve `isError: true` com "jogo não encontrado". O slug de um jogo mesclado responde pelo jogo principal, como o 301 da API v2 (MRG-08); se o principal foi removido, devolve "jogo não encontrado".
- MCP-08: as ferramentas de escrita chamam as mesmas funções de serviço que AP2-10 e a interface, com as mesmas validações, atividades, eventos e notificações:
  - `marcar_status(slug, status)` segue LIS-01/02;
  - `avaliar(slug, nota, texto?, spoiler?)` segue AVA-01..03;
  - `registrar_sessao(slug, duracao_min, data?, zerou?, anotacao?)` segue DIA, com a data padrão de hoje em America/Belem.

  O slug de um jogo mesclado devolve "jogo não encontrado", como o 404 das escritas da API v2.
- MCP-09: `recomendacoes(limite≤10)` devolve as recomendações do dono do token (REC/COL), e `comparar_gosto(username)` devolve a afinidade de AFI. Um usuário inexistente, ou que o dono do token não pode ver (`pode_ver_perfil`, bloqueio ou política de menores), devolve "usuário não encontrado".
- MCP-10: cada `tools/call` conta no limite de taxa do token (AP2-05). Passando do limite, o resultado traz `isError: true` e "limite de requisições; tente em N s". Toda chamada de ferramenta de escrita com token válido grava a auditoria de AP2-06 com o método `MCP`, a rota `mcp:<ferramenta>` e o código que a API v2 gravaria: 200 no sucesso, 403 sem escopo, 404 para jogo não encontrado, 422 para argumento ou regra recusados e 429 no limite. Ferramenta de leitura não grava auditoria.
- MCP-11: `skills/` traz arquivos `SKILL.md` curtos, cada um com frontmatter `name` e `description` e instruções em português:
  - `gamelog-catalogo`: buscar, ver lojas e detalhes;
  - `gamelog-diario`: registrar sessões e marcar status;
  - `gamelog-descoberta`: recomendações e comparar gosto.

  O README mostra como registrar o servidor num cliente MCP (comando, `cwd` e `GAMELOG_TOKEN` no ambiente) e como criar o token. Um teste confere que toda ferramenta citada nas skills existe em `tools/list`.
- MCP-12: os testes rodam sem rede, alimentando o laço de leitura com `io.StringIO` e cobrindo:
  - `initialize`, a chamada antes dele e `ping`;
  - os códigos de erro -32700, -32600, -32601 e -32602, e as notificações sem resposta;
  - stdout só com JSON-RPC, e exceção numa ferramenta com o log no stderr;
  - token ausente e revogado (código 1);
  - `tools/list` e `tools/call` com token só `ler` e só `escrever`;
  - slug mesclado na leitura e na escrita;
  - auditoria da escrita recusada;
  - validação de argumentos;
  - cada ferramenta com sucesso e com jogo ou usuário inexistente;
  - escrita com a mesma regra da interface (status, AVA-11 e DIA de "planejado" para "jogando");
  - limite de taxa;
  - a correspondência entre skills e ferramentas.

## Fora do escopo
Transporte HTTP/SSE ("Streamable HTTP") e servidor MCP hospedado no Render, OAuth do MCP, `resources` e `prompts` do protocolo, amostragem (`sampling`), notificações de mudança de lista de ferramentas, ferramentas de clubes, torneios e listas inteligentes (entram quando essas specs existirem, cada uma na própria spec), SDK oficial `mcp` e publicação das skills em marketplace.
