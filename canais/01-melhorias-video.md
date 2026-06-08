---
projeto: canal-dark
tipo: canal
tema: melhorias-video
atualizado: 2026-06-02c
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
- **B-roll por IA (cascata `_fetch_ai_image`)**: Cloudflare Workers AI → Pollinations → **ImageRouter** (`_fetch_imagerouter`, add 02/06), via `--broll-source ai`. Flags `IMAGEROUTER_MODEL`/`IMAGEROUTER_SIZE`/`IMAGEROUTER_API_KEY`.
  ⚠️ **Stack grátis fragilizado (02/06):** Pollinations virou **402** (pago), Cloudflare tem **cota diária** (429), e o ImageRouter `:free` dá **403 via API até depósito** (free só 1024×1024). Pra destravar: depósito qualquer no imagerouter.io + `IMAGEROUTER_SIZE=1024x1024`, ou esperar o reset do Cloudflare. Erro em todos → b-roll cai em cor sólida.
- **One Piece Controller v4** (02/06, só nicho one-piece + `ALLOW_ANIME=1`): imagem v4 = fórmula
  **SHOT/CAMERA + SUBJECT + ACTION + EMOTION + SETTING** (`_OP_SHOT_LIBRARY` + `_op_pick_shot` escolhe o
  enquadramento pela batida da fala; `visual_context.shot_type`/`camera` forçam). `loki`/`elbaf`/`shanks`/
  `blackbeard` adicionados na `_OP_SUBJECT_LIBRARY` com signature traits. Imagem IA pedida em **9:16 NATIVO**
  (Pollinations 1080×1920; Cloudflare FLUX 720×1280, override por `CLOUDFLARE_IMAGE_WIDTH/HEIGHT`).
  **PURE-AI default**: b-roll one-piece = render IA cinematográfico SOMENTE; still real do Fandom só atrás de
  `OP_USE_FANDOM_STILLS=1` (default OFF). Voz v4 = "SCRIPT VOICE / VOCABULARY mission" injetada via
  `02-roteiro-e-linguagem.md` (léxico fandom, cadência, ban-list, word bank, do/don't, guardrail honesto) —
  só pro one-piece. **Outros nichos não mudam** (sem anime, sem shot, sem voz de fã).
- **`REF_DIR`**: pasta de imagens (.jpg/.png/.webp) forçadas como b-roll nas primeiras N linhas → Ken Burns.
- **Montagem**: 9:16, concat CFR 30fps, narração + música opcional (-volume), legenda queimada.
- **Fluidez de cortes** (02/06, A+B+C): **(A)** cadência amarrada à FALA real — os word-timestamps do
  edge-tts agora são persistidos (`words.json` no work_dir) e consumidos por `compute_line_durations_from_words`
  (casa o fim de cada linha com o `end` da última palavra; fallback pra aproximação antiga se o casamento falhar);
  **hook mais rápido** (sub-shots ≤2.2s nos primeiros 3s). **(B)** Ken Burns reforçado e com velocidade ∝ duração
  (zoom 1.22, pan 0.08 — não "morre" em shot longo). **(C)** punch-in opcional na palavra-chave. Tudo atrás de env
  flags: `CANAL_DARK_CADENCE`(1) · `CANAL_DARK_HOOK_MAX_SHOT`(2.2) · `CANAL_DARK_HOOK_WINDOW`(3) ·
  `CANAL_DARK_MIN_SUB`(0.6) · `CANAL_DARK_KB_ZOOM_END`(1.22) · `CANAL_DARK_KB_PAN`(0.08) · `CANAL_DARK_PUNCH`(0) ·
  `CANAL_DARK_PUNCH_AMP`(0.06). Verificado E2E na venv (short 1080×1920 30fps, 82s). **Pendente:** validação VISUAL
  do Vinicius + tuning dos defaults.
- **One Piece v5 — fidelidade + anti-"gota azul"** (02/06, só nicho one-piece): duas correções na lane de
  imagem (helpers `_op_*`, lane FLUX schnell via Cloudflare/Pollinations, **CFG=1 → ignora negative prompt**).
  - **(1) Artefato "gota/lágrima/suor" sob o olho** — como FLUX ignora negative E renderiza tokens crus de
    uma cláusula `Avoid: tear, teardrop...` (a gota aparecia JUSTAMENTE por listar os termos), a supressão é
    feita por uma **cláusula POSITIVA** `_OP_FACE_CLARITY_LOCK` ("clean dry face with clear smooth skin under
    the eyes, no teardrop...") injetada em TODO prompt one-piece (`_op_build_image_prompt` + `_op_build_montage_prompt`).
    Os termos de artefato-de-face foram REMOVIDOS do stream positivo: `_op_avoid_clause()` filtra
    `_OP_FACE_ARTIFACT_TERMS` da cláusula `Avoid:` (que agora só lista off-style/paisagem-vazia). Também tirei
    o token "tear" do `_OP_SHOT_LIBRARY['close_up']` e do `_OP_SHOT_TRIGGERS` (era injetado como positivo).
    `_OP_AVOID_TERMS` ganhou os termos do spec (tear/teardrop/crying/sweat drop/sweatdrop/water droplet/blue
    drop) só para metadata/doc. A scar-under-left-eye do Luffy é traço REAL — mantida (descrita como STITCHED
    HORIZONTAL SCAR, never a teardrop); o que sai é só a GOTA.
  - **(2) Fidelidade/reconhecibilidade** — `_OP_SUBJECT_LIBRARY` reescrita com as FICHAS CANÔNICAS do
    cd-nicho-onepiece (anchor + hair + eyes + marks + outfit + accessories + build + palette) por personagem;
    a ficha COMPLETA é injetada por aparição via `_op_pick_subject` → `_op_build_image_prompt` (a CENA segue
    livre — ângulo/ação/composição do v4 — só a IDENTIDADE fica travada). Enriquecidos: luffy, gear5_nika,
    joy_boy_nika, imu, gorosei, loki, shanks, blackbeard. Adicionados: `gear5_luffy`, `zoro`, `nami` (+ triggers
    no `_OP_CONCEPT_MAP` e `_OP_TITLE_SUBJECT`).
  - **Teste (juiz humano):** `tools/op_fix_test.py` gera 6+ imagens (loki/luffy/imu/gear5/joy_boy_nika/zoro/
    shanks, cenas variadas) na MESMA lane `_op_*`/`_fetch_ai_image`. Rodado E2E 02/06: 6/6 OK, zero gota, traços
    fiéis. Cloudflare estourou a cota diária grátis (10k neurons) → caiu pro Pollinations (também FLUX schnell,
    mesma estratégia de supressão vale). Saída em `out/_op_fix_test/`.
- **Créditos da descrição** (fix 02/06): `_build_image_credits_block` credita SÓ o b-roll que entrou na
  timeline final (`broll_files`), 1:1 e na ordem. Resolve cada arquivo pelo sidecar de metadata (bancos) ou
  pelo prefixo do nome (`broll_ai_*`=AI, `fandom_char_*`=Fandom, `broll_*.mp4`=Pexels). **Não lê mais o
  `out/CREDITS.jsonl` acumulado** — antes ele vazava créditos de runs/temas antigos (ex.: "Abraham Lincoln",
  "Spermatozoa", Civitai unknown-ip num vídeo de Luffy). `CREDITS.jsonl` segue existindo só como log do
  image_providers (append cross-run), não é mais fonte de verdade da descrição.
- **One Piece — LANE WEB híbrida (foto real PD/CC p/ cenário/objeto)** (02/06, metade 2 — só nicho one-piece):
  o one-piece deixa de ser 100% IA. **Roteamento por shot** via `script["lines"][i]["broll_kind"]`
  (∈ character/scenery/object; default seguro "character"; rede `OP_IP_TOKENS` promove query com IP nomeado a
  character). Em `produce` monta-se `all_kinds` paralelo a `all_queries` → `_build_op_broll` → `_op_plan_scene(broll_kind)`:
  - `broll_kind=='character'` → **SÓ render IA** (`_fetch_ai_image` FLUX) — NUNCA web (Content ID Toei/Shueisha).
  - `broll_kind ∈ {scenery,object}` → tenta **FOTO REAL livre** primeiro (`_op_fetch_web_burn` → `image_providers.find_images(lane="burn")`,
    wikimedia/openverse/archive.org). O que não vier livre **cai pro render IA** (pad de cenário `op_world_scenery`,
    sem personagem). Em qualquer dúvida/falha → IA.
  - **DESACOPLADO de ALLOW_ANIME**: o controlador `_op_mode` agora é o DEFAULT do nicho
    (`CANAL_DARK_NICHE=one-piece-theories-and-stories`), liga com **ALLOW_ANIME=0**. `ALLOW_ANIME` volta a
    significar SÓ frames booru/anime reais (OFF). `_resolve_providers("anime")` agora retorna `[]` com ALLOW_ANIME≠1
    mesmo com `IMG_PROVIDERS_ANIME` setado (hard gate, defesa em profundidade) → fandom/civitai OFF.
  - **Guardrail de licença + anti-OP** (`_op_web_burn_safe`, só scenery/object): aceita SÓ PD/CC0/CC-BY/CC-BY-SA
    (reusa `_license_is_burn_safe`); descarta licença NC/ND/vazia/desconhecida/ai-generated. Heurística anti-IP por
    **palavra inteira** (`_op_word_hit` com `\b` — corrigiu o falso positivo "boa" em "boat", "ace" em "space"):
    descarta se título/atribuição/fonte/url citarem one piece/anime/manga/fanart/cosplay/nome de personagem, ou
    fonte fandom/booru/deviantart/pixiv. Nomes ambíguos (ace/law/kid/boa/dragon...) só descartam se vierem com
    sinal de anime/OP. Em qualquer dúvida → descarta → IA. Imagem reprovada é apagada (não vaza pra timeline/créditos).
  - **Atribuição**: a foto web livre carrega sidecar `.json` (licença+fonte+autor reais) do `find_images`;
    `_credit_for_broll_file`/`_build_image_credits_block` já o resolvem → crédito honesto na descrição.
  - **Fix Gear 5 (anti-louro)**: `gear5_nika`/`gear5_luffy` agora forçam "stark snow-WHITE hair (pure white,
    NOT blond, NOT yellow, NOT golden)" — o render saía louro.
  - **Teste (juiz):** `tools/op_web_lane_test.py` (não depende de LLM). Rodado E2E 02/06: gating OK (controlador
    liga com ALLOW_ANIME=0; lane anime=[]); roteamento OK (character 100% IA/nunca web; scenery/object → WEB+fallback
    IA); guardrail unit 10/10; **5 fotos PD/CC reais aceitas** com atribuição correta; `_build_op_broll` end-to-end
    (IA mockada) roteia certo. Saída p/ inspeção em `out/_op_web_lane_test/`. **Honesto:** shots de PERSONAGEM (IA)
    podem cair em cor sólida nesta janela por infra (Pollinations 402/Cloudflare cota) — é stack, não a lane web,
    que é independente e funciona. Lane web só acha foto quando a query é "fotografável" (lugar/objeto concreto);
    query abstrata/OP-flavored cai no fallback IA (comportamento seguro desejado).

## Backlog (ideias abertas — confirmar o que falta)

- [ ] **Karaokê real (`\k`)**: hoje "punchy" só encurta cues. Realce da palavra ativa exige ASS nativo
      com `\k{dur}` por token (timestamps de palavra já existem). Marcado como PENDENTE no código.
- [~] **Imagem real automática**: FEITO p/ **one-piece** (lane WEB burn p/ cenário/objeto, cache+atribuição+dedupe+guardrail,
      fallback IA — ver bloco "LANE WEB híbrida" acima). Falta plugar nos outros nichos (true-crime/conspiracy já usam
      `lane=burn` no fluxo genérico, mas sem o roteamento por `broll_kind`).
- [~] **Mix de b-roll**: parcial no one-piece (foto real WEB + render IA na mesma peça, por shot via `broll_kind`).
      Falta alternar vídeo Pexels + imagem na MESMA cena.
- [x] **Corte amarrado à fala + Ken Burns ∝ duração + punch opcional** — feito 02/06 (A+B+C). Falta a validação
      VISUAL do Vinicius + tuning dos defaults via env. xfade/crossfade ficou de fora (ROI baixo; concat segue `-c copy`).
- [ ] **Voz**: avaliar ElevenLabs (stub pronto) vs edge-tts; testar vozes por persona do nicho.

## Cuidados (gotchas já sangrados — ver memória de pipeline)

- edge-tts ≥ 7.2 (6.x dá HTTP 403); SubMaker .get_srt() quebrado → montar SRT dos word-timestamps.
- FFmpeg `subtitles` no Windows: `C:` quebra o parser → rodar com cwd na pasta do .srt (já feito).
- Pexels tem fps variável → re-encodar 30fps CFR antes do concat (já feito).

## Links

Fontes de imagem → [[00-pesquisa]]. Prompt do `broll_query`/`visual_context` → [[02-melhorias-prompt]].
Envio de referência que vira `REF_DIR` → [[03-melhorias-telegram]].
