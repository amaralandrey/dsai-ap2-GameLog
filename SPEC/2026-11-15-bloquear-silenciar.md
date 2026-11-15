# Bloquear e silenciar (2026-11-15)

## O quê e por quê
A spec social deixou o bloqueio de usuários fora do escopo, e hoje a única defesa contra alguém inconveniente é denunciar avaliação por avaliação (AVA-08). Com comentários, notificações, clubes e torneios, o contato entre desconhecidos cresce. O ECA Digital (Lei 15.211/2025) exige ferramentas para limitar a interação de adolescentes com outras pessoas (arts. 16 a 18), e a política de menores depende delas.

Esta spec cria duas ferramentas:
- **Bloquear** corta o contato nos dois sentidos: nenhum dos dois vê o conteúdo do outro, segue o outro ou interage com ele.
- **Silenciar** só tira a pessoa do próprio feed e das próprias notificações, sem que ela saiba.

Depende de: NTF (o bloqueio e o silêncio entram como filtro registrado em `notificacoes.registrar_filtro`, NTF-03). Altera: SOC-01 (não é possível seguir quem bloqueou você ou foi bloqueado por você), SOC-04 (o feed exclui silenciados e bloqueados), COM-01 e COM-02 (comentário de bloqueado não aparece nem pode ser criado). Respeita: MAP (`Bloqueio` e `Silencio` entram no mapa) e PMN (o responsável pode bloquear pelo adolescente vinculado, RSP).

## Critérios de aceitação
- BLQ-01: `POST /u/<username>/bloquear` e `POST /u/<username>/desbloquear` exigem login (GLOBAL-02). Ninguém bloqueia a si mesmo. Bloquear de novo não cria duplicata (`Bloqueio` único por par ordenado `bloqueador_id`, `bloqueado_id`). A função `bloqueado_entre(a, b)` responde verdadeiro se qualquer um dos dois bloqueou o outro.
- BLQ-02: bloquear desfaz os `Follow` nos dois sentidos, e desbloquear não os recria. Enquanto o bloqueio existir, `POST /u/<username>/seguir` entre os dois não cria `Follow` e mostra a mesma mensagem genérica "Não foi possível seguir este usuário", sem revelar quem bloqueou quem.
- BLQ-03: para quem está logado e tem bloqueio com o dono, `/u/<username>` e as páginas derivadas (diário, retrospectiva, listas, seguidores) respondem 404, como um perfil inexistente. Um visitante sem login continua vendo o perfil se ele for público.
- BLQ-04: com bloqueio entre os dois, as avaliações e os comentários de um somem para o outro na página do jogo, na linha do tempo do perfil e no feed. A nota média e o histograma do jogo não mudam: o bloqueio só esconde, não altera dados (PAG).
- BLQ-05: com bloqueio entre os dois, curtir ou comentar conteúdo do outro responde 404. Bloquear nunca impede denunciar: a denúncia de risco a menores (`/denunciar-risco-a-menor`, DEN-03) continua aceita contra quem está bloqueado. Além disso, `notificar()` descarta qualquer notificação de um para o outro (NTF).
- BLQ-06: o bloqueio também vale em clubes e torneios: dois usuários com bloqueio não se veem no mural do clube, e uma inscrição em torneio não coloca os dois na mesma partida enquanto houver outro adversário possível (TRN). Nos rankings, quem tem bloqueio com quem consulta não aparece para ele (RNK-05); o ranking dos outros não muda.
- BLQ-07: `POST /u/<username>/silenciar` e `.../dessilenciar` exigem login. Silenciar não desfaz o `Follow`, e o silenciado não é avisado de nada e continua vendo tudo. As atividades do silenciado saem do feed de quem silenciou (SOC-04), e as notificações dele para quem silenciou são descartadas.
- BLQ-08: `/conta/bloqueios` lista, para o dono, os usuários bloqueados e os silenciados, paginados (GLOBAL-04), com botão para desfazer. Um bloqueio feito pelo responsável em nome do adolescente vinculado (RSP-09) grava `Bloqueio.criado_por` com o responsável e aparece na lista do adolescente sem o botão; o POST de desbloquear dele responde 403, e só o responsável desfaz (ECA Digital art. 18, §2º). Nenhuma tela mostra a alguém que ele foi bloqueado ou silenciado.
- BLQ-09: o feed com silenciados e bloqueados continua com o mesmo orçamento de consultas de antes (o filtro entra na consulta do feed, junto do filtro de pedidos de seguir pendentes de PMN-03, sem uma consulta por atividade), conferido no teste com no máximo 2 consultas a mais que a versão atual.
- BLQ-10: testes sem rede para: bloquear a si mesmo, duplicata, follows desfeitos, seguir bloqueado com mensagem genérica, perfil 404 para o bloqueado e visível para visitante, conteúdo escondido nos dois sentidos com nota média inalterada, curtir e comentar com 404, denúncia de risco aceita contra bloqueado, bloqueio do responsável que o adolescente não desfaz, notificação descartada, silenciar sem desfazer o follow, feed sem o silenciado, lista de bloqueios e orçamento de consultas do feed.

## Fora do escopo
Bloqueio por IP ou por e-mail, bloqueio em massa, palavras silenciadas, banimento pelo admin (continua fora, como em ADM), bloqueio de usuários remotos de federação (fora do plano).
