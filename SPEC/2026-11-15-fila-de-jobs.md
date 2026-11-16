# Fila persistente de jobs (2026-11-15)

## O quê e por quê
Hoje todo trabalho do GameLog roda dentro da requisição ou é disparado à mão na linha de comando (`sync-games`, `enriquecer-snapshot`). O `loop/PROMPT.md` da AP1 já registrou a regra: nada de agendador em thread ou processo dentro do Flask, porque o deploy usa 2 workers do gunicorn e o job rodaria duas vezes. Preços, e-mail, resumo semanal, expurgo de dados e backup precisam rodar fora da requisição, com novas tentativas e histórico. Esta spec cria uma fila própria sobre o SQLite, sem framework novo (sem Celery, RQ ou Redis). As tarefas ficam gravadas no banco e são executadas por `flask jobs rodar`, chamado por um agendador externo (cron do host ou cron job do Render).

Altera: o README e o `loop/PROMPT.md` da AP1 citavam "cron job do Render" para o `sync-games`; o agendamento passa a ser o desta spec. O comando `sync-games` e o `enriquecer-snapshot` continuam existindo e passam também a ter tarefas registradas (`sync_games` e `enriquecer_snapshot`), que reaproveitam a mesma função de serviço. Respeita: MAP (os argumentos dos jobs não guardam dados pessoais além de ids, e o histórico tem retenção no mapa de dados).

## Critérios de aceitação
- JOB-01: o modelo `Job` grava:
  - `tipo` e `args` (JSON);
  - `status`: `pendente`, `rodando`, `ok`, `falhou` ou `morto`;
  - `tentativas` e `max_tentativas` (padrão 5);
  - `executar_em`;
  - `travado_por` e `travado_em`;
  - `ultimo_erro` (até 2000 caracteres);
  - `chave_unica` (opcional e única entre os jobs `pendente` e `rodando`);
  - `criado_em` e `concluido_em`.

  `enfileirar(tipo, executar_em=None, chave_unica=None, **args)` cria o job pendente e o devolve. Um tipo não registrado levanta `ValueError`. `args` que não vira JSON também levanta `ValueError`.
- JOB-02: se já existir um job `pendente` ou `rodando` com a mesma `chave_unica`, `enfileirar` devolve o job existente e não cria outro. Exemplo: um único `email_resumo:<user_id>:<semana>`.
- JOB-03: tarefas se registram com o decorador `@tarefa("nome")` sobre uma função que recebe os `args` como parâmetros nomeados. O registro é um dicionário único em `jobs.py`. Registrar duas vezes o mesmo nome levanta erro na criação do app.
- JOB-04: `flask --app app jobs rodar --uma-vez` pega até `--limite` (padrão 50) jobs `pendente` com `executar_em` já vencido, do mais antigo para o mais novo, executa cada um e imprime `executados=N ok=M falhas=F mortos=K`. Sem `--uma-vez`, o comando repete o ciclo a cada `--intervalo` segundos (padrão 30) até receber SIGINT ou SIGTERM, terminando o job em andamento antes de sair.
- JOB-05: a trava contra execução dupla é um `UPDATE ... WHERE id = ? AND status = 'pendente'` que muda para `rodando` com `travado_por` (host:pid) e `travado_em`. Só quem altera a linha executa o job. Dois executores simultâneos sobre a mesma fila nunca executam o mesmo job duas vezes, o que um teste confere com duas conexões. Um job `rodando` há mais de 15 minutos é considerado abandonado e volta para `pendente`, contando uma tentativa.
- JOB-06: uma tarefa que levanta exceção é desfeita (rollback do que ela gravou), incrementa `tentativas`, grava `ultimo_erro` (tipo e mensagem, sem traceback com valores de variáveis nem segredos) e volta a `pendente`. A espera até a próxima tentativa cresce: `executar_em = agora + 30 s × 2^(tentativas−1)`, com no máximo 6 horas. Esgotadas as `max_tentativas`, o job vira `morto`. Uma tarefa pode levantar `ErroPermanente`, que leva direto a `morto` sem novas tentativas.
- JOB-07: o modelo `Agendamento` tem `nome` único, `cron` (expressão de 5 campos), `tipo`, `args`, `ativo` e `ultima_execucao`. O cron aceita `*`, números, listas (`1,15`), intervalos (`1-5`) e passos (`*/15`), no fuso America/Belem. Uma expressão inválida é recusada na gravação com a mensagem do campo. A função `proxima_execucao(cron, depois_de)` é pura e testada com casos de virada de hora, dia, mês e ano.
- JOB-08: no início de cada ciclo, `jobs rodar` enfileira cada agendamento ativo cuja próxima execução, contada da `ultima_execucao`, já venceu. Usa `chave_unica = "agenda:<nome>:<instante previsto>"`, de modo que vários executores ou um ciclo repetido não dupliquem a execução. Execuções perdidas enquanto nada rodava geram um único job, sem recuperar uma por uma.
- JOB-09: `flask --app app jobs agendar` cria ou atualiza os agendamentos padrão de forma idempotente e imprime `criados=N atualizados=M`:
  - `sync_games` (diário, 04:00);
  - `enriquecer_snapshot` (semanal, desligado por padrão);
  - os das specs que registrarem tarefas (preços, resumo semanal, expurgo, backup), cada uma declarando o seu.

  Uma tarefa que precisa de chave ausente (RAWG) termina com `ErroPermanente` e uma mensagem clara.
- JOB-10: `/admin/jobs` (só admin, GLOBAL-05) lista os jobs com filtro por status e tipo, 20 por página, do mais recente para o mais antigo, com tentativas, próximo horário e último erro. O admin pode "Tentar de novo" um job `morto` ou `falhou` (volta a `pendente`, com `tentativas = 0`), "Cancelar" um `pendente` e ligar ou desligar um agendamento. Cada ação grava no `AuditLog` (`jobs.retentar`, `jobs.cancelar` e `jobs.agendamento`).
- JOB-11: o processo web não tem agendador nem thread: um teste confere que criar o app não inicia nenhum dos dois. No plano gratuito do Render, um serviço `cron` não enxerga o SQLite do serviço web (discos separados), então o ciclo é disparado de fora:
  - `POST /jobs/ciclo` com `Authorization: Bearer <JOBS_TOKEN>` roda um ciclo igual a `jobs rodar --uma-vez --limite 10` e responde JSON com `executados`, `ok`, `falhas` e `mortos`; a rota não usa cookie e fica fora da checagem de CSRF de formulário (`csrf_protect`, em `__init__.py`);
  - o token é comparado em tempo constante (`hmac.compare_digest`); token errado responde 401; sem `JOBS_TOKEN` configurado, a rota responde 404;
  - a trava de JOB-05 impede que os 2 workers do gunicorn executem o mesmo job;
  - quem chama a rota a cada 15 minutos é um serviço externo de cron HTTP (por exemplo, cron-job.org), configurado fora do repositório com o `JOBS_TOKEN` (declarado no `render.yaml` com `sync: false`); o repositório não ganha workflow agendado, para que as chamadas que dependem do Render acordar não entrem no histórico de builds do CI; em host próprio, o cron do host chama `flask jobs rodar`.

  O README explica os dois modos.
- JOB-12: os testes rodam sem rede e com relógio injetável, e cobrem:
  - enfileirar com tipo desconhecido e com `chave_unica` repetida;
  - execução com sucesso, com falha e espera crescente (30 s, 60 s, 120 s…), com o teto de 6 h e virando `morto`, e com `ErroPermanente`;
  - rollback do que a tarefa gravou;
  - trava com dois executores;
  - job abandonado;
  - parser de cron (válidos, inválidos e `proxima_execucao` nas viradas);
  - agendamento sem duplicar;
  - `jobs agendar` idempotente;
  - rotas admin (403 e ações com auditoria);
  - ausência de agendador no processo web e a rota `/jobs/ciclo` (token certo, errado e ausente).

## Fora do escopo
Celery, RQ, Redis ou qualquer broker externo; prioridades e filas nomeadas; paralelismo dentro de um executor (um job por vez); jobs encadeados ou com dependências; painel em tempo real; métricas e alertas de fila; garantia de execução exatamente uma vez (a garantia é de no máximo uma execução simultânea, e as tarefas devem ser idempotentes).
