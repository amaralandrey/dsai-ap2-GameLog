# Épico 5 (API v2, MCP e backup) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar tokens pessoais com API REST v2, o servidor MCP por stdio e o backup assinado do SQLite, nos critérios AP2-01..14, MCP-01..12 e BKP-01..12.

**Architecture:** Três PRs em sequência, um por spec. O PR 1 cria `tokens.py`, `api_v2.py` e os serializadores. Ele também extrai as funções de serviço das rotas de status, avaliação, lista e diário, que a interface, a API e o MCP passam a chamar. O PR 2 (`src/app/mcp/`) e o PR 3 (`backup.py`) partem do PR 1 e só consomem as interfaces dele.

**Tech Stack:** Python 3.12, Flask 3, Flask-SQLAlchemy (SQLite), Jinja2, pytest. Só biblioteca padrão (GLOBAL-11).

**Spec:** `SPEC/2026-11-15-api-v2-tokens.md`, `SPEC/2026-11-15-servidor-mcp.md`, `SPEC/2026-11-15-backup-restore.md` e `SPEC/2026-11-15-visao-geral-ap2.md` (GLOBAL-09..18). Leia as quatro antes da primeira tarefa.

## Global Constraints

- Sem dependência nova no `requirements.txt` (GLOBAL-11).
- Nenhum comentário novo no código, template, YAML ou CSS. Comentário existente fica intacto.
- Domínio em português (`token`, `sessao`, `backup`), técnico em inglês (`bp`, `query`).
- Coluna nova em `models.py` é anotada em `src/app/privacidade/mapa.py` (GLOBAL-12). O teste do MAP falha sem isso.
- Visibilidade de usuário só por `diario.pode_ver_perfil`, `bloqueios.bloqueado_entre` e `menores.politica.pode` (GLOBAL-13).
- Rota HTML, API v2 e MCP chamam a mesma função de serviço. JSON de entidade só por `serializadores.py` (GLOBAL-14).
- Ação de admin grava `AuditLog` `<area>.<acao>` por `admin.audit` (GLOBAL-17).
- Token, `BACKUP_KEY` e `GAMELOG_TOKEN` nunca entram em log, saída, resposta ou arquivo (GLOBAL-16). Token guardado só como SHA-256.
- Testes sem rede (GLOBAL-10). O relógio é fixado com `monkeypatch.setattr(tempo, "agora", ...)`.
- Nome do teste com a sigla: `test_ap2_05_...`, `test_mcp_03_...`, `test_bkp_06_...`. O teste falha quando a mudança é revertida.
- Rodar só o teste da parte: `rtk .venv/bin/python -m pytest -q tests/<arquivo>.py -k <sigla>`.
- **Git:** o agente faz `git add` e sugere a mensagem. O dev comita. Sem `Co-Authored-By`. Trailers obrigatórios:
  `Agent: claude-code/claude-opus-5-5` e `Spec: SPEC/2026-11-15-<spec>.md`. Commit por camada, mediana < 400 linhas.
- Coluna nova em tabela existente exige `./dev.sh reset` (dizer no handoff). Este plano só cria tabelas novas.
- Todo dia com commit de agente tem `prompts/sessoes/AAAA-MM-DD-HHMM-fabricio-claude-code.md` com tokens e US$.

## Review Focus

1. **Corpo JSON com tipo errado na escrita** (`"nota": true`, `"nota": 8.5`, corpo que é lista, sem `Content-Type`): a API responde 422 com o campo, nunca 500 nem conversão silenciosa. Teste na Tarefa 4 (`ler_nota`) e na Tarefa 8.
2. **Cookie de sessão junto com o token de outra pessoa:** a v2 age só como o dono do token, e o cookie sozinho em `/api/v2/eu` responde 401. Teste na Tarefa 6.
3. **Preflight CORS** (`OPTIONS` de um `PUT` com `Authorization`): responde 200 com `Access-Control-Allow-Headers`, sem exigir token. Teste na Tarefa 6.
4. **Restauração pelo painel com sessões abertas:** os ids se repetem no backup, então um cookie antigo não pode virar outro usuário. Teste na Tarefa 17.
5. **Id da prévia no segundo POST de restauração** (`../`, texto fora de 32 hex) e upload acima de 20 MB: 400 ou 413, sem abrir arquivo fora de `BACKUP_DIR`. Teste na Tarefa 19.

## Pré-requisitos (dev, antes da Tarefa 1)

- [ ] Abrir issue "Extrair status, avaliação, lista e sessão para funções de serviço (AP2-10)". Corpo: a API v2 e o MCP precisam das mesmas regras das rotas HTML sem duplicar validação. Cobertura: `tests/test_listas.py`, `tests/test_avaliacoes.py` e `tests/test_diario.py` continuam verdes. Citar o número no PR 1.
- [ ] `git checkout main && git pull && git checkout -b feat/api-v2-tokens`. As specs já estão na `main`.

---

## PR 1 — Tokens e API v2 (`feat/api-v2-tokens`, Spec AP2)

### Tarefa 1: Modelos de token e mapa de dados

**Files:**
- Modify: `src/app/models.py` (três classes novas depois de `AuditLog`)
- Modify: `src/app/privacidade/mapa.py` (`FINALIDADES` e `CAMPOS`)
- Test: `tests/test_api_v2_tokens.py`

**Interfaces:**
- Produces: `TokenApi` (tabela `token_api`): `id`, `user_id` (FK `user.id`, index), `nome` String(60), `prefixo` String(10), `token_hash` String(64) unique, `escopos` String(20) (`"ler"`, `"escrever"` ou `"ler escrever"`), `criado_em`, `expira_em`, `ultimo_uso_em` (nullable), `revogado_em` (nullable), `user = relationship(User)`.
- Produces: `LimiteTaxaToken` (tabela `limite_taxa_token`): `id`, `token_id` (FK), `janela` DateTime, `contagem` Integer; `UniqueConstraint("token_id", "janela")`.
- Produces: `AuditoriaToken` (tabela `auditoria_token`): `id`, `token_id` (FK), `user_id` (FK, index), `acao` String(10) (`criar`, `revogar`, `escrita`), `metodo` String(10), `rota` String(200), `status_http` Integer nullable, `criado_em`.
- Produces: `mapa.FINALIDADES["integracoes"] = "Tokens pessoais, API v2 e servidor MCP"`.

- [ ] **Step 1: Escrever o teste que falha**

```python
def test_ap2_02_exclusao_da_conta_revoga_os_tokens(app):
    uid = make_user(app, "ana")
    with app.app_context():
        from app.privacidade.anonimizar import anonimizar_conta
        from app.tokens import autenticar, criar
        _, texto = criar(db.session.get(User, uid), "cli", ["ler"], 30)
        db.session.commit()
        anonimizar_conta(db.session.get(User, uid))
        db.session.commit()
        assert autenticar(texto) is None
```

- [ ] **Step 2: Rodar e ver falhar.** `rtk .venv/bin/python -m pytest -q tests/test_api_v2_tokens.py -k ap2_02` → FAIL (`ModuleNotFoundError: app.tokens`). O teste só passa depois da Tarefa 2. Nesta tarefa, o critério de pronto é o teste do mapa verde.
- [ ] **Step 3: Criar os modelos e anotar o mapa.** Regras do mapa: `token_api` com `finalidade="integracoes"`, `categoria="autenticacao"`, `exportar=False`, `anonimizar="apagar_linha"` (`id` TECNICO, `user_id` DONO). `auditoria_token` como `token_api`, com `categoria="atividade"`. `limite_taxa_token` com `categoria="tecnico"`, `exportar=False` e `anonimizar="apagar_linha"`. O `apagar_linha` faz `anonimizar_conta` apagar os tokens, e isso cumpre AP2-02 sem código novo.
- [ ] **Step 4: Rodar o teste do mapa.** `rtk .venv/bin/python -m pytest -q tests/test_mapa_dados.py` → PASS.
- [ ] **Step 5: Stage e mensagem.** `feat(tokens): modelos de token, limite de taxa e auditoria no mapa de dados`.

### Tarefa 2: Serviço de tokens

**Files:**
- Create: `src/app/tokens.py`
- Test: `tests/test_api_v2_tokens.py`

**Interfaces:**
- Consumes: modelos da Tarefa 1, `tempo.agora_utc()`.
- Produces, em `tokens.py`:
  - constantes: `PREFIXO = "glp_"`, `TAMANHO = 40`, `ESCOPOS = ("ler", "escrever")`, `VALIDADES = (30, 90, 365)`, `MAX_ATIVOS = 10`, `LIMITE_POR_MINUTO = 60`;
  - `hash_token(texto: str) -> str` (SHA-256 hex);
  - `validar(user, form) -> tuple[dict, dict]`: devolve `(dados {"nome", "escopos", "dias"}, erros por campo)`;
  - `criar(user, nome, escopos: list[str], dias: int) -> tuple[TokenApi, str]`: grava a auditoria `criar` e não faz commit;
  - `ativos(user_id) -> Query`;
  - `autenticar(texto: str | None) -> TokenApi | None`;
  - `tem_escopo(token, escopo) -> bool`;
  - `revogar(token) -> None`: grava a auditoria `revogar` e não faz commit;
  - `consumir(token) -> tuple[bool, int, int]`: devolve `(permitido, restantes, retry_after_s)`;
  - `auditar(token, acao, metodo="", rota="", status_http=None) -> None`.

- [ ] **Step 1: Escrever os testes que falham.** Fixture compartilhada no topo do arquivo, que as tarefas seguintes importam:

```python
def token(app, username="ana", escopos=("ler", "escrever"), dias=30):
    with app.app_context():
        from app.tokens import criar
        user = User.query.filter_by(username_lower=username).first() or db.session.get(User, make_user(app, username))
        _, texto = criar(user, "teste", list(escopos), dias)
        db.session.commit()
        return texto

def auth(texto):
    return {"Authorization": f"Bearer {texto}"}

def relogio(monkeypatch, quando):
    monkeypatch.setattr(tempo, "agora", lambda: quando.replace(tzinfo=tempo.FUSO))
```

Testes:
  - `test_ap2_01_formato_e_so_hash_no_banco`: `re.fullmatch(r"glp_[A-Za-z0-9]{40}", texto)`. A linha tem `token_hash == sha256(texto)`, `prefixo == texto[:10]`, e o texto não aparece em nenhuma coluna.
  - `test_ap2_01_validacao`: com `nome=""` e com 61 caracteres, `erros["nome"]`. Sem escopo, `erros["escopos"]`. Com `dias=7`, `erros["dias"]`. No 11º token ativo, `erros["nome"]` cita o limite de 10. Um token revogado ou expirado não conta no limite.
  - `test_ap2_03_autenticar_recusa_expirado_revogado_desconhecido`: o relógio passa de `expira_em` e o resultado é `None`. Depois de `revogar`, é `None`. Com `"glp_x"` e com `None`, é `None`.
  - `test_ap2_05_janela_fixa_por_minuto`: com o relógio em 10:00:05, 60 chamadas a `consumir` dão `permitido=True` e a última dá `restantes=0`. A 61ª dá `(False, 0, 55)`. Às 10:01:00, a chamada dá `(True, 59, …)`, e só existe uma linha de `LimiteTaxaToken` para o token.

- [ ] **Step 2: Rodar e ver falhar.** `rtk .venv/bin/python -m pytest -q tests/test_api_v2_tokens.py` → FAIL (import).
- [ ] **Step 3: Implementar `tokens.py`.** Regras de implementação:
  - `gerar` usa `secrets.choice(string.ascii_letters + string.digits)` 40 vezes.
  - `consumir` grava com `insert(LimiteTaxaToken).values(...).on_conflict_do_update(index_elements=["token_id", "janela"], set_={"contagem": LimiteTaxaToken.contagem + 1}).returning(LimiteTaxaToken.contagem)`. A janela é `agora_utc().replace(second=0, microsecond=0)`.
  - Quando a contagem volta a 1, `consumir` apaga as janelas antigas do token.
  - `retry_after = 60 - agora.second` (mínimo 1).
  - Nenhuma função lê `request` nem `current_user()`.
- [ ] **Step 4: Rodar.** O mesmo comando dá PASS, inclusive `ap2_02` da Tarefa 1.
- [ ] **Step 5: Stage e mensagem.** `feat(tokens): criação, hash, validade, revogação e limite por minuto`.

### Tarefa 3: Telas de tokens no perfil

**Files:**
- Modify: `src/app/tokens.py` (`bp = Blueprint("tokens", __name__)` e as rotas)
- Modify: `src/app/routes.py` (`"tokens"` e `"api_v2"` em `MODULES`)
- Create: `src/app/templates/tokens/lista.html`, `src/app/templates/tokens/detalhe.html`
- Modify: `src/app/templates/conta/seguranca.html` (link "Tokens de API" para `/u/<me.username>/tokens`)
- Test: `tests/test_api_v2_tokens.py`

**Interfaces:**
- Produces, todas com `@login_required`, e só o dono (outro usuário, inclusive admin, recebe 403):
  - `GET /u/<username>/tokens` e `POST /u/<username>/tokens` (campos `nome`, `escopos` multivalor, `dias`);
  - `GET /u/<username>/tokens/<int:tid>` (auditoria, 20 por página, `util.paginate`);
  - `POST /u/<username>/tokens/<int:tid>/revogar`.
  - O token de outro dono responde 404.
- O POST válido renderiza a lista com o texto do token **uma vez**, status 201 e `Cache-Control: no-store`, sem redirect. O POST inválido responde 400 com os erros por campo.

- [ ] **Step 1: Escrever os testes que falham:**
  - `test_ap2_01_tela_mostra_o_token_uma_vez`: o POST responde 201 e o corpo contém o `glp_…` de 44 caracteres. O GET seguinte não contém o token, e contém o prefixo, os escopos e as datas.
  - `test_ap2_01_acesso`: o visitante vai para `/entrar?next=...`. A `bia` em `/u/ana/tokens` recebe 403. O admin recebe 403.
  - `test_ap2_02_revogar`: o POST `.../revogar` faz `autenticar(texto)` devolver `None`, e a lista mostra "revogado".
  - `test_ap2_02_trocar_senha_e_encerrar_sessoes_nao_revogam`: o POST `/conta/seguranca/sessoes` e a troca de senha mantêm `autenticar(texto)` ativo.
  - `test_ap2_06_auditoria_paginada_sem_token`: 21 linhas de `auditar(...)` dão 2 páginas. O HTML tem a rota e o código HTTP, e o texto do token não aparece em nenhuma página.
- [ ] **Step 2: Rodar e ver falhar.** `rtk .venv/bin/python -m pytest -q tests/test_api_v2_tokens.py -k "ap2_01 or ap2_02 or ap2_06"` → FAIL (404).
- [ ] **Step 3: Implementar as rotas e os templates.** Os templates seguem o padrão de `templates/conta/seguranca.html`: `csrf_token()` no formulário e checkbox para cada escopo.
- [ ] **Step 4: Rodar.** O mesmo comando dá PASS.
- [ ] **Step 5: Stage e mensagem.** `feat(tokens): telas de criação, revogação e auditoria do token`.

### Tarefa 4: Funções de serviço extraídas das rotas (refatoração da issue)

**Files:**
- Modify: `src/app/listas.py`, `src/app/avaliacoes.py`, `src/app/diario.py`
- Test: `tests/test_api_v2.py` (novo), mais as suítes atuais das três partes

**Interfaces:**
- Produces:
  - `listas.definir_status(user, game, valor: str | None) -> str | None`: devolve a mensagem de erro ou `None`. `""` e `None` removem o status. Faz o commit (com o tratamento de `IntegrityError` atual) e chama `emitir_evento`.
  - `listas.adicionar_jogo(gl, game) -> str | None`: aplica LIS-06 e LIS-10, a `Activity` da lista pública, o commit e `emitir_evento`.
  - `avaliacoes.ler_nota(valor) -> int | None`: aceita `int` (não `bool`) ou texto só de dígitos, de 0 a 10. Recusa `float`.
  - `avaliacoes.avaliar(user, game, nota, texto, spoiler) -> tuple[Review | None, bool, dict]`: devolve `(review, nova, erros)`. Os erros usam as chaves `nota` e `texto` e as mensagens atuais do `flash`. Repete o `IntegrityError` e os dois `emitir_evento`.
  - `diario.registrar_sessao(user, game, form) -> tuple[SessaoJogo | None, dict]`: roda `validar_campos`, `gravar_sessao`, `avisar_amigo_zerou`, o commit e `emitir_evento`.
- As rotas `set_status`, `rate`, `add_to_list` e `registrar` passam a chamar essas funções e mantêm o `flash`, os redirects e os status HTTP atuais. Nenhuma função de serviço chama `flash`, `request` ou `current_user()`.

- [ ] **Step 1: Escrever o teste que falha.**

```python
@pytest.mark.parametrize("valor,esperado", [(8, 8), ("10", 10), (0, 0), (True, None), (8.5, None), ("8.5", None), (11, None), (-1, None), (None, None)])
def test_ap2_10_ler_nota(valor, esperado):
    from app.avaliacoes import ler_nota
    assert ler_nota(valor) == esperado
```

- [ ] **Step 2: Rodar e ver falhar.** `rtk .venv/bin/python -m pytest -q tests/test_api_v2.py -k ler_nota` → FAIL (import).
- [ ] **Step 3: Extrair as quatro funções e trocar as rotas.**
- [ ] **Step 4: Rodar a regressão das partes tocadas.** `rtk .venv/bin/python -m pytest -q tests/test_api_v2.py tests/test_listas.py tests/test_avaliacoes.py tests/test_diario.py tests/test_gamificacao.py tests/test_notificacoes.py` → PASS, sem mudar nenhum teste existente.
- [ ] **Step 5: Stage e mensagem.** `refactor(servicos): status, avaliação, lista e sessão como funções de serviço (#<issue>)`.

### Tarefa 5: Serializadores comuns (AP2-07)

**Files:**
- Modify: `src/app/serializadores.py`, `src/app/api.py` (`fontes` sai de `api.py` e passa a ser importado)
- Test: `tests/test_api_v2.py`

**Interfaces:**
- Produces, em `serializadores.py`:
  - `absoluta(url)`: sem contexto de requisição, usa `os.environ.get("URL_PUBLICA") or "http://localhost:5000"`, como `emails.py`;
  - `fontes(game) -> list[dict]`: movido de `api.py`, com saída idêntica;
  - `usuario_publico(user) -> dict`: `username`, `nome` (display_name), `bio`, `perfil_privado`, `membro_desde` (data ISO) e `url` (`/u/<username>`). Nunca leva `email`, `password_hash`, `nascimento`, `steam_id`, `totp_*`, `session_version` nem `role`;
  - `avaliacao(review, curtidas=0) -> dict`: `id`, `jogo` (slug), `usuario` (username), `nota`, `texto`, `spoiler`, `editada`, `data`, `curtidas`;
  - `sessao(s) -> dict`: `id`, `jogo` (slug), `titulo`, `jogada_em`, `duracao_min`, `inicio` (`"HH:MM"` ou `None`), `plataforma` (nome ou `None`), `zerou`, `spoiler`, `anotacao`, `origem`;
  - `conquista(uc) -> dict`: `codigo`, `nome`, `descricao`, `categoria`, `raridade`, `icone`, `progresso`, `meta`, `desbloqueada_em`. Uma conquista secreta e bloqueada sai com `nome = "Conquista secreta"` e `descricao = None`;
  - `status(st) -> dict`: `jogo` (`jogo(st.game)`), `status`, `atualizado_em`, `zerado_em`.

- [ ] **Step 1: Escrever os testes que falham:**
  - `test_ap2_07_usuario_publico_sem_conta`: as chaves de `usuario_publico(u)` são exatamente as listadas. Valores sensíveis colocados no usuário (e-mail, `steam_id`) não aparecem em `json.dumps`.
  - `test_ap2_07_datas_iso_e_urls_absolutas`: `sessao(s)["jogada_em"] == "2026-11-20"`. Fora de requisição, com `URL_PUBLICA=https://x.test`, `jogo(g)["url"]` começa com `https://x.test/`.
  - `test_ap2_07_conquista_secreta_bloqueada`: o nome é `"Conquista secreta"` e a descrição é `None`. Desbloqueada, mostra o nome real.
  - `test_ap2_07_v1_inalterada`: `rtk .venv/bin/python -m pytest -q tests/test_api.py` continua verde (comando, não teste novo).
- [ ] **Step 2: Rodar e ver falhar.** `-k ap2_07` → FAIL.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar** `tests/test_api_v2.py -k ap2_07` e `tests/test_api.py` → PASS.
- [ ] **Step 5: Stage e mensagem.** `feat(serializadores): usuário público, avaliação, sessão, conquista e status`.

### Tarefa 6: Núcleo da API v2 (autenticação, escopo, limite, cabeçalhos, paginação, índice)

**Files:**
- Create: `src/app/api_v2.py`, `src/app/templates/api_v2/docs.html`
- Modify: `src/app/__init__.py` (`csrf_protect` ignora `/api/v2/`), `src/app/api.py` (`cabecalhos` com `setdefault`, `ENDPOINTS` com `/api/v2`, `mesclado_ou_404(slug, sufixo="", prefixo="/api/jogos")`)
- Test: `tests/test_api_v2.py`

**Interfaces:**
- Consumes: `tokens.autenticar`, `tem_escopo`, `consumir`, `auditar`; `api.resposta`, `api.erro`; `util.paginate`, `util.Page`.
- Produces, em `api_v2.py`:
  - `bp = Blueprint("api_v2", __name__, url_prefix="/api/v2")`;
  - `@bp.before_request autenticar_requisicao()`. Se houver cabeçalho `Authorization`, ele precisa ser `Bearer <token>` válido, senão a resposta é 401. Com token válido, chama `consumir`: estourado dá 429 com `Retry-After`. Grava `g.token`, `g.limite = (restantes)` e `token.ultimo_uso_em`. Sem cabeçalho, `g.token = None`;
  - `do_dono(f)`: decorator. Sem `g.token`, responde 401. O escopo vem do método: `GET`/`HEAD` pede `ler`, os outros pedem `escrever`. Sem o escopo, responde 403 `{"erro": "escopo insuficiente", "requer": "<escopo>"}`;
  - `nao_autorizado()`: 401 `{"erro": "token inválido"}` com `WWW-Authenticate: Bearer`;
  - `@bp.after_request`: `X-RateLimit-Limit: 60` e `X-RateLimit-Remaining` com token. `Cache-Control: no-store` quando há `g.token`. `Access-Control-Allow-Headers: Authorization, Content-Type` e `Access-Control-Allow-Methods: GET, POST, PUT, OPTIONS`. Uma requisição com token e método de escrita chama `auditar(g.token, "escrita", método, request.path, status)`. Commit no fim;
  - `paginado(page, itens) -> dict`: devolve `{"items", "page", "pages", "total", "per_page", "proxima"}`. `proxima` é a URL absoluta com os mesmos args e `page+1`, ou `None`;
  - `paginar_lista(linhas: list, page_arg) -> Page`, para os rankings;
  - `corpo_json() -> dict | None`: `request.get_json(silent=True)` só se for `dict`;
  - `invalido(campos: dict, erro="entrada inválida")`: responde 422 `{"erro", "campos"}`;
  - `GET /api/v2`: `{"versao": "2", "endpoints": [{"metodo", "rota", "escopo"}], "docs": "<abs>/api/v2/docs"}`, com a lista declarada numa constante `ENDPOINTS` que cobre todas as rotas das Tarefas 7 e 8;
  - `GET /api/v2/docs`: HTML curto com exemplos de `curl`.
- A v2 nunca chama `current_user()`. O cookie não autentica.

- [ ] **Step 1: Escrever os testes que falham:**
  - `test_ap2_03_401_uniforme`: com token ausente em `/api/v2/eu`, desconhecido, expirado (relógio) e revogado, o corpo é igual (`{"erro": "token inválido"}`), o status é 401 e há `WWW-Authenticate: Bearer`. `Authorization: Basic x` também dá 401.
  - `test_ap2_03_cookie_nao_autentica` (Review Focus 2): a `ana` logada por cookie, sem token, recebe 401 em `/api/v2/eu`. Com o cookie da `ana` e o token da `bia`, `/api/v2/eu` devolve `bia`.
  - `test_ap2_03_sem_csrf`: com `create_app({"CSRF": True, ...})`, o `POST /api/v2/eu/diario` com token não responde 400 de CSRF.
  - `test_ap2_04_escopo`: o token só `ler` em `PUT /api/v2/eu/status/<slug>` recebe 403 `{"erro": "escopo insuficiente", "requer": "escrever"}`. O token só `escrever` em `GET /api/v2/eu` recebe 403 com `requer: "ler"`.
  - `test_ap2_05_429_e_cabecalhos`: com o relógio fixo, 60 GET dão 200 com `X-RateLimit-Remaining` de 59 a 0. O 61º dá 429 com `Retry-After`.
  - `test_ap2_11_paginacao_com_proxima`: com 25 jogos e `per_page=10`, `page=1` tem `proxima` absoluta com `page=2` e o filtro preservado. A página 3 tem `proxima` igual a `None`. Com `per_page=99`, o resultado é 20.
  - `test_ap2_13_indice_docs_e_cache`: `GET /api/v2` lista `{"metodo": "PUT", "rota": "/api/v2/eu/status/<slug>", "escopo": "escrever"}`, e `docs` é absoluta. `/api/v2/docs` responde 200 em HTML. `/api/v2/nada` responde 404 em JSON e `DELETE /api/v2` responde 405 em JSON. O GET público tem `Cache-Control: public, max-age=60`, e o mesmo GET com token tem `no-store`. `/api` lista `/api/v2`.
  - `test_ap2_13_preflight` (Review Focus 3): `OPTIONS /api/v2/eu/status/x` sem token responde 200 com `Authorization` em `Access-Control-Allow-Headers` e `Access-Control-Allow-Origin: *`.
- [ ] **Step 2: Rodar e ver falhar.** `-k "ap2_03 or ap2_04 or ap2_05 or ap2_11 or ap2_13"` → FAIL. Para ter rotas com o que testar, crie nesta tarefa só `GET /api/v2/eu` (`usuario_publico(g.token.user)`) e `GET /api/v2/jogos`. As outras rotas entram na Tarefa 7.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar** o mesmo filtro e `tests/test_api.py` → PASS.
- [ ] **Step 5: Stage e mensagem.** `feat(api-v2): autenticação por token, escopos, limite de taxa e paginação`.

### Tarefa 7: Leitura da API v2 (AP2-08, AP2-09, AP2-12)

**Files:**
- Modify: `src/app/api_v2.py`, `src/app/rankings.py` (extração)
- Test: `tests/test_api_v2.py`

**Interfaces:**
- Consumes: `busca.search_games`, `api.jogo_ou_404`, `api.mesclado_ou_404(..., prefixo="/api/v2/jogos")`, `diario.ocultos_para`, `diario.pode_ver_perfil`, `serializadores.*`.
- Produces, em `rankings.py`: `linhas_do_ranking(categoria, escopo, janela_arg, me, args) -> tuple[list[tuple[User, int]], str, Temporada | None, dict] | None`. É o corpo de `pagina()` que calcula as linhas. Devolve `None` quando o escopo pede login e `me` é `None`. `pagina()` passa a chamar essa função, e `tests/test_rankings.py` continua verde.
- Rotas novas, todas `GET`:
  - públicas, que usam `g.token.user` como visitante quando há token: `/jogos/<slug>` (`{**jogo(g, com_precos=True), "sinopse", "histograma", "fontes", "nota_media", "avaliacoes"}`); `/jogos/<slug>/avaliacoes` (filtro `ocultos_para(visitante)`, curtidas numa consulta); `/usuarios/<username>`; `/usuarios/<username>/diario`; `/rankings/<categoria>` (`escopo`, `janela`, `page`; categoria fora de `CATEGORIAS` responde 404; `seguindo` sem token responde 401; item `{"posicao", "usuario": usuario_publico, "valor"}`);
  - do dono (`@do_dono`): `/eu/status` (`?status=`), `/eu/listas`, `/eu/listas/<int:lid>` (`{"id", **lista(...)}`), `/eu/diario` (`de` e `ate` em `AAAA-MM-DD`, data inválida responde 422, filtro `jogo`), `/eu/conquistas`.

- [ ] **Step 1: Escrever os testes que falham:**
  - `test_ap2_08_jogos_publicos_loja_e_fontes`: `?loja=gog` filtra. O detalhe traz `fontes`. O jogo removido responde 404 em JSON. O mesclado responde 301 para `/api/v2/jogos/<principal>`.
  - `test_ap2_08_avaliacao_de_perfil_privado`: sem token, a avaliação da `bia` (privada) não aparece e `nota_media` do detalhe a inclui. Com o token de quem a `bia` segue, ela aparece.
  - `test_ap2_09_eu`: `/eu/listas` traz a lista privada do dono. `/eu/listas/<id da bia>` responde 404. `/eu/diario?de=2026-11-01&ate=2026-11-10&jogo=alfa` filtra. `?de=ontem` responde 422 com `campos.de`. `/eu/status?status=jogado` filtra. `/eu/conquistas` traz `progresso`.
  - `test_ap2_12_perfil_privado_404_igual_inexistente`: `/usuarios/bia` (privado) e `/usuarios/naoexiste` dão o mesmo 404 e o mesmo corpo. Com bloqueio entre `ana` e `carla`, o token da `ana` em `/usuarios/carla` recebe 404.
  - `test_ap2_12_ranking_segue_rnk`: o global exclui o perfil privado e o menor (`pode(..., "ranking_global")`). `seguindo` sem token responde 401. `/rankings/xyz` responde 404.
  - `test_ap2_14_consultas_listagem_20`: com 20 sessões, `GET /api/v2/eu/diario` com token gera no máximo 8 consultas, contadas com `before_cursor_execute` como em `test_api10_*`.
- [ ] **Step 2: Rodar e ver falhar.** `-k "ap2_08 or ap2_09 or ap2_12 or ap2_14"` → FAIL.
- [ ] **Step 3: Implementar.** Use `selectinload` de `Game.genres`, `Game.platforms` e `Game.developer` em `/eu/status`. Use `joinedload(SessaoJogo.game)` e `joinedload(SessaoJogo.plataforma)` no diário.
- [ ] **Step 4: Rodar** o mesmo filtro e `tests/test_rankings.py` → PASS.
- [ ] **Step 5: Stage e mensagem.** Dois commits: `refactor(rankings): linhas_do_ranking compartilhada pela página e pela API` e `feat(api-v2): leitura pública, do dono, de perfis e de rankings`.

### Tarefa 8: Escrita da API v2 (AP2-10) e auditoria de escrita (AP2-06)

**Files:**
- Modify: `src/app/api_v2.py`
- Test: `tests/test_api_v2.py`

**Interfaces:**
- Consumes: os serviços da Tarefa 4 e `api_v2.corpo_json`, `invalido`, `do_dono`.
- Produces:
  - `PUT /eu/status/<slug>` com `{"status": "<STATUS>" | null}`: devolve 200 `{"jogo": slug, "status": valor}`. Status fora de `STATUSES` responde 422 `campos.status`;
  - `PUT /eu/avaliacoes/<slug>` com `{"nota", "texto"?, "spoiler"?}`: devolve 201 quando é nova e 200 quando é edição, com `avaliacao(review)`. `spoiler` precisa ser `bool`, senão 422;
  - `POST /eu/diario` com `{"jogo", "jogada_em", "duracao", "inicio"?, "plataforma_id"?, "anotacao"?, "zerou"?, "spoiler"?}`: devolve 201 com `sessao(s)`. Jogo inexistente responde 422 `campos.jogo`;
  - `POST /eu/listas/<int:lid>/jogos` com `{"jogo"}`: devolve 201 com a lista. A lista de outra pessoa responde 404. A duplicata ou o limite responde 422 `campos.jogo`;
  - `como_formulario(corpo, booleanos) -> tuple[dict, dict]`: converte o JSON para o formato que `validar_campos` lê. Um booleano vira `"1"` ou `""`, `None` vira `""` e o resto vira `str`. Um campo booleano que não é `bool` entra nos erros;
  - jogo inexistente na URL (`/eu/status/<slug>`, `/eu/avaliacoes/<slug>`) responde 404 em JSON;
  - corpo ausente ou que não é objeto responde 422 `{"erro": "corpo JSON inválido", "campos": {}}`.

- [ ] **Step 1: Escrever os testes que falham:**
  - `test_ap2_10_status_mesma_regra_da_interface`: `PUT` com `jogado` cria a `Activity` "jogado" uma vez só, e um segundo `PUT` não duplica. `null` remove. `"zerado"` responde 422.
  - `test_ap2_10_avaliacao_ava_11_e_422` (Review Focus 1): a nota 8 sem status cria o status `jogado`. As notas 11, `true`, `8.5` e o texto com 5001 caracteres respondem 422 com o campo. O corpo `[1]` e o corpo vazio respondem 422. Nenhum caso responde 500.
  - `test_ap2_10_diario_planejado_vira_jogando`: com status `planejado`, o POST responde 201 e o status vira `jogando`. Duração 0, data futura e `"zerou": "sim"` respondem 422 com o campo.
  - `test_ap2_10_lista`: adicionar duas vezes responde 422. A lista da `bia` responde 404.
  - `test_ap2_10_efeitos_iguais_a_interface`: com `monkeypatch` em `app.api_v2.emitir_evento` ou na função usada, o `PUT` de avaliação emite `avaliacao` e `status`, como `rate`.
  - `test_ap2_06_escrita_auditada_sem_corpo`: depois de um `PUT`, `AuditoriaToken` tem `acao="escrita"`, `metodo="PUT"`, a rota e `status_http=200`. O texto do token e o valor do corpo não aparecem em nenhuma coluna. Um GET não grava auditoria e atualiza `ultimo_uso_em`.
- [ ] **Step 2: Rodar e ver falhar.** `-k "ap2_10 or ap2_06"` → FAIL.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar** `tests/test_api_v2.py tests/test_api_v2_tokens.py tests/test_api.py` → PASS.
- [ ] **Step 5: Stage e mensagem.** `feat(api-v2): escrita de status, avaliação, diário e lista com auditoria`.

### Tarefa 9: Fechamento do PR 1

- [ ] README: um parágrafo "API v2 e tokens" (criar o token em `/u/<usuario>/tokens`, `curl -H "Authorization: Bearer glp_..."`, escopos, limite de 60/min, `/api/v2/docs`) e as linhas `api-v2-tokens (AP2-01..14) | tokens.py, api_v2.py, serializadores.py | tests/test_api_v2_tokens.py, tests/test_api_v2.py` na tabela spec → código → testes.
- [ ] Rodar `tests/test_api_v2*.py`, `tests/test_api.py`, `tests/test_mapa_dados.py` e as suítes da Tarefa 4 → PASS.
- [ ] `./dev.sh`: criar o token na tela, chamar `/api/v2/eu` com `curl` e conferir `/api/v2/docs` no navegador.
- [ ] Sessão em `prompts/sessoes/` com custo. Mensagem `docs(api-v2): README e tabela spec → código → testes`, com `Spec: none` só para o arquivo de sessão.
- [ ] Texto do PR (skill `git-pr`) com `Closes #<issue da refatoração>`.

---

## PR 2 — Servidor MCP (`feat/servidor-mcp`, a partir de `feat/api-v2-tokens` ou da `main` depois do merge)

### Tarefa 10: Transporte e despacho JSON-RPC (MCP-01..03)

**Files:**
- Create: `src/app/mcp/__init__.py` (vazio), `src/app/mcp/protocolo.py`
- Test: `tests/test_mcp.py`

**Interfaces:**
- Produces, em `protocolo.py`:
  - `VERSOES = ("2025-06-18", "2025-03-26", "2024-11-05")`, da mais recente para a mais antiga;
  - códigos `ERRO_JSON = -32700`, `ERRO_REQUISICAO = -32600`, `ERRO_METODO = -32601`, `ERRO_PARAMS = -32602`, `ERRO_INTERNO = -32603` e `NAO_INICIADO = -32002`;
  - `class ErroProtocolo(Exception)` com `codigo` e `mensagem`;
  - `class Sessao`: `__init__(self, token_id)`, `iniciado = False`, `tratar(self, msg: dict) -> dict | None` (o `result` ou levanta `ErroProtocolo`);
  - `processar(sessao, linha: str) -> str | None`: devolve a linha JSON da resposta, ou `None` para uma notificação;
  - `servir(sessao, entrada, saida) -> None`: lê linha a linha, ignora linhas vazias, escreve `resposta + "\n"` e faz `flush`. Depois de cada mensagem, chama `db.session.remove()`.
- Regras:
  - `initialize` devolve `protocolVersion` (a pedida, se estiver em `VERSOES`, senão `VERSOES[0]`), `capabilities: {"tools": {}}` e `serverInfo: {"name": "gamelog", "version": "1"}`;
  - `ping` funciona antes de `initialize`. Os outros métodos, antes dele, dão `-32002`;
  - um array (lote) responde `-32600` com `id: null`;
  - uma exceção inesperada vira `-32603` com a mensagem `"erro interno"`, e o traceback vai só para o `logging` (stderr);
  - uma mensagem sem `id` nunca recebe resposta, nem quando dá erro.

- [ ] **Step 1: Escrever os testes que falham.** Helper:

```python
def rodar(app, linhas, token_id=1):
    from app.mcp.protocolo import Sessao, servir
    saida = io.StringIO()
    with app.app_context():
        servir(Sessao(token_id), io.StringIO("\n".join(json.dumps(l) if not isinstance(l, str) else l for l in linhas) + "\n"), saida)
    return [json.loads(x) for x in saida.getvalue().splitlines()]
```

Testes:
  - `test_mcp_02_initialize_e_ping`: a versão pedida `"2025-03-26"` volta igual, e a versão `"1999-01-01"` volta como `VERSOES[0]`. `serverInfo.name == "gamelog"`. `notifications/initialized` não gera linha. `ping` responde `{}`. `tools/list` antes de `initialize` responde `-32002`.
  - `test_mcp_03_codigos`: `"{x"` dá -32700. `{"id": 1, "method": "ping"}` sem `jsonrpc` dá -32600. `[]` dá -32600. Método desconhecido dá -32601. `initialize` com `params` igual a uma lista dá -32602. Depois de cada erro, um `ping` ainda responde.
  - `test_mcp_03_notificacao_sem_resposta`: um método desconhecido sem `id` não gera linha.
  - `test_mcp_01_stdout_so_json_rpc`: cada linha de saída é JSON com `jsonrpc == "2.0"`.
- [ ] **Step 2: Rodar e ver falhar.** `rtk .venv/bin/python -m pytest -q tests/test_mcp.py -k "mcp_01 or mcp_02 or mcp_03"` → FAIL.
- [ ] **Step 3: Implementar.** `tools/list` e `tools/call` só delegam a `ferramentas` (Tarefa 12). Até lá, `tools/list` devolve `{"tools": []}`.
- [ ] **Step 4: Rodar** → PASS.
- [ ] **Step 5: Stage e mensagem.** `feat(mcp): transporte stdio e despacho JSON-RPC 2.0`.

### Tarefa 11: Comando `flask mcp` e token pelo ambiente (MCP-04)

**Files:**
- Modify: `src/app/mcp/protocolo.py`, `src/app/cli.py` (comando `mcp`)
- Test: `tests/test_mcp.py`

**Interfaces:**
- Produces:
  - `protocolo.iniciar(texto_token: str | None, entrada, saida, erros) -> int`: com `tokens.autenticar(texto) is None`, escreve `"erro=token inválido\n"` em `erros` e devolve 1 sem ler `entrada`. Senão, roda `servir(Sessao(token.id), ...)` e devolve 0;
  - em `cli.register`, `@app.cli.command("mcp")`: `raise click.exceptions.Exit(iniciar(os.environ.get("GAMELOG_TOKEN"), sys.stdin, sys.stdout, sys.stderr))`.

- [ ] **Step 1: Escrever os testes que falham:**
  - `test_mcp_04_sem_token_e_revogado`: com `iniciar(None, ...)` e com o token revogado, o resultado é 1, `erros.getvalue() == "erro=token inválido\n"`, `saida` fica vazia e a entrada não é lida (`entrada.tell() == 0`).
  - `test_mcp_04_cli`: `app.test_cli_runner().invoke(args=["mcp"], env={"GAMELOG_TOKEN": ""})` dá `exit_code == 1`. Com um token válido e `input='{"jsonrpc":"2.0","id":1,"method":"ping"}\n'`, dá `exit_code == 0` e a saída tem `"result": {}`. O texto do token não aparece em nenhuma saída.
- [ ] **Step 2: Rodar e ver falhar.** `-k mcp_04` → FAIL.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar** → PASS.
- [ ] **Step 5: Stage e mensagem.** `feat(mcp): comando flask mcp autenticado por GAMELOG_TOKEN`.

### Tarefa 12: Registro, esquemas e validação de ferramentas (MCP-05, MCP-06)

**Files:**
- Create: `src/app/mcp/ferramentas.py`
- Modify: `src/app/mcp/protocolo.py` (`tools/list` e `tools/call` delegam)
- Test: `tests/test_mcp.py`

**Interfaces:**
- Produces, em `ferramentas.py`:
  - `@dataclass(frozen=True) Ferramenta(nome, descricao, schema, escrita, executar)`, em que `executar(token, args) -> dict`;
  - `FERRAMENTAS: dict[str, Ferramenta]` e o decorator `ferramenta(nome, descricao, schema, escrita=False)`;
  - `listar(token) -> list[dict]`: itens `{"name", "description", "inputSchema"}`. As ferramentas de escrita só entram com `tem_escopo(token, "escrever")`;
  - `validar(schema, args) -> str | None`: devolve a mensagem `"<campo>: <motivo>"`. Cobre `type` (`string`, `integer` sem `bool`, `boolean`), `required`, `enum`, `minimum`, `maximum`, `minLength`, `maxLength`, `pattern` e `additionalProperties: false`;
  - `resultado(texto, dados) -> dict`: `{"content": [{"type": "text", "text": texto}], "structuredContent": dados, "isError": False}`;
  - `falha(texto) -> dict`: `isError: True`, sem `structuredContent`;
  - `chamar(token_id, nome, args) -> dict`, nesta ordem:
    1. nome desconhecido levanta `ErroProtocolo(-32602)`;
    2. recarrega o token (revogado no meio da sessão dá `falha("token inválido")`);
    3. `tokens.consumir` (MCP-10);
    4. escopo de escrita;
    5. `validar`;
    6. `executar`.
- Esquemas, todos com `"type": "object"` e `"additionalProperties": false`:
  - `buscar_jogos`: `termo` (string, 1..100, obrigatório), `genero` (string), `loja` (enum `steam`, `gog`, `epic`), `limite` (integer, 1..20);
  - `detalhes_jogo` e `disponivel_em`: `slug` (obrigatório);
  - `minhas_listas`: `status` (enum `STATUSES`);
  - `marcar_status`: `slug` e `status` (enum `[*STATUSES, "nenhum"]`; `nenhum` remove);
  - `avaliar`: `slug`, `nota` (integer, 0..10), `texto` (maxLength 5000), `spoiler` (boolean);
  - `registrar_sessao`: `slug`, `duracao_min` (integer, 1..1440), `data` (pattern `^\d{4}-\d{2}-\d{2}$`), `zerou` (boolean), `anotacao` (maxLength 1000);
  - `recomendacoes`: `limite` (integer, 1..10);
  - `comparar_gosto`: `username` (obrigatório).

- [ ] **Step 1: Escrever os testes que falham:**
  - `test_mcp_05_lista_com_e_sem_escrita`: o token `ler` lista as 6 de leitura. O token `ler escrever` lista as 9. Cada item tem `required` e `additionalProperties is False`.
  - `test_mcp_06_validacao_antes_do_servico`: com `monkeypatch` em `FERRAMENTAS["avaliar"].executar` (ou no serviço), a nota `"8"`, a nota `true`, a nota 11, a falta de `slug` e o campo extra dão `isError` com o nome do campo, e o serviço não é chamado. A ferramenta `xyz` dá `-32602`.
- [ ] **Step 2: Rodar e ver falhar.** `-k "mcp_05 or mcp_06"` → FAIL.
- [ ] **Step 3: Implementar** o registro com as 9 ferramentas. Nesta tarefa, `executar` pode ser `lambda t, a: resultado("", {})`. As Tarefas 13 e 14 trocam os corpos.
- [ ] **Step 4: Rodar** → PASS.
- [ ] **Step 5: Stage e mensagem.** `feat(mcp): ferramentas com JSON Schema e validação de argumentos`.

### Tarefa 13: Ferramentas de leitura (MCP-07, MCP-09)

**Files:**
- Modify: `src/app/mcp/ferramentas.py`
- Test: `tests/test_mcp.py`

**Interfaces:**
- Consumes:
  - `busca.search_games(MultiDict({"q": termo, "genero": ..., "loja": ...}))`;
  - `models.visible_games`, `catalogo_merge.principal_do_mesclado` (o mesclado segue para o principal);
  - `serializadores.jogo`, `fontes`, `status`, `lista`;
  - `recomendacoes.recommend(user_id, limite)`;
  - `afinidade.resumo_com`, `diario.pode_ver_perfil`, `menores.politica.pode(outro, "historico_para_terceiros")`, como em `afinidade.comparar`.
- Produces o `structuredContent` (sempre objeto):
  - `buscar_jogos`: `{"jogos": [jogo]}`;
  - `detalhes_jogo`: `{**jogo(g, com_precos=True), "sinopse", "fontes"}`;
  - `disponivel_em`: `{"slug", "lojas": fontes(g)}`;
  - `minhas_listas`: `{"status": [status], "listas": [{"id", **lista}]}`;
  - `recomendacoes`: `{"recomendacoes": [{"jogo", "nota_media", "motivo"}], "aviso"}`;
  - `comparar_gosto`: `{"username", "afinidade", "jogos_em_comum"}`.
- Textos de erro: `"jogo não encontrado"` e `"usuário não encontrado"`. O próprio usuário em `comparar_gosto` também dá `"usuário não encontrado"`.

- [ ] **Step 1: Escrever os testes que falham:**
  - `test_mcp_07_buscar_detalhes_disponivel`: o `text` cita o título, e `structuredContent.jogos[0]` é igual a `serializadores.jogo(...)`. `loja="gog"` filtra. O slug inexistente e o removido dão `isError` com "jogo não encontrado".
  - `test_mcp_07_minhas_listas_inclui_privadas`.
  - `test_mcp_09_recomendacoes_e_comparar`: devolve no máximo `limite` itens. A `bia` privada, o bloqueado, o menor e o inexistente dão "usuário não encontrado".
- [ ] **Step 2: Rodar e ver falhar.** `-k "mcp_07 or mcp_09"` → FAIL.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar** → PASS.
- [ ] **Step 5: Stage e mensagem.** `feat(mcp): ferramentas de catálogo, listas, recomendações e afinidade`.

### Tarefa 14: Ferramentas de escrita, limite e auditoria (MCP-08, MCP-10)

**Files:**
- Modify: `src/app/mcp/ferramentas.py`
- Test: `tests/test_mcp.py`

**Interfaces:**
- Consumes: `listas.definir_status`, `avaliacoes.avaliar`, `diario.registrar_sessao` (com `form = {"jogada_em": data or tempo.hoje().isoformat(), "duracao": str(duracao_min), "zerou": "1" if zerou else "", "anotacao": anotacao or ""}`), `tokens.auditar(token, "escrita", "MCP", f"mcp:{nome}", None)`.
- Um erro de serviço vira `falha("<campo>: <mensagem>")`. O limite estourado dá `falha(f"limite de requisições; tente em {retry} s")`.

- [ ] **Step 1: Escrever os testes que falham:**
  - `test_mcp_08_status_avaliar_sessao_mesma_regra`: `avaliar` sem status cria `jogado` (AVA-11). `registrar_sessao` de um jogo `planejado` muda o status para `jogando`, e sem `data` grava `tempo.hoje()` com o relógio fixo. Uma sessão que passa de 24h no dia dá `isError`.
  - `test_mcp_08_sem_escopo`: com o token `ler`, `tools/call` de `marcar_status` dá `isError` com "escopo insuficiente".
  - `test_mcp_10_limite_e_auditoria`: 60 chamadas passam e a 61ª dá `isError` com "limite de requisições; tente em". Depois de `avaliar`, `AuditoriaToken.rota == "mcp:avaliar"`.
- [ ] **Step 2: Rodar e ver falhar.** `-k "mcp_08 or mcp_10"` → FAIL.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar** `tests/test_mcp.py` → PASS.
- [ ] **Step 5: Stage e mensagem.** `feat(mcp): ferramentas de escrita com limite de taxa e auditoria`.

### Tarefa 15: Skills, README e fechamento do PR 2 (MCP-11)

**Files:**
- Create: `skills/gamelog-catalogo/SKILL.md`, `skills/gamelog-diario/SKILL.md`, `skills/gamelog-descoberta/SKILL.md`
- Modify: `README.md`
- Test: `tests/test_mcp.py`

**Interfaces:**
- Cada `SKILL.md` tem frontmatter `name` (igual ao diretório) e `description`, de 10 a 30 linhas em português, e uma linha `Ferramentas: \`a\`, \`b\``.
  - catálogo: `buscar_jogos`, `detalhes_jogo`, `disponivel_em`;
  - diário: `registrar_sessao`, `marcar_status`, `minhas_listas`, `avaliar`;
  - descoberta: `recomendacoes`, `comparar_gosto`.

- [ ] **Step 1: Escrever o teste que falha.** `test_mcp_11_skills_citam_ferramentas_existentes`: para cada `skills/*/SKILL.md`, o frontmatter tem `name` e `description`, e todo nome entre crases na linha `Ferramentas:` está em `listar(token_com_escrita)`. Existem exatamente os 3 diretórios.
- [ ] **Step 2: Rodar e ver falhar** → FAIL (arquivos ausentes).
- [ ] **Step 3: Escrever as skills.** No README, a seção "Servidor MCP" tem:
  - o JSON de configuração do cliente: `"command": ".venv/bin/flask"`, `"args": ["--app", "app", "mcp"]`, `"cwd": "<repo>/src"` e `"env": {"GAMELOG_TOKEN": "<seu token>"}`;
  - como criar o token;
  - o aviso de que o token nunca vai em argumento;
  - a linha da tabela `servidor-mcp (MCP-01..12) | mcp/ | tests/test_mcp.py`.
- [ ] **Step 4: Rodar** `tests/test_mcp.py` → PASS. Rodar de verdade: `cd src && GAMELOG_TOKEN=<token local> ../.venv/bin/flask --app app mcp`, com `initialize` e `tools/list` colados no stdin.
- [ ] **Step 5: Stage e mensagem.** `docs(mcp): skills de uso e registro do servidor no README`. Depois, a sessão e o PR.

---

## PR 3 — Backup e restore (`feat/backup-restore`, a partir do PR 1 na `main`)

### Tarefa 16: Ajuste da spec BKP (commit só da spec)

**Files:**
- Modify: `SPEC/2026-11-15-backup-restore.md`

- [ ] **Step 1: Editar dois critérios:**
  - BKP-03: acrescentar "`user.session_version` é estado de sessão e não entra no arquivo. Na restauração, cada conta recebe um valor aleatório novo, para nenhum cookie anterior valer sobre um id reaproveitado; quem restaura pelo painel entra de novo (BKP-09)";
  - BKP-04: "cada linha leva todas as colunas pelo serializador genérico, e as linhas de jogos, avaliações, sessões e listas levam também `_api`, com o serializador de AP2-07; a restauração ignora as chaves que começam com `_`".
- [ ] **Step 2: Stage só a spec.** `docs(spec): backup sem session_version e com a visão _api dos serializadores`, com `Spec: SPEC/2026-11-15-backup-restore.md`. O dev comita antes de qualquer código.

### Tarefa 17: Geração, assinatura e restauração (BKP-01..07)

**Files:**
- Create: `src/app/backup.py`
- Modify: `src/app/privacidade/mapa.py`, `src/app/routes.py` (`"backup"` em `MODULES`)
- Test: `tests/test_backup.py`

**Interfaces:**
- Produces, em `backup.py`:
  - `bp = Blueprint("backup", __name__, cli_group="backup")`;
  - `FORMATO = "gamelog-backup"`, `VERSAO_FORMATO = 1`;
  - `EXCLUIDAS = frozenset({"login_attempt", "limite_taxa_token"})`;
  - `FILTROS = {"job": lambda t: t.c.status.in_(("pendente", "rodando", "falhou"))}`;
  - `COLUNAS_VOLATEIS = {"user": frozenset({"session_version"})}`;
  - `VISOES_API = {"game": ..., "review": ..., "sessao_jogo": ..., "game_list": ...}`, que dá `_api` pela instância ORM;
  - `class ErroBackup(Exception)`: a mensagem é o motivo;
  - `chave() -> str | None` (de `BACKUP_KEY`);
  - `incluidas() -> list[Table]` (`db.metadata.sorted_tables` sem `EXCLUIDAS`);
  - `gerar() -> dict`: o conteúdo sem `assinatura`. `versao_app` vem de `RENDER_GIT_COMMIT` ou é `None`;
  - `canonico(conteudo) -> bytes` (`sort_keys`, `separators=(",", ":")`, `ensure_ascii=False`, UTF-8);
  - `assinar(conteudo, chave) -> str` (HMAC-SHA256 hex);
  - `exportar(chave) -> dict`;
  - `conferir(dados, chave) -> None`: levanta `ErroBackup` com `"arquivo malformado"`, `"formato desconhecido"`, `"versão do formato não suportada"` ou `"assinatura inválida"`. Usa `hmac.compare_digest`;
  - `previa(dados) -> tuple[int, int]`;
  - `restaurar(dados, avisar: Callable[[str], None]) -> tuple[int, int]`: sem commit. Quem chama faz o commit ou o rollback;
  - `contagens() -> dict[str, int]`.
- Em `mapa.py`:
  - `FINALIDADES["continuidade"] = "Cópia de segurança do banco (backup)"`;
  - `BACKUPS_MANTIDOS = 7`;
  - `registro_operacoes()` acrescenta, ao fim, a linha `continuidade` com `tabelas=["todas"]`, `retencoes=[f"{BACKUPS_MANTIDOS} arquivos mais recentes"]`, `bases_legais=["legitimo_interesse"]`, `exportado="não"` e `anonimizacao=["manter"]`.
- Algoritmo de `restaurar`, que nem o teste nem a assinatura determinam:

```text
db.session.execute(text("PRAGMA defer_foreign_keys = ON"))
para tabela do arquivo fora do metadata: avisar uma vez
para t em reversed(incluidas()): delete(t)
para t em incluidas():
    linhas do arquivo -> dict só com colunas do modelo (chave "_..." ignorada; coluna sem modelo: avisar uma vez)
    coluna ausente: omitida (o default do Column vale); ausente, NOT NULL e sem default -> ErroBackup
    texto ISO -> date/time/datetime pelo tipo da coluna
    COLUNAS_VOLATEIS -> secrets.randbelow(2**31)
    insert(t) em lotes
flush; contagens() != dados["contagens"] -> ErroBackup("contagens não conferem")
```

- [ ] **Step 1: Escrever os testes que falham.** Use `monkeypatch.setenv("BACKUP_KEY", "k" * 32)`, um mundo com usuário, jogo, avaliação, sessão, lista, token e job `ok`, e um job `pendente`.
  - `test_bkp_01_estrutura`: as chaves de topo são as de BKP-01. A ordem de `tabelas` põe `user` antes de `review`. `contagens` bate com o banco.
  - `test_bkp_02_assinatura_canonica`: `assinatura == hmac(canonico(sem assinatura))`. Sem `BACKUP_KEY`, a CLI falha (Tarefa 18).
  - `test_bkp_03_exclusoes_cobrem_o_modelo`: `{t.name for t in db.metadata.sorted_tables} == {t.name for t in incluidas()} | EXCLUIDAS`. `login_attempt` fica fora. O job `ok` fica fora e o `pendente` entra. `token_hash` e `password_hash` entram iguais. `session_version` fica fora.
  - `test_bkp_04_serializadores_e_tipos`: `linha_game["_api"] == serializadores.jogo(game)`. `release_date` é `"2020-05-01"`. Um `DateTime` sai em ISO e um booleano sai como `true`/`false`.
  - `test_bkp_06_ida_e_volta`: exportar, apagar os dados, restaurar, fazer commit e exportar de novo dá o mesmo `tabelas` e o mesmo `contagens`.
  - `test_bkp_06_rollback_em_linha_invalida`: com uma `review.user_id` que não existe, `restaurar` mais o commit levantam erro. Depois do rollback, `contagens()` é igual ao de antes.
  - `test_bkp_07_coluna_nova_e_removida`: sem `user.bio`, a restauração usa o padrão `""`. Com `user.coluna_velha`, há um aviso só. Com a tabela `tabela_velha`, há um aviso. Sem `user.email`, a restauração falha.
  - `test_bkp_restauracao_invalida_sessoes` (Review Focus 4): o `session_version` de cada usuário depois da restauração é diferente do de antes, e o cookie de antes em `/u/ana` não fica logado.
  - `test_bkp_mapa_registra_continuidade`: `registro_operacoes()` tem a linha `continuidade` com "7 arquivos mais recentes".
- [ ] **Step 2: Rodar e ver falhar.** `rtk .venv/bin/python -m pytest -q tests/test_backup.py` → FAIL.
- [ ] **Step 3: Implementar** com `sqlalchemy.select(t)` e `insert(t)` na camada Core, para cobrir as tabelas de associação (`game_genres`, `game_platforms`).
- [ ] **Step 4: Rodar** `tests/test_backup.py tests/test_mapa_dados.py` → PASS.
- [ ] **Step 5: Stage e mensagem.** `feat(backup): exportação assinada e restauração numa transação`.

### Tarefa 18: Comandos `flask backup exportar|importar` (BKP-01, BKP-02, BKP-05)

**Files:**
- Modify: `src/app/backup.py`
- Test: `tests/test_backup.py`

**Interfaces:**
- Produces:
  - `exportar --saida <arquivo>`: escreve em `<arquivo>.tmp` e faz `os.replace`, e imprime `tabelas=N linhas=M arquivo=<caminho>`;
  - `importar --arquivo <arquivo> [--confirmar]`: sem `--confirmar`, imprime `tabelas=N linhas=M` e sai com 0. Com ele, restaura, faz o commit e imprime `restaurado tabelas=N linhas=M`;
  - os avisos de BKP-07 vão para o stderr (`click.echo(..., err=True)`);
  - um erro imprime `erro=<motivo>` e sai com `click.exceptions.Exit(1)`, sem traceback. Sem chave, a mensagem é `erro=BACKUP_KEY não configurada`, e `exportar` não cria o arquivo.

- [ ] **Step 1: Escrever os testes que falham** (`app.test_cli_runner()`):
  - `test_bkp_01_cli_exportar`: a saída casa com `^tabelas=\d+ linhas=\d+ arquivo=`.
  - `test_bkp_02_sem_chave`: com `delenv`, `exportar` sai com 1, imprime a mensagem e não cria o arquivo. `importar` também sai com 1.
  - `test_bkp_02_chave_nunca_na_saida`: o valor de `BACKUP_KEY` não aparece na saída nem no arquivo.
  - `test_bkp_05_adulterado_um_byte`: com um byte trocado num valor do arquivo, a saída é `erro=assinatura inválida`, o código é 1 e as contagens não mudam. JSON truncado também dá código 1. `versao_formato: 2` também dá código 1.
  - `test_bkp_05_previa_sem_confirmar`: sai com 0, imprime `tabelas=`, e o banco não muda (crie um usuário depois do export e confira que ele continua lá).
- [ ] **Step 2: Rodar e ver falhar.** `-k "bkp_01_cli or bkp_02 or bkp_05"` → FAIL.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar** → PASS.
- [ ] **Step 5: Stage e mensagem.** `feat(backup): comandos exportar e importar com prévia`.

### Tarefa 19: Painel `/admin/backup` (BKP-08, BKP-09)

**Files:**
- Modify: `src/app/backup.py`, `src/app/templates/admin/index.html` (link)
- Create: `src/app/templates/admin/backup.html`, `src/app/templates/admin/backup_previa.html`
- Test: `tests/test_backup.py`

**Interfaces:**
- Consumes: `admin.audit`, `util.admin_required`, `auth.start_session`, `tempo.agora`.
- Produces, todas com `@admin_required`:
  - `GET /admin/backup`: mostra as contagens atuais;
  - `POST /admin/backup/baixar`: anexo `gamelog-backup-AAAAMMDD-HHMM.json` (hora de Belém), `Cache-Control: no-store`, `audit("backup.exportar", "tabelas=N linhas=M")`;
  - `POST /admin/backup/previa` (campo `arquivo`): com `request.content_length > 20 * 1024 * 1024`, responde 413 antes de ler `request.files`. Confere e grava em `backup_dir()/previa-<secrets.token_hex(16)>.json`. Grava `audit("backup.previa", ...)` e renderiza a prévia com o campo oculto `previa` e o checkbox `confirmar`. Arquivo inválido responde 400 com a mensagem e grava a auditoria com `resultado=erro`;
  - `POST /admin/backup/restaurar`: `previa` precisa casar `^[0-9a-f]{32}$`, senão 400. Sem `confirmar`, responde 400 na tela da prévia. Confere, restaura e grava `AuditLog(admin_id=<id do usuário restaurado com o mesmo username_lower, ou None>, action="backup.restaurar", detail="admin=<username> tabelas=N linhas=M")`. Faz o commit e apaga o arquivo da prévia. Se o usuário existir no backup, chama `start_session` e redireciona para `/admin/backup`. Senão, chama `session.clear()` e redireciona para `/entrar`. Uma falha faz rollback, grava a auditoria `backup.restaurar` com `resultado=erro`, faz o commit e responde 400;
  - `backup_dir() -> str`: `os.environ.get("BACKUP_DIR") or os.path.join(current_app.instance_path, "backups")`, criado com `os.makedirs(exist_ok=True)`.

- [ ] **Step 1: Escrever os testes que falham:**
  - `test_bkp_08_acesso`: o jogador recebe 403 nas 4 rotas e o visitante vai ao login.
  - `test_bkp_08_baixar_e_auditoria`: o nome do arquivo casa com `gamelog-backup-\d{8}-\d{4}\.json`, o JSON passa em `conferir` e existe `AuditLog` `backup.exportar`.
  - `test_bkp_08_previa_e_confirmacao`: o upload responde 200 com `tabelas=` e o banco não muda. O POST sem `confirmar` responde 400. Com `confirmar`, os dados do arquivo ficam no banco e a auditoria `backup.restaurar` sobrevive à troca.
  - `test_bkp_09_admin_continua_logado_ou_vai_ao_login`: com o admin dentro do backup, o GET `/admin/backup` seguinte responde 200. Com um backup sem esse admin, o GET seguinte redireciona para `/entrar`.
  - `test_bkp_08_previa_maliciosa_e_tamanho` (Review Focus 5): `previa="../../x"` e `previa="ab"` respondem 400. Um `content_length` acima de 20 MB responde 413.
- [ ] **Step 2: Rodar e ver falhar.** `-k "bkp_08 or bkp_09"` → FAIL.
- [ ] **Step 3: Implementar.** Os templates seguem `admin/retencao.html`, com `csrf_token()` e `enctype="multipart/form-data"`.
- [ ] **Step 4: Rodar** → PASS. Conferir no navegador com `./dev.sh`: baixar, enviar e confirmar.
- [ ] **Step 5: Stage e mensagem.** `feat(backup): painel do admin com download, prévia e restauração auditados`.

### Tarefa 20: Job agendado com retenção de 7 (BKP-10)

**Files:**
- Modify: `src/app/backup.py`
- Test: `tests/test_backup.py`

**Interfaces:**
- Consumes: `jobs.tarefa`, `jobs.agenda_padrao`, `jobs.ErroPermanente`, `mapa.BACKUPS_MANTIDOS`.
- Produces:
  - `@tarefa("backup") backup_agendado()`: sem chave, levanta `ErroPermanente("BACKUP_KEY não configurada")`. Grava `gamelog-backup-AAAAMMDD-HHMMSS.json` por `.tmp` e `os.replace` e depois chama `rotacionar`;
  - `rotacionar(diretorio, manter=BACKUPS_MANTIDOS) -> int`: apaga os mais antigos pelo nome, só os que casam com `gamelog-backup-*.json`, e devolve quantos apagou;
  - `agenda_padrao("backup", "0 3 * * *", "backup")`.

- [ ] **Step 1: Escrever os testes que falham:**
  - `test_bkp_10_retencao_7`: com 8 arquivos antigos, mais um `previa-…json` e um `outro.txt`, o job deixa 7 `gamelog-backup-*`, inclui o novo e mantém os dois outros arquivos.
  - `test_bkp_10_falha_nao_apaga`: sem chave, `jobs.executar(job.id)` deixa o job `morto` com o erro, e os 8 arquivos continuam.
  - `test_bkp_10_agenda_padrao`: `"backup" in jobs.AGENDAS_PADRAO`.
- [ ] **Step 2: Rodar e ver falhar.** `-k bkp_10` → FAIL.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar** `tests/test_backup.py tests/test_jobs.py` → PASS.
- [ ] **Step 5: Stage e mensagem.** `feat(backup): job diário com os 7 arquivos mais recentes`.

### Tarefa 21: `restaurar-inicial`, `render.yaml` e README (BKP-11)

**Files:**
- Modify: `src/app/backup.py`, `render.yaml`, `README.md`, `.env.example`
- Test: `tests/test_backup.py`

**Interfaces:**
- Consumes: `fontes.base.baixar(url, limite)` (uma tentativa, sem seguir redirecionamento, GLOBAL-10).
- Produces o comando `restaurar-inicial`, que sempre sai com 0:
  - sem `BACKUP_RESTAURAR_DE` ou com `User.query.first()` presente, imprime `restaurado=0`;
  - com `https://`, chama `baixar(url, 20 * 1024 * 1024)`. Com `http://` ou outro esquema, imprime `erro=URL precisa ser https`. Os outros valores são caminho local;
  - depois roda `conferir`, `restaurar`, o commit e imprime `restaurado=1 tabelas=N linhas=M`;
  - qualquer `ErroBackup`, erro de rede ou de arquivo imprime `erro=<motivo>`. O motivo nunca inclui a URL, que pode ter credencial.
- No `render.yaml`:
  - o `startCommand` passa a ser `cd src && flask --app app backup restaurar-inicial && flask --app app seed && flask --app app jobs agendar && gunicorn ...` (o resto igual);
  - `BACKUP_KEY` e `BACKUP_RESTAURAR_DE` entram com `sync: false`.
- No `.env.example`, `BACKUP_KEY=` e `BACKUP_RESTAURAR_DE=` com placeholder vazio.

- [ ] **Step 1: Escrever os testes que falham:**
  - `test_bkp_11_banco_vazio_restaura`: num app novo sem usuários, com `BACKUP_RESTAURAR_DE` apontando para um arquivo exportado antes, a saída é `restaurado=1` e os usuários do arquivo existem.
  - `test_bkp_11_banco_populado_ou_sem_variavel`: os dois casos imprimem `restaurado=0`, e as contagens não mudam.
  - `test_bkp_11_assinatura_invalida_sai_com_0`: `exit_code == 0` e a saída é `erro=assinatura inválida`.
  - `test_bkp_11_https_pela_fonte_comum`: com a fixture `api` de `conftest.py`, `api.rotas["https://x.test/b.json"] = <bytes do arquivo>`, e o arquivo é restaurado. Com `http://x.test`, a saída é `erro=URL precisa ser https` e nenhuma requisição sai (`api.pedidos == []`).
  - `test_bkp_02_render_yaml`: o `render.yaml` declara `BACKUP_KEY` com `sync: false`, e o `startCommand` tem `backup restaurar-inicial` antes de `seed`.
- [ ] **Step 2: Rodar e ver falhar.** `-k "bkp_11 or render_yaml"` → FAIL. Confira se `baixar` devolve `(tipo, corpo)` com a fixture `api`. Se a fixture só simular `get_json`, ajuste com `monkeypatch` em `base.baixar`.
- [ ] **Step 3: Implementar.** No README, a seção "Backup e restore" tem:
  - os comandos;
  - `/admin/backup`;
  - o job das 03:00 e os 7 arquivos;
  - o fluxo do Render: baixar o backup antes do deploy, publicar o arquivo numa URL `https` privada e preencher `BACKUP_RESTAURAR_DE` no painel;
  - os limites do plano gratuito;
  - a linha da tabela `backup-restore (BKP-01..12) | backup.py | tests/test_backup.py`;
  - `BACKUP_KEY`, `BACKUP_DIR` e `BACKUP_RESTAURAR_DE` na lista de variáveis.
- [ ] **Step 4: Rodar** `tests/test_backup.py` → PASS. Rodar localmente `cd src && ../.venv/bin/flask --app app backup exportar --saida /tmp/b.json` e depois `importar --arquivo /tmp/b.json`.
- [ ] **Step 5: Stage e mensagem.** `feat(backup): restauração na subida pelo startCommand do Render`. Depois, a sessão do dia e o PR.

---

## Ordem e paralelismo

- O PR 1 é a base (Tarefas 1 a 9, nessa ordem). A Tarefa 4 não depende das Tarefas 1 a 3 e pode rodar em paralelo.
- O PR 2 (Tarefas 10 a 15) e o PR 3 (Tarefas 16 a 21) dependem só das interfaces do PR 1 e não se tocam. Podem correr em paralelo em branches separadas.
- Corte se o prazo apertar (plan-ap2 §4.2): o backup (PR 3) cai primeiro. Dentro do PR 3, a Tarefa 21 (restauração na subida) cai antes da Tarefa 19 (painel).
