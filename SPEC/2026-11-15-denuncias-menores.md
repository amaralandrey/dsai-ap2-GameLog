# Denúncia de risco a menores (2026-11-15)

## O quê e por quê
Hoje só se denuncia avaliação: `Report` tem `review_id` obrigatório e os motivos spam, ofensivo, spoiler e outro (AVA-08). A fila do admin é uma só, da mais antiga para a mais nova (ADM-05). Não há como denunciar um perfil ou um comentário, nem como sinalizar risco a uma criança ou adolescente, e a decisão não tem fundamentação, recurso ou trilha.

O ECA Digital (Lei 15.211/2025) pede três coisas:
- o provedor remove conteúdo que viole direitos de crianças e adolescentes após notificação da vítima, de um representante, do Ministério Público ou de uma entidade, sem ordem judicial, e não aceita notificação anônima (art. 29);
- a decisão é fundamentada e tem recurso (art. 30);
- casos de exploração ou abuso sexual, sequestro e aliciamento são removidos e comunicados às autoridades, com os dados preservados (art. 27).

O Decreto 12.880/2026 põe a Polícia Federal como autoridade central e não fixa prazo em horas. O prazo abaixo é uma meta da dupla.

Depende de: PMN, IDA (contestação de idade), NTF, CLB (post de clube, quando existir).
Altera: AVA-08 (denúncia também de comentário, perfil e post de clube, com a categoria de risco a menores, e o próprio conteúdo não pode ser denunciado); AVA-04, COM-03 e COM-06 (conteúdo preservado não é apagado, DEN-09); ADM-05 (a fila separa e prioriza o risco a menores); ADM-06 (decisão com fundamentação); o "Fora do escopo" de painel-admin (passa a existir a suspensão de conta, com `User.suspensa_em` de IDA-05); CLB-11 (a denúncia de post de clube é um `Report` desta spec, não uma tabela própria).
Respeita: MAP (campos novos do `Report`, `EventoDenuncia` e `ComunicacaoAutoridade` anotados, com retenção ligada à preservação) e PMN.

## Critérios de aceitação
- DEN-01: `Report` passa a ter `alvo_tipo` (`avaliacao`, `comentario`, `perfil`, `post_clube`) e `alvo_id`. O `review_id` das denúncias antigas continua válido, e elas são lidas como `alvo_tipo="avaliacao"`. Existe um botão "Denunciar" em comentários, perfis e posts de clube. Para os motivos antigos vale a regra de AVA-08 (uma denúncia por usuário e alvo, recusada com a mensagem de hoje). Regra nova: denunciar o próprio conteúdo é recusado com mensagem e não grava nada. Os erros seguem o padrão atual de `avaliacoes.report` (mensagem e volta à página), não um 400.
- DEN-02: há uma nova categoria, `risco_menor`, com as subcategorias `aliciamento`, `exploracao_sexual`, `conteudo_sexual_menor`, `contato_inadequado`, `usuario_menor_de_12` e `outro_risco`, e comentário obrigatório (10 a 1000 caracteres). Subcategoria inválida é recusada com mensagem, sem gravar.
- DEN-03: a página pública `/denunciar-risco-a-menor` recebe notificação de quem não tem conta, desde que identificado (ECA Digital art. 29). Exige nome, e-mail, qualidade (`vitima`, `responsavel`, `ministerio_publico`, `entidade`), o link do conteúdo e a descrição. Sem identificação responde 400. A página não aceita notificação anônima e tem o mesmo CSRF do resto do site.
- DEN-04: `/admin/denuncias` mostra primeiro a aba "Risco a menores", da mais antiga para a mais nova, com contador próprio no painel. Cada denúncia mostra o prazo de resposta (`PRAZO_DENUNCIA_MENOR_HORAS`, padrão 24) e destaca as atrasadas. As outras denúncias seguem como antes (ADM-05).
- DEN-05: denúncias `exploracao_sexual` e `conteudo_sexual_menor` ocultam o conteúdo na hora, de forma cautelar, até a decisão. O autor vê o aviso de ocultação (como em AVA-09). As outras subcategorias só entram na fila.
- DEN-06: toda decisão exige fundamentação de pelo menos 20 caracteres (ECA Digital art. 30). As decisões possíveis são: ocultar o conteúdo, suspender a conta (`User.suspensa_em`, IDA-05; o conteúdo some como no AVA-09), restaurar o conteúdo ocultado na hora ou rejeitar a denúncia. Decisão sem fundamentação responde 400.
- DEN-07: o autor do conteúdo é notificado (NTF tipo `sistema`) com a decisão e a fundamentação e pode recorrer uma única vez em até 15 dias. O recurso volta para a fila marcado como recurso e precisa de uma nova decisão fundamentada, por outro administrador quando houver mais de um.
- DEN-08: nas subcategorias `aliciamento`, `exploracao_sexual` e `conteudo_sexual_menor`, o admin registra a comunicação às autoridades (`ComunicacaoAutoridade`: órgão, protocolo, data e quem registrou). O GameLog não envia nada sozinho. Enquanto não houver registro, a denúncia aparece como "comunicação pendente".
- DEN-09: o conteúdo e a conta envolvidos em denúncia dessas subcategorias ficam com `preservar_ate` (padrão de 180 dias depois da decisão, `PRESERVACAO_DIAS`). Até essa data, apagar pelo autor (AVA-04, COM-03) só oculta o conteúdo e mantém a denúncia; a exclusão de conta (DIR-06) anonimiza o perfil público e bloqueia o login, mas guarda o e-mail e o conteúdo envolvidos, visíveis só ao admin na denúncia; e o expurgo (RTN) não toca nessas linhas. Passada a data, a anonimização e o expurgo normais seguem.
- DEN-10: cada passo grava um `EventoDenuncia` (criada, ocultada_cautelar, decidida, recurso, comunicada, preservação), só de inserção, além do `AuditLog`. `/admin/denuncias/<id>` mostra a trilha completa. Nenhuma rota edita ou apaga eventos.
- DEN-11: uma denúncia `usuario_menor_de_12` decidida como procedente marca a idade como contestada (IDA-11). Quem denunciou recebe uma notificação de que a denúncia foi analisada, sem detalhes da decisão sobre outra pessoa.
- DEN-12: os testes rodam sem rede e cobrem: denúncia de cada tipo de alvo, denúncias antigas lidas como avaliação, subcategoria inválida, notificação pública identificada e anônima recusada, ordem e prazo na fila, ocultação cautelar, decisão sem fundamentação, recurso único, comunicação pendente e registrada, preservação que impede apagar e trilha imutável. Todos nomeados `test_den_NN_*`.

## Fora do escopo
Envio automático à Polícia Federal ou a outro canal oficial, detecção automática de conteúdo (hash de imagens, IA), relatório semestral de transparência (o ECA Digital, art. 31, só exige acima de 1 milhão de usuários), upload de provas e moderação por usuários que não são administradores.
