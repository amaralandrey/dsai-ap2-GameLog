# Política de menores (2026-11-15)

## O quê e por quê
Depois da verificação de idade (IDA), cada parte do GameLog precisa tratar adolescentes de outro jeito. Perfil, feed, rankings, clubes, ofertas, e-mail, notificações, recomendações e torneios tomam essa decisão, e se cada um tiver o próprio `if idade < 18`, as regras divergem e uma tela nova esquece de checar. Esta spec cria um módulo central, `menores/politica.py`, com uma tabela de ações e a função `pode(user, acao, alvo=None)`. Todo ponto que expõe um menor ou o alcança pergunta à política antes de agir.

A tabela segue o ECA Digital (Lei 15.211/2025):
- configuração mais protetiva por padrão (art. 7º);
- evitar uso compulsivo: sem recompensa por tempo de uso nem notificações excessivas (art. 8º, IV; Decreto 12.880/2026, art. 9º, §1º);
- sem perfilamento para publicidade a menores (arts. 22 e 26);
- supervisão pelo responsável para contas de até 16 anos (art. 24; RSP).

A política é transversal: toda spec escrita depois desta consulta `pode()` em vez de reimplementar a regra. Um menor nunca tem menos proteção por falta de dado, porque, sem data de nascimento, a faixa é a mais protetiva (IDA-03).

Depende de: IDA (faixas), DIA (`User.perfil_privado`, `pode_ver_perfil`). Consultada por: DIA, NTF, GAM, PRG, RNK, CLB, AP2, MCP, EML, PRC, OFR, REC/COL, TRN, RSP.
Altera: SOC-01 (seguir um menor vira pedido pendente); COM-01 (quem pode comentar em avaliação de menor).
Respeita: MAP (`Follow.aprovado_em` anotado).

## Critérios de aceitação
- PMN-01: `pode(user, acao, alvo=None)` consulta a tabela `ACOES` de `menores/politica.py` e devolve `True` ou `False` conforme a faixa (IDA-03) de quem age e, quando houver, do alvo. Uma ação que não está na tabela levanta `ValueError`, para nunca liberar por omissão. A tabela cobre ao menos as ações que as outras specs consultam: `seguir`, `comentar`, `perfil_publico`, `ranking_global`, `conquista_por_tempo`, `aviso_engajamento`, `email_engajamento`, `ver_ofertas`, `alerta_preco`, `entrar_clube`, `publicar_clube` e `torneio_inscrever`. Uma conta com `aguardando_responsavel` tem negadas todas as ações sociais (seguir, comentar, avaliar publicamente, clubes, torneios).
- PMN-02: uma conta de menor (adolescente ou vinculado) nasce com `perfil_privado = True` e com listas novas privadas por padrão. De 16 a 17, o próprio usuário pode tornar o perfil público. De 12 a 15, só o responsável pode (RSP-07); o POST do menor responde 403.
- PMN-03: seguir um menor cria `Follow` com `aprovado_em = NULL`, um pedido pendente que não dá acesso ao perfil privado nem ao feed do menor. Quem aprova é o próprio menor (16–17) ou o responsável (12–15, RSP-06). Isso não muda o seguir entre adultos (SOC-01, SOC-02).
- PMN-04: `pode(menor, "ranking_global")` é falso: um menor nunca aparece no ranking global (RNK). Nos escopos "Seguindo" e "Clube", só aparece para quem pode ver o perfil dele (`pode_ver_perfil`).
- PMN-05: `pode(menor, "ver_ofertas")` e `pode(menor, "alerta_preco")` são falsos. Para menores, a página do jogo omite o bloco de preços e de "onde está mais barato" (PRC, OFR), `/ofertas` responde 404 e o POST de alerta de preço responde 403.
- PMN-06: recomendações para menores não usam preço, desconto nem loja como critério. O histórico de um menor não alimenta as recomendações colaborativas de outros usuários (COL) nem a comparação de gosto feita por terceiros (AFI).
- PMN-07: `notificar()` (NTF) descarta para menores os tipos `sequencia`, `oferta` e `preco_alvo`. Também não envia notificação no site nem e-mail entre 22h e 7h (`America/Belem`); a notificação fica para as 7h.
- PMN-08: e-mails de engajamento (resumo semanal, EML) ficam desligados por padrão para menores. De 16 a 17, o próprio usuário pode ligar. De 12 a 15, só o responsável. E-mails de conta e segurança (confirmação, senha, vínculo) não são afetados.
- PMN-09: para menores não valem recompensas por tempo de uso: `pode(menor, "conquista_por_tempo")` é falso, e as conquistas marcadas com `Conquista.por_tempo` (horas registradas, maratonista e semelhantes, GAM-14) não são avaliadas nem mostradas; `pode(menor, "aviso_engajamento")` é falso, e o aviso de sequência prestes a quebrar (PRG-10) não sai. O widget de sequência não aparece. As demais conquistas e o XP por ações continuam.
- PMN-10: `pode(x, "comentar", avaliacao_de_menor)` só é verdadeiro para quem segue o menor com o pedido aprovado e para o próprio menor. O POST de outra pessoa responde 403. Quando o responsável desligou os comentários (RSP-07), o menor também não comenta e ninguém comenta nas avaliações dele.
- PMN-11: entrar em clube, postar no mural do clube e se inscrever em torneio respeitam os controles do responsável (RSP-07). A inscrição de um menor de 12 a 15 em torneio fica `pendente_responsavel` até a aprovação (RSP-08). O GameLog não tem mensagens diretas, e esta política fixa que elas não existem para menores.
- PMN-12: API v1 e v2 e MCP seguem as mesmas regras. Um dado de menor que a tela não mostraria a quem pergunta também não sai nos endpoints: perfil e diário privados respondem 404, e avaliações de perfil privado aparecem na página do jogo e em `/api/jogos/<slug>/avaliacoes` sem nome nem link de perfil ("jogador do GameLog") e continuam contando na média.
- PMN-13: a página `/privacidade/menores` explica em linguagem simples (LGPD art. 14, §6º) o que muda para cada faixa. O texto é gerado da tabela `ACOES`, então mudar a tabela muda a página.
- PMN-14: os testes rodam sem rede, com uma matriz faixa × ação que cobre adulto, adolescente, vinculado, conta pendente e sem data de nascimento para cada ação da tabela, além de: ação desconhecida, pedido de seguir pendente, ranking global sem menores, bloco de preços ausente, notificação descartada e adiada pelo horário, conquista por tempo não concedida e a mesma regra pela API. Todos nomeados `test_pmn_NN_*`.

## Fora do escopo
Mensagens diretas, limites de tempo de tela medidos por acesso (o GameLog não rastreia tempo de uso, só o diário declarado), publicidade (o GameLog não tem anúncios), loot boxes e compras (o GameLog não vende nada), federação e qualquer política para menores de 12 anos.
