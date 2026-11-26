# CLAUDE.md — registros de sessão

Toda sessão de agente que gera commit tem um arquivo em `prompts/sessoes/`, commitado no mesmo PR do trabalho
(`docs(sessoes): <tema>`, `Spec: none`). Sem o registro, o dia zera o critério de sessões (plan-ap2, regra 6).

- **Nome:** `AAAA-MM-DD-HHMM-<autor>-<ferramenta>.md`, com a hora de início da sessão no fuso de Belém;
  `<autor>` é `andrey` ou `fabricio`; `<ferramenta>` é `claude-code`, `copilot`, `claude`…
- **Sessão que continua no mesmo PR** (review, CI, nova rodada): atualiza o mesmo arquivo. Trabalho de outro
  PR, mesmo na mesma conversa, ganha arquivo próprio, com a hora em que esse pedido começou.
- **Nada sensível:** sem segredo, token, senha, e-mail, URL com query ou conteúdo de `.env`. Mascare (`****`).
- **Fatos, não promessas:** o que rodou e deu verde, o que ficou sem conferir e por quê.

## Modelo

```markdown
# Sessão <Ferramenta>: <tema em uma linha> (AAAA-MM-DD)

**Ferramenta:** <ferramenta> (<modelo>, `<id do modelo>`), <subagentes e skills, se houver>
**Quem conduziu:** <nome>
**Ponto de partida:** <branch e commit de onde partiu>

## Custo

<de onde veio a medida e a regra de contagem>

| Tipo de token | Tokens | Preço (US$/1M) | Custo (US$) |
|---|---:|---:|---:|
| Entrada | | | |
| Saída | | | |
| Leitura de cache | | | |
| Escrita de cache (TTL 5 min) | | | |
| Escrita de cache (TTL 1h) | | | |
| **Total** | | | **≈ <US$>** |

<número de requisições, divididas entre sessão principal e subagentes; o que a conta não inclui>

## Prompts

1. <prompt do usuário, literal, com dado sensível mascarado>

## Como foi feito

1. **<etapa>.** <o que foi feito, o que foi conferido e o resultado>

## Artefatos

| Item | Conteúdo | Situação |
|---|---|---|
| <PR, branch ou arquivo> | <o que é> | <aberto, mergeado, a commitar> |

## Decisões

- **<tema>:** <o que foi decidido e por quê>

## Pontos de atenção

- **<tema>:** <pendência, risco, `./dev.sh reset` necessário ou próximo passo>
```

## Como medir o custo (Claude Code)

- Os logs ficam em `~/.claude/projects/<pasta-do-repo>/<id-da-sessão>.jsonl`, e os dos subagentes em
  `<id-da-sessão>/subagents/*.jsonl`. Uma sessão retomada pode ter mais de um `.jsonl`.
- Some só as linhas `type == "assistant"` com `message.usage`. Cada resposta conta uma vez: o maior `usage` por
  `message.id`, mesmo quando o id aparece em mais de um arquivo.
- Separe entrada (`input_tokens`), saída (`output_tokens`), leitura de cache (`cache_read_input_tokens`) e
  escrita de cache por TTL (`cache_creation.ephemeral_5m_input_tokens` e `ephemeral_1h_input_tokens`).
- Use o preço da API do modelo que respondeu, o mesmo dos registros anteriores, e diga que a conta não inclui as
  requisições que escreveram o próprio registro.
- Ferramenta sem log de tokens (Copilot, chat web): registre o que ela mostra (créditos, requisições) e a fonte.
