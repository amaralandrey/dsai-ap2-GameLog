# Sessão Claude Code: plano e correção da issue #56 (BKP) (2026-11-26)

**Ferramenta:** Claude Code CLI (Claude Opus 5.5, `claude-opus-5-5`), skills `superpowers:writing-plans`, `git-pr`, `superpowers:executing-plans` e `superpowers:test-driven-development`, com 1 subagente revisor no fim
**Quem conduziu:** Fabrício Assunção
**Ponto de partida:** `main` local 9 commits atrás da `origin/main`; `git pull --ff-only` até `307e388`; branch `fix/backup-issue-56` a partir da `origin/main`

## Custo

Medido nos logs do Claude Code: `cf072221-....jsonl` (planejamento), `c893bea7-....jsonl` (execução) e `subagents/*.jsonl` (revisor). Cada resposta do modelo conta uma vez só, com o maior `usage` registrado para o mesmo `message.id`, mesmo quando aparece nos dois arquivos. Os preços são os da API do Claude Opus 5.5, os mesmos do registro de 2026-11-23.

| Tipo de token | Tokens | Preço (US$/1M) | Custo (US$) |
|---|---:|---:|---:|
| Entrada | 346 | 4,00 | 0,00 |
| Saída | 130.828 | 20,00 | 2,62 |
| Leitura de cache | 22.020.345 | 0,20 | 4,40 |
| Escrita de cache (TTL 5 min) | 99.910 | 5,00 | 0,50 |
| Escrita de cache (TTL 1h) | 334.823 | 8,00 | 2,68 |
| **Total** | **22.586.252** | | **≈ 10,20** |

São 156 requisições ao modelo: 62 do planejamento (≈ US$ 4,93), 80 da execução (≈ US$ 4,24) e 14 do revisor (≈ US$ 1,02). A conta não inclui as requisições que escreveram esta versão do registro.

## Prompts

1. `/superpowers:writing-plans` monte um plano de ação para fazer a correção dos apontamentos da issue https://github.com/amaralandrey/dsai-ap2-GameLog/issues/56. Ao final do plano quero que registre em sessões a sessão atual em sessões. Gere também a descrição da PR com detalhes acerca da implementação de código
2. `/superpowers:executing-plans .claude/plan/2026-11-26-bkp-issue-56.md`

## Como foi feito

1. **Issue.** Li a issue #56 pela API do GitHub (o `gh issue view` falha por causa do Projects clássico). São 4 correções obrigatórias (BKP-11 e o teste do BKP-03) e 4 menores.
2. **Reprodução antes de planejar.** `hmac.compare_digest("ab", "çé")` lança `TypeError`, e `ler_arquivo` numa pasta lança `IsADirectoryError`. O JSON quebrado no caminho local já virava `arquivo malformado`, então esse item só precisava de teste. Um surrogate isolado (`"\udc80"`) em qualquer texto do arquivo faz `canonico().encode()` lançar `UnicodeEncodeError`, um caso que a issue não cita.
3. **Plano.** Gravado em `.claude/plan/2026-11-26-bkp-issue-56.md`: spec primeiro, 5 tasks com teste que falha e fix, e uma task de verificação. A descrição da PR saiu pela skill `git-pr`.
4. **Execução, uma task por vez, com o teste visto falhando antes do fix:**
   - spec: BKP-03, BKP-05, BKP-08, BKP-09, BKP-11 e BKP-12;
   - `conferir` com assinatura hexadecimal de 64 caracteres, `ler_arquivo` com `OSError`, `restaurar-inicial` com `except Exception`;
   - `instancia` fora do backup e preservada na restauração (`PRESERVADAS`), e o teste do BKP-03 com os 61 nomes de tabela;
   - `MAX_CONTENT_LENGTH` de 20 MB no app;
   - admin reencontrado por id e nome;
   - teste do redirecionamento e README.
5. **Erro de verificação achado na execução.** No planejamento, eu disse que um upload sem `Content-Length` recebia 413. A conferência estava errada: o `EnvironBuilder` do Werkzeug preenche o `CONTENT_LENGTH` a partir do `BytesIO`. O teste do plano passou antes do fix. Com `HTTP_TRANSFER_ENCODING: chunked`, ele falhou com 400, e só passou com o `MAX_CONTENT_LENGTH`.
6. **Mutação manual.** Para o teste do redirecionamento, mudei `obter_inicial` para seguir o `Location`. O teste falhou, e desfiz a mudança.
7. **Revisão final.** Um subagente revisor (Opus) leu o diff sem a minha leitura. Ele achou que um surrogate em `versao_formato` ainda derrubava o `restaurar-inicial` (saída 1) e dava 500 na prévia, porque a mensagem de erro repetia o valor do arquivo. Achou também que um JSON muito aninhado (`RecursionError`) dava 500 e traceback. Os dois ganharam teste que falhou primeiro e depois o fix.
8. **Verificação.** `tests/test_backup.py`, `test_mapa_dados`, `test_importacao_horas`, `test_email`, `test_precos`, `test_diario` e `test_auth`: 284 testes antes da revisão, e 126 das partes depois dela. O `diff-cover` deu 100% (23 linhas). A suíte inteira não rodou localmente: fica com o CI.

## Artefatos

| Item | Conteúdo | Situação |
|---|---|---|
| `SPEC/2026-11-15-backup-restore.md` | critérios ajustados | staged, a commitar sozinha |
| `src/app/backup.py`, `src/app/__init__.py` | correções | a commitar |
| `tests/test_backup.py` | 15 casos novos (8 funções) e o BKP-03 refeito | a commitar |
| `README.md` | `instancia`, limite e URL final | a commitar |
| `.claude/plan/2026-11-26-bkp-issue-56.md` | plano | a commitar |
| `~/Projects/cards/gamelog-backup/pr-fix-backup-issue-56.txt` | título e corpo da PR | fora do repo |

## Decisões

- **`instancia`:** fica fora do backup e a restauração não apaga a tabela. Regenerar o token exigiria avisar os dois workers do gunicorn; manter o token do destino resolve sem isso.
- **Limite de upload no app inteiro:** o hook de CSRF lê `request.form` antes da view, então um limite definido na view chegaria tarde.
- **Admin por consulta SQL:** `db.session.get` devolveria o objeto do identity map, com a linha já trocada pelo `delete`/`insert` do Core.
- **Redirecionamento:** documentado e fixado num teste, sem seguir 3xx (RTN-08).
- **Mensagem de versão sem o valor:** `versão do formato não suportada` não repete o que veio do arquivo. Isso evita o surrogate e uma linha falsa de `restaurado=1` no log do deploy.
- **Sem commit pelo agente:** os passos de commit do plano viraram um roteiro para o dev, por camada.
- **Card do Notion:** não publicado. O projeto é da disciplina, e a PR fica com o placeholder `Card:`.

## Pontos de atenção

- **Pendências menores (não corrigidas):** `restaurar-inicial` imprime `restauração desfeita: <Tipo>` também quando a falha foi antes da restauração. O 413 usa a página padrão do Werkzeug, então um CSV do diário acima de 20 MB perde a mensagem própria.
- **Navegador:** a tela não mudou, mas vale conferir `/admin/backup` com `./dev.sh` e `BACKUP_KEY` no shell do dev.
- **Sem mudança de coluna:** não precisa de `./dev.sh reset`.
- **Depois do merge:** a issue fecha pelo `Fixes #56`, e `BACKUP_RESTAURAR_DE` pode ser configurada no Render.
