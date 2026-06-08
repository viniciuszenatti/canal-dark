---
projeto: canal-dark
tipo: canal
tema: melhorias-prompt
atualizado: 2026-05-31
---

# ✍️ Canal 02 — Melhorias de Prompt

> **Objetivo:** afinar os prompts que geram o conteúdo — roteirista, trend scout, guardrail —
> e o sistema de `visual_context`. É aqui que mora a qualidade do roteiro (= sobrevivência do canal).

**Quando usar:** roteiro genérico/fraco, b-roll incoerente, guardrail errando risco, voz do nicho off.

## Estado atual

- **`SCRIPT_SYSTEM_PROMPT`** (em `short_factory.py`): roteirista de Shorts, **~120-150 palavras / 45-60s**
  (encurtado em **31/05** — a pesquisa da Fase 1 mostrou sweet spot 25-45s; antes era 180-220/90s), hook em ≤3s,
  UM ângulo forte, 6-8 lines, saída JSON validada. **Nicho-agnóstico**: defere ao "NICHE PLAYBOOK" que
  `_load_niche_context()` prefixa (puxa `02-roteiro-e-linguagem.md` + `01-conteudo-e-pesquisa.md` +
  `00-tecnicas-shorts-comum.md` do `CANAL_DARK_NICHE`). Roteiro em INGLÊS, playbook em PT.
- **✅ (2026-05-31) Ciclo de pesquisa de nichos concluído (Fases 1-4):**
  - **Fase 1** — `00-tecnicas-shorts-comum.md` reescrito com técnicas de engajamento **verificadas** (cada fonte
    com URL aberta; fato × hipótese separados): hook 0-3s, cadência de corte, loop=view, **legenda no terço
    central** (YT tapa 400px de baixo), etc.
  - **Fase 2** — banco `nichos/<nicho>/_referencias.md` por nicho (30 referências verificadas: criadores de
    estilo + fontes primárias) + dossiê de gênero (ideias/tom/riscos).
  - **Fase 3** — cada `02-roteiro-e-linguagem.md` ganhou **persona nomeada**, **beat-map → JSON**, **catálogo
    de ganchos**, **word bank**, **regras de risco duras embutidas** e **1-2 roteiros-ouro (few-shot)**:
    - true-crime → **The Cold File** / narrador **Marcus Vale** (`en-US-GuyNeural`)
    - conspiracy → **The Quiet Hour** / **Silas Vance** (`en-GB-RyanNeural`)
    - one-piece → **Poneglyph Theory** / **"Cobb"** (`en-US-AndrewNeural`) + override duro de copyright no b-roll
  - **Fase 4** — validação **v0×v1** (mesmo tema por nicho): v1 venceu nos 3 — persona, contraponto cético
    (conspiracy) e, crítico, **avoid_terms/b-roll limpos no one-piece** (zero personagem/anime na imagem).
- **`visual_context` ("visual bible")**: 1 objeto global (setting/era/mood/palette/subject_mode/anchor_terms/
  avoid_terms) governa TODO b-roll. `broll_query` simbólico (lugar/objeto/atmosfera), nunca pessoa real nomeada.
- **Prompts auxiliares** em `prompts/`: `roteirista.md`, `trend_scout.md`, `guardrail.md` (usados no n8n).
- **✅ (2026-06-02) `broll_kind` por shot — SÓ one-piece (handoff p/ canal 01):** cada item de `lines[]` agora
  carrega `broll_kind ∈ {character, scenery, object}` pra metade 2 rotear a FONTE da imagem (character → render
  IA FLUX; scenery/object → pode foto real PD/CC). Regra dura + lista de IP no `06-visual-broll.md`. Validação
  em `_parse_and_validate_script` é **niche-gated** (só roda no one-piece; ausente/inválido → `character`) + **rede
  de segurança** `OP_IP_TOKENS`: se o `broll_query` nomeia ícone IP (Poneglyph, Thousand Sunny, brasão WG…), força
  `character` mesmo que o LLM diga scenery/object. Campo NÃO existe nos outros nichos. Antes/depois e contrato no
  report da sessão. **Reforça o aprendizado Fase 4:** em amostras o Gemini ora emitia o campo com 1-2 erros pro
  lado inseguro (Poneglyph→object), ora omitia tudo — em ambos os casos o resultado é seguro (default+net=`character`),
  mas casos estilizados/ambíguos (ex.: "D" inicial em tábua) ainda precisam do humano no Checkpoint #1.

## ⚠️ Aprendizado da Fase 4 (o prompt melhora a FORMA, não garante o FATO)
- O Gemini ainda **erra fatos** do caso (ex.: detalhes do Bear Brook) e nem sempre obedece 100% o checklist de
  risco embutido (um roteiro vazou "children/dismembered" no true-crime). **Logo: revisão humana + fontes do
  `_referencias.md` + guardrail continuam obrigatórios** — não são opcionais.
- O modelo ainda **estica a duração** às vezes (conspiracy saiu 9 lines). Aceitável; apertar se incomodar.

## Backlog

- [x] **Persona nomeada por nicho** (anti "inauthentic content") — feito na Fase 3 (3 personas).
- [x] **Few-shot por nicho** — feito (1-2 roteiros-ouro por nicho no `02-roteiro`).
- [ ] **Guardrail mais fino**: alinhar dimensões (misinformation, sensitive, platform_policy) às regras
      duras de cada nicho — e checar especificamente o que o prompt não garante (menor/gore no true-crime;
      "confirmed" sem capítulo no one-piece; desinformação no conspiracy).
- [ ] **broll_query → query de imagem real**: quando o tema tem pessoa/evento real, gerar termo de busca
      pra Wikimedia/Openverse (handoff p/ canal **01**).
- [ ] (opcional) Apertar a duração se o modelo estourar 6-8 lines com frequência.

## Princípio

O prompt é o ativo mais barato de melhorar e o de maior alavanca. Roteiro único e revisável > polimento técnico.

## Links

Nicho ativo → [[04-nicho-decisao]]. Uso do `visual_context` no b-roll → [[01-melhorias-video]].
Guardrail no fluxo → [[06-infra-n8n-servidor]]. Bancos por nicho → `nichos/<nicho>/_referencias.md`.
