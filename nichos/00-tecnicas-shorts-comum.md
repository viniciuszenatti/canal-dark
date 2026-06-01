---
projeto: canal-dark
tipo: tecnicas-comuns
tags: [canal-dark, roteiro, shorts, retencao, engajamento]
atualizado: 2026-05-31
---

# Técnicas de Shorts — comum a todos os nichos

O que vale pra **qualquer** nicho do canal. Os docs de roteiro de cada nicho só adicionam o que é específico. Base do projeto: [[project-canal-dark]].

> **Como ler as marcas** (pesquisa verificada em 2026-05-31, cada fonte foi aberta e conferida):
> - ✅ **FATO** — sustentado por dado/fonte forte que abriu e bate.
> - 🟡 **HIPÓTESE** — boa prática difundida (guia/criador), sem dado duro medido.
> - ⚠️ **número frouxo** — o princípio vale, mas o número exato vem de blog-vendor (OpusClip/Virvid etc.) sem estudo primário, ou não bateu na verificação. Use como direção, não como verdade.
> Lista do que **não** se confirmou está no fim ([§ Honestidade](#-honestidade--o-que-não-se-confirmou)).

## A métrica que manda: retenção
O algoritmo de Shorts/Reels/TikTok promove o que as pessoas **assistem até o fim** e **re-assistem**. Tudo abaixo serve a isso.
- ✅ **50–60% de quem abandona o Short sai nos primeiros 3 segundos.** O hook é onde o jogo é ganho. *(OpusClip, 2025)*
- ⚠️ **Benchmark de retenção:** Shorts virais (1M+ views) sustentam ~**76%** de retenção média; abaixo de **50%** o algoritmo praticamente para de distribuir. *(Virvid, 2025 — número de 3º, não oficial do YouTube)*
- ⚠️ **Alvo prático por duração:** >90% de retenção em vídeos até 30s; 75–85% em 45–60s; abaixo de 70% derruba distribuição. Retenção >100% (loop/rewatch) = sinal de viral. *(Fluxnote, 2026)*

## 1. O gancho (0–3s) — onde 80% do jogo é ganho
A pessoa decide em ~2s se desliza. Regras:
- **Comece pelo ápice**, não pela introdução. Nada de "hey guys, welcome". Já entra no fato mais forte.
- ✅ **Quem passa dos 3s tende a ficar:** pesquisa do Facebook diz que ~65% de quem assiste os 3 primeiros segundos vê ≥10s, e 45% vê 30s+. Por isso vale concentrar o esforço criativo no gancho. *(Brandefy citando Facebook — dado de 3º, não linkado à fonte primária)*
- Gancho = **frase de impacto + imagem de impacto** ao mesmo tempo.

**Tipos de hook que funcionam** (narrados, sem rosto):
- 🟡 **Open loop / curiosity gap** — abre uma pergunta nos 3s e só responde no fim. Apoia-se no **Efeito Zeigarnik** (tarefa inacabada fica "presa" na memória). *(Backlinko 2024; BetterVideoContent)*
- 🟡 **Fato/estatística chocante** — abrir com número contraintuitivo ("Em 1973, algo impossível aconteceu…"). Ativa *novelty bias*. Ótimo pra true-crime/mistério porque dispensa rosto. *(Shorta, 2026)*
- 🟡 **Pattern interrupt** — começar pela conclusão antes de qualquer contexto ("Ela foi presa, mas o crime não foi dela"). Viola a expectativa e dispara atenção. *(OpusClip)*
- 🟡 **Proof-first / bold claim** — afirmação alta demais pra ignorar ("Esse caso nunca foi resolvido — e o suspeito ainda está solto"). Ativa FOMO/social proof. *(OpusClip)*
- 🟡 **Identity / dor do espectador** — nomeia quem é o viewer ("Se você curte true crime, precisa saber disso"). *(Buffer, 2025)*

## 2. Estrutura e duração
- ✅ **Duração-doce: 25–35s pra arco narrativo** completo (sobe e desce sem fadiga); a faixa 50–60s teve a **maior média de views (~1,7M)** num estudo, mas exige conteúdo que segura. *(Taja.AI, 2025)*
- 🟡 **Esqueleto de 5 beats (Short de 30s):**
```
0–3s    HOOK (o "porquê continuar")
3–8s    SETUP — contexto mínimo
8–22s   2–3 VALUE BEATS — entrega a promessa em camadas
22–27s  PAYOFF — fecha o loop de curiosidade (com twist se der)
27–30s  CTA leve + gancho pro próximo
```
*(StratBoost, 2025 — framework prescritivo, sem dado de retenção que o valide)*
- 🟡 **Contagem de palavras da narração** (~2 palavras/s): 30s ≈ 70–110 palavras · 45s ≈ 110–160 · 60s ≈ 150–220. *(StratBoost, 2025)*
- Frases **curtas**, uma ideia por frase. Fisga → explica (nunca o contrário).

## 3. Ritmo e cortes (impede o deslize)
- ✅ **Cadência por categoria:** entretenimento alta-energia corta a cada **1–3s**; conteúdo padrão **3–5s**; educacional/explicativo **4–7s**. *(Clippie.ai, 2025)*
- ⚠️ **Shorts de alta performance:** ~**1 corte a cada 2–4s**. Plano estático longo é um dos maiores "retention killers". *(OpusClip, 2025)*
- 🟡 **Pattern interrupt a cada 2–3s** (zoom-cut, efeito sonoro, legenda animada) reseta a atenção. *(Fluxnote, 2026)*
- 🟡 **J-cut** — deixar o áudio da próxima cena **entrar antes** da virada visual; cria antecipação e transição fluida em vez de corte seco. *(TechSmith, 2024)*
- **Para o nosso pipeline (faceless narrado):** a retenção depende de **dois trilhos** — o arco do roteiro **e** a variação do b-roll. Mudança visual a cada poucos segundos é obrigatória, não enfeite.

## 4. Legenda (caption) — alta alavanca, e barata
> Esta é a seção mais acionável pro nosso `short_factory.py` (`SUB_STYLE`/`SUB_POS`) — conecta com a missão de imagem/legenda.
- ✅ **~92% assiste no mobile SEM som** (83% em todos os dispositivos); sem legenda, 41% do vídeo fica difícil de entender. A legenda **carrega a mensagem**. *(Kapwing citando Verizon & Publicis Media, 2024)*
- ✅ **Posição = zona segura** (1080×1920): YouTube Shorts reserva os **400px de baixo** pra UI; TikTok ~**250px embaixo + 130px no topo**; margens laterais 60–100px. **Legenda no terço central** não é tapada por nenhuma plataforma e evita re-render. *(RAMD Creator School, 2025 — números precisos, fonte forte)*
- ✅ **Blocos curtos: 3–7 palavras por vez, 1–3s** (ótimo 2–3s), quebrando em pontuação/clausula. *(OpusClip, 2025)*
- ✅ **Fonte sans-serif bold** (Helvetica/Arial/Montserrat) com **contorno preto** (ou fundo semi-transparente). Nada de fonte decorativa ou pastel — some no fundo claro ("teste do sol"). *(OpusClip, 2025)*
- 🟡 **Estilo "karaokê"** (destaca a palavra falada em cor) — padrão das ferramentas (CapCut/OpusClip, estilo Hormozi); ancora o olho sem obrigar a escolher entre ler e ver. *(OpusClip, 2025)*
- ⚠️ **Legenda aumenta watch time** (+12% Meta; +40% mais views no YouTube; +7,32% ao longo da vida do vídeo). *(Rev, 2024 — ver ressalvas na §Honestidade)*

## 5. Áudio
- Voz **clara e bem nivelada** (o b-roll é fundo, a voz é a estrela).
- Trilha de **tensão/ambiente** baixa por baixo ajuda retenção; em TikTok, som em alta ajuda alcance.
- Narração ~**150–170 palavras/min** — energia, mas inteligível (bate com ~2 palavras/s da §2).

## 6. CTA e loop de retenção
- ✅ **Loop = nova view.** Desde **31/03/2025** o YouTube conta cada loop/replay como view (sem tempo mínimo). Fazer o fim emendar no começo (transição invisível) infla views e sinais. Canal **faceless leva vantagem**: a voz só continua, o loop não aparece. *(Virvid, 2026)*
- ✅ **"Views" ≠ "Engaged Views".** Só *Engaged Views* (quem passou dos primeiros segundos) contam pra monetização e pesam no ranking. Loop infla a contagem, mas quem **distribui** é a retenção qualificada. *(vidIQ, 2026)*
- 🟡 **CTA de "se inscreve/curte" no fim PODE atrapalhar** — quebra o momentum do loop e suprime engajamento orgânico; valor real gera engajamento sozinho. *(Social Media Examiner citando criador John Scott, 2024)*
- 🟡 **Comentário-isca binário** ("Você acredita nisso? Comenta A ou B") — escolha binária reduz o atrito de comentar. Ótimo pra teoria/true-crime. *(ShortsFaceless, 2025)*
- 🟡 **Loop de replay narrativo** — fechar com frase circular que remete ao hook ("e foi assim que tudo começou…") incentiva re-assistir. *(Gurkha Technology, 2024)*

## 7. Título / descrição / hashtags
- Título curto que **promete a curiosidade** do gancho (não entrega o final).
- 3–5 hashtags: 1 ampla do nicho + 2–3 específicas + tendência do dia.

## ❌ Erros que matam o Short
- Intro lenta / logo / "se inscreve" no começo.
- Explicar o contexto antes de dar motivo pra ficar.
- Plano estático/fundo congelado por mais de 4–5s.
- Legenda pequena, sem contraste, ou no rodapé (tapada pela UI).
- Roteiro **genérico de IA** sem sua mão → além de morno, é o perfil "inauthentic content" que o YouTube desmonetizou em massa (jan/2026). **Voz/ângulo único seu = sobrevivência**, não luxo. Ver [[project-canal-dark]] (RISCO #1).

## 🧪 Honestidade — o que NÃO se confirmou
A verificação abriu cada URL. Estes pontos circulam por aí mas **não passaram** — não use como fato:
- ❌ **"42% usam legenda pra focar"** — número **errado**. A fonte (Kapwing/Preply) traz **27%**, não 42%. O que se confirma é "29% usam pra entender melhor mesmo com som".
- ❌ **"+13,48% de views nas 2 primeiras semanas"** (Discovery Digital Networks) — não apareceu na página; só o **+7,32% (vida útil)** se confirmou.
- ⚠️ **Multiplicadores de alcance por faixa de retenção** (1.6x / 2.2x / 2.8x) — vêm de blogs-agregadores, precisos demais pra serem empíricos. O **princípio** (mais retenção = mais alcance) é sólido; os múltiplos exatos, não.
- ⚠️ **"5–7s como piso mínimo de troca visual"** e o **"padrão de ritmo progressivo"** (10–15s na intro, rajadas de 5–10 cortes a cada 2–3min) — são síntese/extrapolação; a fonte só confirma o "2–4s" e trata os padrões separadamente.
- ⚠️ **Números de legenda da OpusClip/Virvid (+15–25% retenção, etc.)** — vendor que **vende exatamente legenda automática** = conflito de interesse. Direção boa, número auto-publicado.
- ⚠️ **"Shorts de 13s/60s performam melhor" atribuído ao vidIQ** — atribuição não verificada.

## 📚 Fontes (abertas e conferidas em 2026-05-31)
| Fonte | Ano | Usada em |
|---|---|---|
| OpusClip — Ideal Shorts Length & Format | 2025 | retenção, cortes, legenda |
| Taja.AI — Shorts Length Guide | 2025 | duração |
| Virvid.ai — Retention / Looping Structure | 2025–26 | benchmark, loop |
| Fluxnote — Good Retention for Shorts | 2026 | alvos por duração, pattern interrupt |
| StratBoost — 30-Second Script Template | 2025 | 5 beats, contagem de palavras |
| Backlinko — Audience Retention | 2024 | open loop / Zeigarnik |
| Buffer — Good Hooks | 2025 | identity hook, sem-som |
| Brandefy — Psychology of Video Openers | s/d | dado Facebook 65%/45% |
| Clippie.ai — High-Retention Edits | 2025 | cadência de corte |
| TechSmith — L-Cuts & J-Cuts | 2024 | j-cut |
| Social Media Examiner — Hooks & Curiosity Loops | 2024 | CTA |
| vidIQ — Shorts Algorithm | 2026 | engaged views |
| Kapwing — Subtitle Stats | 2024 | sem-som, foco |
| Rev — Closed Captions Stats | 2024 | watch time c/ legenda |
| RAMD Creator School — Safe Zone | 2025 | posição da legenda |

> Próximo: estes achados alimentam a **Fase 2** (aprofundamento por nicho) e a **Fase 3** (prompts/persona). A seção de legenda também cruza com a missão de imagem/legenda ([[_MISSAO-ciclo-imagem-legenda]]).
