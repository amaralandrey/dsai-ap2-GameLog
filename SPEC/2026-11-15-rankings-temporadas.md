# Rankings e temporadas (2026-11-15)

## O quê e por quê
Hoje o GameLog só compara jogadores dois a dois (comparar gosto, AFI). Com o diário (DIA) e o XP (PRG), dá para comparar jogos zerados, horas registradas, curtidas recebidas e XP. Esta spec cria rankings por temporada trimestral. O escopo padrão é o de quem a pessoa segue ou o do clube, porque comparar-se com conhecidos engaja mais e expõe menos do que um placar público. O ranking global existe, mas só com perfis públicos de adultos: o ECA Digital pede a configuração mais protetiva por padrão para menores (Lei 15.211/2025, art. 7), e a política de menores (PMN) os deixa fora da exposição pública. O encerramento de cada temporada grava um retrato imutável e premia o pódio.

Depende de: DIA (`minutos_registrados`, `zerado_em`, `pode_ver_perfil`), PRG (`ExtratoXP`), GAM (conquistas de pódio), CLB (escopo clube), JOB (abertura e encerramento automáticos), BLQ (bloqueio). Altera: AVA-06 (a curtida passa a guardar `created_at`; curtidas antigas, sem data, contam só na janela "sempre"). Respeita: MAP (campos novos anotados) e PMN (menores fora do global).

## Critérios de aceitação
- RNK-01: o modelo `Temporada` tem nome, `slug` único, `inicio` e `fim` (datas). As temporadas são os trimestres civis, por exemplo "2026-T4", de 01/10 a 31/12.
  - O comando `flask --app app rankings abrir-temporada` cria a temporada do trimestre atual se ela não existir e imprime `temporada=<slug> criada=0|1`.
  - O seed e o job trimestral (JOB) chamam o mesmo comando.
- RNK-02: `/rankings` mostra quatro categorias, cada uma somada só dentro da janela escolhida:
  - `zerados`: jogos com `Status.zerado_em` na janela;
  - `horas`: minutos do diário na janela (`minutos_registrados(..., origens=("manual", "csv"))`, DIA-08), só de sessões de origem `manual` ou `csv` — a sessão de saldo da Steam (HRS) traria de uma vez as horas de anos — mostrados em horas com uma casa decimal;
  - `curtidas`: curtidas recebidas em avaliações visíveis, dadas na janela;
  - `xp`: soma do `ExtratoXP` na janela.
  - A tela é paginada de 20 em 20. Quem tem 0 numa categoria não aparece nela.
- RNK-03: a janela é escolhida por `?janela=` e aceita `atual` (temporada atual, o padrão), o `slug` de uma temporada ou `sempre`. A categoria vem de `?categoria=` (padrão `xp`). Valor desconhecido volta ao padrão, sem erro (BUS-08).
- RNK-04: o escopo vem de `?escopo=`.
  - `seguindo` é o padrão para quem está logado: inclui o próprio usuário e quem ele segue.
  - `global`: só perfis públicos (`User.perfil_privado` falso) de usuários para quem `pode(user, "ranking_global")` é verdadeiro (PMN).
  - `clube=<slug>`: só membros do clube (CLB). Clube privado responde 404 a quem não é membro.
  - O visitante vê só o `global`. Um `seguindo` pedido sem login redireciona ao login (GLOBAL-02).
- RNK-05: no escopo `seguindo`, um perfil privado aparece só para quem pode vê-lo (`pode_ver_perfil`, DIA). Um usuário que bloqueou ou foi bloqueado por quem consulta não aparece em nenhum escopo para ele (`bloqueado_entre`, BLQ). Admins não aparecem em nenhum ranking.
- RNK-06: quando o usuário logado está no escopo e na categoria, um cartão fixo no topo mostra a posição e o valor dele, mesmo fora da página atual. Se ele não pontuou, o cartão diz "Você ainda não pontuou nesta temporada".
- RNK-07: o desempate é determinístico. Primeiro vem o maior valor; depois quem alcançou o valor antes (o menor instante do último registro que formou o valor); por fim, o menor id de usuário. A mesma consulta sempre devolve a mesma ordem.
- RNK-08: o comando `flask --app app rankings encerrar-temporada <slug>` só aceita uma temporada cujo `fim` já passou (senão sai com código 1 e `erro=temporada em andamento`). Ele:
  - grava `SnapshotRanking` (temporada, categoria, posição, usuário, valor) com os 100 primeiros do escopo global de cada categoria;
  - desbloqueia para os 3 primeiros de cada categoria a conquista de pódio correspondente (GAM, códigos `podio-<categoria>`);
  - notifica cada premiado (`notificar(..., "sistema")`).
  - Rodar o comando de novo não altera o retrato nem premia de novo: imprime `ja_encerrada=1`.
- RNK-09: os rankings de uma temporada encerrada são lidos do `SnapshotRanking`, que nunca muda depois de gravado, inclusive se dados antigos forem apagados. `/rankings/hall-da-fama` lista, por temporada encerrada (da mais recente para a mais antiga), o pódio de cada categoria. Usuários anonimizados (DIR) aparecem como "usuário removido".
- RNK-10: `/admin/rankings` permite criar uma temporada fora do calendário (nome, datas validadas, sem sobreposição) e desclassificar um usuário de uma temporada e categoria, com motivo obrigatório.
  - O desclassificado some do ranking e da apuração daquela temporada.
  - A desclassificação pode ser revertida enquanto a temporada não foi encerrada.
  - Cada ação grava `AuditLog` (`ranking.temporada`, `ranking.desclassificar`, `ranking.reverter`).
  - Quem não é admin recebe 403.
- RNK-11: cada página de ranking agrega no banco (`GROUP BY` com índices em `SessaoJogo.jogada_em`, `Status.zerado_em`, `Like.created_at` e `ExtratoXP.created_at`). O número de consultas por página fica em no máximo 6, qualquer que seja o número de usuários.
- RNK-12: testes sem rede, nomeados pelo ID (`test_rnk_NN_...`), cobrem:
  - cada categoria e cada janela;
  - os três escopos;
  - perfil privado, menor protegido, admin e bloqueio fora do ranking;
  - o desempate com empate de valor e de instante;
  - o cartão do usuário fora da página;
  - abertura idempotente da temporada;
  - encerramento antes do fim (erro), encerramento e reencerramento;
  - o retrato imutável;
  - a desclassificação com auditoria;
  - o orçamento de consultas.

## Fora do escopo
Prêmios em dinheiro ou itens, rankings por jogo ou gênero, ranking de conquistas, temporadas por clube, cache em memória, mínimo de participações por categoria, apuração em tempo real por WebSocket.
