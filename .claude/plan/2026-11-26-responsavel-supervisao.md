# Plano — RSP: responsável e supervisão parental (2026-11-26)

Spec: `SPEC/2026-11-15-responsavel-supervisao.md` (RSP-01..12), já na main. Branch `feat/responsavel-supervisao`.

## O que já existe e é reaproveitado
- IDA: `aguarda_responsavel`, adaptador `Responsavel` (confiável, sem tela), `registrar` (IDA-06, IDA-08).
- PMN: `pode` com as ações sociais e os pontos de extensão de RSP-07 em `_comentar`, `_torneio` e nos clubes.
- CNS-09: `conceder_pelo_responsavel` e `revogar_pelo_responsavel` esperam `e_responsavel_vinculado`.
- TRN: inscrição `pendente_responsavel` e `autorizar_inscricao` (RSP-08).
- BLQ: `bloquear(..., criado_por=)` e o desbloqueio só por quem criou (BLQ-08).
- RSN: `recuperacao.responsaveis_ativos` (hoje vazio). RTN: `pendentes_vencidas` já apaga a pendente de 30 dias.
- `/conta/pendente` é rota livre da idade; o convite sai dela.

## Camadas
1. **model** — `VinculoResponsavel` e `ControleParental` em `models.py`, anotados no mapa (proteção de menores,
   obrigação legal) e no conjunto de tabelas do backup.
2. **serviço** — `src/app/menores/responsavel.py`: convite assinado (7 dias), limite de 3 por dia, aceite com
   `registrar` pelo adaptador `responsavel`, recusa, vínculos ativos, controles, revogação, encerramento aos 16.
   `e_responsavel_vinculado`, `responsaveis_ativos` e a política (`controle_liberado`) passam a consultar aqui.
3. **rotas** — convite em `/conta/pendente/convite` (também para o segundo responsável, pela página
   `/conta/responsaveis` do menor já vinculado); aceite em `/responsavel/convite/<token>`; painel
   `/responsavel` e `/responsavel/<id>` (atividade, métricas, controles, resumo, pedidos de seguir, torneios,
   interações, remover seguidor, bloquear, desvincular). Menor no painel: 403; outro adulto: 404.
4. **job** — tarefa `responsavel_idade` (diária) encerra o vínculo de quem fez 16 e avisa os dois (RSP-11).
5. **templates** — pendente com o formulário e a data limite; aceite; painel; e-mail `convite_responsavel`.
6. **testes** — `tests/test_responsavel.py`, `test_rsp_NN_*`; ajustes nos testes que usavam conta de 12 a 15
   vinculada comentando, entrando em clube ou em torneio sem controle ligado (os controles nascem desligados).
7. **docs** — README e registro da sessão.

## Premissas
- Os controles valem só na faixa de 12 a 15 (com vínculo); aos 16 a política de PMN volta a decidir sozinha.
- Comentários desligados negam comentar nas avaliações do menor para todos, e o menor não comenta (nota de PMN).
- A atividade do painel junta o `Activity` do menor, os comentários dele, os clubes em que entrou e as inscrições em
  torneio; a paginação é em memória (o volume de uma conta de menor é pequeno).
- O responsável informa a data de nascimento no aceite (a data do menor nunca sai do servidor, IDA-12).
