# Snapshot multi-loja do catálogo (2026-11-15)

## O quê e por quê
Em produção, o Render recria o banco a cada deploy rodando o `seed`, que só lê o snapshot `src/app/dados/jogos.json`, vindo da Steam. Por isso a URL pública mostra só a Steam em "Disponível em" (MRG-12), e a demanda #1 da cliente (catálogo multi-loja: Steam, GOG e Epic) não aparece para quem visita. Além disso, a RAWG pode estar sem chave no servidor. Esta spec enriquece o snapshot uma vez, localmente e com a chave da RAWG, com os links de GOG e Epic de cada jogo e com o id da RAWG. Assim o seed cria esses vínculos sem rede e sem chave.

Altera: CRE-02 (cada jogo do snapshot pode trazer `lojas` e `origens`, opcionais); FNT-05 (o seed também cria os vínculos dessas listas).

## Critérios de aceitação
- SNP-01: `flask --app app enriquecer-snapshot [--arquivo src/app/dados/jogos.json]` procura cada jogo do snapshot na RAWG pelo título, olha no máximo os 3 primeiros resultados e aceita só o resultado cujo link da Steam tem o mesmo id do jogo (GOG-06). Não usa semelhança de título. O comando imprime `jogos=N vinculados=M sem_correspondencia=K falhas=F`.
- SNP-02: num jogo aceito, o comando grava `lojas` (lista de `[loja, url]` com os links de GOG e Epic que passam por FNT-07) e `origens` (`[["rawg", "<id>"]]`). Os demais campos e a ordem dos jogos não mudam. Um jogo sem correspondência fica exatamente como estava.
- SNP-03: o comando é idempotente: rodar de novo troca `lojas` e `origens` do jogo, sem duplicar nada. A chave da RAWG nunca é gravada no arquivo nem impressa (GOG-05).
- SNP-04: sem `RAWG_API_KEY`, o comando avisa `erro=RAWG_API_KEY não configurada`, sai com código 1 e não altera o arquivo (GOG-07).
- SNP-05: uma falha de rede ou de HTTP num jogo conta em `falhas` e não muda esse jogo. Um HTTP 429 que persiste depois das novas tentativas para o comando, grava o que já foi processado e acrescenta `interrompido=rate-limit` à linha de saída (GOG-11).
- SNP-06: na importação e na sincronização, `origens` cria um vínculo para cada fonte registrada da lista (FNT-04). Assim o seed (fonte `arquivo`) passa a criar, sem rede e sem chave, os vínculos `gog`, `epic` e `rawg` dos jogos enriquecidos. A página do jogo mostra "Disponível em" com as três lojas e o crédito da RAWG (MRG-12, GOG-14).
- SNP-07: um jogo do snapshot sem `lojas` e sem `origens` é importado como antes (CRE-02, FNT-05). Um vínculo de `origens` cujo id já pertence a outro jogo não é criado.
- SNP-08: o snapshot versionado é regenerado com o comando. A seção de histórico da spec catalogo-real registra quantos jogos ganharam vínculos.
- SNP-09: os testes rodam sem rede, com respostas falsas da RAWG para: correspondência no 1º e no 3º resultado, sem correspondência, falha num jogo, 429 persistente, falta da chave e seed com o snapshot enriquecido.

## Fora do escopo
Jogos que só existem na GOG ou na Epic no snapshot, preços por loja, persistência do banco em produção (backup/restore do Épico 6), cron de sincronização no Render e acesso direto à loja da Epic (issue #9).
