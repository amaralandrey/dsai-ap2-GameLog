# Plano de trabalho — GameLog (AP2 / DSAI 2026.4)

Repositório: `amaralandrey/dsai-ap2-GameLog` (fork de `fenalk/dsai-ap1-GameLog`).
Dupla: Andrey Amaral & Fabrício Assunção · Cliente: Fernanda Borges (`@fenalk`) & Artur (`@arturgomesc`).
Atualizado: 2026-11-15.

> 📏 **Baseline AP1 (`cloc`):** 4.064 linhas úteis (Python, HTML, CSS, Shell, INI).
> **Meta AP2 (`cloc --diff`):** ≥ 100.000 linhas novas úteis (total alvo: ~104.064 linhas).
> **Meta realista da dupla até 2026-11-24:** 30.000 a 40.000 linhas novas (ver seção 4); a diferença para 100 mil é um risco assumido, sem inflar linhas.

---

## ⚠️ Regras invioláveis (leitura obrigatória para agentes e humanos)

Qualquer violação abaixo **anula a AP2** ou **zera o critério** na auditoria automatizada:

1. **Proibido reescrever o projeto:** nunca troque Python 3.12+, Flask 3, SQLAlchemy/SQLite, Jinja2 ou `pytest` por outra linguagem/framework. Nunca apague módulos existentes (`auth.py`, `catalogo.py`, `avaliacoes.py`, etc.) para refazê-los do zero. Refatoração pontual só é permitida se justificada em uma *issue* e coberta por testes.
2. **Proibido codar sem spec prévia:** nunca crie ou altere código de funcionalidade antes de existir um arquivo datado `SPEC/AAAA-MM-DD-<nome>.md` **já commitado** no Git antes do primeiro commit de código que o referencia.
3. **Proibido inflar linhas artificialmente:** não gere código duplicado (limite `jscpd` ≤ 5%), código morto (*dead code*), comentários inúteis, *lockfiles* ou arquivos gerados para bater a meta de 100 mil linhas. Toda linha deve pertencer a funcionalidades reais, templates ou testes válidos.
4. **Proibido alterar histórico ou commitar direto na `main`:** nunca execute `git push --force`, `git commit --amend` após push ou `rebase` na `main`. 100% dos merges na `main` devem ocorrer via Pull Request aprovado pelo outro integrante da dupla com CI verde.
5. **Proibido vazar segredos:** nunca commite chaves de API, senhas, `.env` ou tokens dentro do código ou dos logs em `prompts/sessoes/` (auditoria via `gitleaks` exige zero ocorrências).
6. **Proibido encerrar o dia sem sessão e custo:** todo dia com commit de agente (`Agent:`) exige um arquivo datado em `prompts/sessoes/` contendo o registro explícito de tokens consumidos ou custo financeiro.

---

## 0. Checklist de conformidade da rubrica (50 pontos)

| Critério | Meta obrigatória | Estratégia no GameLog AP2 | Pts |
| :--- | :--- | :--- | :---: |
| **Produção + CI** | URL pública no ar servindo `HEAD` da `main` + CI verde | Deploy contínuo no Render + GitHub Actions (`pytest` + `cov` + `gitleaks`) | **6** |
| **Tamanho (`cloc`)** | ≥ 100.000 linhas novas (sem gerados/mortos) | Expansão real de conectores, diário de jogatina, gamificação, clubes, API v2, servidor MCP, jobs e e-mail, preços, privacidade (LGPD/GDPR), ECA Digital, torneios e testes por AC | **8** |
| **Duplicação (`jscpd`)** | ≤ 5% de código duplicado | Camadas de serviço reutilizáveis (`FontesBase`, `GamificacaoEngine`, `graficos_svg`, serializadores comuns da API v2) | **3** |
| **Specs (`SPEC/`)** | 100% das features com spec datada commitada **antes** do código | 1 commit exclusivo de `SPEC/AAAA-MM-DD-*.md` abrindo cada branch | **5** |
| **Trailers Git** | ≥ 80% dos commits com `Agent:` e `Spec:` | Hook local `commit-msg` validando os trailers antes de aceitar o commit | **3** |
| **Frequência** | ≥ 2 dias/semana; mediana < 400 linhas/commit | Commits atômicos por camada (`model` → `service` → `route` → `template` → `test`) | **3** |
| **Sessões + Custo** | 100% dos dias com agente registrados em `prompts/sessoes/` com tokens/custo | Fechamento diário obrigatório antes do PR usando o template padrão | **5** |
| **Autoria + Revisão** | ≥ 30% commits cada (Andrey e Fabrício) + 100% merges via PR aprovado | Branch protection na `main` + divisão de épicos paralelos entre a dupla | **5** |
| **Testes + Mutação** | ≥ 70% *diff coverage* + 5/5 testes falham ao reverter mudança | Testes unitários e de integração mapeados 1:1 com IDs de critérios de aceitação | **9** |
| **Segurança** | Zero segredos (`gitleaks`) e alertas críticos | `.env` fora do Git + job `gitleaks` bloqueante no CI | **3** |

---

## 1. Arquitetura herdada e padrão de spec do projeto

### 1.1. Estrutura atual (não quebrar)

- **Stack:** Python 3.12+, Flask 3, Flask-SQLAlchemy (SQLite), Jinja2, CSS puro (`static/style.css`), `pytest`, `gunicorn`, `render.yaml`.
- **Módulos existentes em `src/app/`:** `__init__.py`, `models.py`, `util.py`, `seed.py`, `auth.py`, `catalogo.py`, `busca.py`, `jogo.py`, `avaliacoes.py`, `listas.py`, `social.py`, `recomendacoes.py`, `admin.py`, `api.py`, `afinidade.py`, `perfil.py`, `cli.py` e `fontes/` (`steam.py`, `arquivo.py`).
- **Convenção de testes em `tests/`:** cada arquivo `tests/test_<spec>.py` nomeia os testes com o ID exato do critério de aceitação (ex.: `test_ava_01_exige_login`, `test_fnt_01_...`).

### 1.2. Padrão obrigatório das novas specs (`SPEC/AAAA-MM-DD-<nome>.md`)

Seguindo o padrão estabelecido no repositório (ex.: `SPEC/2026-10-01-avaliacoes.md`), toda nova spec deve conter exatamente estas seções:

```markdown
# Nome da Funcionalidade (AAAA-MM-DD)

## O quê e por quê
Descrição objetiva do problema de negócio (FDE) ou demanda do cliente e o valor entregue.

## Critérios de aceitação
- SIGLA-01: regra verificável 1 (ex: autenticação, validação de entrada)
- SIGLA-02: regra verificável 2 (ex: persistência, cálculo de domínio)
- SIGLA-03: regra verificável 3 (ex: permissão 403/404, atualização de UI)
...

## Fora do escopo
O que explicitamente não será implementado nesta spec para evitar aumento artificial de escopo.
```

---

## 2. Roadmap de expansão (alinhado ao cliente + meta 100k linhas)

Para atingir **100.000 linhas novas** com valor real de produto (duplicação ≤ 5% e cobertura ≥ 70%), o trabalho está
estruturado em **10 épicos funcionais** (~34 specs principais).

> **Origem das demandas.** Na conversa com a cliente (WhatsApp, 2026-11-10), o pedido explícito foi mais catálogos além da Steam (Épico 2). A frase "seria bom utilizar um mpc e kills" respondeu à pergunta sobre como chegar às 100 mil linhas; a dupla a interpreta como **MCP e skills** (ferramentas de agentes), ainda sem confirmação da cliente. Os épicos 3 e 4 e os clubes são propostas da dupla (FDE), não demandas da cliente.

### Épico 1: Kickoff, infraestrutura e governança 

**Entregas imediatas:**

- [x] Registro do repositório com justificativa de 2–3 linhas.
- [x] Issue *Primeiras impressões* aberta com evidências e entrevista da cliente (`@fenalk`).
- [ ] Proteção da branch `main` (PR obrigatório com 1 aprovação; `push --force` bloqueado).
- [x] Pipeline `.github/workflows/ci.yml` rodando `pytest`, `pytest-cov`, `diff-cover`, `jscpd` e `gitleaks`.
- [x] Novo serviço Web no Render apontando para `amaralandrey/dsai-ap2-GameLog` e URL configurada no *About* do GitHub.
- [x] Hook Git local (`.githooks/commit-msg`) validando a presença dos trailers `Agent:` e `Spec:`.

### Épico 2: Multi-catálogo de jogos — Demanda #1 da cliente

Expansão da arquitetura `src/app/fontes/` (citada explicitamente pela cliente Fernanda). A Epic Games Store entra como loja vinculada, com links obtidos pela RAWG, e não como fonte de importação: a loja não tem API pública que responda a clientes HTTP comuns (decisão na issue #9). A refatoração da fonte Steam para a base comum é justificada na issue #10.

| Spec | Critérios | Entrega |
|---|---|---|
| SPEC-01 | FNT-01..12 | **Base comum das fontes e vínculos multi-loja** (`SPEC/2026-11-15-fontes-multiloja.md`; `src/app/fontes/base.py`) — HTTP comum com novas tentativas e User-Agent do GameLog, tabela `JogoFonte` (um jogo, várias lojas, incluindo a Epic), seletor de fonte e importação em lote em `/admin/importar`. |
| SPEC-02 | GOG-01..15 | **Conectores GOG e RAWG** (`SPEC/2026-11-15-conectores-gog-rawg.md`; `src/app/fontes/gog.py`, `src/app/fontes/rawg.py`) — importação da GOG sem chave, RAWG com `RAWG_API_KEY` e vínculos de loja (Steam, GOG, Epic), normalização de gêneros, plataformas e datas, `--fonte todas` e filtro por loja em `/buscar`. |
| SPEC-03 | MRG-01..15 | **Deduplicação e mesclagem de catálogo** (`SPEC/2026-11-15-deduplicacao-catalogo.md`; `src/app/catalogo_merge.py`) — reconhecimento do mesmo jogo vindo de várias fontes, fila de duplicatas para o admin, mesclagem sem perda de histórico (soft delete e 301) e bloco "Disponível em" na página do jogo. |
| SPEC-03b | SNP-01..09 | **Snapshot multi-loja do seed** (`SPEC/2026-11-15-snapshot-multiloja.md`; `src/app/snapshot.py`) — `flask enriquecer-snapshot` grava no `src/app/dados/jogos.json` os links de GOG e Epic e o id da RAWG de cada jogo (correspondência só pelo id da Steam), para a URL pública mostrar o multi-loja sem rede nem chave no servidor, já que o Render recria o banco a cada deploy. |

### Épico 3: Diário e acompanhamento pessoal — proposta da dupla

O GameLog guarda só o status atual de cada jogo, não quando nem quanto se jogou. O diário é o recurso central do Letterboxd, referência do produto, e é a base de dados do Épico 4 (XP, conquistas, rankings e clubes).

| Spec | Critérios | Entrega |
|---|---|---|
| SPEC-04 | DIA-01..14 | **Diário de jogatina e visibilidade do perfil** (`src/app/diario.py`) — registro de sessões por jogo com data, duração, plataforma, progresso e anotação; registrar sessão de jogo "planejado" muda o status para "jogando"; sessões no feed (`Activity`), horas no perfil e na avaliação ("12h registradas"); perfil público ou privado, que o Épico 4 respeita. |
| SPEC-05 | IMP-01..12 | **Importação de horas e do diário** (`src/app/diario_import.py`) — horas jogadas da Steam pelo SteamID (`GetOwnedGames`, com `STEAM_API_KEY` e perfil público), reaproveitando o conector do Épico 2; CSV do diário com prévia antes de gravar e idempotência por linha; consistência: duração até 24h, sem sobreposição de horários do mesmo usuário e sem datas futuras. |
| SPEC-06 | RET-01..12 | **Retrospectiva e gráficos SVG** (`src/app/graficos_svg.py`, `src/app/retrospectiva.py`) — módulo de gráficos SVG server-side (linha, barra, sparkline, sem JS de build), reutilizado no Épico 4; retrospectiva pessoal com horas por mês, gêneros mais jogados e jogos zerados no ano; comparação de horas e gêneros com quem o usuário segue. |
| SPEC-07 | LSA-01..12 | **Listas inteligentes** (`src/app/listas_inteligentes.py`) — listas por regra ("comecei e larguei há 30 dias", "wishlist disponível na GOG", "zerados este ano"), barra de progresso por status e horas do diário, exportação com os serializadores da API v2 e arrastar e soltar sobre a reordenação que já existe (LIS-07). |

### Épico 4: Engajamento e comunidade — proposta da dupla

Preenche a lacuna de gamificação apontada na AP1 e evolui as conquistas atuais (CON-01..06). Depende do diário (Épico 3). As specs seguem a ordem da tabela: cada uma usa as anteriores.

| Spec | Critérios | Entrega |
|---|---|---|
| SPEC-08 | NTF-01..12 | **Notificações** (`src/app/notificacoes.py`) — avisos de curtida, comentário, novo seguidor, conquista, nível, sequência semanal prestes a quebrar e amigo que zerou um jogo da sua wishlist; preferências por tipo e marcar como lida. |
| SPEC-09 | GAM-01..15 | **Motor de conquistas orientado a eventos** (`src/app/gamificacao.py`) — catálogo de +40 conquistas (crítica, coleção, explorador multi-loja, social, diário: maratonista, 100h registradas, zerou 10 no ano), progressivas, secretas e idempotentes, avaliadas após as ações do usuário e avisadas pela NTF. |
| SPEC-10 | PRG-01..12 | **Níveis, XP e sequências semanais** (`src/app/progressao.py`) — XP por avaliações, sessões do diário e jogos zerados, com teto diário anti-farming; sequência **semanal** (registrar algo na semana) com congelamento; extrato de XP no perfil. |
| SPEC-11 | RNK-01..12 | **Rankings e temporadas** (`src/app/rankings.py`) — jogos zerados, horas registradas, avaliações mais curtidas e XP; escopos "Seguindo" e "Clube" em destaque, global só para perfis públicos; temporadas trimestrais com hall da fama e desclassificação pelo admin. |
| SPEC-12 | CLB-01..14 | **Clubes de jogos** (`src/app/clubes.py`) — grupos temáticos com jogo do mês e meta coletiva medida pelo diário ("membros somam 100h", "5 membros zeram"), ranking do clube (escopo do RNK), conquistas de clube e mural moderado. |

### Épico 5: API v2, MCP e portabilidade

| Spec | Critérios | Entrega |
|---|---|---|
| SPEC-13 | AP2-01..14 | **Tokens pessoais e API REST v2** (`src/app/tokens.py`, `src/app/api_v2.py`) — tokens com escopo (leitura, escrita), guardados só como hash, com expiração, revogação no perfil, rate-limit e auditoria; endpoints paginados de catálogo multi-loja, diário, listas, conquistas e rankings. |
| SPEC-14 | MCP-01..12 | **Servidor MCP do GameLog** (`src/app/mcp/`) — ferramentas `buscar_jogos`, `detalhes_jogo`, `minhas_listas`, `marcar_status`, `avaliar`, `registrar_sessao`, `recomendacoes`, `comparar_gosto` e `disponivel_em` sobre a API v2 autenticada por token, via `flask mcp` (stdio), mais skills curtas de uso (`skills/`). Clubes e listas inteligentes entram quando existirem. |
| SPEC-15 | BKP-01..12 | **Backup e restore portável do banco** (`src/app/backup.py`) — exportação/importação completa do estado do SQLite em JSON assinado via CLI e painel admin (solucionando a volatilidade do banco no plano gratuito do Render), com os serializadores da API v2. |

### Épico 6: Jobs em segundo plano e e-mail — proposta da dupla

Base que preços, notificações e o resumo semanal usam. Fila própria sobre o SQLite, sem framework novo.

| Spec | Critérios | Entrega |
|---|---|---|
| SPEC-16 | JOB-01..12 | **Fila persistente de jobs** (`src/app/jobs.py`) — tarefas gravadas no banco com novas tentativas e espera crescente, agendamento tipo cron, trava contra execução dupla, `flask jobs rodar` e painel admin com histórico e falhas. Sincronização da Steam e snapshot passam a rodar como jobs. |
| SPEC-17 | EML-01..12 | **E-mail** (`src/app/email.py`) — caixa de saída enviada pelos jobs, templates em texto e HTML, resumo semanal (atividade de quem o usuário segue e ofertas da wishlist), preferências e descadastro por link assinado; em dev e testes, saída no console. |

### Épico 7: Preços e ofertas multi-loja — extensão da demanda #1 da cliente

| Spec | Critérios | Entrega |
|---|---|---|
| SPEC-18 | PRC-01..14 | **Preços por loja e histórico** (`src/app/precos.py`) — preço atual e desconto da Steam (`appdetails`, sem chave) e da GOG, histórico de preços, menor preço já visto, atualização por job e preços no snapshot do seed (mesmo esquema do SPEC-03b). |
| SPEC-19 | OFR-01..12 | **Ofertas e alertas** (`src/app/ofertas.py`) — bloco "onde está mais barato" na página do jogo, página de ofertas da wishlist, alerta de promoção e de preço-alvo pela NTF e pelo e-mail. |

### Épico 8: Privacidade e proteção de dados (LGPD/GDPR) — proposta da dupla

Hoje o GameLog não tem política de privacidade nem aceite no cadastro, carrega as capas direto da CDN da Steam (o IP de cada visitante vai para terceiros), guarda `LoginAttempt` e `AuditLog` sem prazo e não oferece exportação, exclusão ou revogação. O épico atende a LGPD e já nasce compatível com a GDPR, que passa a valer se o GameLog passar a oferecer o serviço na União Europeia (por exemplo, com uma versão em inglês); as diferenças (prazos, idade de consentimento) são configuração. A base é um **mapa de dados declarativo**: cada campo é anotado uma vez, e exportação, anonimização, expurgo e registro das operações saem dele.

> Antes de escrever as specs, conferir o texto em vigor da LGPD e as resoluções da ANPD citadas (incidentes, transferência internacional, agentes de pequeno porte). O plano não substitui orientação jurídica.

| Spec | Critérios | Entrega |
|---|---|---|
| SPEC-20 | MAP-01..10 | **Mapa de dados** (`src/app/privacidade/mapa.py`) — anotação de cada campo dos modelos com categoria, finalidade, base legal e retenção; teste que falha se um campo novo não for anotado; página admin de registro das operações (LGPD art. 37, GDPR art. 30) gerada do mapa. |
| SPEC-21 | PRV-01..12 | **Transparência** (`src/app/privacidade/transparencia.py`) — política de privacidade e termos versionados, aceite registrado por versão no cadastro, novo aceite quando o texto muda, canal do encarregado, lista de suboperadores (Render, Steam, GOG, RAWG, e-mail) e das transferências internacionais. |
| SPEC-22 | CNS-01..12 | **Consentimentos** (`src/app/privacidade/consentimentos.py`) — consentimentos granulares e revogáveis (resumo por e-mail, ofertas, recomendações personalizadas), com histórico; consentimento do responsável integrado ao Épico 10. |
| SPEC-23 | DIR-01..14 | **Direitos do titular** (`src/app/privacidade/direitos.py`) — acesso e portabilidade (JSON e CSV, com os serializadores da API v2), correção, exclusão com anonimização (avaliações ficam como "usuário removido") e oposição (tirar as próprias notas das recomendações colaborativas de outros); fila admin de pedidos com prazo configurável (15 dias LGPD, 1 mês GDPR). |
| SPEC-24 | RET-01..12 | **Retenção e minimização** (`src/app/privacidade/retencao.py`) — expurgo por job (Épico 6) de `LoginAttempt`, logs e contas inativas conforme o mapa; proxy e cache das capas para o navegador não falar com a CDN da Steam. |
| SPEC-25 | INC-01..12 | **Incidentes e segurança** (`src/app/privacidade/incidentes.py`) — registro e avaliação de risco de incidentes, modelos de comunicação à ANPD e aos titulares com contagem de prazo, cookies `Secure`/`SameSite` e cabeçalhos de segurança (CSP, HSTS). |
| SPEC-26 | CTA-01..12 | **Segurança da conta** (`src/app/conta.py`) — troca de senha e de e-mail com confirmação, encerramento das outras sessões e 2FA por TOTP com códigos de recuperação. |
| SPEC-27 | BLQ-01..10 | **Bloquear e silenciar** (`src/app/bloqueios.py`) — bloqueio esconde perfil, avaliações e comentários nos dois sentidos e impede seguir; silenciar só tira do feed. |

### Épico 9: Torneios e campeonatos — proposta da dupla

| Spec | Critérios | Entrega |
|---|---|---|
| SPEC-28 | CHV-01..14 | **Algoritmos de chaveamento** (`src/app/chaveamento.py`) — eliminação simples e dupla, suíço e pontos corridos, sementes por ranking, byes e critérios de desempate determinísticos. |
| SPEC-29 | TRN-01..14 | **Torneios** (`src/app/torneios.py`) — criação por usuário ou clube, inscrição, reporte e contestação de resultado, chaveamento em SVG (módulo do SPEC-06) e conquistas de campeão. |

### Épico 10: Proteção de crianças e adolescentes (ECA Digital) — proposta da dupla

O ECA Digital (Lei 15.211/2025) vale para produtos digitais de acesso provável por crianças e adolescentes, e uma rede social de jogos com seguidores, comentários e clubes se encaixa. A política de menores é transversal: entra cedo para que as specs seguintes já nasçam respeitando-a.

> Antes de escrever as specs, conferir o texto em vigor e a regulamentação da ANPD. Verificação de idade por provedor real (documento, gov.br) fica fora do alcance do projeto; a arquitetura deixa o adaptador pronto.

| Spec | Critérios | Entrega |
|---|---|---|
| SPEC-30 | IDA-01..12 | **Verificação de idade** (`src/app/menores/idade.py`) — data de nascimento e faixas etárias, adaptadores de verificação (autodeclaração como primeiro passo, confirmação pelo responsável e provedor externo simulado) e reverificação quando a idade é contestada. |
| SPEC-31 | PMN-01..14 | **Política de menores** (`src/app/menores/politica.py`) — módulo central consultado por perfil, feed, rankings, clubes, ofertas e e-mail: menor nasce com perfil privado, fica fora dos rankings globais e não recebe ofertas nem recomendações com fim comercial. |
| SPEC-32 | RSP-01..12 | **Responsável e supervisão** (`src/app/menores/responsavel.py`) — vínculo responsável ↔ menor de 16 anos com consentimento registrado e painel parental (ver atividade, aprovar seguidores, desligar comentários e clubes). |
| SPEC-33 | CLS-01..10 | **Classificação indicativa** (`src/app/menores/classificacao.py`) — idade mínima da Steam (`required_age`) e ESRB da RAWG mapeados para a ClassInd, aviso e filtro no catálogo e na busca para menores. |
| SPEC-34 | DEN-01..12 | **Denúncia de risco a menores** — evolução do `Report` com categoria de risco a menores, fila prioritária, prazo de resposta e trilha de auditoria da comunicação às autoridades. |

**Ordem de dependências:** Épico 3 (SPEC-04 primeiro) → Épico 4 (NTF → GAM → PRG → RNK → Clubes). O Épico 5 começa pelos tokens e pela API v2 em paralelo ao Épico 4; o MCP e o backup vêm depois da API v2. O Épico 6 vem antes do 7 (preços rodam como job e avisam por NTF e e-mail); no Épico 8, MAP e PRV vêm primeiro e DIR depende da API v2 (exportação) e RET dos jobs; o 9 depende do RNK e dos clubes. **MAP (SPEC-20) e PMN (SPEC-31) são transversais** e entram cedo: toda spec escrita depois deles inclui um critério de respeito ao mapa de dados e à política de menores.

> Os itens cortados da AP1 (REC-07, LIS-07, ADM-09, CAD-04, SOC-09) já estão implementados e testados (`test_rec07_*`, `test_lis07_*`, `test_adm09_*`, `test_cad04_*`, `test_soc09_*`) e não entram em nenhum épico.

---

## 3. Divisão de trabalho entre a dupla (meta ≥ 30% cada)

Para garantir equilíbrio na autoria de commits e revisão cruzada constante de PRs:

| Quem | Épicos | Revisão |
|---|---|---|
| **Andrey Amaral** | Épico 1 (CI/CD, Render, hooks) · Épico 2 (base comum e vínculos multi-loja, conectores GOG/RAWG, deduplicação e snapshot) · Épico 4: conquistas, XP/sequências e rankings (SPEC-09..11) · Épicos 6 a 9 (jobs e e-mail, preços, privacidade LGPD/GDPR, torneios) · Épico 10: verificação de idade e política de menores (SPEC-30..31) | Aprova todos os PRs do Fabrício |
| **Fabrício Assunção** | Épico 3 (diário, importação, retrospectiva SVG e listas inteligentes) · Épico 4: notificações e clubes (SPEC-08 e SPEC-12) · Épico 5 (tokens e API v2, servidor MCP, backup/restore) · Épico 10: responsável, classificação indicativa e denúncias (SPEC-32..34) | Aprova todos os PRs do Andrey |

---

## 4. Estimativa de linhas e cronograma até 2026-11-24

> Dados gerados (`src/app/dados/jogos.json`, produzido por `export-games` e `enriquecer-snapshot`) não contam para a meta: medir com `cloc --git --diff main HEAD --exclude-dir=dados`.

### 4.1. Calibração

O Épico 2 (4 specs, 51 critérios) gerou ~3.100 linhas novas em ~1,5 dia: **~60 linhas por critério**, ~780 por spec e ~2.000 linhas por dia por pessoa trabalhando com agente. Funcionalidades com algoritmos ou protocolos (chaveamento, federação, jobs) rendem mais por critério (150 a 300).

| Bloco | Critérios | Linhas novas estimadas |
|---|---|---|
| Épico 2 (feito) | 51 | ~3.100 |
| Épicos 3 a 5 | 154 | ~9.000 a 15.000 |
| Épicos 6, 7 e 9 | ~78 | ~7.000 a 11.000 |
| Épico 8 (privacidade) | ~94 | ~7.000 a 10.000 |
| Épico 10 (ECA Digital) | ~60 | ~5.000 a 7.000 |
| **Total** | **~437** | **~31.000 a 46.000** |

A meta de 100 mil não cabe no prazo no ritmo real do projeto. A dupla mira 30 a 40 mil linhas legítimas e assume o risco no critério de tamanho, priorizando os critérios de testes, mutação, specs e CI.

### 4.2. Cronograma

| Dias | Andrey | Fabrício |
|---|---|---|
| 16–17/11 | Mapa de dados (SPEC-20) · Transparência (SPEC-21) · GAM (SPEC-09) | Diário (SPEC-04) · Importação (SPEC-05) |
| 18/11 | Verificação de idade (SPEC-30) · Política de menores (SPEC-31) · PRG (SPEC-10) | Retrospectiva SVG (SPEC-06) · NTF (SPEC-08) |
| 19/11 | RNK (SPEC-11) · Jobs (SPEC-16) · E-mail (SPEC-17) | Listas inteligentes (SPEC-07) · Tokens e API v2 (SPEC-13) |
| 20/11 | Consentimentos (SPEC-22) · Direitos do titular (SPEC-23) · Retenção (SPEC-24) | MCP (SPEC-14) · Responsável (SPEC-32) |
| 21/11 | Preços (SPEC-18) · Ofertas (SPEC-19) · Incidentes (SPEC-25) | Clubes (SPEC-12) · Classificação indicativa (SPEC-33) · Denúncias (SPEC-34) |
| 22/11 | Segurança da conta (SPEC-26) · Bloquear (SPEC-27) · Torneios (SPEC-28..29) se houver tempo | Backup (SPEC-15) |
| 23/11 | Revisão cruzada, mutação, jscpd, cobertura e merges | idem |
| 24/11 | Entrega: produção servindo a `HEAD` da `main` | |

**Corte se apertar** (o que cai primeiro): torneios (Épico 9) → bloquear e silenciar (SPEC-27) → listas inteligentes (SPEC-07) → backup (SPEC-15) → incidentes (SPEC-25).

**Fora do plano:** federação com ActivityPub (alto risco no prazo; se voltar, menores ficam fora dela por PMN).

---

## 5. Rito diário obrigatório (checklist de execução)

1. **Criar branch:**
   ```bash
   git checkout main && git pull && git checkout -b feat/<spec>
   ```
2. **Commitar a spec primeiro:** criar `SPEC/AAAA-MM-DD-<spec>.md` e commitar isoladamente **antes de qualquer código**.
3. **Codar em commits pequenos (< 400 linhas):** dividir por camada (`model` → `service` → `route` → `template` → `test`)
   em pelo menos 2 dias distintos por semana, sempre incluindo os trailers no final da mensagem:
   ```text
   feat(modulo): descricao objetiva da alteracao

   Agent: <ferramenta/modelo>
   Spec: SPEC/AAAA-MM-DD-<spec>.md
   ```
4. **Validar qualidade localmente:** rodar `./dev.sh test` (garantindo cobertura do diff ≥ 70%), verificar duplicação
   (`jscpd` ≤ 5%) e ausência de segredos.
5. **Registrar sessão e custo no mesmo dia:** criar `prompts/sessoes/AAAA-MM-DD-<andrey|fabricio>-<tema>.md` com o
   resumo dos prompts e o total de **tokens / custo consumido**.
6. **Integrar via PR:** abrir Pull Request para a `main`, aguardar CI verde e aprovação do parceiro de dupla.
