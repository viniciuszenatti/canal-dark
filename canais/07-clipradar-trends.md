---
projeto: canal-dark
tipo: canal
tema: clipradar-trends
atualizado: 2026-05-30
---

# 📡 Canal 07 — ClipRadar · Trends · ML

> **Objetivo:** descobrir o que bomba (scanner de tendências) e, no futuro, pontuar cortes/temas
> automaticamente. Hoje é heurística madura; ML é fase posterior.

**Quando usar:** rodar scanners de tendência, ajustar scoring de cortes, planejar dataset/ML.

## Estado atual (`clipradar/`, v0.5.1)

- **Scanners de tendência**: Google Trends RSS, YouTube trending (scraping), RSS news, High-Attention.
- **Analisador de cortes** (~1.500 linhas): Whisper local (grátis) + ~33 subscores (hook, narrativa,
  standalone, densidade...) + score heurístico ponderado. Filtro de prioridade ANTES do Whisper (economiza).
- **Sinais de copyright** (ex.: MrBeast → `needs_permission_review`).
- LLM Selector opcional (Gemini/GPT) como baseline alternativo.

## Crítica honesta (de `FEEDBACK-CLIPRADAR.md`)

- Engenharia madura, mas o **export joga fora o vetor de features** (salva só score+texto) — conserto barato.
- **Base de calibração minúscula e enviesada**: 4 clipes, todos "good", zero negativos.
- ~11 pesos + ~20 penalidades cravados na mão (baseline pro ML futuro).
- **Pergunta estratégica:** um LLM puro com a transcrição inteira já não acha o melhor corte?
  Medir heurística vs LLM ANTES de treinar ML caseiro (que precisa de centenas/milhares de exemplos).

## Backlog

- [ ] Salvar vetor de features completo no export (destrava treino).
- [ ] Volume de avaliações balanceadas (bons + ruins + needs_adjustment).
- [ ] Definir métrica de sucesso (retenção? views? aprovação humana?).
- [ ] Baseline LLM (Gemini/GPT) vs heurística — decidir se ML caseiro vale.
- [ ] Conectar saída dos scanners → temas no canal **00**.

> Nota: o modelo do projeto MUDOU (de clipping p/ roteiro próprio narrado). O ClipRadar é mais útil
> agora como **radar de TEMAS/tendências** do que como cortador de vídeo de terceiros.

## Links

Temas pra roteiro → [[00-pesquisa]]. Decisão de nicho → [[04-nicho-decisao]].
