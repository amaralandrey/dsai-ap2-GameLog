# Sessão Claude Code — Specs dos épicos 3 a 10 (2026-11-15)

**Ferramenta:** Claude Code CLI (Claude Opus 5.5, `claude-opus-5-5`), com subagentes em paralelo
**Quem conduziu:** Andrey Amaral
**Horário:** 19:52–20:35 (horário de Belém); o processo do Claude Code reiniciou às 20:04 e a sessão continuou
**Plano:** `.claude/plan/plan-ap2.md` como está na `main` depois do PR #19 (épicos 3 a 10, SPEC-04..34)

## Custo

Medido nos logs da sessão do Claude Code: a sessão principal, a continuação depois do reinício e os 18 subagentes (13 da redação e 5 da auditoria de contradições). Cada resposta do modelo conta uma vez só, com o maior `usage` registrado para o mesmo `message.id`, porque o reinício copiou parte do histórico. Preços de API do Claude Opus 5.5.

| Tipo de token | Tokens | Preço (US$/1M) | Custo (US$) |
|---|---:|---:|---:|
| Entrada | 492 | 4,00 | 0,00 |
| Saída | 220.454 | 20,00 | 4,41 |
| Leitura de cache | 41.246.125 | 0,20 | 8,25 |
| Escrita de cache (TTL 1h) | 845.047 | 8,00 | 6,76 |
| **Total** | **42.312.118** | | **≈ 19,42** |

O total soma 236 requisições ao modelo: 79 da conversa principal e 157 dos subagentes. A primeira parte (redação, PR #20) custou ≈ US$ 12,69; a auditoria de contradições, ≈ US$ 6,73. A conta foi feita antes desta atualização, então ficam de fora as últimas respostas (este registro e o push).

## Prompts (em ordem)

1. vamos redigir todas as specs, conforme o plan atualizado - reescrita futura não é um impedimento, reforce os requisitos do projeto não apenas na estrutura da spec, mas com os requisitos gerais do projeto e da avaliação do professor. essa sessão será registrada no diretorio adequado e deve gerar os artefatos pertinentes.
2. (rodada de decisões, primeira tentativa) o reforço mencionado é sobre o contexto, não sobre reforçar literalmente em texto na spec - uma versão mais clara dos requisitos estão na pasta trabalho gustavo em downloads
3. (rodada de decisões, respostas) datas: todas 2026-11-15; idade: 12+ com responsável até 16; MCP: stdlib, JSON-RPC à mão; e-mail: SMTP por variáveis de ambiente e console sem configuração.
4. antes do merge, faça um contraste geral do que ja temos e o que sera implementando pelas novas specs, resolvendo as contradições.

## Como foi feito

1. **Contexto.** Li o plano da `main` (PR #19), as specs do Épico 2, os modelos e as rotas atuais, os rascunhos antigos do Épico 4 (`~/Downloads`) e as regras da AP2 (`REGRAS_AP2.md`, na pasta "trabalho gustavo").
   - As regras ficaram como contexto da redação, sem ser copiadas nas specs. Por isso cada critério é verificável sem rede, para que o teste falhe quando a regra for revertida ("testes que testam").
   - O escopo de cada spec cabe em commits pequenos, separados por camada.
   - Os módulos comuns são reaproveitados (`serializadores`, `graficos_svg`, `notificar`, `emitir_evento`, `enfileirar`, `pode`, mapa de dados), o que mantém a duplicação do jscpd baixa.
   - Nada reescreve código existente: mudanças em critérios antigos aparecem como "Altera:".
2. **Pesquisa legal.** Dois subagentes conferiram o texto em vigor da legislação.
   - Lei e resoluções:
     - LGPD;
     - Res. CD/ANPD 2/2022, 15/2024, 18/2024, 19/2024 e 32/2026;
     - Lei 15.352/2026;
     - GDPR;
     - ECA Digital (Lei 15.211/2025, em vigor desde 17/03/2026) e Decreto 12.880/2026;
     - Portaria MJSP 1.048/2025 (ClassInd).
   - Pontos não confirmados:
     - não existe tabela oficial ESRB→ClassInd, então a das specs é declarada como equivalência da dupla;
     - a ANPD ainda não publicou resolução sobre direitos do titular;
     - não há prazo em horas para responder a denúncias de risco a menores, então o prazo das specs é configurável.
3. **Contrato comum.** Antes de redigir, fixei os nomes compartilhados entre as specs: modelos, funções, rotas e siglas. Seis subagentes escreveram as specs em paralelo, um por grupo de épicos.
   - O processo do Claude Code reiniciou no meio. Um subagente foi dado como falho, mas tinha gravado os arquivos completos.
   - Os subagentes relançados foram parados antes de gravar, quando ficou claro que os originais tinham terminado.
4. **Revisão.** Conferi a contagem de critérios contra o plano (todas exatas), li as specs e corrigi as inconsistências entre elas (abaixo).

## Artefatos

- **31 specs novas em `SPEC/`, todas datadas de 2026-11-15**, com um commit por spec, na ordem de dependência.

  | Épico | Specs (siglas) |
  |---|---|
  | 3 | diario-de-jogatina (DIA-01..14), importacao-horas-diario (HRS-01..12), retrospectiva-graficos (RET-01..12), listas-inteligentes (LSA-01..12) |
  | 4 | notificacoes (NTF-01..12), motor-conquistas (GAM-01..15), niveis-xp-sequencias (PRG-01..12), rankings-temporadas (RNK-01..12), clubes (CLB-01..14) |
  | 5 | api-v2-tokens (AP2-01..14), servidor-mcp (MCP-01..12), backup-restore (BKP-01..12) |
  | 6 | fila-de-jobs (JOB-01..12), email (EML-01..12) |
  | 7 | precos-por-loja (PRC-01..14), ofertas-alertas (OFR-01..12) |
  | 8 | mapa-de-dados (MAP-01..10), transparencia (PRV-01..12), consentimentos (CNS-01..12), direitos-do-titular (DIR-01..14), retencao-minimizacao (RTN-01..12), incidentes-seguranca (INC-01..12), seguranca-da-conta (CTA-01..12), bloquear-silenciar (BLQ-01..10) |
  | 9 | chaveamento (CHV-01..14), torneios (TRN-01..14) |
  | 10 | verificacao-de-idade (IDA-01..12), politica-de-menores (PMN-01..14), responsavel-supervisao (RSP-01..12), classificacao-indicativa (CLS-01..10), denuncias-menores (DEN-01..12) |

  São 385 critérios no total.
- **`.claude/plan/plan-ap2.md`:** as siglas HRS e RTN e o arquivo de cada spec nas tabelas dos épicos.
- **Branch `docs/specs-epicos-3-10`:** 32 commits, todos com `Agent:` e `Spec:`. Cada spec foi commitada sozinha, então o hook `commit-msg` aceitou todos.

## Decisões

- **Datas:** todas as specs são de 2026-11-15, o dia real em que foram escritas e commitadas. A regra só exige que a spec seja commitada antes do código.
- **Siglas:** IMP já pertence à spec importacao-steam, então a importação de horas vira HRS. RET ficava repetida, então a retenção vira RTN e RET fica com a retrospectiva.
- **Idade:** o cadastro aceita a partir de 12 anos. Contas de 12 a 15 só são ativadas com o vínculo de um responsável (ECA Digital, art. 24); de 16 a 17 vale a política de menores, sem vínculo obrigatório; abaixo de 12 o cadastro é recusado. Sem data de nascimento, a conta cai na faixa mais protetiva.
  - A lei diz contas "de até 16 anos". A dupla lê isso como menores de 16, o que precisa ser revisto se a ANPD orientar de outro jeito.
- **MCP:** JSON-RPC 2.0 sobre stdio, escrito só com a biblioteca padrão, sem SDK.
- **E-mail:** `smtplib` com as variáveis `SMTP_*`. Sem `SMTP_HOST`, a mensagem vai para o console e fica na caixa de saída.
- **Sem dependência nova:** nenhuma spec acrescenta uma. O TOTP segue a RFC 6238 com `hmac` da stdlib, e os links assinados usam `itsdangerous`, que já vem com o Flask.
- **Sequência semanal sem multiplicador de XP:** é uma escolha para evitar uso compulsivo (ECA Digital, art. 8, IV; Decreto 12.880/2026, art. 9, §1). Menores não recebem o aviso de sequência prestes a quebrar nem conquistas por tempo de jogo.
- **Jobs em produção (JOB-11, corrigido na revisão):** no plano gratuito do Render, um serviço `cron` não enxerga o SQLite do serviço web. Por isso um cron HTTP externo (por exemplo, cron-job.org) chama `POST /jobs/ciclo`, protegida pelo token `JOBS_TOKEN`, a cada 15 minutos; a primeira versão usava um workflow agendado do GitHub Actions, trocado na auditoria para não encher o histórico de builds com execuções que dependem do Render acordar, e a trava do JOB-05 impede execução dupla entre os 2 workers do gunicorn. O processo web continua sem agendador nem thread.
- **Backup (BKP-11, corrigido na revisão):** `BACKUP_RESTAURAR_DE` aceita uma URL `https`, porque o disco do plano gratuito some a cada deploy.
- **Recomendações (corrigido na revisão):** saíram dos consentimentos (CNS) e ficam na base de execução do contrato, com a oposição prevista em DIR-09. Assim REC e COL e os testes deles não mudam.
- **Ranking de horas (corrigido na revisão):** conta só sessões `manual` e `csv`, porque a sessão de saldo da Steam traria anos de horas de uma vez. Para isso, `minutos_registrados` (DIA-08) ganhou o filtro `origens`.
- **Nomes alinhados entre specs:**
  - `Conquista.por_tempo` (GAM) no lugar de `recompensa_tempo` (PMN);
  - o status `suprimido` do EML no lugar de `cancelado_sem_consentimento` (CNS);
  - as ações de `pode()` listadas em PMN-01;
  - a rota do jogo é `/jogos/<slug>`, como no código.
- **Preço da GOG (PRC-03):** o endpoint `https://api.gog.com/products/<id>/prices?countryCode=BR` foi conferido nesta sessão. Ele devolve uma entrada por moeda em `_embedded.prices`, com valores como `"3299 BRL"`, e `PRICES_NOT_FOUND` para produto sem venda no Brasil.

## Pontos de atenção

- **Fabrício:** o plano divide as specs entre a dupla, mas o Andrey vai implementar sozinho nesta semana. Os PRs #12, #13, #14 e #16 do Épico 2 continuam abertos esperando a aprovação do Fabrício.
- **ECA Digital:** as specs do Épico 10 citam artigos do decreto lidos por resumo automático. Vale conferir a redação exata dos arts. 24 e 39 da lei e do art. 27 do decreto antes de implementar.
- **Specs como contrato:** reescrever uma spec antes do código é permitido (o pedido não vê isso como impedimento). Uma mudança depois do primeiro commit de código entra como novo critério ou como "Altera:" em outra spec datada.

## Auditoria de contradições (prompt 4)

Quatro subagentes compararam as specs novas, agrupadas por épicos (3–4, 5–7, 8 e 9–10), com as specs da AP1 e do Épico 2 (lidas da branch `feat/snapshot-multiloja`), com o código e os testes atuais e com as specs vizinhas. Cada um corrigiu o que dava para resolver só nos arquivos do próprio grupo. Depois eu resolvi as divergências entre grupos e escrevi a spec `SPEC/2026-11-15-visao-geral-ap2.md`. Ela registra:
- o contraste entre o que já existe e o que as specs acrescentam;
- os critérios globais GLOBAL-09..18 (fuso, testes sem rede, sem dependência nova, mapa de dados, pontos únicos de entrada, segredos, auditoria e permissões);
- a tabela das divergências resolvidas.

Achados principais, já corrigidos nas specs:
- **Código:**
  - o logout já encerra todas as sessões (CAD-05), então CTA-05 só acrescenta "sair dos outros aparelhos";
  - a CSRF própria (`__init__.py`) barraria a API v2 e `/jobs/ciclo`;
  - o cache da v1 (`public, max-age=60` em todo `/api/`) sobrescreveria o `no-store` da v2;
  - `FLASK_ENV` não existe no Flask 3 e foi trocado por `GAMELOG_PRODUCAO`;
  - os templates usam `style=` e `onchange`, então a CSP permite estilos inline e o `onchange` vai para arquivo estático;
  - o e-mail anonimizado precisava passar no `EMAIL_RE`;
  - o `seed` não sobrescreve o banco: os dados somem porque o disco do plano gratuito é apagado a cada deploy.
- **Testes existentes:**
  - o cadastro com aceite e data de nascimento e o redirecionamento para `/conta/nascimento` quebrariam `tests/test_auth.py` e todo login;
  - as specs agora mandam o seed e o `make_user` criarem adultos e o cadastro de teste enviar a data;
  - `test_con*` passa a chamar `conquistas recalcular`;
  - os `Follow` existentes são migrados como aprovados.
- **Entre specs (decididas por mim):**
  - avaliação de perfil privado fica oculta e conta na média (DIA-13 vale sobre a versão anônima de PMN-12);
  - `/ofertas` responde 403 com mensagem para menores;
  - o adiamento noturno vale só para o e-mail não transacional de menor;
  - o resumo semanal é ligado e desligado só pelo consentimento `email_resumo`;
  - a denúncia de post de clube vira `Report(alvo_tipo="post_clube")`;
  - a exclusão de conta com denúncia em preservação desativa a conta na hora e anonimiza depois do prazo;
  - as conquistas de torneio e de pódio entram nas métricas do GAM.
- **Specs da AP1:**
  - EST repetida (estatisticas e avaliacao-interativa) e `/jogo/<id>` em COM-01 e EST-01 ficam registradas na visão geral da AP2, sem reescrever as specs antigas;
  - a meia estrela de EST-02 nunca foi implementada (o código e `test_estrelas.py` usam nota inteira), e a AP2 confirma a nota inteira de 0 a 10.

Ponto que ficou de fora: a recuperação de senha por e-mail não está em nenhuma spec (CTA e EML a deixam de fora) e precisa de uma spec própria, se a dupla quiser.

Commits da auditoria:
- `spec(visao-geral-ap2)`, a spec nova, sozinha no commit;
- um commit de ajustes por épico, com os critérios mantidos na mesma quantidade;
- este registro e o ponteiro no plano.
