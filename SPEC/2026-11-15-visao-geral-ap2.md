# Visão geral da AP2: GameLog (2026-11-15)

## O quê e por quê

A `2026-10-01-visao-geral.md` descreve o GameLog da AP1. Desde então o produto mudou:
- o catálogo passou a ser real (CRE), importado da Steam (IMP) e, no Épico 2, também da GOG e da RAWG (FNT, GOG, MRG, SNP);
- os épicos 3 a 10 acrescentam diário, gamificação, comunidade, API v2 e MCP, jobs e e-mail, preços, privacidade (LGPD/GDPR) e proteção de crianças e adolescentes (ECA Digital).

Esta spec atualiza a visão geral sem reescrever a antiga. Ela registra o contraste entre o que já existe e o que as specs novas acrescentam, fixa as regras globais que valem para todas elas e resolve as divergências entre specs já escritas. As specs de cada parte continuam sendo a fonte dos critérios. Aqui só ficam as regras que atravessam várias delas.

Altera:
- GLOBAL-07: os jogos vêm de lojas reais (CRE-01), e só usuários e atividade do seed são fictícios;
- o "Fora do escopo" e as "Restrições" da visão geral da AP1: a integração com lojas entrou com IMP, FNT e GOG, e as capas vêm das lojas, servidas pelo próprio GameLog (CAP, RTN-07);
- EST-02 da `2026-11-07-avaliacao-interativa.md`: a nota continua inteira de 0 a 10 (AVA-01), como o código e `tests/test_estrelas.py` já fazem, e cada estrela vale 1 ponto.

## Personas

- **Visitante**, **Jogador** e **Administrador**: como na AP1.
- **Jogador menor de idade**:
  - de 16 a 17 anos, segue a política de menores (PMN);
  - de 12 a 15 anos, só ativa a conta vinculada a um responsável (IDA, RSP).
- **Responsável**: conta adulta vinculada a um ou dois menores, com painel de supervisão (RSP).
- **Moderador de clube**: dono ou moderador de um clube; organiza metas, mural e torneios do clube (CLB, TRN).
- **Agente de IA do jogador**: cliente MCP ou script autenticado por token pessoal, sempre agindo como o dono do token (AP2, MCP).

## Contraste: o que existe e o que as specs acrescentam

| Área | Hoje (AP1 e Épico 2) | Com as specs da AP2 |
|---|---|---|
| Catálogo e lojas | Jogos reais da Steam no snapshot; GOG e RAWG, vínculos de loja, deduplicação e "Disponível em" (Épico 2) | Preço e histórico por loja e menor preço (PRC); ofertas e alertas (OFR); classificação indicativa (CLS) |
| Acompanhamento | Um status por jogo, sem data nem horas; "zerado" só como texto (PRF-01) | Diário de sessões com `Status.zerado_em` e perfil privado (DIA); horas da Steam e CSV (HRS); retrospectiva em SVG (RET); listas por regra (LSA) |
| Engajamento | 3 conquistas recalculadas a cada abertura do perfil (CON) | Notificações (NTF); motor de conquistas por eventos (GAM); XP e sequência semanal (PRG); rankings por temporada (RNK); clubes (CLB); torneios (CHV, TRN) |
| Social e moderação | Seguir livre; feed; denúncia só de avaliação; perfil sempre público | Bloquear e silenciar (BLQ); pedido de seguir para menores (PMN-03); denúncia de comentário, perfil e post de clube, com fila de risco a menores (DEN) |
| API e agentes | API v1 pública e só de leitura (API) | Tokens com escopo e API v2 com escrita (AP2); servidor MCP e skills (MCP) |
| Operação | Comandos manuais; banco recriado a cada deploy | Fila de jobs (JOB); e-mail (EML); backup assinado e restauração na subida (BKP) |
| Conta | Cadastro, login com bloqueio e logout de todas as sessões | Troca de senha e e-mail, sair dos outros aparelhos, 2FA por TOTP (CTA); data de nascimento e faixas (IDA) |
| Privacidade | Nenhuma política, aceite, exportação ou exclusão; `LoginAttempt` e `AuditLog` sem prazo; capas carregadas da CDN da Steam | Mapa de dados (MAP); política e termos versionados (PRV); consentimentos (CNS); direitos do titular (DIR); retenção e proxy de capas (RTN); incidentes e cabeçalhos de segurança (INC) |

## Critérios de aceitação globais da AP2

Continuam valendo GLOBAL-01 a GLOBAL-06 e GLOBAL-08. Valem também:

- **GLOBAL-09:** "dia", "semana" e "mês" são contados no fuso `America/Belem`; a semana é a ISO, começando na segunda. Os testes fixam o relógio.
- **GLOBAL-10:** os testes rodam sem rede. Toda chamada HTTP externa passa pelo cliente comum das fontes (FNT-01), e os testes injetam respostas falsas.
- **GLOBAL-11:** nenhuma spec acrescenta dependência ao `requirements.txt`. TOTP, MCP, cron, SVG e SMTP usam a biblioteca padrão ou o que o Flask já traz. Uma dependência nova exige uma issue com a justificativa.
- **GLOBAL-12:** toda coluna nova de modelo é anotada no mapa de dados (MAP-02), e o teste de MAP-03 falha se faltar alguma.
- **GLOBAL-13:** toda tela, endpoint (v1, v2 ou MCP), notificação ou e-mail que mostra um usuário ou chega a ele consulta as mesmas três funções, sem reimplementar as regras:
  - `pode_ver_perfil` (DIA-12);
  - `bloqueado_entre` (BLQ);
  - `pode` (PMN-01).
- **GLOBAL-14:** cada efeito colateral tem um único ponto de entrada, e as rotas HTML e da API v2 chamam as mesmas funções de serviço:

  | Efeito | Ponto de entrada |
  |---|---|
  | Avisar o usuário | `notificar()` (NTF-01) |
  | Mandar e-mail | `enviar_email()` (EML-01) |
  | Trabalho fora da requisição | `enfileirar()` (JOB-01) |
  | Reação às ações do usuário | `emitir_evento()` (GAM-03) |
  | JSON de entidades | os serializadores (AP2-07) |
  | Desenho de gráfico | `graficos_svg` (RET-01) |
- **GLOBAL-15:** o processo web não roda agendador nem thread. Os jobs rodam por `flask jobs rodar` ou pela rota protegida de JOB-11.
- **GLOBAL-16:** chaves, senhas e tokens vêm de variáveis de ambiente, declaradas no `render.yaml` com `sync: false`. Nunca entram no repositório, em logs, saídas de comando, respostas ou arquivos exportados. Tokens e códigos ficam só como hash.
- **GLOBAL-17:** toda ação administrativa nova grava no `AuditLog` (ADM-09) com o nome `<area>.<acao>`.
- **GLOBAL-18:** rotas que só um papel acessa respondem como hoje:
  - visitante vai ao login (GLOBAL-02);
  - quem não é admin recebe 403 (GLOBAL-05);
  - recurso privado de outra pessoa responde 404, como uma lista privada (LIS-08).

## Divergências resolvidas entre specs

| Divergência | Resolução |
|---|---|
| Sigla IMP repetida (importacao-steam e importação de horas) e RET repetida (retrospectiva e retenção) | A importação de horas usa HRS e a retenção usa RTN |
| Sigla EST repetida em `2026-11-07-estatisticas.md` e `2026-11-07-avaliacao-interativa.md` | Os testes já distinguem as duas (`test_estatisticas.py` e `test_estrelas.py`). Specs novas citam EST com o nome da spec; as specs antigas não são reescritas |
| COM-01 e EST-01 citam `/jogo/<id>` | A rota é `/jogos/<slug>` (`jogo.py`), e as specs novas usam essa |
| Meia estrela (EST-02) e nota inteira (AVA-01) | Vale a nota inteira de 0 a 10 (ver "Altera") |
| Avaliação de perfil privado: oculta (DIA-13) ou anônima (PMN-12) | Vale DIA-13: ela não aparece para quem não pode ver o perfil, e a nota continua contando na média, inclusive nas APIs |
| `/ofertas` para menores: 403 (PMN-05) ou 404 (OFR-08) | 403 com mensagem; o preço por loja continua visível para todos |
| Aviso noturno a menores: adiado na notificação ou no e-mail | A notificação no site sai na hora; o e-mail não transacional fica para as 7h (PMN-07, EML-01) |
| Resumo semanal: preferência própria (EML) ou consentimento (CNS) | Um único interruptor, o consentimento `email_resumo` (CNS-06); o link de descadastro grava a revogação |
| Recomendações exigiriam consentimento | Ficam na execução do contrato, com oposição (DIR-09); REC e COL não mudam |
| Denúncia própria de post de clube (CLB-11) | Vira `Report` com `alvo_tipo="post_clube"` (DEN-01) |
| Exclusão de conta (DIR) e preservação de denúncia (DEN-09) | Com `preservar_ate` vigente, a conta é desativada na hora e anonimizada pelo expurgo depois do prazo (DIR-07, RTN-01) |
| Horas da Steam em rankings, metas e teto diário | A sessão de saldo da Steam conta nas horas pessoais, mas fica fora de RNK, CLB e do teto de HRS-09 (`origens`, DIA-08) |
| Jobs no Render gratuito | O serviço `cron` não enxerga o SQLite do serviço web; um cron HTTP externo chama `POST /jobs/ciclo` (JOB-11) |

## Fora do escopo

Continuam fora do escopo:
- compra ou venda de jogos e pagamentos (preços e ofertas só informam e levam à loja);
- chat em tempo real e mensagens diretas;
- aplicativo mobile nativo;
- upload de imagens de usuário;
- federação (ActivityPub);
- conta para menores de 12 anos;
- recuperação de senha por e-mail, que fica para uma spec própria.
