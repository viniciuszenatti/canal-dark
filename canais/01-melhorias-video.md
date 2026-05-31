---
projeto: canal-dark
tipo: canal
tema: melhorias-video
atualizado: 2026-05-30
---

# 🎬 Canal 01 — Melhorias de Vídeo

> **Objetivo:** elevar a qualidade técnica/visual do `short_factory.py` — legenda, b-roll,
> montagem, voz. Tudo que faz o vídeo prender mais e parecer menos "robô".

**Quando usar:** mexer em legenda, busca de b-roll, FFmpeg, voz, imagens reais/IA, Ken Burns.

## Estado atual (o que JÁ existe no `short_factory.py`)

Mais avançado do que parece — antes de pedir algo, confira se já não está feito:

- **Legenda**: SRT por word-timestamp do edge-tts; `PlayResX/Y` fixos em 1080×1920 (FontSize previsível);
  quebra de linha (`_wrap_subtitle_text`, ~30 char/linha, máx 2 linhas); fonte Montserrat com fallback Arial.
- **`SUB_POS`** = `lower` (Alignment=2, MarginV=220, longe da UI) ou `center` (Alignment=5, centro vertical).
- **`SUB_STYLE`** = `clean` (2 linhas legíveis, FontSize=18) ou `punchy` (1-3 palavras, FontSize=22).
- **B-roll Pexels**: scoring determinístico por slug, veto de `avoid_terms`/pessoas, dedup por `video_id`, fallback escalonado.
- **B-roll por IA grátis**: `_fetch_pollinations` (Pollinations.ai, flux, 1080×1920, sem chave) via `--broll-source ai`.
- **`REF_DIR`**: pasta de imagens (.jpg/.png/.webp) forçadas como b-roll nas primeiras N linhas → Ken Burns.
- **Montagem**: 9:16, concat CFR 30fps, narração + música opcional (-volume), legenda queimada.

## Backlog (ideias abertas — confirmar o que falta)

- [ ] **Karaokê real (`\k`)**: hoje "punchy" só encurta cues. Realce da palavra ativa exige ASS nativo
      com `\k{dur}` por token (timestamps de palavra já existem). Marcado como PENDENTE no código.
- [ ] **Imagem real automática**: plugar 1-2 fontes do canal **00** (Wikimedia/Openverse) como provider,
      com cache + atribuição + dedupe; cair pro Pexels/Pollinations quando não achar.
- [ ] **Mix de b-roll**: alternar vídeo (Pexels) + imagem (real/IA) na mesma peça pra ritmo.
- [ ] **Transições/corte a cada 1-3s** (técnica de retenção do `00-tecnicas-shorts-comum.md`).
- [ ] **Voz**: avaliar ElevenLabs (stub pronto) vs edge-tts; testar vozes por persona do nicho.

## Cuidados (gotchas já sangrados — ver memória de pipeline)

- edge-tts ≥ 7.2 (6.x dá HTTP 403); SubMaker .get_srt() quebrado → montar SRT dos word-timestamps.
- FFmpeg `subtitles` no Windows: `C:` quebra o parser → rodar com cwd na pasta do .srt (já feito).
- Pexels tem fps variável → re-encodar 30fps CFR antes do concat (já feito).

## Links

Fontes de imagem → [[00-pesquisa]]. Prompt do `broll_query`/`visual_context` → [[02-melhorias-prompt]].
Envio de referência que vira `REF_DIR` → [[03-melhorias-telegram]].
