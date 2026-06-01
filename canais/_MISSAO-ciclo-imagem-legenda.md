---
projeto: canal-dark
tipo: missao-reutilizavel
tags: [canal-dark, imagem, legenda, qualidade, ciclo-de-melhoria]
atualizado: 2026-05-31
---

# MISSÃO — Ciclo de melhoria de imagem/vídeo & legenda

> Cole este texto no chat (na pasta `C:\Users\aless\canal-dark`) para rodar o ciclo de
> melhoria de QUALIDADE VISUAL guiado pela sua avaliação. Faça depois da de nichos/prompts
> ([[_MISSAO-pesquisa-nichos]]) — primeiro melhora o roteiro, depois o visual.

MISSÃO: Elevar a qualidade de IMAGEM/VÍDEO e LEGENDA do pipeline e montar um CICLO DE
MELHORIA guiado pela MINHA avaliação. Você é o cd-gerente (PM): planeje e DELEGUE aos
agentes certos — não implemente na mão.

CONTEXTO:
- Pipeline local funciona: short_factory.py (roteiro → voz Edge-TTS → b-roll/imagem →
  legenda → short.mp4 9:16). image_providers.py tem 3 providers de imagem: AI Horde +
  Pollinations + Pexels. Legenda já é parametrizável (SUB_STYLE / SUB_POS).
- NICHO DECIDIDO (31/05): rodamos os 3 como CANAIS SEPARADOS — true-crimes,
  conspiracy-theories, one-piece — cada um com persona, voz e b-roll PRÓPRIOS.
- COPYRIGHT (regra atual, afrouxada): b-roll PODE nomear lugar, evento e figura pública
  real. EXCEÇÃO DURA — ONE PIECE: b-roll SÓ IA-gerado/genérico, NUNCA frame do anime ou
  do mangá (Content ID Toei/Shueisha).
- Skills disponíveis: /cd-revisar-roteiro (checkpoint de revisão) e /cd-short (pipeline ponta-a-ponta).

EQUIPE:
- cd-pesquisa        → comparação de providers/ferramentas, grátis × pago
- cd-melhorias       → QUALIDADE: tuning de prompt/visual_context/b-roll/legenda/voz (parâmetro/prompt, não código novo)
- cd-desenvolvimento → implementa FEATURE nova ou corrige BUG (short_factory.py, image_providers.py)
- cd-testes          → roda o pipeline, gera os vídeos de teste e valida a saída (QA)

FASE 1 — PESQUISA DE FERRAMENTAS (delega: cd-pesquisa)
(a) qual dos 3 providers rende melhor POR NICHO; (b) como montar prompts de geração melhores
a partir do visual_context; (c) ferramenta image-to-prompt GRÁTIS pra extrair estilo das
referências (Gemini AI Studio aceita vídeo). Sempre GRÁTIS × PAGO com custo real.
⏸️ CHECKPOINT: mostrar a comparação. ▶ Próximo passo: com OK, Fase 2.

FASE 2 — PLANO DE MELHORIA (consolida: cd-gerente)
Lista de mudanças candidatas em image_providers.py / short_factory.py / SUB_STYLE pra:
(1) imagem com mais contexto e qualidade; (2) legenda MAIOR e mais legível. Marcar cada item
[bug]/[feature]/[tuning] e qual agente faria. NÃO implementar.
⏸️ CHECKPOINT: aprovar a lista. ▶ Próximo passo: com OK, Fase 3a.

FASE 3a — PILOTO DE 3 VÍDEOS (executa: cd-testes)
3 vídeos com a ferramenta ATUAL (v1): 1 true-crime, 1 conspiracy, 1 one-piece. Roteiros
diferentes. Entregar no formato da tabela da Fase 4.
⏸️ CHECKPOINT: aprovar seguir pras 15. ▶ Próximo passo: com OK, Fase 3b.

FASE 3b — 15 VÍDEOS v1 (executa: cd-testes)
15 shorts: 5 true-crime, 5 conspiracy, 5 one-piece, todos v1. Roteiros DIFERENTES dentro de
cada nicho (risco #1). Nome {nicho}-{01..05}. Salvar em "video testes/review-<data>/".
⚠️ RATE LIMIT (AI Horde/Pollinations/Pexels): gerar EM LOTES com pausa.
⚠️ HONESTIDADE: se um vídeo falhar, REPORTAR — nunca fingir.
▶ Próximo passo: montar a tabela da Fase 4.

FASE 4 — TABELA DE AVALIAÇÃO (monta: cd-gerente)
Tabela com TODOS os vídeos + salvar idêntica em "video testes/review-<data>/review_index.md":
| # | Nicho | Tema do roteiro | Arquivo | Duração | Nota (1–5) | O que melhorar |
"Nota" e "O que melhorar" EM BRANCO — o Vinicius preenche e responde no chat por número.
▶ Próximo passo: aguardar avaliação → Fase 5.

FASE 5 — LER E IMPLEMENTAR (processa: cd-gerente)
1. Agrupar feedback por tema (legenda/imagem/voz/roteiro/ritmo).
2. Cruzar com a Fase 2 e PRIORIZAR (mais frequente + maior impacto).
3. Delegar: bug/feature → cd-desenvolvimento · tuning/roteiro/persona/visual → cd-melhorias.
4. Regenerar SÓ os afetados e devolver v2 na MESMA tabela (coluna "v2") → novo ciclo.
5. Documentar no [[Canal Dark — MOC]] e [[Decisões Travadas]].
▶ Próximo passo: novo ciclo sobre a v2.

REGRAS: GRÁTIS PRIMEIRO; HONESTIDADE (falha = reportar); RISCO #1 (roteiros únicos, persona
coerente, nada de molde); ESCOPO = tuning de prompt/parâmetro, não treino. Terminar cada fase
com "▶ Próximo passo".
