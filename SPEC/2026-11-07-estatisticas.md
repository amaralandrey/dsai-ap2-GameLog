# Estatísticas Globais do Catálogo (2026-11-07)

## O quê e por quê
Uma nova página pública de *dashboard* que resume o estado atual do catálogo e o engajamento da comunidade do GameLog. Acrescenta valor visual e analítico, complementando o catálogo base (CAT-01) e incentivando a curiosidade dos visitantes sobre quais são os jogos mais populares da plataforma.

## Critérios de aceitação
- EST-01: a aplicação disponibiliza uma nova rota `/stats` (ou `/estatisticas`), acessível a utilizadores anónimos e logados, com um link no rodapé ou no menu principal de navegação.
- EST-02: a página exibe contadores gerais com o número total de jogos registados, utilizadores cadastrados e avaliações escritas.
- EST-03: a página exibe uma lista (ou *cards*) dos "5 Jogos Mais Avaliados da Comunidade" (maior contagem de avaliações).
- EST-04: a página exibe o "Jogo do Momento", definido como aquele com a maior nota média, exigindo que tenha pelo menos 3 avaliações para ser elegível e evitar distorções de notas 10 isoladas.
- EST-05: se o banco de dados estiver recém-criado ou sem dados suficientes (ex: nenhum jogo com 3 avaliações), a página exibe estados vazios de forma amigável (ex: "Ainda não há avaliações suficientes") e nunca falha com erros de divisão por zero ou listas vazias.
- EST-06: as consultas (`COUNT`, `GROUP BY`) utilizam a API do SQLAlchemy de forma otimizada para consolidar os dados numa única passagem no banco, mantendo o carregamento da página rápido.

## Fora do escopo
Gráficos dinâmicos renderizados com bibliotecas JavaScript (como Chart.js ou D3), filtros temporais (ex: "estatísticas deste mês"), exportação de relatórios (PDF/CSV) e páginas de estatísticas individuais por utilizador.
