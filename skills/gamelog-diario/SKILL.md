---
name: gamelog-diario
description: Use quando a pessoa quiser registrar quanto jogou, mudar o status de um jogo, avaliar ou ver as próprias listas no GameLog.
---

# Diário e status no GameLog

Ferramentas: `registrar_sessao`, `marcar_status`, `avaliar`, `minhas_listas`

1. Ache o `slug` com `buscar_jogos` (skill gamelog-catalogo) se a pessoa citar o jogo pelo nome.
2. Para registrar tempo de jogo, chame `registrar_sessao` com `duracao_min` em minutos (2h = 120). Sem `data`, vale hoje no fuso de Belém. Use `zerou: true` só quando a pessoa disser que terminou o jogo.
3. Registrar sessão de um jogo "planejado" muda o status para "jogando". Não chame `marcar_status` só por isso.
4. Para mudar o status, chame `marcar_status` com `planejado`, `jogando`, `jogado` ou `abandonado`; `nenhum` remove o status.
5. Para avaliar, chame `avaliar` com `nota` inteira de 0 a 10. Avaliar de novo edita a avaliação anterior.
6. Para ver o que a pessoa tem, chame `minhas_listas` (filtro `status`, por exemplo `planejado` para a wishlist).
7. Erro de validação volta com o campo e o motivo. Corrija o argumento e tente de novo. Com "escopo insuficiente", diga que o token precisa do escopo `escrever`.

Exemplo: "registra 2h de Hades hoje" → `registrar_sessao` com `slug: "hades"` e `duracao_min: 120`.
