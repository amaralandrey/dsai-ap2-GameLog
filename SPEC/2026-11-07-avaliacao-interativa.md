# Avaliação por Estrelas (2026-11-07)

## O quê e por quê
Substitui a caixa de entrada numérica (0 a 10) por um componente visual interativo de 10 estrelas para a submissão de avaliações. Melhora a experiência do usuário (UX) tornando o processo mais intuitivo e lúdico. Permite o uso de meias-estrelas (ex: 9.5) para manter a granularidade das notas atuais, sem quebrar a lógica de recomendações existente.

## Critérios de aceitação
- EST-01: o formulário de avaliação na página do jogo (`/jogo/<id>`) substitui o campo `<input type="number">` por uma interface interativa contendo 10 estrelas.
- EST-02: a interface permite a seleção de valores intermediários (meias-estrelas), resultando em passos de 0.5 (ex: 0.5, 1.0, 1.5... até 10.0). O usuário pode clicar na metade esquerda ou direita de uma estrela.
- EST-03: o backend continua recebendo a nota como um valor numérico (decimal) através do formulário submetido. Nenhuma alteração estrutural no banco de dados (`models.py`) é necessária, garantindo compatibilidade com o catálogo e as recomendações (REC-02).
- EST-04: a exibição das notas em toda a aplicação (página do jogo, feed social, perfil) também passa a renderizar as estrelas preenchidas, meias-estrelas e estrelas vazias, substituindo a exibição do número puro.
- EST-05: a implementação deve respeitar a regra de "sem JS de build". O componente deve ser construído usando truques de HTML/CSS (ex: técnica do `input type="radio"` oculto) ou um script Vanilla JS limpo injetado apenas no template da avaliação.
- EST-06: a interface deve ser acessível e responsiva, garantindo que o toque em dispositivos móveis consiga registrar as meias-estrelas com facilidade.

## Fora do escopo
Mudança da escala de notas para 1-5 (a escala de 10 é mantida para não invalidar os cálculos e dados legados do seed), animações complexas de clique, frações menores que 0.5 (como 9.3 ou 9.7), e refatoração das avaliações antigas do banco de dados (que já estão em formato compatível).
