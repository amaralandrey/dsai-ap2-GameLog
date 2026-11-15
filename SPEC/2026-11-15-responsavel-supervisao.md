# Responsável e supervisão parental (2026-11-15)

## O quê e por quê
O ECA Digital (Lei 15.211/2025) exige três coisas:
- as contas de até 16 anos em redes sociais ficam vinculadas à conta de um responsável legal (art. 24);
- o serviço oferece ferramentas de supervisão: ver a atividade e com quem o menor interage, e limitar funções (arts. 16 a 18);
- o design não pode enfraquecer essa supervisão (art. 18, §2º).

A verificação de idade (IDA) deixa a conta de 12 a 15 anos "aguardando responsável" (IDA-04), e a política de menores (PMN) remete os controles a esta spec. Aqui ficam o convite e o aceite do vínculo, que também confirma a idade (adaptador `responsavel`, IDA-07), e o painel `/responsavel`.

Depende de: IDA, PMN, EML (convite por e-mail), NTF, CNS (registro do consentimento).
Respeita: MAP (`VinculoResponsavel` e `ControleParental` anotados) e PMN.

## Critérios de aceitação
- RSP-01: em `/conta/pendente`, o menor informa o e-mail do responsável. O sistema grava um `VinculoResponsavel(menor_id, email_convite, responsavel_id NULL, convidado_em, aceito_em NULL, revogado_em NULL)` e envia um convite com link assinado (`itsdangerous`) válido por 7 dias. O menor manda no máximo 3 convites por dia, e o 4º responde 429 com mensagem. O e-mail do responsável não aparece em nenhuma página do menor depois de enviado.
- RSP-02: o link exige login de uma conta com faixa `adulto` e sem `aguardando_responsavel`. Quem não tem conta é levado ao cadastro e volta ao convite (GLOBAL-02). No aceite, o responsável declara ser o responsável legal e confirma ou corrige a data de nascimento do menor. Isso grava o `VerificacaoIdade` com `metodo="responsavel"` (IDA-06; havendo divergência, vale IDA-08), registra o consentimento do vínculo (CNS) e tira o `aguardando_responsavel` do menor.
- RSP-03: um link expirado, já usado ou com assinatura inválida responde 400 e não cria vínculo. Um adolescente, um vinculado ou o próprio menor tentando aceitar recebe 403. Recusar o convite marca o vínculo como recusado, e a conta do menor continua pendente.
- RSP-04: uma conta de 12 a 15 anos que fica 30 dias sem vínculo aceito é apagada pelo expurgo (RTN), com e-mail e dados removidos. O menor é avisado na página pendente da data limite.
- RSP-05: um menor tem até 2 responsáveis com vínculo ativo, e um responsável pode ter vários menores. O painel `/responsavel` lista os menores vinculados. Qualquer rota do painel sobre um menor que não é do responsável responde 404.
- RSP-06: o painel mostra a atividade do menor, 20 itens por página: sessões do diário, avaliações, comentários, listas, clubes e torneios. Mostra também as métricas dos últimos 7 e 30 dias (horas registradas no diário e número de ações) e os pedidos de seguir pendentes, que o responsável aprova ou recusa (PMN-03).
- RSP-07: o responsável liga e desliga em `ControleParental(menor_id, comentarios, clubes, torneios, perfil_publico, email_resumo)`, que nasce com tudo desligado. Cada mudança grava quem mudou e quando. O menor recebe uma notificação do tipo `responsavel` ("seu responsável mudou ...").
- RSP-08: uma inscrição de menor de 12 a 15 em torneio pendente de aprovação (PMN-11) aparece no painel, e o responsável aprova ou recusa. Aprovar confirma a inscrição; recusar a cancela e avisa o menor.
- RSP-09: o painel lista as contas adultas com quem o menor interage (seguidores aprovados, quem ele segue, quem comentou nas avaliações dele, colegas de clube). O responsável pode remover um seguidor ou bloquear uma conta em nome do menor (BLQ).
- RSP-10: o menor não enfraquece a supervisão (ECA Digital art. 18, §2º). Um POST do menor nas rotas de controle, de aprovação de seguidor ou de desfazer o vínculo responde 403. Só o responsável desfaz o vínculo. Se o menor ainda tiver de 12 a 15 anos e nenhum outro vínculo ativo, a conta volta a `aguardando_responsavel`.
- RSP-11: quando o menor faz 16 anos, os controles deixam de ser impostos e passa a valer a política de 16–17 (PMN). O vínculo fica registrado como encerrado por idade. O responsável e o menor são notificados.
- RSP-12: os testes rodam sem rede, com o e-mail na caixa de saída (EML), e cobrem: convite e limite diário, aceite por adulto, recusa a não adulto, link expirado e adulterado, divergência de nascimento, painel isolado entre responsáveis (404), controles que bloqueiam comentar e entrar em clube, aprovação de torneio e de seguidor, menor tentando mudar controle (403), revogação, expurgo aos 30 dias e virada dos 16 anos. Todos nomeados `test_rsp_NN_*`.

## Fora do escopo
Prova documental de que o adulto é responsável legal (é declaração, com a arquitetura pronta para um provedor), limites de tempo de tela, controle de compras (o GameLog não vende), localização do menor e vínculo para menores de 12 anos.
