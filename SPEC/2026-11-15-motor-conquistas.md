# Motor de conquistas orientado a eventos (2026-11-15)

## O quê e por quê
As conquistas da AP1 são três regras fixas calculadas a cada abertura do perfil (`perfil.conquistas()`, CON-01..06): não há tabela, data de desbloqueio, progresso, aviso nem como o admin criar outra. A investigação da AP1 apontou a gamificação como a lacuna do produto. Esta spec cria um catálogo de conquistas guardado no banco e um barramento de eventos (`emitir_evento`) chamado pelas ações do usuário (avaliar, registrar sessão no diário, zerar, seguir, comentar, curtir, criar lista). O motor avalia só as conquistas ligadas ao evento, grava o progresso e desbloqueia cada conquista uma única vez. O diário (DIA) é a principal fonte de dados novos. As mesmas chamadas alimentam o XP (PRG), os clubes (CLB) e os torneios (TRN).

Depende de: DIA (sessões, `Status.zerado_em`, `pode_ver_perfil`), NTF (`notificar`). Altera: CON-01 (as três conquistas viram itens do catálogo, com os mesmos códigos e regras), CON-02 (a seção mostra a estante e as 6 mais recentes, GAM-09), CON-03 (passam a ser gravadas em tabela, a partir de eventos), CON-04 (avaliação oculta deixa de contar no progresso, mas não retira conquista já desbloqueada, GAM-07), CON-06 (o perfil lê as conquistas gravadas, sem recalcular); os testes `test_con*`, que gravam avaliações e listas direto no banco, passam a chamar `conquistas recalcular` (GAM-13) antes de olhar o perfil. SOC-05 (desbloquear gera `Activity(kind="conquista")`). Respeita: MAP (campos novos anotados no mapa de dados) e PMN (conquistas por tempo de jogo não valem para menores).

## Critérios de aceitação
- GAM-01: existem os modelos `Conquista` e `UsuarioConquista`.
  - `Conquista`: `codigo` único, nome, descrição, categoria, raridade, ícone (emoji), `metrica`, `meta` (inteiro ≥ 1), `secreta`, `por_tempo` e `ativa`. Categorias: critica, colecao, explorador, diario, social e comunidade. Raridades: comum, rara, epica e lendaria.
  - `UsuarioConquista`: usuário, conquista, `progresso` e `desbloqueada_em` (nula enquanto bloqueada). É única por (usuário, conquista).
- GAM-02: o seed e o comando `flask --app app conquistas sincronizar` gravam o catálogo inicial: no mínimo 40 conquistas, com pelo menos 4 em cada categoria. Exemplos: "Maratonista" (sessão de 6 h ou mais), "100 horas registradas", "Zerou 10 no ano", "Explorador multi-loja" (jogos com vínculo em 3 lojas). O comando é idempotente: rodar de novo atualiza textos e metas pelo `codigo` e não duplica nada. Uma conquista criada pelo admin não é alterada pelo comando.
- GAM-03: a regra de uma conquista é a dupla (`metrica`, `meta`). `metrica` vem de um catálogo fixo em `gamificacao.py`, e cada métrica declara os eventos que a afetam e uma função que a calcula a partir dos dados.
  - Exemplos de métrica: avaliacoes_visiveis, jogos_em_listas, primeira_avaliacao, sessoes, minutos_registrados, maior_sessao_min, zerados_no_ano, lojas_distintas, seguidores, curtidas_recebidas, comentarios_feitos, semanas_seguidas, metas_clube (evento `meta_clube`, CLB-08), torneios_jogados e torneios_podio (evento `torneio`, TRN-11).
  - A métrica especial `concedida` não escuta evento: a conquista só é desbloqueada por `conceder(user_id, codigo)`, usada pelo encerramento de temporada (pódios `podio-zerados`, `podio-horas`, `podio-curtidas` e `podio-xp`, RNK-08).
  - `emitir_evento(user_id, tipo, **ctx)` só avalia as conquistas ativas cujas métricas escutam aquele `tipo`.
  - Um evento que nenhuma métrica escuta não faz nenhuma consulta às conquistas.
  - As rotas de avaliar, apagar avaliação, registrar/editar/apagar sessão, mudar status, seguir, comentar, curtir e criar lista chamam `emitir_evento` depois do commit da ação. Uma exceção dentro do motor é registrada em log e não desfaz nem quebra a ação do usuário: a resposta HTTP é a mesma de sem o motor.
- GAM-04: conquistas progressivas gravam `progresso` (valor atual da métrica, limitado à meta). O perfil mostra a barra "N de META". A conquista é desbloqueada quando o progresso alcança a meta, e `desbloqueada_em` recebe a data e hora.
- GAM-05: o desbloqueio é idempotente.
  - Duas avaliações do mesmo evento, ou dois eventos simultâneos, resultam em uma única `UsuarioConquista` desbloqueada.
  - Uma violação da unicidade é tratada como "já desbloqueada", sem erro.
  - O aviso de GAM-06 sai uma única vez.
- GAM-06: ao desbloquear uma conquista, o motor faz três coisas:
  - chama `notificar(user_id, "conquista", ...)`;
  - cria `Activity(kind="conquista")`, que só aparece no feed de quem pode ver o perfil (`pode_ver_perfil`, DIA);
  - emite o evento `conquista` com a raridade, usado pelo XP (PRG).
- GAM-07: as métricas contam só dados visíveis: avaliações ocultas pela moderação (CON-04) e jogos removidos do catálogo não contam. Uma conquista já desbloqueada nunca é retirada quando o usuário desfaz uma ação (apaga a avaliação ou a sessão). O progresso de uma conquista ainda bloqueada é recalculado pelos dados atuais e pode diminuir.
- GAM-08: conquista secreta aparece como "Conquista secreta" (sem nome, descrição nem meta) em todas as telas, inclusive para o dono, até ser desbloqueada por ele. Depois de desbloqueada, aparece normalmente no perfil dele.
- GAM-09: `/u/<username>/conquistas` mostra as conquistas desbloqueadas, da mais recente para a mais antiga, com filtro por categoria e raridade (`?categoria=`, `?raridade=`; valor inválido é ignorado). Só o dono vê as bloqueadas com a barra de progresso. Perfil privado responde 404 para quem não pode vê-lo (DIA). A seção "Conquistas" de `/u/<username>` (CON-02) mostra a estante e as 6 mais recentes, com 1 consulta (PRF-09, como alterado por DIA).
- GAM-10: o dono fixa até 3 conquistas desbloqueadas na "estante", que aparece no topo do perfil. Fixar uma quarta responde com a mensagem "A estante tem no máximo 3 conquistas" e não grava. Fixar uma conquista que o usuário não desbloqueou responde 404.
- GAM-11: `/conquistas` lista o catálogo ativo por categoria. Cada conquista mostra a porcentagem de usuários (excluídos os admins) que a desbloquearam, com uma casa decimal, ou "0%" com o banco vazio, sem erro. O cálculo usa no máximo 3 consultas, qualquer que seja o tamanho do catálogo.
- GAM-12: `/admin/conquistas` lista, cria, edita e desativa conquistas, escolhendo métrica e meta de um formulário validado no servidor (métrica fora do catálogo ou meta < 1 dá mensagem por campo).
  - Uma conquista desativada deixa de ser desbloqueada, mas continua no perfil de quem já a tem, com o selo "aposentada".
  - Cada ação grava `AuditLog` (`conquista.criar`, `conquista.editar`, `conquista.desativar`).
  - Quem não é admin recebe 403 (GLOBAL-05).
- GAM-13: o comando `flask --app app conquistas recalcular [--usuario USERNAME]` reavalia o histórico (de todos ou de um usuário), desbloqueia o que faltar sem duplicar e imprime `usuarios=N desbloqueadas=M`.
  - O recálculo não gera aviso nem atividade para conquistas cuja data de desbloqueio estimada tem mais de 7 dias.
  - As três conquistas da AP1 (`critico`, `colecionador`, `pioneiro`) são recalculadas assim no primeiro deploy.
- GAM-14: conquistas com `por_tempo` (baseadas em horas ou duração de sessão, como "Maratonista") não são avaliadas nem mostradas para usuários que a política de menores protege (`pode(user, "conquista_por_tempo")` falso, PMN). Elas também não entram na porcentagem de GAM-11 para esses usuários.
- GAM-15: testes sem rede, nomeados pelo ID (`test_gam_NN_...`), cobrem:
  - o desbloqueio de ao menos uma conquista de cada categoria;
  - progresso e regressão;
  - idempotência com o evento repetido;
  - avaliação oculta;
  - conquista secreta;
  - o limite da estante;
  - permissões do admin (403) e do perfil privado (404);
  - o recálculo pelo comando;
  - o bloqueio por menoridade;
  - a falha do motor sem quebrar a ação.

## Fora do escopo
Troféus da Steam, PSN ou Xbox; conquistas com regra escrita pelo admin em código ou expressão livre; animação ou pop-up em tempo real; ranking de conquistas (RNK usa XP); conquistas pagas ou itens cosméticos; API de conquistas (entra na API v2, AP2).
