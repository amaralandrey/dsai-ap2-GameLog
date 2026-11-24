---
name: gamelog-descoberta
description: Use quando a pessoa pedir sugestões do que jogar no GameLog ou quiser comparar o gosto dela com o de outra pessoa.
---

# Descoberta no GameLog

Ferramentas: `recomendacoes`, `comparar_gosto`

1. Para sugerir jogos, chame `recomendacoes` (até 10 com `limite`). Mostre o motivo de cada sugestão.
2. Se vier um `aviso`, a pessoa ainda avaliou poucos jogos. Sugira avaliar alguns para melhorar as recomendações.
3. Para comparar o gosto, chame `comparar_gosto` com o `username`. A afinidade vai de 0 a 100 e precisa de 3 jogos em comum.
4. "usuário não encontrado" também vale para perfil privado ou bloqueado. Não tente adivinhar o motivo.
