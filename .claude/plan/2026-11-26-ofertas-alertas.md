# Plano — OFR: ofertas e alertas de preço (2026-11-26)

Spec: `SPEC/2026-11-15-ofertas-alertas.md` (OFR-01..12), já na main. Branch `feat/ofertas-alertas`.

## O que já existe e é reaproveitado
- PRC: `precos.atuais`, `atual`, `menor_preco`, `reais`, `ORDEM_DAS_LOJAS`; o job `precos` já junta em `mudaram`
  os jogos com linha nova e deixou o gancho da OFR.
- NTF: tipos `oferta` e `preco_alvo` já existem; `notificar()` é o único ponto de entrada.
- PMN: ações `ver_ofertas` e `alerta_preco` (só adulto) e o filtro que barra `oferta`/`preco_alvo` para menores.
- CNS: finalidade `ofertas` (só adultos), `tem_consentimento`, `pedido`, partial `_pedido_consentimento.html`.
- EML: `NAO_TRANSACIONAIS["oferta"]` e `CONSENTIMENTO_DO_TIPO` já tratam o modelo `oferta` e a supressão no envio.

## Camadas
1. **model** — `AlertaPreco(user_id, game_id, preco_alvo_centavos, criado_em, disparado_em)`, único por pessoa e
   jogo; anotação no `privacidade/mapa.py` (atividade, base consentimento `ofertas`); `alerta_preco` entra no
   conjunto de tabelas do backup (BKP-03).
2. **serviço** — `src/app/ofertas.py`: `mais_barato` (OFR-01), `consulta` das ofertas da wishlist com filtros
   (OFR-02/03), `centavos` (reais → centavos), `avaliar(game_ids, desde)` chamado pelo job `precos` (OFR-05/06/11),
   `do_resumo` (seção do resumo semanal, OFR-07). Consentimento em lote: `consentimentos.vale` (antes `_vale`).
3. **rotas** — `/ofertas`, `/ofertas/alertas`, `POST /jogos/<slug>/alerta`, `POST /ofertas/alertas/<id>/apagar`;
   menor recebe 403 com a mensagem da spec (OFR-08).
4. **e-mail** — `notificar` passa o contexto da oferta a `avisar_por_email`, que usa o modelo `oferta` (OFR-09).
5. **templates** — bloco "Onde está mais barato" e formulário de alerta na página do jogo; `ofertas/lista.html`,
   `ofertas/alertas.html`; `email/oferta.*`; seção de ofertas no resumo semanal; link "Ofertas" no menu.
6. **testes** — `tests/test_ofertas.py`, um ou mais por critério, sem rede.
7. **docs** — README (seção e tabela spec → código → testes) e registro da sessão.

## Premissas
- OFR-02: cada jogo aparece uma vez, com a oferta mais barata entre as lojas que passam nos filtros (empate: Steam).
- OFR-05: "último desconto avisado" é o maior desconto da promoção corrente da loja (linhas desde o último
  desconto 0); só conta linha gravada nesta coleta, para outra loja mudar não reavisar.
- OFR-11: o orçamento conta as consultas da avaliação; cada `notificar()` de quem recebe o aviso fica fora, porque
  é o ponto único de NTF e cresce com quem é avisado, não com quem só tem o jogo.

## Banco
Tabela nova: `db.create_all()` cria sozinho, sem `./dev.sh reset`.
