# Conquistas e Badges (2026-11-07)

## O quê e por quê
Adiciona um sistema de medalhas (badges) automáticas para engajar os usuários a interagirem mais com o catálogo (avaliar, organizar em listas, interagir). As conquistas são exibidas no perfil do usuário, complementando os dados do perfil rico (PRF-01..09) e incentivando o uso das funções centrais do GameLog.

## Critérios de aceitação
- CON-01: o sistema possui três conquistas iniciais fixas, calculadas automaticamente com base no histórico do usuário: "Crítico" (ter escrito pelo menos 5 avaliações), "Colecionador" (ter pelo menos 20 jogos marcados em suas listas/wishlist) e "Pioneiro" (ter avaliado um jogo que não tinha nenhuma avaliação anterior).
- CON-02: o perfil público e privado (`/perfil/<username>`) exibe uma nova seção "Conquistas" logo abaixo das estatísticas (PRF-02), mostrando o ícone e o nome das badges que o usuário desbloqueou.
- CON-03: as conquistas são derivadas diretamente dos dados existentes (tabelas de avaliações e listas), sem exigir gatilhos (triggers) complexos no banco de dados para sincronização de estado.
- CON-04: avaliações ocultadas pela moderação não contam para o cálculo da conquista "Crítico".
- CON-05: se o usuário não possuir nenhuma conquista, a seção "Conquistas" exibe uma mensagem amigável indicando como ele pode ganhar sua primeira medalha (ex: "Avalie 5 jogos para ganhar sua primeira medalha").
- CON-06: o cálculo das conquistas na página de perfil deve ser feito com no máximo 3 consultas simples (`COUNT`), mantendo o tempo de resposta da página de perfil rápido (menos de 200ms na máquina local).

## Fora do escopo
Pontos de experiência (XP), subida de níveis, animações ou pop-ups no momento em que a conquista é atingida (notificações), criação ou edição de novas conquistas pelo painel do admin, e conquistas baseadas em tempo de permanência ou login consecutivo (streaks).
