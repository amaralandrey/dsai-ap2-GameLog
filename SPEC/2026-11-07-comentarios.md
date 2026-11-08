# Comentários nas Avaliações (2026-11-07)

## O quê e por quê
Permite que os utilizadores interajam diretamente respondendo às avaliações uns dos outros. Isso expande o lado social da plataforma (SOC-01..10) e a página de avaliações (AVA-01..11), transformando as reviews num espaço de discussão sobre os jogos em vez de apenas texto estático.

## Critérios de aceitação
- COM-01: utilizadores autenticados veem um formulário simples de "Adicionar comentário" abaixo de cada avaliação na página do jogo (`/jogo/<id>`) e no feed social.
- COM-02: os comentários são exibidos abaixo da avaliação correspondente, ordenados cronologicamente (do mais antigo para o mais recente).
- COM-03: o autor de um comentário tem um botão para apagá-lo. O administrador (`admin`) também pode apagar qualquer comentário pelo frontend.
- COM-04: comentários são limitados a 500 caracteres, não aceitam submissões vazias e o seu texto deve ser escapado pelo Jinja2 (sem renderização de HTML/Markdown) para prevenir vulnerabilidades de XSS.
- COM-05: avaliações que foram ocultadas pela moderação (AVA-08) não exibem a secção de comentários e não permitem novas submissões.
- COM-06: a exclusão de uma avaliação em cascata (`ON DELETE CASCADE`) apaga automaticamente todos os comentários associados a ela no banco de dados.

## Fora do escopo
Respostas a outros comentários (aninhamento/threads), sistema de reações (likes) nos comentários, edição de comentários (apenas apagar é permitido), notificações por e-mail ou no painel para o autor da avaliação e menções com `@username`.
