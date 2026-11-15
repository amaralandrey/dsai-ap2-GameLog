# Incidentes e segurança (2026-11-15)

## O quê e por quê
O GameLog guarda e-mails e hashes de senha, e com o Épico 10 passa a guardar dados de adolescentes. A Res. CD/ANPD 15/2024 trata qualquer um desses como incidente de risco relevante (art. 5). Mesmo assim, o GameLog não tem como registrar um incidente nem contar os prazos de comunicação.

Esses prazos são de 3 dias úteis para comunicar à ANPD e aos titulares (arts. 6 e 9), contados em dobro para agente de pequeno porte (Res. CD/ANPD 2/2022, art. 14). Todo incidente, comunicado ou não, fica registrado por 5 anos (Res. 15/2024, art. 10). Se o GameLog for oferecido na União Europeia, o prazo da GDPR é de 72 horas (art. 33).

Além disso, a aplicação não envia nenhum cabeçalho de segurança e o cookie de sessão sai com os padrões do Flask, sem `Secure`. O art. 46 da LGPD pede essas medidas técnicas.

Depende de: JOB (lembrete de prazo), EML (comunicação aos titulares), NTF (aviso aos admins), MAP (categorias de dados afetados), RTN (5 anos de retenção do registro). Respeita: MAP (o modelo `Incidente` entra no mapa) e PMN (incidente com dados de menores fica marcado como tal na avaliação de risco).

## Critérios de aceitação
- INC-01: só administradores acessam `/admin/incidentes` (GLOBAL-05). O admin registra um incidente com: descrição, data e hora em que o GameLog tomou ciência, data provável da ocorrência, categorias de dados afetados (opções vindas das categorias do mapa de dados, MAP), número estimado de titulares, se há dados de menores e as medidas tomadas. A validação é feita no servidor e o erro aparece por campo (GLOBAL-03).
- INC-02: a avaliação de risco é calculada e mostrada na hora do registro. O risco é "relevante" quando o incidente envolve dados de autenticação, dados de menores ou dados em larga escala (definida no registro como ≥ 1.000 titulares). Nos outros casos o risco fica "a avaliar" e o admin o confirma ou descarta com uma justificativa obrigatória. Toda decisão grava `AuditLog` `incidente.risco`.
- INC-03: a contagem dos prazos usa dias úteis a partir da ciência, ignorando sábados, domingos e os feriados nacionais fixos de `privacidade/feriados.py`. O prazo base é `INCIDENTE_PRAZO_DIAS_UTEIS=3` e é dobrado com `AGENTE_PEQUENO_PORTE=1`. A página mostra o prazo final e quantos dias faltam para a ANPD e para os titulares.
- INC-04: com `GDPR_ATIVO=1`, a página também mostra o prazo de 72 horas corridas desde a ciência. Os dois prazos aparecem lado a lado, e o mais próximo fica em destaque.
- INC-05: o modelo de comunicação à ANPD vem preenchido com os dados do registro e com campos marcados para o que falta, cobrindo o conteúdo mínimo do art. 6 §2 da Res. 15/2024. O admin baixa o texto em `.txt` e registra a data do envio (feito fora do GameLog, no peticionamento da ANPD). O registro fica com o estado "comunicado à ANPD".
- INC-06: a comunicação aos titulares tem linguagem simples e o conteúdo mínimo do art. 9 da Res. 15/2024. Ela é enviada pelo admin a uma lista de usuários afetados (todos, ou uma lista de nomes de usuário colada) e chega a cada um por e-mail (EML) e por notificação `sistema` (NTF). O registro guarda quantos titulares foram avisados e quando.
- INC-07: um job diário confere os incidentes de risco relevante ainda não comunicados. Quando falta 1 dia útil ou menos para o prazo, ele notifica todos os administradores (NTF, tipo `sistema`). O mesmo prazo não gera mais de um aviso por dia.
- INC-08: incidente não é apagado nem editado depois de encerrado. Correções entram como anotações datadas com o autor. O expurgo (RTN) só considera incidentes encerrados há mais de 5 anos.
- INC-09: em produção (`FLASK_ENV` diferente de `development`, sem `TESTING`), o cookie de sessão sai com `Secure`, `HttpOnly` e `SameSite=Lax`. Em desenvolvimento, `Secure` fica desligado para o login funcionar em `http://127.0.0.1`.
- INC-10: toda resposta HTML leva os cabeçalhos: `Content-Security-Policy` com `default-src 'self'`, `img-src 'self' data:`, `frame-ancestors 'none'` e sem `unsafe-inline` em `script-src`; `X-Content-Type-Options: nosniff`; `Referrer-Policy: strict-origin-when-cross-origin`; `Permissions-Policy` desligando câmera, microfone e geolocalização. Em produção, também `Strict-Transport-Security: max-age=31536000`. As respostas da API mantêm os cabeçalhos atuais (API-01, API-09) e ganham só `nosniff`.
- INC-11: todas as páginas existentes continuam funcionando com a CSP. Script ou estilo inline que hoje dependa de `unsafe-inline` vai para arquivo estático, e um teste percorre as páginas principais procurando `<script>` inline sem `nonce`.
- INC-12: testes sem rede para: acesso de não admin, validação, risco relevante automático por categoria e por menores, justificativa obrigatória, contagem de dias úteis com feriado e fim de semana, prazo em dobro, prazo GDPR, modelos com o conteúdo mínimo, envio aos titulares, aviso de prazo uma vez por dia, imutabilidade após o encerramento, flags do cookie em produção e em desenvolvimento, e cada cabeçalho de segurança.

## Fora do escopo
Detecção automática de intrusão, integração com o peticionamento eletrônico da ANPD, feriados estaduais e municipais e os móveis (Carnaval, Corpus Christi), relatório de impacto (RIPD), testes de penetração, WAF, rotação automática da `SECRET_KEY`.
