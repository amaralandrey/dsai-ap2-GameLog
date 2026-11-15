# Classificação indicativa (2026-11-15)

## O quê e por quê
O catálogo tem 153 jogos reais da Steam e, pelo Épico 2, jogos da GOG e da RAWG, mas não guarda faixa etária nenhuma. Um adolescente de 13 anos vê na busca, na página inicial e nas recomendações jogos que a própria loja restringe a maiores de 18. As fontes já trazem a informação: a Steam traz `required_age` no `appdetails` que o conector já chama (`fontes/steam.detalhe`) e a RAWG traz `esrb_rating`. Esta spec guarda a classificação de cada jogo na escala da ClassInd e a usa no catálogo para menores (PMN).

Ressalva: a classificação oficial no Brasil é a da ClassInd (Portaria MJSP 1.048/2025, faixas L, 6, 10, 12, 14, 16 e 18; jogos classificados pelo IARC). O `required_age` da Steam é o bloqueio etário escolhido pelo publisher, e não existe tabela oficial de ESRB para ClassInd. Os mapeamentos abaixo são uma equivalência da dupla, que arredonda sempre para a faixa mais restritiva, e a página diz isso.

Depende de: FNT/GOG (conectores Steam e RAWG), SNP (snapshot do seed), IDA (faixa do usuário), PMN.
Respeita: PMN (filtro para menores).

## Critérios de aceitação
- CLS-01: `Game.classificacao` aceita `L`, `6`, `10`, `12`, `14`, `16`, `18` ou `NULL` (não informada). `Game.classificacao_origem` aceita `steam`, `rawg` ou `admin`. Qualquer outro valor é recusado no serviço e no formulário do admin com mensagem (GLOBAL-03).
- CLS-02: na importação e na sincronização da Steam, `required_age` 0 ou ausente não muda nada. Um valor positivo vira a menor faixa da ClassInd maior ou igual a ele (ex.: 13 → `14`, 17 → `18`, 18 → `18`). Valor inválido é ignorado e não conta como falha.
- CLS-03: na RAWG, `esrb_rating.slug` mapeia `everyone` → `L`, `everyone-10-plus` → `10`, `teen` → `14`, `mature` → `18` e `adults-only` → `18`. `rating-pending` ou ausente não muda nada. As duas tabelas ficam em `menores/classificacao.py`.
- CLS-04: quando as fontes divergem, vale a faixa mais restritiva. A classificação definida pelo admin (`origem=admin`) nunca é sobrescrita por sincronização. A mudança pelo admin grava `AuditLog` com o valor antigo e o novo.
- CLS-05: `export-games` e `enriquecer-snapshot` gravam `classificacao` em cada jogo do `jogos.json`, e o seed importa o campo sem rede. Um jogo do snapshot sem o campo fica `NULL`, como antes (CRE-02).
- CLS-06: a página do jogo mostra o selo da faixa, ou "Classificação não informada", com a nota "equivalência do GameLog a partir da Steam/RAWG; não é a classificação oficial". A API v1 e a v2 trazem `classificacao` no detalhe do jogo.
- CLS-07: para um usuário cuja idade (IDA-03) é menor que a faixa do jogo, o jogo não aparece na página inicial, no catálogo, na busca, nos gêneros, nas recomendações, nas listas de outras pessoas, nos rankings de jogos nem na API. A contagem e a paginação já saem filtradas, e a página da busca não fica com buracos.
- CLS-08: esse usuário, ao abrir direto a página do jogo, recebe 200 com o aviso "Este jogo é classificado para N anos", sem capa, sinopse, avaliações nem botões de status. Jogos com classificação `NULL` aparecem para todos com o selo de não informada.
- CLS-09: `/buscar` e `/api/jogos` aceitam `classificacao_max` (uma das faixas). Valor inválido é ignorado (BUS-08). O filtro está disponível para qualquer pessoa e, para menores, nunca permite passar da própria idade.
- CLS-10: os testes rodam sem rede, com respostas falsas da Steam e da RAWG, e cobrem: mapeamento de cada `required_age` limite (0, 10, 13, 16, 17, 18) e de cada ESRB, divergência entre fontes, admin preservado na sincronização, snapshot com e sem o campo, filtro em cada listagem para 12, 16 e adulto, página direta com aviso e `classificacao_max` inválido. Todos nomeados `test_cls_NN_*`.

## Fora do escopo
Classificação oficial pelo IARC ou pela ClassInd, descritores de conteúdo (violência, drogas, sexo) e eixo de interatividade, bloqueio de páginas de jogos 18 para visitantes sem conta (o GameLog não distribui o jogo, só informa sobre ele; a decisão fica a revisar com a regulamentação) e classificação dos jogos fictícios antigos.
