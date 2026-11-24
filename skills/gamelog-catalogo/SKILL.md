---
name: gamelog-catalogo
description: Use quando a pessoa perguntar sobre jogos do catálogo do GameLog, em que loja um jogo está ou detalhes de um jogo.
---

# Catálogo do GameLog

Ferramentas: `buscar_jogos`, `detalhes_jogo`, `disponivel_em`

1. Para achar um jogo, chame `buscar_jogos` com o nome em `termo`. Para filtrar, use `genero` (slug, como `rpg`) e `loja` (`steam`, `gog` ou `epic`).
2. Use o `slug` do resultado nas outras ferramentas. Não invente slug.
3. Para sinopse, nota média, gêneros, plataformas e preços, chame `detalhes_jogo`.
4. Para saber onde comprar, chame `disponivel_em` e mostre os links das lojas.
5. Com "jogo não encontrado", busque de novo com outro termo antes de dizer que o jogo não existe.

Exemplo: "O Hades está na GOG?" → `buscar_jogos` com `termo: "hades"` e `loja: "gog"`, e depois `disponivel_em` com o slug.
