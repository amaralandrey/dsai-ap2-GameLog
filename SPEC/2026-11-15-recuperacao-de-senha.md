# Recuperação de senha (2026-11-15)

## O quê e por quê
Quem esquece a senha do GameLog perde a conta: a spec de cadastro deixou a recuperação fora do escopo, e o único caminho é o admin mexer no banco. A segurança da conta (CTA) trata da troca de senha de quem está logado, e a spec de e-mail (EML) cria o canal de envio, mas nenhuma das duas cobre o "esqueci minha senha". Esta spec cria esse fluxo. O link de redefinição é enviado ao e-mail da conta e é assinado com `itsdangerous`, sem tabela nova. Ele vence em 1 hora e serve uma vez só, porque a assinatura inclui uma impressão da senha atual. A resposta é sempre a mesma, exista a conta ou não, como já acontece no login (CAD-03).

Depende de: EML (envio transacional e caixa de saída), CTA (2FA no login seguinte e histórico de segurança), NTF (aviso `sistema`), IDA (`suspensa_em`), DIR (conta anonimizada), RSP (aviso ao responsável), INC (`GAMELOG_PRODUCAO`).

Altera:
- CAD-03: a tela de login ganha o link "Esqueci minha senha", e a mensagem genérica continua;
- CAD-04: redefinir a senha zera as tentativas falhas daquele e-mail;
- EML-10: a caixa de saída não mostra o corpo do modelo `recuperar_senha`;
- o "Fora do escopo" de cadastro-e-login, CTA e EML.

Respeita: MAP (nenhuma coluna nova; o link não fica guardado na caixa de saída depois do envio) e PMN (o responsável de uma conta de 12 a 15 anos é avisado).

## Critérios de aceitação
- RSN-01: `/entrar` mostra o link "Esqueci minha senha" para `/senha/recuperar`. Essa página pede o e-mail.
  - Um e-mail em formato inválido volta com a mensagem do campo e status 400 (GLOBAL-03).
  - Um e-mail válido responde sempre 200 com o mesmo texto: "Se houver uma conta com esse e-mail, enviamos um link para redefinir a senha. Ele vale por 1 hora." Isso vale exista a conta ou não, sem diferença no status nem no corpo da resposta.
  - Quem já está logado é redirecionado para `/conta/seguranca` (CTA-01).
- RSN-02: para uma conta existente e apta, o pedido chama `enviar_email(user, "recuperar_senha", link=...)` (EML-01). É um e-mail transacional: ignora as preferências (EML-05) e não leva link de descadastro.
  - O link é `/senha/redefinir/<token>`.
  - O token é gerado com `URLSafeTimedSerializer` (chave `SECRET_KEY`, sal `"recuperar-senha"`) sobre o id do usuário e uma impressão da conta: SHA-256 de `password_hash` com `session_version`.
  - O token nunca é gravado no banco nem aparece em log fora do backend de console de desenvolvimento.
- RSN-03: não recebem e-mail, e a resposta continua a mesma de RSN-01:
  - conta inexistente;
  - conta anonimizada (DIR-06);
  - conta suspensa (`suspensa_em`, IDA-05).

  Uma conta pendente de responsável (`aguardando_responsavel`, IDA-04) pode recuperar a senha, porque precisa entrar para convidar o responsável.
- RSN-04: cada conta recebe no máximo 3 e-mails `recuperar_senha` por hora. O limite é contado nas linhas de `EmailSaida` daquele modelo e usuário, sem tabela nova e sem guardar IP. Pedidos acima do limite recebem a mesma resposta de RSN-01 e não enfileiram nada.
- RSN-05: `GET /senha/redefinir/<token>` mostra o formulário de nova senha quando o token é válido. Um token adulterado, com outro sal, de mais de 1 hora ou cuja impressão não confere mais (senha já trocada ou sessões já encerradas) responde 400 com "Link inválido ou vencido" e um link para pedir outro, sem dizer de qual conta seria. As respostas dessa rota levam `Cache-Control: no-store` e `Referrer-Policy: no-referrer`, para o token não vazar pelo histórico nem pelo cabeçalho `Referer`.
- RSN-06: o `POST` pede a nova senha duas vezes; ela segue CAD-01 (mínimo de 8 caracteres) e as duas precisam ser iguais, senão volta com a mensagem do campo e status 400, sem gastar o token. Com sucesso:
  - grava o novo hash (GLOBAL-06), incrementa `session_version` (todas as sessões abertas caem, CAD-05) e apaga os `LoginAttempt` daquele e-mail (CAD-04);
  - redireciona para `/entrar` com "Senha redefinida. Entre com a nova senha."; não faz login automático;
  - o mesmo link usado de novo responde 400 (RSN-05), porque a impressão da conta mudou.
- RSN-07: a redefinição não desliga o 2FA. No login seguinte, a conta com 2FA ainda passa por `/entrar/2fa` (CTA-08), aceitando TOTP ou código de recuperação. Os tokens pessoais da API v2 continuam valendo, como na troca de senha (CTA-11).
- RSN-08: depois da redefinição:
  - o e-mail transacional `senha_alterada` vai para a conta, com data e hora e a orientação de procurar o canal do titular (PRV-07) se não foi a própria pessoa;
  - a conta recebe a notificação `sistema` (NTF) e uma linha "senha redefinida por e-mail" no histórico de segurança (CTA-11);
  - numa conta `adolescente_vinculado`, cada responsável com vínculo ativo recebe a notificação `responsavel` (RSP).
- RSN-09: em produção (`GAMELOG_PRODUCAO=1`, INC-09) sem `SMTP_HOST`, `/senha/recuperar` mostra "A recuperação de senha está indisponível no momento; fale com a gente pelo canal do titular (PRV-07)" e não enfileira nada. Assim o link nunca vai parar no log do servidor. Depois do envio, o corpo da linha `recuperar_senha` em `EmailSaida` é trocado por "[link removido após o envio]", e `/admin/emails` (EML-10) nunca mostra o corpo desse modelo.
- RSN-10: os testes rodam sem rede, com o relógio fixado e a caixa de saída de EML, e são nomeados `test_rsn_NN_*`. Cobrem:
  - a resposta igual para conta existente, inexistente, anonimizada e suspensa;
  - o e-mail inválido (400);
  - o limite de 3 por hora;
  - o token válido, adulterado, com outro sal, vencido (1 h e 1 s) e reutilizado;
  - a senha curta e a confirmação diferente sem gastar o token;
  - o `session_version` incrementado com outra sessão derrubada;
  - as tentativas de login zeradas;
  - o 2FA ainda exigido;
  - o e-mail `senha_alterada`, a notificação e o aviso ao responsável;
  - os cabeçalhos `no-store` e `no-referrer`;
  - a indisponibilidade em produção sem SMTP;
  - o corpo removido da caixa de saída e oculto no admin.

## Fora do escopo
Recuperação por SMS ou por perguntas de segurança, recuperação de conta sem acesso ao e-mail (fica com o canal do titular, PRV-07), login por link mágico, recuperação do 2FA perdido sem códigos de recuperação, limite por IP e CAPTCHA.
