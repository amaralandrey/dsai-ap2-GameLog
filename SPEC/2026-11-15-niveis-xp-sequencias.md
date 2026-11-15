# Níveis, XP e sequências semanais (2026-11-15)

## O quê e por quê
Fora as três conquistas da AP1, nada no GameLog mostra a evolução do jogador ao longo do tempo. Esta spec acrescenta pontos de experiência (XP), níveis numa curva documentada, um extrato auditável de cada lançamento e uma sequência **semanal**: basta registrar algo na semana, em vez de todo dia. A sequência é semanal de propósito. Uma sequência diária premia o tempo de uso e estimula o uso compulsivo, que o ECA Digital manda evitar (Lei 15.211/2025, art. 8, IV; Decreto 12.880/2026, art. 9, §1). Pelo mesmo motivo não há multiplicador de XP por sequência. O XP sai dos eventos do motor de conquistas (`emitir_evento`, GAM-03) e é a base do ranking de XP (RNK).

Depende de: GAM (eventos e raridades), DIA (sessões e `Status.zerado_em`), NTF (avisos de nível e de sequência), JOB (aviso semanal). Respeita: MAP (campos novos anotados) e PMN (sem aviso de sequência para menores).

## Critérios de aceitação
- PRG-01: o modelo `PerfilProgressao` é único por usuário e guarda `xp_total`, `nivel`, `sequencia_semanas`, `maior_sequencia`, `ultima_semana_ativa` (semana ISO no formato `AAAA-Www`) e `congelamentos` (0 a 2).
  - É criado na primeira vez que o usuário ganha XP. Antes disso, o perfil mostra nível 1 com 0 XP.
  - O modelo `ExtratoXP` registra cada lançamento: usuário, `origem_tipo`, `origem_id`, `xp` (pode ser negativo), `motivo` e `created_at`.
- PRG-02: o XP vem de uma tabela fixa em `progressao.py`:
  - avaliação sem resenha: +10;
  - avaliação com resenha de 140 caracteres ou mais: +25;
  - sessão do diário registrada à mão (origem `manual`): +10;
  - zerar um jogo (`Status.zerado_em` preenchido): +50;
  - conquista desbloqueada: +20, +50, +100 ou +200, conforme a raridade (comum, rara, épica, lendária).
  - Sessões importadas (origem `steam` ou `csv`, HRS), seguir, curtir e criar lista não dão XP.
- PRG-03: o lançamento é idempotente por (usuário, `origem_tipo`, `origem_id`). Editar a avaliação ou a sessão não gera XP de novo. Desmarcar e marcar de novo o mesmo jogo como zerado também não. Apagar uma avaliação ou sessão que deu XP cria um lançamento negativo de estorno com o mesmo valor. Assim, apagar e recriar não acumula XP.
- PRG-04: o nível é o maior `n` tal que 50·n·(n−1) ≤ `xp_total`. Exemplos: nível 1 com 0 XP, nível 2 com 100, nível 3 com 300, nível 10 com 4.500. O progresso exibido é a porcentagem entre o XP do nível atual e o do próximo.
  - Um lançamento que atravessa vários níveis atualiza o nível direto para o final.
  - Emite um único evento `nivel` e uma única notificação (`notificar(..., "nivel")`) com o nível alcançado.
  - Estorno nunca baixa o nível já alcançado. O `xp_total` cai e a barra mostra 0%.
- PRG-05: as ações repetíveis (avaliação e sessão) somam no máximo 150 XP por dia, por usuário, contando o dia no fuso `America/Belem`. Passando do teto, o lançamento é gravado com `xp` igual a 0 e motivo "teto diário". Zerar um jogo e desbloquear conquistas não entram no teto.
- PRG-06: a sequência conta semanas ISO consecutivas (segunda a domingo, `America/Belem`) com pelo menos uma ação válida: uma sessão manual no diário ou uma avaliação. Na primeira ação válida de uma semana:
  - se a última semana ativa foi a anterior, `sequencia_semanas` soma 1;
  - se foi a mesma semana, nada muda;
  - `maior_sequencia` acompanha o maior valor já alcançado.
- PRG-07: quando faltam semanas entre a última semana ativa e a atual, cada semana vazia consome 1 congelamento. Se houver congelamentos para todas, a sequência continua e soma 1. Senão, os congelamentos disponíveis são consumidos e a sequência recomeça em 1. A página mostra a sequência como 0 enquanto as semanas vazias já passadas não puderem ser cobertas.
- PRG-08: a cada 4 semanas seguidas de sequência, o usuário ganha 1 congelamento, até o máximo de 2. O ganho e o consumo de congelamentos aparecem no extrato com `xp` igual a 0.
- PRG-09: `/u/<username>/xp` mostra ao dono o nível, a barra de progresso, a sequência, os congelamentos e o extrato paginado (20 por página, do mais recente para o mais antigo).
  - Para outro usuário, a rota responde 404: o extrato é pessoal.
  - O perfil `/u/<username>` mostra o nível e a sequência a quem pode ver o perfil (`pode_ver_perfil`, DIA), com 1 consulta (PRF-09, como alterado por DIA).
- PRG-10: o cabeçalho de quem está logado mostra o nível e a barra de XP, com no máximo 1 consulta a mais por página.
  - Nas noites de domingo, o job semanal (`@tarefa("aviso_sequencia")`, JOB) avisa pela NTF (tipo `sequencia`), no máximo uma vez por semana, quem tem sequência de 2 semanas ou mais e ainda não agiu na semana.
  - O aviso não é enviado quando `pode(user, "aviso_engajamento")` é falso (PMN) nem quando o usuário desligou esse tipo de notificação (NTF).
- PRG-11: o admin aplica um ajuste manual de XP (positivo ou negativo, diferente de zero) a um usuário, com justificativa obrigatória de até 200 caracteres.
  - O ajuste entra no extrato com `origem_tipo="ajuste"` e grava `AuditLog` (`xp.ajustar`).
  - Formulário inválido responde com mensagem por campo (GLOBAL-03).
  - Quem não é admin recebe 403.
- PRG-12: testes sem rede, nomeados pelo ID (`test_prg_NN_...`), com o relógio fixado, cobrem:
  - cada linha da tabela de XP;
  - idempotência e estorno;
  - subida de vários níveis num lançamento;
  - estorno sem perder nível;
  - teto diário na virada do dia em `America/Belem`;
  - virada de semana ISO e virada de ano;
  - consumo e ganho de congelamentos;
  - extrato privado (404);
  - aviso semanal com e sem menor protegido;
  - ajuste do admin com auditoria.

## Fora do escopo
Multiplicadores de XP, sequência diária, loja de itens, moedas virtuais ou qualquer conversão de XP em dinheiro, XP retroativo para o histórico anterior ao deploy (a conquista retroativa vem pelo GAM, sem XP), XP por clube ou torneio (entra nas specs deles se for preciso).
