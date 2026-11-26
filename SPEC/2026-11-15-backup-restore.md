# Backup e restore portável do banco (2026-11-15)

## O quê e por quê
No plano gratuito do Render, o disco do serviço web é apagado a cada deploy e a cada reinício, então o `gamelog.db` volta vazio e o `startCommand` do `render.yaml` roda o `seed` de novo (o `seed` em si não apaga nada: `seed.run` não faz nada quando já há jogos). Na issue *Primeiras impressões*, isso aparece como evidência de que nenhum dado de usuário real sobrevive em produção. Com diário, conquistas, clubes e tokens, perder o banco passa a custar mais. Esta spec cria um backup completo do estado do SQLite em JSON, portável entre versões do esquema, assinado com HMAC para que um arquivo adulterado não seja restaurado. Ele funciona pela linha de comando e pelo painel admin, usando os serializadores da API v2 para as entidades que eles já cobrem.

Depende de: AP2-07 (serializadores), JOB (backup agendado), ADM-09 (auditoria). Respeita: MAP (o backup é cópia integral e não usa `campos_exportaveis`, que é da portabilidade; ele carrega dados pessoais e hashes de autenticação; o mapa de dados registra a finalidade "continuidade do serviço" e a retenção dos arquivos) e INC (um backup vazado é incidente).

## Critérios de aceitação
- BKP-01: `flask --app app backup exportar --saida <arquivo.json>` grava um JSON com:
  - `formato: "gamelog-backup"`, `versao_formato: 1`;
  - `gerado_em` (ISO-8601, UTC);
  - `versao_app` (commit, se houver);
  - `tabelas` (um objeto por tabela do modelo, com a lista de linhas);
  - `contagens` (linhas por tabela);
  - `assinatura`.

  A ordem das tabelas respeita as chaves estrangeiras (pais antes de filhos). O comando imprime `tabelas=N linhas=M arquivo=<caminho>`.
- BKP-02: a assinatura é o HMAC-SHA256, em hexadecimal, do JSON canônico do conteúdo (`sort_keys`, separadores compactos, UTF-8, sem o campo `assinatura`). A chave vem de `BACKUP_KEY`. Sem `BACKUP_KEY`, exportar e importar terminam com `erro=BACKUP_KEY não configurada` e código 1, sem traceback e sem criar o arquivo. O `render.yaml` declara `BACKUP_KEY` com `sync: false`. A chave nunca aparece no arquivo, na saída nem nos logs.
- BKP-03: entram no backup todas as tabelas do `db.metadata`, exceto as voláteis (`LoginAttempt`, os jobs já concluídos e a contagem de limite de taxa da API v2) e a `instancia`, cujo token identifica o banco em uso: a restauração não apaga nem troca esse token, então um cookie emitido pelo banco de origem não vale no banco restaurado. Os hashes de senha e de token entram como estão (nunca em texto puro, GLOBAL-06). A lista de exclusões é uma constante única, e um teste fixa o conjunto completo dos nomes de tabela incluídos, para que uma tabela nova exija decidir se entra no backup ou na constante. `user.session_version` é estado de sessão e não entra no arquivo. Na restauração, cada conta recebe um valor aleatório novo, para nenhum cookie anterior valer sobre um id reaproveitado; quem restaura pelo painel entra de novo (BKP-09).
- BKP-04: cada linha leva todas as colunas pelo serializador genérico: datas em ISO-8601, `Date`, `Time` e `DateTime` distinguidos pelo tipo da coluna e booleanos como JSON. As linhas das entidades com serializador de AP2-07 (jogos, avaliações, sessões, listas) levam também a chave `_api`, com a saída desse serializador. A restauração ignora as chaves que começam com `_`, porque os serializadores não carregam todas as colunas.
- BKP-05: `flask --app app backup importar --arquivo <arquivo.json> [--confirmar]` primeiro confere o formato, a `versao_formato` suportada e a assinatura. Arquivo malformado, de versão desconhecida ou com assinatura que não confere termina com código 1 e `erro=assinatura inválida` (ou o motivo), sem tocar no banco. Sem `--confirmar`, o comando só imprime o que faria (`tabelas=N linhas=M`) e sai com 0. Uma assinatura que não é hexadecimal de 64 caracteres (inclusive com caractere não ASCII) responde `assinatura inválida`; um texto que não codifica em UTF-8 responde `arquivo malformado`. Nenhum dos dois vira erro 500.
- BKP-06: com `--confirmar`, a restauração apaga e regrava as tabelas incluídas numa única transação, preservando os ids. Em qualquer erro (chave estrangeira, linha inválida), tudo é desfeito com rollback, e o banco fica exatamente como antes. Ao final, `contagens` do arquivo e do banco conferem, ou o comando falha. Restaurar e exportar de novo produz o mesmo conteúdo (sem contar `gerado_em` e `assinatura`).
- BKP-07: colunas que existem no banco mas não no arquivo (backup de versão anterior do esquema) recebem o valor padrão do modelo. Colunas do arquivo que o modelo não tem mais são ignoradas, e cada uma é avisada uma vez no stderr. Uma tabela do arquivo que não existe mais é ignorada com aviso. Uma coluna obrigatória sem padrão e ausente no arquivo aborta a restauração (BKP-06).
- BKP-08: `/admin/backup` (só admin; jogador recebe 403, GLOBAL-05) mostra as contagens atuais e oferece "Baixar backup", que gera o mesmo arquivo de BKP-01 como download `gamelog-backup-AAAAMMDD-HHMM.json`. Também oferece "Restaurar", que recebe o arquivo por upload (até 20 MB; o limite vale para o corpo de toda requisição do app, com ou sem `Content-Length`, e acima dele a resposta é 413), mostra a prévia de BKP-05 e só restaura depois de um segundo POST com a confirmação marcada.
- BKP-09: baixar, pré-visualizar e restaurar gravam no `AuditLog` (`backup.exportar`, `backup.previa` e `backup.restaurar`), com as contagens e o resultado. A restauração preserva o próprio registro de auditoria dela, gravado depois da troca das tabelas. O admin que restaura continua logado se a conta dele existir no backup com o mesmo id e o mesmo nome de usuário. Uma conta homônima com outro id não vale, e o admin vai ao login.
- BKP-10: o job `backup` (JOB) roda conforme o agendamento configurado e grava o arquivo em `BACKUP_DIR` (padrão `instance/backups/`, fora do Git e coberto pelo `.gitignore`), mantendo os 7 mais recentes (retenção conferida pelo mapa de dados). Uma falha do job fica no histórico de `/admin/jobs` sem apagar backups antigos.
- BKP-11: `flask --app app backup restaurar-inicial` roda no `startCommand` antes do `seed`. Com `BACKUP_RESTAURAR_DE` (caminho local ou URL `https`, porque o disco do plano gratuito some a cada deploy) e o banco sem usuários, obtém o arquivo, confere a assinatura (BKP-05) e restaura (BKP-06); com o banco já populado ou sem a variável, não faz nada e imprime `restaurado=0`. Como o `seed` não faz nada quando já há jogos, os dados restaurados não são sobrescritos. Qualquer falha (arquivo ilegível ou que é pasta, JSON quebrado, assinatura malformada, download que falha ou erro inesperado) imprime `erro=<motivo>` e sai com 0, para o deploy não cair, e o `seed` roda como hoje. O download não segue redirecionamento: `BACKUP_RESTAURAR_DE` aponta para a URL final do arquivo, e uma resposta 3xx imprime `erro=download falhou: Redirecionado`, sem o destino. O README explica o fluxo e os limites do plano gratuito.
- BKP-12: os testes rodam sem rede, num banco temporário, e cobrem:
  - ida e volta (exportar, apagar, importar e exportar de novo dá o mesmo conteúdo);
  - assinatura adulterada em um byte (recusada, banco intacto);
  - falta de `BACKUP_KEY`;
  - prévia sem `--confirmar`;
  - rollback em linha inválida;
  - coluna nova e coluna removida (BKP-07);
  - tabelas excluídas de propósito e a constante cobrindo todo o modelo;
  - rotas admin (403 e auditoria);
  - retenção de 7 arquivos;
  - `backup restaurar-inicial` com banco vazio, com banco populado, sem a variável e com assinatura inválida;
  - assinatura não ASCII, arquivo que é pasta, JSON quebrado e erro inesperado em `restaurar-inicial`;
  - upload sem `Content-Length` acima de 20 MB;
  - admin homônimo no backup;
  - `instancia` fora do arquivo e preservada;
  - redirecionamento no download.

## Fora do escopo
Backup incremental ou contínuo, envio automático para nuvem (S3, Google Drive), criptografia do conteúdo (a assinatura garante integridade, não sigilo), restauração parcial por usuário ou por tabela, migração para PostgreSQL e troca do plano do Render.
