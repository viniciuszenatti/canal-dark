---
projeto: canal-dark
tipo: canal
tema: melhorias-prompt
atualizado: 2026-05-30
---

# ✍️ Canal 02 — Melhorias de Prompt

> **Objetivo:** afinar os prompts que geram o conteúdo — roteirista, trend scout, guardrail —
> e o sistema de `visual_context`. É aqui que mora a qualidade do roteiro (= sobrevivência do canal).

**Quando usar:** roteiro genérico/fraco, b-roll incoerente, guardrail errando risco, voz do nicho off.

## Estado atual

- **`SCRIPT_SYSTEM_PROMPT`** (em `short_factory.py`): roteirista de Shorts, ~180-220 palavras, hook em ≤3s,
  UM ângulo forte, estrutura hook→contexto→insight→takeaway→CTA. Saída JSON validada.
- **`visual_context` ("visual bible")**: o prompt obriga 1 objeto global (setting/era/mood/palette/
  subject_mode/anchor_terms/avoid_terms) que governa TODO b-roll. `broll_query` deve ser simbólico
  (lugar/objeto/atmosfera), nunca pessoa real nomeada — defesa de copyright + coerência visual.
- **Injeção de nicho** (`_load_niche_context`): puxa `02-roteiro-e-linguagem.md` + `01-conteudo-e-pesquisa.md`
  + `00-tecnicas-shorts-comum.md` do nicho ativo (`CANAL_DARK_NICHE`) pro system prompt. Roteiro em INGLÊS.
- **Prompts auxiliares** em `prompts/`: `roteirista.md`, `trend_scout.md`, `guardrail.md` (usados no n8n).

## Backlog

- [ ] **Persona nomeada por nicho** (anti "inauthentic content"): dar nome/voz consistente ao narrador.
- [ ] **Few-shot por nicho**: incluir 1-2 exemplos de roteiro-ouro (estilo MrBallen/Ohara/LEMMiNO) no prompt.
- [ ] **Guardrail mais fino**: alinhar dimensões (misinformation, sensitive, platform_policy) às regras
      duras de cada nicho (ex.: conspiracy NUNCA saúde/eleição/negação; true-crime "alleged").
- [ ] **broll_query → query de imagem real**: quando o tema tem pessoa/evento real, gerar termo de busca
      pra Wikimedia/Openverse (handoff p/ canal **01**), mantendo a regra de não nomear no b-roll genérico.
- [ ] Medir: roteiro do prompt atual vs versão com few-shot — retenção/qualidade.

## Princípio

O prompt é o ativo mais barato de melhorar e o de maior alavanca. Roteiro único e revisável > polimento técnico.

## Links

Nicho ativo → [[04-nicho-decisao]]. Uso do `visual_context` no b-roll → [[01-melhorias-video]].
Guardrail no fluxo → [[06-infra-n8n-servidor]].
