"""
short_factory.py — Motor de Shorts Narrados do Canal Dark
==========================================================
Pipeline completo para criar um YouTube Short faceless narrado:

  (a) Roteiro  : Gemini gera JSON estruturado OU carrega de arquivo aprovado
  (b) Voz      : edge-tts sintetiza narração .mp3 + SRT de legendas por palavra
  (c) B-roll   : Pexels API baixa clipes/imagens verticais por query
  (d) Montagem : FFmpeg monta 9:16 1080x1920, sobrepõe voz, queima legenda
  (e) Output   : short.mp4 + metadata.json (título/desc/hashtags por plataforma)

Uso rápido:
    # Modo --topic: gera roteiro via Gemini, cria o short (bom para testes)
    python short_factory.py --topic "Why ancient Stoics slept on the floor"

    # Modo --script-file: usa roteiro já aprovado pelo humano (fluxo normal)
    python short_factory.py --script-file ./approved_script.json

    # Modo --script-only: apenas gera o roteiro JSON e sai (sem voz/render)
    python short_factory.py --script-only --topic "Why ancient Stoics slept on the floor"

Variáveis de ambiente necessárias:
    GEMINI_API_KEY   — https://aistudio.google.com/app/apikey
    PEXELS_API_KEY   — https://www.pexels.com/api/ (chave grátis)

Variáveis opcionais:
    ELEVENLABS_API_KEY — para trocar engine TTS por ElevenLabs (stub pronto)
    SUB_POS            — posição da legenda: 'lower' (padrão) ou 'center'
    SUB_STYLE          — estilo da legenda: 'clean' (padrão) ou 'punchy' (1-3 palavras grandes)
    REF_DIR            — pasta com imagens (.jpg/.png/.webp) forçadas como b-roll nas primeiras linhas

Dependências do sistema (não pip):
    ffmpeg — https://ffmpeg.org/download.html (adicione ao PATH)

Fontes das legendas:
    Usa Montserrat (melhor legibilidade em Shorts/Reels).
    Coloque Montserrat-SemiBold.ttf em assets/fonts/ ao lado deste script.
    Download: https://fonts.google.com/specimen/Montserrat → baixe e extraia o .ttf
    Se a fonte não for encontrada, o pipeline usa Arial como fallback e loga um aviso.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

# ── .env ───────────────────────────────────────────────────────────────────────
# Carrega o .env ao lado deste script (independe do diretório de onde foi chamado).
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass  # python-dotenv é opcional; variáveis podem vir do ambiente do SO

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("short_factory")

# ── Constantes ────────────────────────────────────────────────────────────────
GEMINI_MODEL = "gemini-2.5-flash-lite"

# Voz padrão do edge-tts: en-US-AriaNeural soa natural e é amplamente compatível.
# Lista completa: python -m edge_tts --list-voices
DEFAULT_TTS_VOICE = "en-US-AriaNeural"

# Resolução alvo: Shorts/Reels/TikTok — 9:16 vertical
OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920

# Duração máxima recomendada para Shorts (segundos)
MAX_SHORT_DURATION = 90

# Cor de fallback quando nenhum b-roll é encontrado: preto sólido
FALLBACK_BG_COLOR = "black"

# Duração máxima por shot de b-roll em segundos.
# Quando uma cena dura mais que isso, o shot é dividido em sub-shots
# para evitar imagem congelada por períodos longos.
# Configurável via env CANAL_DARK_MAX_SHOT (float, em segundos).
DEFAULT_MAX_SHOT_SEC = 4.0

# Stop-words ignoradas ao escolher a palavra-chave do punch-in (Frente C).
# São funções gramaticais de baixa carga — nunca o clímax da frase.
_PUNCH_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at", "for",
    "is", "was", "are", "were", "be", "been", "it", "its", "this", "that",
    "with", "as", "by", "from", "he", "she", "they", "you", "we", "i", "his",
    "her", "their", "your", "our", "my", "so", "if", "then", "than", "not",
    "no", "do", "did", "had", "has", "have", "will", "would", "can", "could",
}

# ── Configurações de legenda ──────────────────────────────────────────────────
# Resolução base do ASS: fixada em 1080x1920 para que FontSize seja idêntico em
# QUALQUER máquina, independentemente da resolução de exibição do ffmpeg.
# O filtro 'subtitles' do FFmpeg usa PlayResX/Y do cabeçalho ASS para escalar a
# fonte; ao usar force_style com SRT (que não tem cabeçalho), injetamos
# PlayResX/PlayResY diretamente no force_style — o FFmpeg libass respeita esses
# campos mesmo em SRT, tratando-os como override do cabeçalho.
SUBTITLE_PLAY_RES_X = 1080
SUBTITLE_PLAY_RES_Y = 1920

# Largura máxima de uma linha de legenda em caracteres (~28-32 colunas a 1080p)
SUBTITLE_MAX_CHARS_PER_LINE = 30

# Máximo de linhas por cue (SRT block)
SUBTITLE_MAX_LINES = 2

# Fonte preferida (colocar Montserrat-SemiBold.ttf em assets/fonts/ ao lado do script).
# Segunda opção: BebasNeue-Regular.ttf (já inclusa em assets/fonts/ — look "punchy").
# Se nenhuma encontrada, cai para SUBTITLE_FONT_FALLBACK sem erro silencioso.
SUBTITLE_FONT_PREFERRED = "Montserrat SemiBold"
SUBTITLE_FONT_BEBAS = "Bebas Neue"
SUBTITLE_FONT_FALLBACK = "Arial"

# Pasta de fontes local (ao lado deste script)
ASSETS_FONTS_DIR = Path(__file__).resolve().parent / "assets" / "fonts"

# Termos que nunca devem aparecer no slug de um vídeo Pexels selecionado.
# Evitam resultados off-topic como lagoas/praias/festas que o Pexels associa
# erroneamente a queries de crime/drama.
DEFAULT_AVOID_TERMS = [
    "lake", "water", "fish", "beach", "party", "helicopter",
    "wildlife", "nature", "ocean", "river", "pool", "aquarium",
]

# Palavras que identificam pessoas específicas no slug do Pexels.
# Usadas para vetar clipes com rosto/personagem visível quando subject_mode
# é 'places' ou 'objects' (evita mulher loira, homem careca, etc.).
# Inclui plurais e variantes comuns que aparecem em slugs do Pexels.
PEOPLE_WORDS = [
    "man", "men", "male", "woman", "women", "female",
    "person", "persons", "people",
    "face", "faces", "portrait", "portraits",
    "suspect", "suspects", "inmate", "inmates",
    "prisoner", "prisoners", "convict", "convicts",
    "model", "models", "girl", "girls", "boy", "boys",
    "guy", "guys", "lady", "ladies",
]

# One Piece — ícones que PARECEM objeto/cenário mas são IP da Toei/Shueisha e
# precisam SEMPRE ser render IA (broll_kind='character'). Rede de segurança: se o
# broll_query contém um destes tokens, o validador promove a linha pra 'character'
# mesmo que o LLM tenha rotulado scenery/object (o modelo erra pro lado inseguro).
# Tokens em minúsculo; match por substring no broll_query (cobre plural simples).
OP_IP_TOKENS = [
    "poneglyph", "devil fruit", "jolly roger", "thousand sunny", "going merry",
    "oro jackson", "straw hat", "world government symbol", "world government emblem",
    "world government flag", "marine flag", "one piece treasure",
]


# ═════════════════════════════════════════════════════════════════════════════
# UTILIDADES GERAIS
# ═════════════════════════════════════════════════════════════════════════════

def check_ffmpeg() -> None:
    """Verifica se o FFmpeg está disponível no PATH. Aborta com mensagem clara se não."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            raise FileNotFoundError
    except (FileNotFoundError, subprocess.TimeoutExpired):
        log.error(
            "FFmpeg não encontrado no PATH.\n"
            "Instale em https://ffmpeg.org/download.html e adicione ao PATH.\n"
            "Windows: winget install ffmpeg"
        )
        sys.exit(1)
    log.info("FFmpeg OK.")


def strip_markdown_fences(text: str) -> str:
    """
    Remove cercas de markdown (```json ... ```) que o Gemini às vezes inclui
    mesmo quando instruído a retornar JSON puro.
    """
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove a primeira linha (```json ou ```) e a última (```)
        inner = lines[1:] if len(lines) > 1 else lines
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        text = "\n".join(inner).strip()
    return text


def fmt_srt_time(seconds: float) -> str:
    """Converte segundos (float) para o formato HH:MM:SS,mmm do SRT."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds % 1) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _resolve_subtitle_font() -> str:
    """
    Resolve a fonte de legenda em ordem de preferência:
      1. Montserrat SemiBold (Montserrat*.ttf em assets/fonts/)
      2. Bebas Neue          (BebasNeue*.ttf em assets/fonts/) — look punchy
      3. Arial               (fallback do sistema)

    Loga qual fonte foi escolhida para auditar em CI/produção.
    Retorna o nome da fonte para uso no force_style do FFmpeg.
    """
    if ASSETS_FONTS_DIR.exists():
        ttf_files = list(ASSETS_FONTS_DIR.glob("*.ttf")) + list(ASSETS_FONTS_DIR.glob("*.TTF"))
        for ttf in ttf_files:
            if "montserrat" in ttf.name.lower():
                log.info("Fonte de legenda: %s (Montserrat encontrada em assets/fonts/)", ttf.name)
                return SUBTITLE_FONT_PREFERRED
        for ttf in ttf_files:
            if "bebasneue" in ttf.name.lower() or "bebas" in ttf.name.lower():
                log.info("Fonte de legenda: %s (Bebas Neue encontrada em assets/fonts/)", ttf.name)
                return SUBTITLE_FONT_BEBAS
    log.warning(
        "Nenhuma fonte personalizada encontrada em %s (procurou Montserrat e Bebas Neue). "
        "Usando fallback '%s'. "
        "Para usar Bebas Neue: arquivo BebasNeue-Regular.ttf já deve estar em assets/fonts/.",
        ASSETS_FONTS_DIR, SUBTITLE_FONT_FALLBACK
    )
    return SUBTITLE_FONT_FALLBACK


def _wrap_subtitle_text(text: str, max_chars: int = SUBTITLE_MAX_CHARS_PER_LINE,
                        max_lines: int = SUBTITLE_MAX_LINES) -> str:
    """
    Quebra o texto em no máximo max_lines linhas de no máximo max_chars caracteres.
    Usa espaços como pontos de quebra (não corta palavras).
    Retorna string com '\\n' (newline real do SRT) separando as linhas.

    Estratégia:
      - Divide em palavras e vai acumulando até que a linha extrapole max_chars.
      - Se já atingiu max_lines, trunca com '...' (evita overflow de tela).
    """
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    current_len = 0

    for word in words:
        # +1 pelo espaço que precede a palavra (exceto primeira)
        word_len = len(word) + (1 if current else 0)
        if current and current_len + word_len > max_chars:
            # Fecha a linha atual
            lines.append(" ".join(current))
            if len(lines) >= max_lines:
                # Atingiu o limite de linhas — trunca
                lines[-1] = lines[-1].rstrip() + "..."
                return "\n".join(lines)
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len += word_len

    if current:
        lines.append(" ".join(current))

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# (a) ROTEIRO
# ═════════════════════════════════════════════════════════════════════════════

# Esquema esperado do script.json:
# {
#   "title": "Why Stoics Slept on the Floor",
#   "hook": "In ancient Rome, sleeping on the floor wasn't poverty — it was a power move.",
#   "lines": [
#     {"text": "Marcus Aurelius chose a hard bed...", "broll_query": "ancient roman bedroom stone"},
#     ...
#   ],
#   "cta": "Follow for more ancient wisdom that still works today.",
#   "hashtags": ["#stoicism", "#ancientrome", "#philosophy"]
# }

SCRIPT_SYSTEM_PROMPT = """\
You are a scriptwriter for a faceless YouTube Shorts channel targeting a global English-speaking audience. \
Your niche, narrator voice, and content rules are defined by the NICHE PLAYBOOK provided above this prompt. \
Follow that playbook strictly. If NO playbook is present, infer the single most fitting angle for the given topic. \
You write SHORT narrated scripts (~45–60 seconds at a natural speaking pace, roughly 120–150 words total). \

KEY RULES:
1. HOOK FIRST: the opening line must grab attention in ≤3 seconds. Use a surprising fact, \
   a bold counter-intuitive claim, or a direct question. NO "welcome back" or "today we're talking about".
2. ONE SPECIFIC ANGLE: pick ONE strong opinion or unique insight about the topic. Do NOT be generic. \
   The script should feel like it was written by someone who genuinely studied this — not an AI summary.
3. STORY STRUCTURE: hook → context (why it matters) → surprising insight → practical takeaway → CTA.

VISUAL CONTEXT (visual bible):
Before writing the lines, define ONE global "visual_context" object that governs every b-roll shot:
  - setting: physical location + social context + country (e.g. "upper-class home in São Paulo Brazil")
  - era: time period (e.g. "early 2000s", "medieval", "present day")
  - mood: 2-3 adjectives that describe the overall tone (e.g. "tense somber cold")
  - palette: color grading description (e.g. "cold blue low light", "warm sepia", "dark neon")
  - subject_mode: what the b-roll should show — one of: "places" (locations/architecture), \
"objects" (props/evidence), or "atmosphere" (abstract mood shots). Default: "places".
  - anchor_terms: 2-3 reusable keywords that tie every shot to this story \
(e.g. ["true crime", "night"] — will be injected into every Pexels search)
  - avoid_terms: list of topics that must NEVER appear (e.g. ["lake","beach","wildlife"])

4. BROLL QUERIES — each broll_query is 2-4 CONCRETE keywords for ONE scene:
  - Keywords of PLACE / OBJECT / ATMOSPHERE; you MAY name a real place, event or public figure \
directly when that would return relevant footage (copyright is not a constraint here).
  - NO commas, NO lists, NO full sentences — space-separated keywords only
  - Must be coherent with visual_context (setting, era, mood)
  - Avoid bare nouns that pull off-topic ("police" alone, "light", "water") — \
qualify in scene context ("crime scene investigation at night")
  - Practical tip: stock libraries (Pexels) rarely hold footage of one specific private person, so a \
symbolic place/object query often returns better results there; name directly for famous landmarks/events \
or when the b-roll is AI-generated.
  - GOOD example: "crime scene investigation at night"
  - broll_kind (OPTIONAL — emit it ONLY if the NICHE PLAYBOOK above asks for it; otherwise omit the field): \
a machine-readable tag for WHAT the shot depicts, so the pipeline can route the image source. \
THE NICHE PLAYBOOK DEFINES the exact set of allowed broll_kind values, their per-line mapping, and the \
matching subject_mode — use EXACTLY the values and mapping that the playbook gives; do NOT invent values \
and do NOT carry over a value list from a different niche. Follow the playbook's "broll_kind" / "subject palette" \
section and its few-shots to the letter (including the "WHEN IN DOUBT" default the playbook names). \
Examples by niche (illustrative only — obey YOUR niche's playbook): one-piece maps to "character" \
(IP icon that MUST be AI-rendered — Poneglyph, Devil Fruit, Jolly Roger, named ship, straw hat; WHEN IN DOUBT \
"character"), "scenery" (real/generic world) or "object" (generic real prop). A football/history playbook \
instead maps a named player's face to "player_real" (free CC photo, never photoreal AI of a real player), a \
trophy/crest/match prop to "object", a stadium/crowd/city/atmosphere to "scene", and a stylized illustration \
to "caricature".
  - SUBJECT DIVERSITY (apply ONLY when the NICHE PLAYBOOK defines a SUBJECT PALETTE / list of subject types): \
do NOT let every line collapse onto one generic subject. VARY the TYPE of subject across the script following \
the playbook's palette, NEVER repeat the same subject type in two consecutive lines, and include at least one \
human-face line if the playbook lists one. Each broll_query must NAME a concrete subject + context + era (the \
playbook's GOOD examples), never a bare generic noun — apply the playbook's smell test: if you could swap your \
query for a generic catch-all (e.g. "soccer ball") without changing the meaning, the query is BAD; specify the \
subject. (Niches with no subject palette in their playbook: ignore this bullet entirely.)

5. HUMAN REVIEW: this script will be reviewed and edited by a human before production. \
   Write with a clear perspective so the reviewer can agree/disagree and refine.

OUTPUT FORMAT — return ONLY a valid JSON object, no markdown fences, no explanation:
{
  "title": "<YouTube title, max 80 chars, front-loads the hook keyword>",
  "hook": "<opening line — the very first sentence spoken, must hook in 3s>",
  "visual_context": {
    "setting": "<location + social context + country>",
    "era": "<time period>",
    "mood": "<2-3 adjectives>",
    "palette": "<color grading description>",
    "subject_mode": "<places|objects|atmosphere>",
    "anchor_terms": ["<keyword1>", "<keyword2>"],
    "avoid_terms": ["<topic1>", "<topic2>"]
  },
  "lines": [
    {"text": "<sentence or two>", "broll_query": "<2-4 keywords ONE scene no commas>"},
    // add "broll_kind": "character|scenery|object" to each line ONLY if the NICHE PLAYBOOK asks for it
    ...
  ],
  "cta": "<call to action — 1 short sentence, conversational>",
  "hashtags": ["#tag1", "#tag2", "#tag3", "#tag4", "#tag5"]
}

The "hook" MUST also appear as the first item in "lines" (so it gets voice + b-roll treatment).
Total spoken text (hook + all lines.text + cta) must be 120–150 words. \
Aim for 6–8 lines.\
"""


def _load_niche_context() -> str:
    """Carrega a base do nicho (CANAL_DARK_NICHE) pra injetar no system prompt do Gemini."""
    niche = os.environ.get("CANAL_DARK_NICHE", "").strip()
    if not niche:
        return ""
    base = Path(__file__).resolve().parent / "nichos"
    parts = []
    # 06-visual-broll.md (quando existe — hoje só o one-piece tem) é o PLAYBOOK VISUAL:
    # ensina o roteirista a emitir broll_query = SUBJECT+ACTION+EMOTION e o visual_context
    # com as âncoras do estilo anime. Carregado 1º pra ancorar o restante do contexto.
    for rel in (f"{niche}/06-visual-broll.md",
                f"{niche}/02-roteiro-e-linguagem.md",
                f"{niche}/01-conteudo-e-pesquisa.md",
                "00-tecnicas-shorts-comum.md"):
        p = base / rel
        if p.exists():
            parts.append(p.read_text(encoding="utf-8"))
    if not parts:
        log.warning("Nicho '%s' sem docs em nichos/. Sem contexto de nicho.", niche)
        return ""
    log.info("Nicho carregado: %s (%d docs de contexto)", niche, len(parts))
    return ("YOU WRITE FOR A CHANNEL IN THIS NICHE. Strictly follow the voice, structure and "
            "techniques below (they are in Portuguese, but the SCRIPT ITSELF MUST BE IN ENGLISH):\n\n"
            "=== NICHE PLAYBOOK ===\n" + "\n\n".join(parts) + "\n=== END NICHE PLAYBOOK ===\n\n")


def generate_script_via_gemini(topic: str) -> dict:
    """
    Chama o Gemini para gerar o roteiro estruturado a partir de um tema.
    Retorna o dict do roteiro validado.
    Modo --topic: para testes rápidos. No fluxo normal, use --script-file
    com um JSON já revisado pelo humano.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        log.error(
            "GEMINI_API_KEY não definida.\n"
            "Obtenha em: https://aistudio.google.com/app/apikey\n"
            "Defina: set GEMINI_API_KEY=AIza..."
        )
        sys.exit(1)

    try:
        import google.generativeai as genai
    except ImportError:
        log.error("google-generativeai não instalado. Execute: pip install google-generativeai")
        sys.exit(1)

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        GEMINI_MODEL,
        system_instruction=_load_niche_context() + SCRIPT_SYSTEM_PROMPT,
    )

    user_prompt = f'Write a narrated Short script about this topic: "{topic}"'

    log.info("Gerando roteiro via Gemini (%s) para: %s", GEMINI_MODEL, topic)
    try:
        response = model.generate_content(user_prompt)
        raw = response.text
    except Exception as exc:
        err = str(exc)
        if "ResourceExhausted" in err or "429" in err:
            log.error(
                "Quota do Gemini esgotada. Aguarde o reset diário (meia-noite PT).\n"
                "Detalhes: %s", err
            )
        else:
            log.error("Erro ao chamar Gemini: %s", err)
        sys.exit(1)

    return _parse_and_validate_script(raw)


def load_script_from_file(path: str) -> dict:
    """
    Carrega e valida o roteiro a partir de um arquivo JSON aprovado pelo humano.
    Este é o fluxo de produção normal — o roteiro passa por revisão antes de entrar aqui.
    """
    script_path = Path(path)
    if not script_path.exists():
        log.error("Arquivo de roteiro não encontrado: %s", path)
        sys.exit(1)

    try:
        with open(script_path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        log.error("script.json inválido: %s", exc)
        sys.exit(1)

    return _parse_and_validate_script(json.dumps(data))


def _infer_visual_context(script: dict) -> dict:
    """
    Infere um visual_context mínimo a partir dos metadados do roteiro quando
    o LLM não gerou (ou gerou mal) o objeto visual_context.
    Usado como fallback em setdefault — não é chamado se o campo já existir.
    """
    hashtags = script.get("hashtags", [])
    anchor_terms = [h.lstrip("#") for h in hashtags if h.startswith("#")][:3]
    return {
        "setting": script.get("title", "")[:60],
        "era": "present day",
        "mood": "cinematic",
        "palette": "dark low light",
        "subject_mode": "places",
        "anchor_terms": anchor_terms,
        "avoid_terms": list(DEFAULT_AVOID_TERMS),
    }


def _parse_and_validate_script(raw: str) -> dict:
    """
    Faz parse do JSON do roteiro e valida os campos obrigatórios.
    Aceita tanto string JSON bruta quanto string que veio do Gemini com fences.
    """
    cleaned = strip_markdown_fences(raw)
    try:
        script = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        log.error("Roteiro não é JSON válido. Erro: %s\nRaw:\n%s", exc, raw[:500])
        sys.exit(1)

    # visual_context NÃO é obrigatório (retrocompatibilidade com roteiros antigos)
    required = {"title", "hook", "lines", "cta", "hashtags"}
    missing = required - set(script.keys())
    if missing:
        log.error("Roteiro faltando campos: %s", missing)
        sys.exit(1)

    if not isinstance(script["lines"], list) or len(script["lines"]) == 0:
        log.error("'lines' deve ser uma lista não-vazia.")
        sys.exit(1)

    for i, line in enumerate(script["lines"]):
        if "text" not in line or "broll_query" not in line:
            log.error("Linha %d do roteiro faltando 'text' ou 'broll_query': %s", i, line)
            sys.exit(1)
        # Avisa (sem falhar) sobre queries legadas com vírgula — serão tratadas em _fetch_pexels
        if "," in str(line.get("broll_query", "")):
            log.warning(
                "Linha %d: broll_query contém vírgula (query legada): '%s'. "
                "Somente a 1ª cena será usada na busca primária.",
                i, line["broll_query"]
            )

    # Garante que visual_context existe; infere se ausente
    script.setdefault("visual_context", _infer_visual_context(script))

    # Normaliza visual_context para tipos esperados
    vctx = script["visual_context"]
    if not isinstance(vctx, dict):
        log.warning("visual_context inválido (não é dict). Recriando via inferência.")
        script["visual_context"] = _infer_visual_context(script)
        vctx = script["visual_context"]

    # anchor_terms: lista de str, máx 3
    raw_anchor = vctx.get("anchor_terms")
    if isinstance(raw_anchor, list):
        vctx["anchor_terms"] = [str(t) for t in raw_anchor if t][:3]
    else:
        vctx["anchor_terms"] = []

    # avoid_terms: lista de str
    raw_avoid = vctx.get("avoid_terms")
    if isinstance(raw_avoid, list):
        vctx["avoid_terms"] = [str(t) for t in raw_avoid if t]
    else:
        vctx["avoid_terms"] = []

    # subject_mode: apenas valores válidos. O one-piece pode declarar "characters"
    # (personagens/ícones em estilo anime) — os outros nichos NÃO viram estilo anime,
    # então pra eles "characters" cai no default seguro 'places'.
    valid_modes = {"places", "objects", "atmosphere"}
    niche = os.environ.get("CANAL_DARK_NICHE", "").strip().lower()
    if niche == "one-piece-theories-and-stories":
        valid_modes = valid_modes | {"characters"}
    if vctx.get("subject_mode") not in valid_modes:
        vctx["subject_mode"] = "places"

    # shot_type / camera (v4, opcionais): só o one-piece usa pra escolher o SHOT/CAMERA
    # do render IA. Normaliza pra str enxuta; outros nichos ignoram (não tocam o estilo).
    if niche == "one-piece-theories-and-stories":
        for k in ("shot_type", "camera"):
            v = vctx.get(k)
            vctx[k] = str(v).strip()[:60] if isinstance(v, str) and v.strip() else ""

    # broll_kind por linha (SÓ one-piece): rotula cada shot como character|scenery|object
    # pra metade 2 do pipeline rotear a fonte da imagem (character -> render IA FLUX;
    # scenery/object -> pode foto real PD/CC). Default seguro = "character" (força IA contra
    # Content ID Toei/Shueisha). NÃO introduzir esse campo nos outros nichos.
    if niche == "one-piece-theories-and-stories":
        valid_kinds = {"character", "scenery", "object"}
        n_fixed = 0      # ausente/inválido -> character
        n_promoted = 0   # IP no broll_query -> forçado a character
        for line in script["lines"]:
            kind = str(line.get("broll_kind", "")).strip().lower()
            if kind not in valid_kinds:
                kind = "character"  # ausente/inválido -> lado seguro (IA)
                n_fixed += 1
            # Rede de segurança contra Content ID: se a query nomeia um ícone IP
            # (Poneglyph, Devil Fruit, navio nomeado...), força 'character' mesmo
            # que o LLM tenha dito scenery/object. O modelo tende a errar pro lado
            # inseguro nesses casos-armadilha (visto em amostra). Validador > prompt.
            if kind != "character":
                q = str(line.get("broll_query", "")).lower()
                if any(tok in q for tok in OP_IP_TOKENS):
                    kind = "character"
                    n_promoted += 1
            line["broll_kind"] = kind
        if n_fixed:
            log.warning(
                "broll_kind: %d/%d linha(s) sem valor válido -> default seguro 'character' (força render IA).",
                n_fixed, len(script["lines"]),
            )
        if n_promoted:
            log.warning(
                "broll_kind: %d linha(s) com ícone IP na query promovida(s) a 'character' (anti Content ID).",
                n_promoted,
            )

    # broll_kind por linha (SÓ futebol-historia): rotula cada shot como
    # scene|object|player_real|caricature pra a metade 2 do pipeline rotear a FONTE:
    #   scene/object  -> foto/vídeo real genérico do Pexels (broll_query, o que já existe);
    #   player_real   -> SÓ foto livre PD/CC (Wikimedia/Openverse/IA, com crédito) do jogador;
    #   caricature    -> render IA em estilo CARICATO/cartoon NÃO-fotorrealista.
    # Default seguro = "scene" (genérico, sem pessoa real, sem IP). É o oposto do one-piece
    # (lá o default seguro é IA); aqui o lado seguro é o b-roll genérico de banco.
    # NÃO introduzir esse campo nos outros nichos.
    if niche == "futebol-historia":
        valid_kinds = {"scene", "object", "player_real", "caricature"}
        n_fixed = 0
        for line in script["lines"]:
            kind = str(line.get("broll_kind", "")).strip().lower()
            if kind not in valid_kinds:
                kind = "scene"  # ausente/inválido -> lado seguro (genérico, zero likeness/IP)
                n_fixed += 1
            line["broll_kind"] = kind
        if n_fixed:
            log.warning(
                "broll_kind: %d/%d linha(s) sem valor válido -> default seguro 'scene' "
                "(b-roll genérico, sem rosto real, sem IP).",
                n_fixed, len(script["lines"]),
            )

    log.info(
        "Roteiro carregado: '%s' (%d linhas) | setting='%s' mode='%s' anchors=%s",
        script["title"], len(script["lines"]),
        vctx.get("setting", "")[:40], vctx.get("subject_mode"),
        vctx.get("anchor_terms"),
    )
    return script


def build_full_narration(script: dict) -> str:
    """
    Junta todo o texto falado do roteiro em uma string única para o TTS.
    Ordem: hook (já está em lines[0]) → demais lines → cta.
    Inclui pausas implícitas via pontuação.
    """
    parts = [line["text"].strip() for line in script["lines"]]
    parts.append(script["cta"].strip())
    return " ".join(parts)


# ═════════════════════════════════════════════════════════════════════════════
# (b) VOZ (TTS)
# ═════════════════════════════════════════════════════════════════════════════

def synthesize_voice(text: str, engine: str, voice: str, out_path: Path) -> Path:
    """
    Sintetiza a narração e salva o .mp3 em out_path.
    Retorna o path do arquivo gerado.

    engine:
        "edge"       — edge-tts (grátis, sem chave, padrão)
        "elevenlabs" — ElevenLabs (requer ELEVENLABS_API_KEY; stub preparado)
    """
    if engine == "edge":
        return _synthesize_edge_tts(text, voice, out_path)
    elif engine == "elevenlabs":
        return _synthesize_elevenlabs(text, voice, out_path)
    else:
        log.error("Engine TTS desconhecido: '%s'. Use 'edge' ou 'elevenlabs'.", engine)
        sys.exit(1)


def _synthesize_edge_tts(text: str, voice: str, out_path: Path) -> Path:
    """
    Usa edge-tts para gerar narração .mp3.
    edge-tts é gratuito, sem necessidade de chave de API — usa a infraestrutura
    da Microsoft Edge Read Aloud via HTTPS.

    Nota: edge-tts é uma biblioteca assíncrona; rodamos via asyncio.run().
    """
    try:
        import edge_tts
    except ImportError:
        log.error("edge-tts não instalado. Execute: pip install edge-tts")
        sys.exit(1)

    log.info("Sintetizando voz com edge-tts (voz: %s)...", voice)

    async def _run():
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(str(out_path))

    try:
        asyncio.run(_run())
    except Exception as exc:
        log.error("Erro no edge-tts: %s", exc)
        sys.exit(1)

    if not out_path.exists() or out_path.stat().st_size < 1000:
        log.error("edge-tts não gerou o arquivo de áudio esperado: %s", out_path)
        sys.exit(1)

    log.info("Narração gerada: %s (%.1f KB)", out_path.name, out_path.stat().st_size / 1024)
    return out_path


def _synthesize_elevenlabs(text: str, voice: str, out_path: Path) -> Path:
    """
    STUB — ElevenLabs TTS.
    Para ativar: instale 'pip install elevenlabs' e preencha ELEVENLABS_API_KEY.
    voice = voice_id da ElevenLabs (ex: "21m00Tcm4TlvDq8ikWAM" para Rachel).
    """
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        log.error(
            "ELEVENLABS_API_KEY não definida.\n"
            "Obtenha em: https://elevenlabs.io/app/settings/api-keys\n"
            "Ou use --tts-engine edge (padrão, sem chave)."
        )
        sys.exit(1)

    try:
        from elevenlabs.client import ElevenLabs  # type: ignore
        from elevenlabs import save as el_save    # type: ignore
    except ImportError:
        log.error("elevenlabs não instalado. Execute: pip install elevenlabs")
        sys.exit(1)

    log.info("Sintetizando voz com ElevenLabs (voice_id: %s)...", voice)
    client = ElevenLabs(api_key=api_key)
    audio = client.generate(text=text, voice=voice, model="eleven_multilingual_v2")
    el_save(audio, str(out_path))

    log.info("Narração ElevenLabs gerada: %s", out_path.name)
    return out_path


# Nome do arquivo onde persistimos os word-timestamps do edge-tts dentro do work_dir.
WORDS_JSON_NAME = "words.json"


def _persist_word_timestamps(words, work_dir: Path) -> None:
    """
    Salva a lista de word-timestamps do edge-tts em <work_dir>/words.json.

    Cada item de `words` é uma tupla (start_sec, end_sec, palavra). Persistimos
    como lista de objetos {"start","end","text"} p/ a montagem reaproveitar a
    cadência real da fala (casar fim das linhas + punch-in). É best-effort:
    qualquer falha vira só um warning (o pipeline cai no fallback por contagem
    de palavras). NÃO entra em out público — vive no _work, descartável.
    """
    try:
        if not words:
            return
        payload = [
            {"start": float(s), "end": float(e), "text": str(t)}
            for (s, e, t) in words
        ]
        (work_dir / WORDS_JSON_NAME).write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        log.info("Word-timestamps persistidos: %s (%d palavras)", WORDS_JSON_NAME, len(payload))
    except Exception as exc:  # noqa: BLE001 — best-effort, não pode quebrar o TTS
        log.warning("Não consegui persistir word-timestamps (%s); seguirá por aproximação.", exc)


def _load_word_timestamps(work_dir: Path):
    """
    Lê <work_dir>/words.json de volta como lista de tuplas (start, end, texto).

    Retorna [] se o arquivo não existir ou estiver corrompido (a montagem então
    cai no fallback por contagem de palavras — proteção anti-quebra).
    """
    try:
        path = work_dir / WORDS_JSON_NAME
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return [(float(d["start"]), float(d["end"]), str(d["text"])) for d in data]
    except Exception as exc:  # noqa: BLE001
        log.warning("Não consegui ler %s (%s); usando aproximação por palavras.", WORDS_JSON_NAME, exc)
        return []


def generate_srt_from_edge_tts(text: str, voice: str, srt_path: Path, mp3_path: Path) -> Path:
    """
    Gera legendas com timestamps por palavra usando eventos WordBoundary do edge-tts.

    Quando SUB_STYLE=karaoke (padrão), escreve um .ass nativo com realce por palavra.
    Para clean/punchy, escreve o .srt tradicional.

    Retorna o path do arquivo de legenda gerado (.srt ou .ass).
    """
    try:
        import edge_tts
        from edge_tts import SubMaker  # noqa: F401 (import mantido p/ compatibilidade)
    except ImportError:
        log.error("edge-tts não instalado. Execute: pip install edge-tts")
        sys.exit(1)

    sub_style = os.environ.get("SUB_STYLE", "karaoke").strip().lower()
    log.info("Gerando legenda (SUB_STYLE=%s) com timestamps via edge-tts...", sub_style)

    async def _run():
        communicate = edge_tts.Communicate(text, voice)

        # Acumula áudio + eventos de boundary (start/end em segundos).
        # edge-tts >=7.x emite SentenceBoundary (não WordBoundary) por padrão →
        # capturamos os dois e, se só houver sentença, interpolamos por palavra.
        audio_chunks = []
        words = []       # (start_sec, end_sec, palavra) — quando há WordBoundary
        sentences = []   # (start_sec, end_sec, frase)   — fallback comum (7.x)
        async for chunk in communicate.stream():
            t = chunk["type"]
            if t == "audio":
                audio_chunks.append(chunk["data"])
            elif t in ("WordBoundary", "SentenceBoundary"):
                # offset/duration em unidades de 100ns → segundos
                start_sec = chunk["offset"] / 10_000_000
                end_sec = (chunk["offset"] + chunk["duration"]) / 10_000_000
                bucket = words if t == "WordBoundary" else sentences
                bucket.append((start_sec, end_sec, chunk["text"]))

        # Salva o .mp3 reunindo os chunks de áudio
        mp3_path.write_bytes(b"".join(audio_chunks))

        # Sem WordBoundary? Deriva palavras interpolando DENTRO de cada sentença
        # (fatia de tempo proporcional ao tamanho de cada token) → sincronia por
        # palavra suficiente p/ karaokê e SRT, ancorada no timing real da frase.
        if not words and sentences:
            for s_start, s_end, s_text in sentences:
                toks = s_text.split()
                total = sum(len(tok) for tok in toks) or 1
                dur = max(0.0, s_end - s_start)
                cur = s_start
                for tok in toks:
                    w = dur * (len(tok) / total)
                    words.append((cur, cur + w, tok))
                    cur += w
        return words

    try:
        words = asyncio.run(_run())
    except Exception as exc:
        log.error("Erro ao gerar legenda via edge-tts: %s", exc)
        log.warning("Fallback: gerando SRT aproximado sem timestamps por palavra.")
        srt_path.write_text(_generate_fallback_srt(text), encoding="utf-8")
        return srt_path

    # Persiste os word-timestamps reais (start/end/palavra) em words.json no work_dir.
    # A montagem (assemble_short) lê esse arquivo para casar o FIM de cada linha com a
    # fala real (cadência) e para o punch-in na palavra-chave. NÃO vai pro out público:
    # fica ao lado do .srt dentro de _work, que é descartável/regenerável.
    _persist_word_timestamps(words, srt_path.parent)

    # Modo karaokê: produz .ass com realce palavra-a-palavra
    if sub_style == "karaoke" and words:
        ass_path = srt_path.with_suffix(".ass")
        # Precisa dos parâmetros de estilo para montar o cabeçalho do ASS.
        # Resolve aqui (duplica a lógica de assemble_short intencionalmente —
        # o ASS carrega o estilo embutido, então precisa saber fonte/tamanho).
        font_name = _resolve_subtitle_font()
        sub_pos = os.environ.get("SUB_POS", "lower").strip().lower()
        _SUB_FONT_RATIO = float(os.environ.get("SUB_FONT_RATIO", "0.075"))
        font_size = int(round(SUBTITLE_PLAY_RES_Y * _SUB_FONT_RATIO))
        alignment = 5 if sub_pos == "center" else 2
        margin_v  = 0 if sub_pos == "center" else 220

        ass_content = _build_ass_karaoke(words, font_name, font_size, alignment, margin_v)
        if not ass_content or not ass_content.strip():
            log.warning("ASS karaokê vazio; fallback para SRT clean.")
            srt_path.write_text(_build_srt_from_words(words) or _generate_fallback_srt(text),
                                 encoding="utf-8")
            return srt_path
        ass_path.write_text(ass_content, encoding="utf-8")
        log.info("ASS karaokê gerado: %s (%d bytes, %d palavras)",
                 ass_path.name, len(ass_content), len(words))
        return ass_path

    # Modos clean / punchy → SRT tradicional
    srt_content = _build_srt_from_words(words) if words else _generate_fallback_srt(text)
    if not srt_content or not srt_content.strip():
        log.warning("SRT vazio após geração; aplicando fallback estimado.")
        srt_content = _generate_fallback_srt(text)
    srt_path.write_text(srt_content, encoding="utf-8")
    log.info("SRT gerado: %s (%d bytes)", srt_path.name, len(srt_content))
    return srt_path


def _build_srt_from_words(words, words_per_cue: int = 7) -> str:
    """
    Monta um SRT agrupando palavras por legenda usando os timestamps reais dos
    eventos WordBoundary do edge-tts (independe da versão do SubMaker).

    Comportamento por SUB_STYLE:
      clean  (padrão): agrupa palavras até ~SUBTITLE_MAX_CHARS_PER_LINE chars
                       por linha, máx SUBTITLE_MAX_LINES linhas por cue.
                       O parâmetro words_per_cue é ignorado neste modo.
      punchy          : 1-3 palavras por cue, fonte grande — para impacto visual.
                       Karaokê por palavra (\k) não implementado (requer timestamps
                       precisos por subpalavra não fornecidos pelo edge-tts boundary).
    """
    sub_style = os.environ.get("SUB_STYLE", "clean").strip().lower()

    if sub_style == "punchy":
        # Modo punchy: 1-3 palavras por cue para impacto máximo
        punchy_group_size = 2  # 2 palavras em média (varia 1-3 pelo comprimento)
        blocks = []
        idx = 1
        i = 0
        while i < len(words):
            # Agrupa 1-3 palavras de forma adaptativa: se a palavra for longa (>8 chars), usa 1
            group_size = 1 if len(words[i][2]) > 8 else punchy_group_size
            group = words[i:i + group_size]
            start = group[0][0]
            end = group[-1][1]
            cue_text = " ".join(w[2] for w in group).strip()
            if cue_text:
                blocks.append(f"{idx}\n{fmt_srt_time(start)} --> {fmt_srt_time(end)}\n{cue_text}\n")
                idx += 1
            i += group_size
        return "\n".join(blocks)

    # Modo clean (padrão): agrupa por largura de caracteres, máx 2 linhas por cue
    # A lógica: acumula palavras enquanto cabem em SUBTITLE_MAX_CHARS_PER_LINE *
    # SUBTITLE_MAX_LINES chars total. Quando extrapola, fecha o cue.
    max_total_chars = SUBTITLE_MAX_CHARS_PER_LINE * SUBTITLE_MAX_LINES
    blocks = []
    idx = 1
    i = 0
    while i < len(words):
        # Inicia novo cue com a palavra atual
        group = [words[i]]
        group_text = words[i][2]
        i += 1
        # Continua adicionando palavras enquanto o texto acumulado couber
        while i < len(words):
            candidate_text = group_text + " " + words[i][2]
            if len(candidate_text) > max_total_chars:
                break
            group.append(words[i])
            group_text = candidate_text
            i += 1
        start = group[0][0]
        end = group[-1][1]
        # Quebra o texto do cue em linhas de até SUBTITLE_MAX_CHARS_PER_LINE
        cue_text = _wrap_subtitle_text(group_text)
        if cue_text.strip():
            blocks.append(f"{idx}\n{fmt_srt_time(start)} --> {fmt_srt_time(end)}\n{cue_text}\n")
            idx += 1
    return "\n".join(blocks)


def _generate_fallback_srt(text: str) -> str:
    """
    Gera um SRT de fallback dividindo o texto em blocos respeitando
    SUBTITLE_MAX_CHARS_PER_LINE * SUBTITLE_MAX_LINES, estimando ~2.5 palavras/s.
    Usado quando o SubMaker falha ou não retorna timestamps por palavra.

    Em modo SUB_STYLE=punchy usa blocos de 1-2 palavras.
    """
    sub_style = os.environ.get("SUB_STYLE", "clean").strip().lower()
    words_per_sec = 2.5  # estimativa: ~150 palavras/min

    all_words = text.split()
    # Agrupa palavras em chunks respeitando o limite de caracteres por cue
    chunks: list[str] = []

    if sub_style == "punchy":
        # 1-2 palavras por cue para modo punchy
        i = 0
        while i < len(all_words):
            size = 1 if len(all_words[i]) > 8 else 2
            chunk = all_words[i:i + size]
            chunks.append(" ".join(chunk))
            i += size
    else:
        # Modo clean: agrupa por largura de caracteres
        max_total_chars = SUBTITLE_MAX_CHARS_PER_LINE * SUBTITLE_MAX_LINES
        i = 0
        while i < len(all_words):
            group_words = [all_words[i]]
            group_text = all_words[i]
            i += 1
            while i < len(all_words):
                candidate = group_text + " " + all_words[i]
                if len(candidate) > max_total_chars:
                    break
                group_words.append(all_words[i])
                group_text = candidate
                i += 1
            chunks.append(group_text)

    lines = []
    cursor = 0.0

    for idx, chunk_text in enumerate(chunks, start=1):
        word_count = len(chunk_text.split())
        duration = max(word_count / words_per_sec, 0.5)  # mínimo 0.5s por cue
        end = cursor + duration

        wrapped = _wrap_subtitle_text(chunk_text)
        lines.append(str(idx))
        lines.append(f"{fmt_srt_time(cursor)} --> {fmt_srt_time(end)}")
        lines.append(wrapped)
        lines.append("")  # linha em branco obrigatória
        cursor = end

    return "\n".join(lines)


# ── Cor de realce karaokê (formato ASS BGR little-endian &HAABBGGRR& sem alpha)
# Amarelo = B=0, G=255, R=255 → &H0000FFFF& (alpha=00 = opaco)
# Configurável via env SUB_HL_COLOR (ex: SUB_HL_COLOR=&H0000FFFF& para amarelo)
_DEFAULT_HL_COLOR = "&H0000FFFF&"


def fmt_ass_time(seconds: float) -> str:
    """Converte segundos (float) para o formato H:MM:SS.cc do ASS (centiseconds)."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds % 1) * 100))
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _build_ass_karaoke(
    words: list,
    font_name: str,
    font_size: int,
    alignment: int,
    margin_v: int,
) -> str:
    """
    Gera um arquivo ASS completo com legenda karaokê palavra-a-palavra.

    Estratégia: agrupa palavras em cues (mesma lógica 'clean' do SRT) e para
    cada cue emite:
      - Um evento Layer=0 cobrindo toda a duração do cue: texto em branco.
      - Um evento Layer=1 por palavra: mesmo texto do cue mas a palavra ativa
        em amarelo (SUB_HL_COLOR) e as demais mantidas em branco, usando
        overrides de cor inline ASS {\c&H...&}...{\r} — timing exato da palavra
        do WordBoundary.
    O Layer=1 sobrepõe o Layer=0 dentro da janela da palavra ativa, garantindo
    que o viewer sempre veja o cue inteiro mas com destaque progressivo.

    Parâmetros:
      words      — lista de (start_sec, end_sec, texto) do WordBoundary
      font_name  — nome da fonte (Montserrat SemiBold ou Arial)
      font_size  — tamanho em pontos ASS (proporcional a PlayResY=1920)
      alignment  — alinhamento ASS (2=inferior-centro, 5=centro)
      margin_v   — MarginV em pixels (=0 quando center)
    """
    hl_color = os.environ.get("SUB_HL_COLOR", _DEFAULT_HL_COLOR).strip()

    # ── Cabeçalho ASS ───────────────────────────────────────────────────────────
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {SUBTITLE_PLAY_RES_X}\n"
        f"PlayResY: {SUBTITLE_PLAY_RES_Y}\n"
        "ScaledBorderAndShadow: yes\n"
        "Collisions: Normal\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font_name},{font_size},"
        "&H00FFFFFF&,"          # PrimaryColour: branco
        "&H000000FF&,"          # SecondaryColour: azul (não usado no modo override)
        "&HC0000000&,"          # OutlineColour: preto ~75% opaco
        "&H80000000&,"          # BackColour: sombra translúcida
        "1,"                    # Bold
        "0,0,0,"                # Italic, Underline, StrikeOut
        "100,100,"              # ScaleX, ScaleY
        "0,0,"                  # Spacing, Angle
        "1,"                    # BorderStyle: 1 = contorno+sombra
        "3,2,"                  # Outline=3, Shadow=2
        f"{alignment},"         # Alignment
        f"0,0,{margin_v},"      # MarginL, MarginR, MarginV
        "1\n"                   # Encoding: ANSI=1
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    # ── Agrupa palavras em cues CURTOS (estilo Shorts) ──────────────────────────
    # Cues curtos = poucas palavras na tela por vez, troca rápida, 1–2 linhas.
    # Evita a "parede de texto" (parágrafo inteiro de uma vez).
    max_total_chars = int(os.environ.get("SUB_KARAOKE_MAX_CHARS", "28"))
    cues: list[list] = []  # cada cue = lista de (start, end, texto) por palavra
    i = 0
    while i < len(words):
        group = [words[i]]
        group_text = words[i][2]
        i += 1
        while i < len(words):
            candidate_text = group_text + " " + words[i][2]
            if len(candidate_text) > max_total_chars:
                break
            group.append(words[i])
            group_text = candidate_text
            i += 1
        cues.append(group)

    white = "&H00FFFFFF&"

    # ── Emite UM evento por cue, com karaokê NATIVO \kf ─────────────────────────
    # Por que mudou: o design antigo usava 2 camadas (base branca + 1 evento por
    # palavra com pop de escala 128%). Ao escalar a palavra ativa, a LARGURA da
    # linha mudava e o libass RE-CENTRALIZAVA a linha → cada palavra aparecia 2x
    # com leve deslocamento ("ddestruction", "cconflict") = texto fantasma. Além
    # disso, eventos de palavra de cues vizinhos se sobrepunham no tempo e
    # empilhavam (parede de 5 linhas).
    #
    # Solução: 1 único Dialogue por cue. O karaokê nativo \kf preenche cada
    # palavra de branco→destaque conforme é narrada (sweep), SEM mexer na
    # geometria (sem re-centralização, sem fantasma). Cues não se sobrepõem no
    # tempo (são consecutivos) → sem empilhamento.
    dialogue_lines = []
    for cue in cues:
        cue_start = fmt_ass_time(cue[0][0])
        cue_end   = fmt_ass_time(cue[-1][1])

        # Quebra o cue em linhas por contagem de chars (sem cortar palavra),
        # guardando o índice de cada palavra p/ anexar o \kf com a duração certa.
        line_groups: list = [[]]
        cur_len = 0
        for idx, (_, _, wt) in enumerate(cue):
            add = len(wt) + (1 if line_groups[-1] else 0)
            if line_groups[-1] and cur_len + add > SUBTITLE_MAX_CHARS_PER_LINE:
                line_groups.append([idx])
                cur_len = len(wt)
            else:
                line_groups[-1].append(idx)
                cur_len += add

        rendered_lines = []
        for grp in line_groups:
            parts = []
            for idx in grp:
                w_start, w_end, wt = cue[idx]
                dur_cs = max(1, int(round((w_end - w_start) * 100)))  # centissegundos
                parts.append(f"{{\\kf{dur_cs}}}{wt}")
            rendered_lines.append(" ".join(parts))
        kara_text = "\\N".join(rendered_lines)

        # Prefixo inline: primary = cor de destaque (palavra "cantada"),
        # secondary = branco (palavra ainda não narrada). Vence o force_style do burn.
        prefix = f"{{\\1c{hl_color}\\2c{white}}}"
        dialogue_lines.append(
            f"Dialogue: 0,{cue_start},{cue_end},Default,,0,0,0,,{prefix}{kara_text}"
        )

    return header + "\n".join(dialogue_lines) + "\n"


def get_audio_duration(audio_path: Path) -> float:
    """
    Retorna a duração em segundos do arquivo de áudio usando ffprobe.
    Usado para ajustar os tempos dos segmentos de b-roll.
    """
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(audio_path),
        ],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0 or not result.stdout.strip():
        log.warning("ffprobe não conseguiu ler duração de %s. Usando estimativa.", audio_path.name)
        return 0.0
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


# ═════════════════════════════════════════════════════════════════════════════
# (c) B-ROLL
# ═════════════════════════════════════════════════════════════════════════════

def _lane_for(vctx: Optional[dict]) -> str:
    """
    Determina a lane de copyright para image_providers.find_images.

    Prioridade:
      1. IMG_STYLE=cinematic → 'generate' (override de modo cinematográfico)
      2. IMG_LANE env var (override explícito do operador)
      3. CANAL_DARK_NICHE → roteamento automático por nicho:
           true-crimes / conspiracy-theories → 'burn' (Wikimedia/Openverse/IA real)
             com 'generate' como fallback se burn não retornar resultado
           one-piece-theories-and-stories    → 'anime' se ALLOW_ANIME=1,
             senão 'generate' (fallback seguro)
      4. visual_context.subject_mode=='anime' + ALLOW_ANIME=1 → 'anime'
      5. default conservador: 'burn' (só licenças livres confirmadas)

    vctx é o visual_context do roteiro; pode ser None.
    """
    niche = os.environ.get("CANAL_DARK_NICHE", "").strip().lower()

    # 1. Modo cinematográfico (IA) tem PRECEDÊNCIA: gera imagens coesas a partir do
    #    visual_context em vez de banco genérico. Ligado por IMG_STYLE=cinematic.
    #    (Vem antes de IMG_LANE pra não ser sobreposto pelo IMG_LANE=burn do .env.)
    if os.environ.get("IMG_STYLE", "").strip().lower() == "cinematic":
        # PRESERVA A COTA ESCASSA DO GERADOR (Cloudflare): em true-crime/conspiracy, um shot
        # de CENÁRIO/OBJETO (subject_mode places/objects) pode ser servido por FOTO REAL livre
        # da lane 'burn' (wikimedia/openverse/archive — grátis, sem cota). Então esses tentam
        # 'burn' PRIMEIRO mesmo no cinematic; só 'atmosphere' (mood puro, sem assunto concreto
        # que banco CC resolva) e os demais casos seguem 'generate'. Desliga com
        # IMG_CINEMATIC_PLACES_BURN=0. NÃO afeta one-piece (que nem passa por _lane_for).
        if (niche in ("true-crimes", "conspiracy-theories")
                and os.environ.get("IMG_CINEMATIC_PLACES_BURN", "1") != "0"
                and vctx and (vctx.get("subject_mode") or "").lower() in ("places", "objects")):
            return "burn"
        return "generate"

    # 2. Override explícito do operador
    env_lane = os.environ.get("IMG_LANE", "").strip().lower()
    if env_lane in ("burn", "generate", "anime", "ref"):
        return env_lane

    # 3. Roteamento automático por nicho (CANAL_DARK_NICHE)
    niche = os.environ.get("CANAL_DARK_NICHE", "").strip().lower()
    if niche in ("true-crimes", "conspiracy-theories"):
        # Imagem real prefere burn; generate é o fallback natural na cascata do
        # image_providers quando burn não retorna resultado.
        return "burn"
    if niche == "one-piece-theories-and-stories":
        if os.environ.get("ALLOW_ANIME", "0") == "1":
            return "anime"
        # Sem ALLOW_ANIME, gera via IA (evita IP da Toei/Shueisha)
        return "generate"

    # 4. visual_context.subject_mode=='anime' + ALLOW_ANIME=1
    if vctx:
        subject_mode = (vctx.get("subject_mode") or "").lower()
        if subject_mode == "anime" and os.environ.get("ALLOW_ANIME", "0") == "1":
            return "anime"

    # 5. Default conservador
    return "burn"


def fetch_broll(
    query: str,
    source: str,
    out_dir: Path,
    index: int,
    vctx: Optional[dict] = None,
    used_ids: Optional[set] = None,
) -> Optional[Path]:
    """
    Baixa 1 vídeo ou imagem vertical para usar como b-roll de fundo.

    source:
        "pexels" — Pexels API (gratuita, requer PEXELS_API_KEY)
        "ai"     — Pollinations.ai (geração grátis, sem chave)
        "image"  — image_providers: Wikimedia/Openverse/IA (sem chave paga)

    vctx:
        Visual context dict do roteiro (visual_context). Usado para enriquecer a query,
        aplicar vetos de avoid_terms/people e deduplicar resultados.

    used_ids:
        Set mutável de video_ids já baixados. Passado entre calls para deduplicar
        clipes no mesmo vídeo. Passe o mesmo objeto em todas as chamadas do mesmo pipeline.

    Retorna o Path do arquivo baixado, ou None se nada encontrado.
    O chamador deve lidar com None usando fallback de cor sólida.
    """
    # ── image_providers: tenta 1º se source=="image", IMG_PROVIDERS setado, OU modo cinematic ──
    cinematic = os.environ.get("IMG_STYLE", "").strip().lower() == "cinematic"
    use_image_providers = (
        source == "image"
        or bool(os.environ.get("IMG_PROVIDERS", "").strip())
        or cinematic
    )
    if use_image_providers:
        try:
            import image_providers  # type: ignore
            niche = os.environ.get("CANAL_DARK_NICHE", "")
            lane = _lane_for(vctx)
            # Estilo coeso (bíblia visual) → todo shot do vídeo compartilha paleta/mood/era.
            style = None
            if vctx:
                style = {
                    "palette": vctx.get("palette"),
                    "mood": vctx.get("mood"),
                    "era": vctx.get("era"),
                    "style": vctx.get("setting"),
                    # COERÊNCIA (Bug 2): repassa avoid_terms do roteiro p/ o prompt de IA
                    # não gerar cena que contradiz o próprio visual_context (ex.: "modern
                    # stadium"/"skyline" num tópico antigo). _build_gen_prompt anexa "Avoid: ...".
                    "avoid": list(vctx.get("avoid_terms") or []),
                }
            paths = image_providers.find_images(
                query,
                niche=niche,
                lane=lane,
                count=1,
                out_dir=out_dir,
                style=style,
            )
            if paths:
                log.info("B-roll via image_providers: %s", paths[0].name)
                return paths[0]
            # FALLBACK CINEMATIC: a lane 'burn' de places/objects (true-crime/conspiracy) não
            # achou foto CC livre. Em modo cinematic, em vez de cair em Pexels VÍDEO (pode estar
            # fora do ar) ou cor sólida, geramos a cena pela CASCATA viva (Cloudflare → AI Horde
            # → ...) com um prompt coeso. Só aqui o gerador é tocado pra places/objects — depois
            # de a foto livre falhar — preservando a cota nos shots que a foto resolveu.
            if cinematic and lane == "burn":
                gen_prompt = f"{query}, cinematic, dramatic lighting, highly detailed"
                if style:
                    extra = ", ".join(str(style.get(k)).strip() for k in ("palette", "mood", "era")
                                      if style.get(k))
                    if extra:
                        gen_prompt = f"{gen_prompt}, {extra}"
                log.info("image_providers (burn) sem foto livre — gerando cena pela cascata viva.")
                gen_img = _fetch_ai_image(gen_prompt, out_dir, index)
                if gen_img is not None:
                    return gen_img
        except ImportError:
            log.warning("image_providers não disponível — seguindo para Pexels.")

    # source=="image" sem resultado REAL → HÍBRIDO: cai na IA (Pollinations), NÃO no
    # Pexels. Motivo: o Pexels é stock LIVE-ACTION e, enriquecido com a query, devolvia
    # imagens DESCONEXAS (ex.: prateleira de geleia p/ "Tylenol Chicago"; turista de
    # ski p/ "Dyatlov 1959"). A IA gera uma cena coerente a partir da própria query do
    # roteirista ("foggy chicago street at night" → cena nevoenta de Chicago). O Pexels
    # só entra como último recurso e apenas se IMG_ALLOW_PEXELS_FALLBACK=1.
    if source == "image":
        log.info("image_providers sem resultado — fallback HÍBRIDO para IA (Pollinations).")
        ai_img = _fetch_pollinations(query, out_dir, index)
        if ai_img is not None:
            return ai_img
        pexels_key = os.environ.get("PEXELS_API_KEY", "").strip()
        if pexels_key and os.environ.get("IMG_ALLOW_PEXELS_FALLBACK", "0") == "1":
            log.info("IA falhou — último recurso Pexels (IMG_ALLOW_PEXELS_FALLBACK=1).")
            return _fetch_pexels(query, out_dir, index, vctx=vctx, used_ids=used_ids)
        log.warning("Nenhuma imagem real nem IA p/ '%s' — segmento usará cor sólida.", query[:50])
        return None

    if source == "pexels":
        return _fetch_pexels(query, out_dir, index, vctx=vctx, used_ids=used_ids)
    elif source == "ai":
        return _fetch_pollinations(query, out_dir, index)
    else:
        log.error("Fonte de b-roll desconhecida: '%s'. Use 'pexels', 'ai' ou 'image'.", source)
        sys.exit(1)


def _fetch_pollinations(query: str, out_dir: Path, index: int,
                        prompt: Optional[str] = None,
                        seed: Optional[int] = None) -> Optional[Path]:
    """
    Gera uma imagem 9:16 grátis no Pollinations.ai (sem chave de API).

    Se `prompt` for fornecido, usa-o INTEGRAL (caso do One Piece Visual Controller,
    que monta um prompt em estilo One Piece a partir de um SUBJECT). Senão, deriva
    um prompt cinematográfico genérico da própria query.

    `seed` ESTÁVEL por personagem (One Piece): quando passado, força o MESMO ponto de
    ruído entre shots do mesmo personagem → mais consistência. Sem seed, mantém o
    comportamento antigo (determinístico por índice, varia por cena).
    """
    import urllib.parse
    import requests
    import time
    if not prompt:
        prompt = f"{query}, cinematic, dramatic lighting, highly detailed"
    encoded = urllib.parse.quote(prompt, safe="")
    if seed is not None:
        seed = int(seed) % 1000000  # Pollinations aceita seed na query string
    else:
        seed = (index * 7919 + len(prompt)) % 1000000  # determinístico, varia por cena
    url = (f"https://image.pollinations.ai/prompt/{encoded}"
           f"?width=1080&height=1920&model=flux&nologo=true&seed={seed}")
    out_path = out_dir / f"broll_ai_{index:02d}.jpg"
    log.info("Gerando b-roll por IA (Pollinations): '%s'", prompt[:80])

    # O tier grátis do Pollinations é RATE-LIMITED: devolve 402/429 intermitente,
    # principalmente sob concorrência. Em vez de desistir (que deixava a cena sem
    # imagem), tentamos de novo com backoff crescente + jitter por índice — assim a
    # imagem quase sempre acaba saindo, mesmo gerando várias em paralelo.
    attempts = max(1, int(os.environ.get("CANAL_DARK_AI_RETRIES", "4")))
    backoffs = [4, 9, 18, 30]
    for attempt in range(attempts):
        try:
            r = requests.get(url, timeout=120)
            if r.status_code == 200 and len(r.content or b"") >= 1000:
                out_path.write_bytes(r.content)
                return out_path
            transient = r.status_code in (402, 429, 500, 502, 503, 504)
            if transient and attempt < attempts - 1:
                wait = backoffs[min(attempt, len(backoffs) - 1)] + (index % 5)
                log.warning("Pollinations status %s (tentativa %d/%d) — aguardando %ds...",
                            r.status_code, attempt + 1, attempts, wait)
                time.sleep(wait)
                continue
            log.warning("Pollinations falhou (status %s) após %d tentativa(s).",
                        r.status_code, attempt + 1)
            return None
        except Exception as e:
            if attempt < attempts - 1:
                wait = backoffs[min(attempt, len(backoffs) - 1)] + (index % 5)
                log.warning("Erro no Pollinations: %s — retry em %ds (%d/%d).",
                            e, wait, attempt + 1, attempts)
                time.sleep(wait)
                continue
            log.warning("Erro no Pollinations: %s. Sem mais tentativas.", e)
            return None
    return None


def _fetch_cloudflare(prompt: str, out_dir: Path, index: int,
                      account: str, token: str,
                      seed: Optional[int] = None) -> Optional[Path]:
    """
    Gera 1 imagem via Cloudflare Workers AI (FLUX-1-schnell) a partir de um prompt COMPLETO.
    Tier grátis: ~10.000 neurons/dia (centenas de imagens) e SEM o rate-limit anônimo do
    Pollinations. Pede 9:16 NATIVO (width/height, default 720x1280 — múltiplos de 8 dentro
    do limite do FLUX schnell) pra já sair vertical; o Ken Burns só ajusta o enquadramento.
    Override por CLOUDFLARE_IMAGE_WIDTH / CLOUDFLARE_IMAGE_HEIGHT. Retorna Path|None.
    """
    import requests
    import base64
    import time
    # Gate de submissão COMPARTILHADO com image_providers._prov_cloudflare (uma casa só pra
    # constante): o One Piece chama ESTE _fetch_cloudflare (via _fetch_ai_image), não o
    # _prov_cloudflare — sem reusar o mesmo lock/timestamp o throttle não cobriria o caminho
    # do One Piece e a rajada paralela seguiria estourando 429.
    import image_providers as _ip
    model = os.environ.get("CLOUDFLARE_IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell")
    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    steps = int(os.environ.get("CLOUDFLARE_FLUX_STEPS", "6"))
    # 9:16 NATIVO onde o provider aceita. FLUX schnell exige múltiplos de 8 e clampa o
    # tamanho; 720x1280 é 9:16 exato e dentro do limite. Override por env se precisar.
    cf_w = int(os.environ.get("CLOUDFLARE_IMAGE_WIDTH", "720"))
    cf_h = int(os.environ.get("CLOUDFLARE_IMAGE_HEIGHT", "1280"))
    out_path = out_dir / f"broll_ai_{index:02d}.jpg"
    log.info("Gerando b-roll por IA (Cloudflare FLUX %dx%d): '%s'", cf_w, cf_h, prompt[:80])
    attempts = max(1, int(os.environ.get("CANAL_DARK_AI_RETRIES", "4")))
    backoffs = [3, 7, 15, 25]

    def _cf_throttle() -> None:
        # Espaça as submissões usando o lock/timestamp/gap COMPARTILHADOS do image_providers.
        with _ip._CLOUDFLARE_SUBMIT_LOCK:
            elapsed = time.time() - _ip._CLOUDFLARE_LAST_SUBMIT[0]
            if elapsed < _ip._CF_MIN_SUBMIT_GAP:
                time.sleep(_ip._CF_MIN_SUBMIT_GAP - elapsed)
            _ip._CLOUDFLARE_LAST_SUBMIT[0] = time.time()

    for attempt in range(attempts):
        try:
            # seed ESTÁVEL por personagem (consistência entre shots). O FLUX-1-schnell da
            # Cloudflare aceita 'seed'; mesmo seed + mesmo prompt → mesma "cara". Sem seed
            # (cenário/objeto) deixa o provider sortear.
            cf_body = {"prompt": prompt, "steps": steps, "width": cf_w, "height": cf_h}
            if seed is not None:
                cf_body["seed"] = int(seed)
            _cf_throttle()
            resp = requests.post(url, json=cf_body, headers=headers, timeout=120)
            if resp.status_code == 200:
                ct = resp.headers.get("content-type", "")
                if ct.startswith("image/"):
                    img_bytes = resp.content
                else:
                    data = resp.json()
                    b64 = (data.get("result") or {}).get("image", "")
                    img_bytes = base64.b64decode(b64) if b64 else b""
                if len(img_bytes) >= 2000:
                    out_path.write_bytes(img_bytes)
                    return out_path
                log.warning("[cloudflare] resposta 200 sem imagem válida.")
                return None
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
                wait = backoffs[min(attempt, len(backoffs) - 1)]
                log.warning("[cloudflare] HTTP %s (tentativa %d/%d) — aguardando %ds...",
                            resp.status_code, attempt + 1, attempts, wait)
                time.sleep(wait)
                continue
            log.warning("[cloudflare] HTTP %s: %s", resp.status_code, resp.text[:160])
            return None
        except Exception as exc:
            if attempt < attempts - 1:
                time.sleep(backoffs[min(attempt, len(backoffs) - 1)])
                continue
            log.warning("[cloudflare] erro: %s", exc)
            return None
    return None


def _fetch_imagerouter(prompt: str, out_dir: Path, index: int,
                       api_key: str) -> Optional[Path]:
    """
    Gera 1 imagem via ImageRouter (https://imagerouter.io) a partir de um prompt
    COMPLETO. Serve de fallback quando os 2 grátis morrem: Cloudflare estoura a cota
    diária de neurons (429) e o Pollinations vira pago (402). Diferente dos outros, a
    API NÃO devolve bytes/base64 — devolve uma URL hospedada → baixamos num 2º GET.
    Pede 9:16 NATIVO (1080x1920) e PNG; override por IMAGEROUTER_SIZE.

    ⚠️ PRÉ-REQUISITO (verificado no smoke test 2026-06): os modelos ':free' do
    ImageRouter respondem HTTP 403 via API ("Free models are only available on the
    website... To gain API access, please deposit any amount") ENQUANTO a conta não
    receber um depósito inicial — mesmo a geração custando $0. Além disso o tier grátis
    só aceita size 1024x1024. Por isso o default aqui mira o cenário que de fato roda
    (modelo pago em 1080x1920); pra rodar no grátis após destravar, setar
    IMAGEROUTER_MODEL=...:free + IMAGEROUTER_SIZE=1024x1024. O 403/400 cai como None
    no próximo fallback (nunca derruba o pipeline). Retorna Path|None.
    """
    import requests
    import time
    model = os.environ.get("IMAGEROUTER_MODEL",
                           "black-forest-labs/FLUX-1-schnell:free")
    size = os.environ.get("IMAGEROUTER_SIZE", "1080x1920")  # 9:16 nativo (free só 1024x1024)
    url = "https://api.imagerouter.io/v1/openai/images/generations"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "prompt": prompt,
        "model": model,
        "size": size,                 # 9:16 nativo — sai vertical, Ken Burns só enquadra
        "response_format": "url",     # ImageRouter entrega URL hospedada, não bytes
        "output_format": "png",
    }
    out_path = out_dir / f"broll_ai_{index:02d}.png"
    log.info("Gerando b-roll por IA (ImageRouter %s): '%s'", model, prompt[:80])
    attempts = max(1, int(os.environ.get("CANAL_DARK_AI_RETRIES", "4")))
    backoffs = [3, 7, 15, 25]
    for attempt in range(attempts):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=120)
            if resp.status_code == 200:
                try:
                    data = (resp.json().get("data") or [])
                except Exception:
                    data = []
                img_url = data[0].get("url", "") if data else ""
                if not img_url:
                    log.warning("[imagerouter] resposta 200 sem 'data[0].url'.")
                    return None
                # 2º GET: baixa a imagem hospedada e grava nos bytes locais.
                img_resp = requests.get(img_url, timeout=120)
                if img_resp.status_code == 200 and len(img_resp.content or b"") >= 2000:
                    out_path.write_bytes(img_resp.content)
                    return out_path
                log.warning("[imagerouter] download da imagem falhou (HTTP %s).",
                            img_resp.status_code)
                return None
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
                wait = backoffs[min(attempt, len(backoffs) - 1)]
                log.warning("[imagerouter] HTTP %s (tentativa %d/%d) — aguardando %ds...",
                            resp.status_code, attempt + 1, attempts, wait)
                time.sleep(wait)
                continue
            log.warning("[imagerouter] HTTP %s: %s", resp.status_code, resp.text[:160])
            return None
        except Exception as exc:
            if attempt < attempts - 1:
                time.sleep(backoffs[min(attempt, len(backoffs) - 1)])
                continue
            log.warning("[imagerouter] erro: %s", exc)
            return None
    return None


def _fetch_aihorde(prompt: str, out_dir: Path, index: int,
                   seed: Optional[int] = None,
                   negative: Optional[str] = None) -> Optional[Path]:
    """
    Gera 1 imagem via AI Horde (fila pública grátis) a partir de um prompt COMPLETO,
    reusando image_providers._prov_aihorde (mesma fila/poll/anti-censura, sem duplicar
    código). É o 2º elo de _fetch_ai_image: sobrevive ao Cloudflare cair, usando só
    fonte grátis. Lento (poll de fila anônima) — aceita-se a latência. Retorna Path|None.

    Tunagem do free-tier via env (AIHORDE_WIDTH/HEIGHT/STEPS/UPSCALE/MODELS) — ver
    image_providers._prov_aihorde.
    """
    try:
        import image_providers as _ip
        # query="" → o prompt COMPLETO já vem montado; _build_gen_prompt anexa só sufixos
        # cinematográficos. Passamos o prompt como query pra preservar o conteúdo da cena.
        # seed/negative são honrados pelo AI Horde (Stable Diffusion, CFG>1) — não pelo
        # FLUX schnell. É o motor onde o negative prompt de fato corta off-model/3d/etc.
        results = _ip._prov_aihorde(prompt, 1, seed=seed, negative=negative)
    except Exception as exc:
        log.warning("[ai][aihorde] erro: %s", exc)
        return None
    if not results:
        return None
    r = results[0]
    img_bytes = r._bytes
    if img_bytes is None and r.url.startswith("http"):
        try:
            import requests
            dl = requests.get(r.url, timeout=120)
            dl.raise_for_status()
            img_bytes = dl.content
        except Exception as exc:
            log.warning("[ai][aihorde] falha ao baixar imagem: %s", exc)
            return None
    if not img_bytes or len(img_bytes) < 2000:
        return None
    out_path = out_dir / f"broll_ai_{index:02d}.jpg"
    out_path.write_bytes(img_bytes)
    return out_path


def _fetch_nvidia(prompt: str, out_dir: Path, index: int,
                  seed: Optional[int] = None,
                  negative: Optional[str] = None) -> Optional[Path]:
    """
    Gera 1 imagem via NVIDIA NIM (SD 3.5 Large) a partir de um prompt COMPLETO, reusando
    image_providers._prov_nvidia (uma casa só pra a chamada HTTP). 3º elo da rede anti-preto:
    só dispara com NVIDIA_API_KEY setada (senão _prov_nvidia já retorna [] → None). SD3.5
    HONRA negative/seed → bom no One Piece como fallback rápido após o AI Horde. Retorna Path|None.
    """
    try:
        import image_providers as _ip
        results = _ip._prov_nvidia(prompt, 1, seed=seed, negative=negative)
    except Exception as exc:
        log.warning("[ai][nvidia] erro: %s", exc)
        return None
    if not results:
        return None
    img_bytes = results[0]._bytes
    if not img_bytes or len(img_bytes) < 2000:
        return None
    out_path = out_dir / f"broll_ai_{index:02d}.jpg"
    out_path.write_bytes(img_bytes)
    return out_path


def _fetch_together(prompt: str, out_dir: Path, index: int,
                    seed: Optional[int] = None) -> Optional[Path]:
    """
    Gera 1 imagem via Together AI (FLUX.1-schnell-Free) a partir de um prompt COMPLETO,
    reusando image_providers._prov_together. 4º elo da rede anti-preto: só dispara com
    TOGETHER_API_KEY setada (senão _prov_together retorna [] → None). FLUX schnell IGNORA
    negative (como o Cloudflare), então é fallback de CENÁRIO/genérico. Retorna Path|None.
    """
    try:
        import image_providers as _ip
        results = _ip._prov_together(prompt, 1)
    except Exception as exc:
        log.warning("[ai][together] erro: %s", exc)
        return None
    if not results:
        return None
    img_bytes = results[0]._bytes
    if not img_bytes or len(img_bytes) < 2000:
        return None
    out_path = out_dir / f"broll_ai_{index:02d}.jpg"
    out_path.write_bytes(img_bytes)
    return out_path


# ── Ordem da cascata de geradores AI (CONFIGURÁVEL) ──────────────────────────
# Ponto ÚNICO de config da ordem em que _fetch_ai_image tenta os geradores.
#   _AI_ORDER_DEFAULT  — nichos GERAIS (true-crime/conspiracy): vivo-primeiro,
#       Cloudflare 1º (rápido, estável). NÃO mexer aqui sem motivo — mudar isso
#       deixa true-crime/conspiracy mais lentos.
#   _AI_ORDER_ONE_PIECE — SÓ One Piece: AI Horde (Anything Diffusion) PRIMEIRO,
#       porque só ele honra o negative prompt e renderiza traço de anime ("parece
#       Oda"). Cloudflare/Pollinations viram FALLBACK (rápidos, mas FLUX schnell
#       ignora negative e não tem checkpoint de anime). Latência +~45s aceita numa
#       fábrica de 1 short/dia; se o AI Horde 403/falhar, cai no Cloudflare (igual
#       ao caminho geral). Override por env OP_IMG_ORDER (CSV), ex.:
#           OP_IMG_ORDER="aihorde,cloudflare,pollinations,imagerouter"
#   nvidia/together entram GATEADOS em chave (skipam sozinhos sem NVIDIA_API_KEY/
#   TOGETHER_API_KEY) → a cascata grátis (cloudflare→aihorde→...) é idêntica pra quem
#   não configurou. nvidia em 2º (após cloudflare; SD3.5 honra negative, fallback rápido),
#   together após aihorde (FLUX schnell free, mais um elo grátis antes dos mortos).
# Pollinations (402 paywall x402 permanente) e ImageRouter (400/depósito) saíram da cascata
# VIVA: estavam mortos e o retry 4x deles desperdiçava ~1min/cena. As funções `_fetch_*`/`_prov_*`
# seguem existindo — quem quiser reativá-los é só listá-los em OP_IMG_ORDER/IMG_PROVIDERS_GENERATE.
_AI_ORDER_DEFAULT = ["cloudflare", "nvidia", "aihorde", "together"]
_AI_ORDER_ONE_PIECE = [
    n.strip() for n in os.environ.get(
        # AI Horde 1º (honra negative/anime → "parece Oda"); nvidia logo após como fallback
        # RÁPIDO (SD3.5 também honra negative) antes do Cloudflare (FLUX ignora negative).
        "OP_IMG_ORDER", "aihorde,nvidia,cloudflare,together"
    ).split(",") if n.strip()
] or ["aihorde", "nvidia", "cloudflare", "together"]


def _fetch_ai_image(prompt: str, out_dir: Path, index: int,
                    seed: Optional[int] = None,
                    negative: Optional[str] = None,
                    order: Optional[list] = None) -> Optional[Path]:
    """
    Gera 1 imagem por IA a partir de um prompt COMPLETO. A ORDEM dos geradores é
    data-driven (param `order`, default = _AI_ORDER_DEFAULT):
      - cloudflare   — Workers AI (FLUX schnell); precisa CLOUDFLARE_ACCOUNT_ID +
                       CLOUDFLARE_API_TOKEN. Rápido/estável, mas IGNORA negative.
      - nvidia       — NIM SD3.5 Large; só com NVIDIA_API_KEY (skip sem chave). HONRA
                       seed+negative → fallback rápido (2º no geral, após aihorde no OP).
      - aihorde      — fila pública grátis (Anything Diffusion). Lento (~45s) mas
                       HONRA seed+negative → traço de anime. É o 1º no One Piece.
      - together     — FLUX schnell free; só com TOGETHER_API_KEY (skip sem chave). IGNORA
                       negative (fallback de cenário/genérico).
      - pollinations — HTTP 402 hoje (best-effort).
      - imagerouter  — HTTP 403 "deposit" hoje (best-effort, só com IMAGEROUTER_API_KEY).
    Cada falha cai pra próxima fonte na ordem; tudo falhou → None (chamador usa
    cor sólida). seed/negative são repassados a quem suporta (AI Horde).

    Nichos gerais (true-crime/conspiracy) NÃO passam `order` → ordem default
    (cloudflare 1º). Só o caminho One Piece (_op_execute_plans) passa
    _AI_ORDER_ONE_PIECE (aihorde 1º) — assim a reordenação NÃO afeta os outros.
    """
    order = order or _AI_ORDER_DEFAULT

    def _try(name: str) -> Optional[Path]:
        if name == "cloudflare":
            account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
            token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
            if not (account and token):
                return None
            return _fetch_cloudflare(prompt, out_dir, index, account, token, seed=seed)
        if name == "nvidia":
            # Gateado em NVIDIA_API_KEY (skip→None sem chave). SD3.5 honra seed+negative.
            if not os.environ.get("NVIDIA_API_KEY", "").strip():
                return None
            return _fetch_nvidia(prompt, out_dir, index, seed=seed, negative=negative)
        if name == "aihorde":
            return _fetch_aihorde(prompt, out_dir, index, seed=seed, negative=negative)
        if name == "together":
            # Gateado em TOGETHER_API_KEY (skip→None sem chave). FLUX schnell ignora negative.
            if not os.environ.get("TOGETHER_API_KEY", "").strip():
                return None
            return _fetch_together(prompt, out_dir, index, seed=seed)
        if name == "pollinations":
            return _fetch_pollinations("ai", out_dir, index, prompt=prompt, seed=seed)
        if name == "imagerouter":
            ir_key = os.environ.get("IMAGEROUTER_API_KEY", "").strip()
            if not ir_key:
                return None
            return _fetch_imagerouter(prompt, out_dir, index, ir_key)
        log.warning("[ai] provider desconhecido na ordem: '%s' — pulando.", name)
        return None

    for i, name in enumerate(order):
        img = _try(name)
        if img is not None:
            return img
        nxt = order[i + 1] if i + 1 < len(order) else None
        if nxt:
            log.info("[ai] %s não retornou — caindo no %s.", name, nxt)
    return None


# ════════════════════════════════════════════════════════════════════════════
# ONE PIECE — VISUAL CONTROLLER (subject library + concept→subject mapper)
# ════════════════════════════════════════════════════════════════════════════
# Spec do Vinicius: TODA linha do One Piece vira UM subject on-brand (personagem,
# emblema ou ícone) — NUNCA cenário genérico (pedra solta / oceano vazio). Beats
# abstratos usam o EMBLEMA do conceito. Copyright afrouxado (decisão do canal): pode
# retratar personagens reais como AI art em estilo One Piece (Content ID aceito →
# canal mira alcance em TikTok/Reels). Ordem de prioridade da imagem por cena:
#   1) personagem NOMEADO na fala → imagem REAL do Fandom (mais fiel) [já existente]
#   2) beat abstrato → CONCEITO→subject deste mapa → AI render estilo One Piece
#   3) nada casou → subject default (luffy; jolly_roger se o beat é sombrio)
# Em nenhum caso emite paisagem vazia ou Pexels live-action.
#
# v5 — FICHAS CANÔNICAS (sheet injection). Cada entrada de PERSONAGEM agora carrega a
# ficha COMPLETA do cd-nicho-onepiece (anchor + hair + eyes + marks + outfit + accessories
# + build + palette), pra a IDENTIDADE ficar fiel/consistente entre shots. As CENAS seguem
# livres (ângulo/ação/composição do v4) — só os TRAÇOS do personagem ficam travados.
# NOTA "gota azul/lágrima": a scar-under-left-eye do Luffy/Gear5 é traço REAL (mantida e
# descrita como STITCHED HORIZONTAL SCAR, não teardrop). A supressão da GOTA é feita por
# clause POSITIVA (_OP_FACE_CLARITY_LOCK) injetada em todo prompt — FLUX schnell (CFG=1)
# ignora negative, então descrevemos o estado desejado ("clean dry face"), não o proibido.
_OP_SUBJECT_LIBRARY = {
    "luffy":           "straw hat with a red band, an open sleeveless RED vest over a bare chest, messy spiky BLACK hair, a SHORT HORIZONTAL stitched scar below his left eye (NOT a teardrop), a lean athletic young man with a huge carefree grin, blue knee-length shorts and sandals, a cartoonish rubber body, fist raised in a dynamic low-angle heroic pose, a clean dry face",
    "gear5_luffy":     "pure SNOW-WHITE hair flared upward with flame-like wavy tips (pure white, NOT blond, NOT yellow, NOT golden), an all-WHITE sleeveless vest and shorts, a floating WHITE SMOKE-RING halo hovering above his head like a cloud ring, pure white blank joyful eyes with thick dark rims, the same short horizontal stitched scar below his left eye (a scar, NOT a teardrop), an enormous manic carefree grin, the straw hat tilted on his head, a cartoonish elastic rubber-toon godlike body, warm golden liberation light, a clean dry face",
    "straw_hats":      "the Straw Hat Pirates crew standing together on the deck of their ship, backs to camera facing the horizon, heroic ensemble",
    "joy_boy_nika":    "a luminous pure-WHITE mythic warrior of liberation with flame-shaped curling-upward WHITE hair, a glowing white liberation aura, a wide joyful freeing grin with simple happy glowing curved eyes (iconographic, not a detailed portrait), beating the drums of liberation, a joyful dancing larger-than-life posture, warm golden light, a clean dry face",
    "imu":             "a SINGLE ominous glowing piercing eye visible through deep shadow, the face KEPT FULLY IN DARKNESS (never a clear rendered face), a slender androgynous ruler seated on the Empty Throne, long dark flowing hair draping over the shoulders, a long regal flowing dark royal-blue and gold cloak, a severe monarchic silhouette, candlelit darkness, cold royal blue and deep shadow",
    "gorosei":         "five grim elderly powerful men in matching DARK formal suits with long white and grey hair and beards, clearly differentiated (one bald with a long white beard, one with a topknot, one slim and severe, one stout), each carrying a personal weapon, standing in a dim marble chamber radiating cold sinister authority",
    "yonko":           "a Yonko Emperor of the Sea — a colossal intimidating pirate figure radiating menace, a huge muscular scarred body and a commanding presence, against a stormy ocean and lightning",
    "vegapunk":        "an old genius scientist with a glowing lightbulb-shaped emblem on his forehead, wild grey hair, a white lab coat, surrounded by glowing holographic screens and humming machinery in a high-tech Egghead laboratory",
    "straw_hat":       "a single weathered straw hat with a red ribbon resting on a wooden ship deck, soft cinematic backlight, the sea behind",
    "jolly_roger":     "the Straw Hat Pirates Jolly Roger, a white skull wearing a straw hat on a tattered black flag snapping in a stormy wind",
    "thousand_sunny":  "a grand lion-headed golden pirate galleon with a sunflower figurehead and billowing sails cutting across a glittering ocean sunset",
    "gear5_nika":      "pure SNOW-WHITE hair flared upward with flame-like tips (pure white, NOT blond, NOT yellow, NOT golden), an all-WHITE vest and shorts, a floating WHITE SMOKE-RING halo above his head, pure white blank joyful eyes with thick dark rims, the short horizontal stitched scar below his left eye (a scar, NOT a teardrop), an enormous manic carefree grin, the straw hat tilted on his head, a cartoonish elastic rubber-toon body, joyful reality-bending rings of light and warm golden liberation glow, a clean dry face",
    "haki":            "a clenched fist erupting with conqueror's Haki, crackling black-and-purple lightning splitting a dark stormy sky",
    "poneglyph":       "a massive ancient cube-shaped Poneglyph of black stone carved with glowing red ancient hieroglyphs, standing in a torchlit ruin",
    "road_poneglyph":  "a glowing red Road Poneglyph projecting a beam of light across an antique sea chart toward a hidden island",
    "devil_fruit":     "a single exotic Devil Fruit with a hypnotic spiral pattern, floating and pulsing with a surreal otherworldly glow",
    "ancient_weapon":  "a colossal silhouette of an ancient weapon of mass destruction rising over the sea — a titanic battleship and a giant sea king — apocalyptic scale",
    "buster_call":     "a Buster Call: a ring of Marine warships unleashing a storm of cannon fire onto a burning island, smoke and explosions",
    "grand_line_map":  "an antique nautical map of the One Piece world, the Red Line and Grand Line inked across aged parchment with a brass compass rose",
    "void_century":    "the ruins of a lost ancient kingdom from the Void Century — toppled stone pillars and fading carved inscriptions under a brooding sky",
    "empty_throne":    "the Empty Throne of the World Government, an ornate seat of swords in a vast dark hall, a looming hooded shadow, candlelight",
    "one_piece":       "the One Piece, a mysterious glowing treasure at the end of the world bathed in golden light beams, its true form hidden in radiance",
    "world_government":"the World Government emblem of four linked dark circles on a white flag, raised over an imposing marble government hall",
    "marines":         "a fleet of Marine warships under the Marine seagull emblem and banners, sailing in formation under a dramatic sky",
    "celestial_dragons":"the Celestial Dragons, arrogant nobles in white robes and bubble helmets on golden thrones, oppressive grandeur",
    "will_of_d":       "the mysterious initial D glowing within an ancient carved stone, the unspoken Will of D, fateful and ominous",
    "ancient_kingdom": "a magnificent lost ancient kingdom at its height — grand spires and banners — moments before its fall, golden-age grandeur",
    "laugh_tale":      "Laugh Tale, the final mythical island at the end of the Grand Line shrouded in mist and golden light, journey's end",
    # Cenário/objeto NEUTRO on-brand (pad de fallback p/ shots scenery/object quando a lane
    # WEB não trouxe foto livre e a fala não casou conceito) — SEM personagem, SEM emblema IP.
    "op_world_scenery":"an evocative establishing landscape of the One Piece world — a vast open pirate sea under a dramatic sky, distant weathered islands, old sailing ships on the horizon, mist and golden light, NO people, NO characters, NO logos",
    # v4/v5 — signature traits do spec + fichas canônicas (mantém os demais; só ADICIONA / enriquece).
    "loki":            "a COLOSSAL ancient giant bound by enormous Sea-Prism-Stone iron CHAINS to the World Tree, long messy light MAGENTA hair styled into TWIN FRONT BRAIDS, massive ancient-giant HORNS (one wrapped in bandage), slitted eyes KEPT COVERED by bandage wrappings, a pointed magenta goatee, red tribal TATTOOS across his arms and shoulders, a metal plate on the ridge of his nose, a black horned helmet, a large PURPLE cape, a muscular bare tattooed torso, a manic tongue-out grin, extreme low-angle towering scale against frozen Elbaf cliffs",
    "elbaf":           "Elbaf, the legendary island of giants — colossal warriors, vast frozen cliffs and a towering World Tree under an icy sky, mythic scale",
    "shanks":          "medium-length tousled RED hair, THREE parallel vertical SCARS over his left eye, a MISSING LEFT ARM with the empty sleeve pinned, a tall lean-muscular man, a black captain's cloak draped over the shoulders like a mantle, an open white shirt and sash, calm commanding Emperor's authority, a clean dry face",
    "blackbeard":      "a huge heavyset bulky pirate with thick wild unkempt BLACK hair and a black beard, a signature GAP-TOOTHED missing-teeth grin, dark sunken menacing eyes under a heavy brow, a dark open captain's coat over a broad hairy chest, a swirling black darkness aura, overwhelming dread",
    "zoro":            "short cropped spiky GREEN moss-green hair, THREE katana sheathed at his left hip, a GREEN haramaki belly-wrap, a long vertical SCAR over his closed left eye, three gold ball earrings on his left ear, a tall broad heavily-muscled swordsman, a dark green-black open long coat over a bare scarred chest, black pants, stern composed, a clean dry face",
    "nami":            "long straight ORANGE hair, a blue pinwheel-and-tangerine tattoo on her left upper arm, a blue-and-white midriff-baring outfit, a slim athletic young woman holding the segmented blue Clima-Tact staff, sharp confident brown eyes, a confident smirk, a clean dry face",
    # v6 — 10 personagens NOVOS (recognition-first; cd-nicho-onepiece).
    "robin":           "raven-BLACK shoulder-length hair, calm dark blue eyes, a serene knowing half-smile, a slim adult woman in a long dark coat, the only scholar who can read Poneglyphs, a hand-bloom (Hana Hana) motif blossoming nearby, scholarly composed, a clean dry face",
    "garp":            "a massive elderly Marine with white hair and a scar across his left eye, the word MARINE on a coat of justice, a barrel-chested towering grandfather figure, a dog-eared cap, a gruff fearless grin, an immense raised fist",
    "dragon":          "a red DRAGON-shaped tattoo covering the left side of his face, long black hair, a hooded green cloak, a calm severe revolutionary leader with arms crossed, ominous swirling wind, world's most wanted, a clean dry face",
    "sabo":            "wavy BLOND hair under a top hat with goggles, a burn scar over his left eye, a long blue dragon-crest coat, wielding the flickering Mera Mera flame fruit and a metal pipe-staff, a noble defiant grin, a clean dry face",
    "saul":            "a gentle GIANT with tall white hair sweeping back, a wide goofy open-mouthed laugh, a Marine coat, a towering kindly old giant in a protective stance over a small child, warm",
    "kaido":           "a colossal muscular man with long wild black hair and RED demon-like horns, a blue Hawaiian shirt, a dragon tattoo, a club-like spiked kanabo mace, a fearsome scarred torso, storm and lightning, towering brute scale",
    "big_mom":         "a gigantic woman with frizzy salmon-PINK hair, a flower-and-polka-dot dress, manic wide eyes and a terrifying childlike grin, an enormous towering matron with candy and soul-power motifs, oppressive scale",
    "whitebeard":      "an enormous elderly pirate with a white CRESCENT moustache, a bare muscular chest with a purple crescent scar, a black bandana, a giant bisento glaive, the strongest man in the world, a tremor shockwave aura",
    "roger":           "the Pirate King with swept-back BLACK hair and a distinctive curved black moustache, a red captain's coat with a fur collar, a broad confident larger-than-life grin, golden-age pirate aura, a clean dry face",
    "shamrock":        "Shanks' twin — medium RED hair but a cruel cold expression, white Holy Knight armor and robes, a three-headed Cerberus longsword, a World Noble's regal menace, a clean dry face",
}

# (gatilhos, subject) — ordem importa: específico ANTES de genérico. 1º match vence.
_OP_CONCEPT_MAP = [
    (["loki", "prince of elbaf", "chained prince", "harald", "giant prince"], "loki"),
    (["elbaf", "land of giants", "island of giants", "giant warriors", "world tree"], "elbaf"),
    (["gear 5", "gear fifth", "gear fourth", "awakening", "awakened", "drums of liberation"], "gear5_nika"),
    (["joy boy", "joyboy", "nika", "sun god", "liberation", "liberate", "liberator", "freedom", "free the", "savior", "warrior of liberation"], "joy_boy_nika"),
    (["empty throne", "the throne", "imu", "imu-sama", "ruler of the world", "monarch"], "imu"),
    (["gorosei", "five elders", "the elders", "warrior god", "saint jaygarcia"], "gorosei"),
    (["celestial dragon", "celestial dragons", "tenryubito", "world nobles", "the nobles"], "celestial_dragons"),
    (["road poneglyph", "four roads", "red poneglyph"], "road_poneglyph"),
    (["laugh tale", "raftel", "final island", "end of the grand line"], "laugh_tale"),
    (["world government", "world gov", "the government", "the marines hq", "mariejois", "holy land"], "world_government"),
    (["buster call", "annihilat", "wiped out", "wipe out", "destroyed the island", "ohara", "razed"], "buster_call"),
    (["marine", "marines", "navy", "admiral", "fleet admiral", "warships"], "marines"),
    (["poneglyph", "poneglyphs", "carved", "tablet", "hieroglyph", "cannot read", "can't read", "no one can read", "no one alive can read", "stones", "red stone"], "poneglyph"),
    (["void century", "blank period", "lost century", "hundred year", "forbidden history", "lost history", "erased history", "missing century"], "void_century"),
    (["ancient kingdom", "lost kingdom", "great kingdom", "the kingdom that"], "ancient_kingdom"),
    (["will of d", "the d", "initials d", "born with the d", "carry the d"], "will_of_d"),
    (["haki", "conqueror", "conqueror's", "willpower", "armament", "observation", "advanced haki"], "haki"),
    (["devil fruit", "paramecia", "zoan", "logia", "mythical zoan", "the fruit"], "devil_fruit"),
    (["ancient weapon", "pluton", "poseidon", "uranus", "weapon of mass", "weapon that"], "ancient_weapon"),
    (["thousand sunny", "going merry", "set sail", "sailing", "the ship", "aboard"], "thousand_sunny"),
    (["straw hat pirates", "the crew", "strawhats", "straw hats", "nakama", "his crew"], "straw_hats"),
    # ── PERSONAGENS NOMEADOS — ANTES de 'luffy' (luffy claima o substring 'monkey d',
    #    que colidiria com 'monkey d garp'/'monkey d dragon') e ANTES do genérico 'yonko'.
    (["robin", "nico robin", "read poneglyph", "archaeologist"], "robin"),
    (["monkey d garp", "garp", "the fist"], "garp"),
    (["monkey d dragon", "dragon", "revolutionary"], "dragon"),
    (["sabo", "flame fist", "revolutionary army"], "sabo"),
    (["saul", "jaguar d saul", "jaguar d"], "saul"),
    (["shamrock", "figarland", "holy knight"], "shamrock"),
    (["gol d roger", "gold roger", "roger", "pirate king"], "roger"),
    (["whitebeard", "edward newgate"], "whitebeard"),
    (["big mom", "charlotte linlin"], "big_mom"),
    (["kaido", "beast pirates"], "kaido"),
    (["pirate king", "future king", "captain luffy", "luffy", "monkey d", "straw hat luffy"], "luffy"),
    (["shanks", "red-haired", "red haired"], "shanks"),
    (["blackbeard", "teach", "marshall d", "kurohige"], "blackbeard"),
    (["zoro", "roronoa", "santoryu", "three-sword", "three sword", "swordsman"], "zoro"),
    (["nami", "navigator", "cat burglar", "clima-tact", "clima tact"], "nami"),
    (["yonko", "yonkou", "emperor", "emperors"], "yonko"),
    (["vegapunk", "scientist", "technology", "science", "laboratory", "lineage factor"], "vegapunk"),
    (["jolly roger", "pirate flag", "their flag", "emblem", "symbol of"], "jolly_roger"),
    (["grand line", "red line", "navigate", "world map", "the route", "new world", "calm belt"], "grand_line_map"),
    (["greatest treasure", "the one piece", "roger's treasure", "wealth fame power", "left behind", "the treasure", "ultimate treasure"], "one_piece"),
    (["devil", "power", "war", "battle", "clash", "fight", "fought", "conflict"], "haki"),
    (["history", "truth", "secret", "secrets", "hidden", "erase", "erased", "covered up"], "poneglyph"),
]
_OP_DEFAULT_SUBJECT = "luffy"
_OP_DARK_DEFAULT_SUBJECT = "jolly_roger"
# Subjects que retratam uma PESSOA NOMEADA específica (não cenário/emblema/objeto). Uma
# cena 'scenery'/'object' NUNCA deve renderizar um destes — é o que causava o bug "Elbaf
# virou Loki": a cena de cenário caía no concept-map e o 1º gatilho que casava no blob era
# um personagem (loki/luffy/imu...). Cenário usa só conceitos de LUGAR/OBJETO/EMBLEMA.
_OP_CHARACTER_SUBJECTS = frozenset({
    "luffy", "gear5_luffy", "gear5_nika", "joy_boy_nika", "imu", "gorosei", "yonko",
    "vegapunk", "loki", "shanks", "blackbeard", "zoro", "nami", "robin", "garp",
    "dragon", "sabo", "saul", "kaido", "big_mom", "whitebeard", "roger", "shamrock",
    "celestial_dragons", "straw_hats",
})
# Pad de fallback p/ shots scenery/object (render IA SEM personagem) quando a lane WEB
# não trouxe foto livre e a fala não disparou um conceito do _OP_CONCEPT_MAP.
_OP_SCENERY_PAD_SUBJECT = "op_world_scenery"
_OP_DARK_WORDS = ("death", "dark", "fear", "erase", "destroy", "war", "blood",
                  "betray", "kill", "shadow", "evil", "danger", "threat", "doom")

# ── CONTROLLER v3: ART STYLE LOCK + AVOID TERMS ─────────────────────────────
# Trava de estilo anexada a TODO prompt One Piece: força o FLUX a render no estilo
# OFICIAL do anime (cel-shaded 2D, traço Oda) em vez de "digital art" genérica/3D.
# É o que faz a imagem PARECER One Piece (requisito a). NÃO afeta outros nichos:
# só os helpers _op_* (exclusivos do canal one-piece) usam estas constantes.
_OP_ART_STYLE_LOCK = (
    "in the official One Piece anime style by Eiichiro Oda, Toei Animation, "
    "cel-shaded 2D anime, bold clean black ink outlines, flat vibrant saturated colors, "
    "expressive exaggerated Oda character proportions, dramatic shonen anime key-visual composition"
)
# ── PROBLEM 1: supressão da "gota azul / lágrima / suor" sob o olho ──────────────
# NUANCE CRÍTICA: a lane usa FLUX schnell (Cloudflare/Pollinations), destilado, CFG=1 →
# **IGNORA negative prompt**. Pior: jogar os tokens proibidos ("tear, teardrop, sweat
# drop") numa cláusula "Avoid:" no fim do prompt POSITIVO faz o FLUX renderizar a gota
# (ele lê os tokens, não a negação). Por isso NÃO usamos negative e NÃO despejamos esses
# tokens no prompt. A supressão que FUNCIONA é uma cláusula POSITIVA descrevendo o ESTADO
# desejado da face — "clean dry face, clear smooth skin under the eyes" — injetada em TODO
# prompt One Piece. Descrever o que QUEREMOS (pele limpa e seca) é o que o FLUX honra.
# A scar-under-left-eye do Luffy é traço REAL e fica nas fichas (descrita como STITCHED
# HORIZONTAL SCAR, never a teardrop) — o que removemos é só a GOTA.
_OP_FACE_CLARITY_LOCK = (
    "clean dry face with clear smooth skin under the eyes, "
    "no teardrop, no falling tear, no sweat drop, no water droplet on the face, "
    "no blue drop or marking under the eye"
)
# Termos a EVITAR sempre (paisagem vazia / off-style / artefato da gota). Mantidos como
# DOCUMENTAÇÃO/metadata e usados na cláusula "Avoid:" SÓ para os termos de COMPOSIÇÃO
# (paisagem vazia, off-style). Os termos de ARTEFATO-DE-FACE (tear/sweat/drop) NÃO são
# despejados como tokens no prompt — eles são tratados pela _OP_FACE_CLARITY_LOCK positiva
# acima (FLUX ignora negative e renderiza tokens crus). Ver _op_avoid_clause().
_OP_AVOID_TERMS = (
    "empty ocean", "calm sea", "plain seascape", "random rock", "lone boulder",
    "generic island", "empty beach", "plain sky", "nature b-roll",
    "scenery without subject", "unrelated landscape", "photorealistic",
    "3d render", "western cartoon", "generic anime", "neutral portrait",
    # artefatos de face (a "gota azul"): listados para metadata/doc — NÃO injetados como
    # tokens crus no prompt (FLUX renderiza); suprimidos via _OP_FACE_CLARITY_LOCK positiva.
    "tear", "teardrop", "crying", "sweat drop", "sweatdrop",
    "water droplet on face", "blue drop under eye",
)
# Termos de ARTEFATO-DE-FACE que NUNCA podem virar token cru no prompt do FLUX (CFG=1).
_OP_FACE_ARTIFACT_TERMS = frozenset({
    "tear", "teardrop", "crying", "sweat drop", "sweatdrop",
    "water droplet on face", "blue drop under eye",
})

# ── FIDELIDADE v6: NEGATIVE PROMPT + SEED ESTÁVEL POR PERSONAGEM ─────────────
# PONTO ÚNICO de configuração do negative prompt do One Piece. Só vale onde o motor
# HONRA negative (AI Horde via cláusula '### <neg>'; ver _prov_aihorde). O FLUX schnell
# do Cloudflare/Pollinations é destilado (CFG=1) e IGNORA negative — nesses motores os
# tokens proibidos NÃO são despejados (renderizariam). Por isso o negative é um lever
# extra do AI Horde, não a defesa principal (a defesa de face é a _OP_FACE_CLARITY_LOCK
# positiva). Mantenha curto: negative gigante dispersa o sampler.
_OP_NEGATIVE_PROMPT = (
    "western cartoon, american cartoon, disney style, pixar, 3d render, cgi, "
    "photorealistic, realistic photo, live action, generic anime, off-model, "
    "wrong character, inconsistent face, deformed, disfigured, bad anatomy, "
    "extra fingers, extra limbs, mutated hands, blurry, lowres, watermark, "
    "signature, text, logo, ugly, malformed"
)


def _op_char_seed(subject_key: str) -> Optional[int]:
    """Seed ESTÁVEL por PERSONAGEM (ataca a inconsistência entre shots do mesmo vídeo de
    forma barata): o MESMO subject_key sempre mapeia pro MESMO seed → o motor parte do
    mesmo ponto de ruído e tende a manter a mesma "cara". Hash estável (sha1 do nome) →
    inteiro em [0, 2**31). Retorna None se subject_key vier vazio (cena sem subject travado
    → seed livre, p/ não congelar cenário/objeto). Determinístico entre execuções (sha1,
    não hash() — que é salgado por processo no Python)."""
    key = (subject_key or "").strip().lower()
    if not key:
        return None
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return int(h[:8], 16) % (2 ** 31)


def _op_avoid_clause() -> str:
    """Cláusula 'Avoid:' SÓ com os termos de COMPOSIÇÃO/off-style (paisagem vazia, 3d, etc).
    Filtra os termos de ARTEFATO-DE-FACE (tear/sweat/drop): no FLUX schnell (CFG=1, ignora
    negative) jogar esses tokens no prompt positivo CAUSA a gota — a supressão deles é feita
    pela _OP_FACE_CLARITY_LOCK positiva. Retorna 'Avoid: a, b, c.'."""
    safe = [t for t in _OP_AVOID_TERMS if t not in _OP_FACE_ARTIFACT_TERMS]
    return "Avoid: " + ", ".join(safe) + "."

# ── CONTROLLER v4: SHOT / CAMERA LIBRARY ────────────────────────────────────
# Em vez de sempre um retrato neutro, o v4 enquadra CADA cena como um SHOT
# cinematográfico deliberado (POV, over-the-shoulder, low/high angle, close-up,
# wide, dutch tilt, montagem). Cada entrada é uma cláusula de câmera pronta pra
# entrar no início do prompt. As chaves casam com o que o roteirista pode pedir
# em visual_context.shot_type (opcional); senão o motor escolhe pela BATIDA da
# linha (ver _op_pick_shot). Exclusivo do one-piece (só os helpers _op_* usam).
_OP_SHOT_LIBRARY = {
    "pov":          "first-person POV shot, the viewer looking at the scene",
    "over_shoulder":"over-the-shoulder shot, a dark foreground silhouette framing the subject ahead in focus",
    "low_angle":    "dramatic low-angle worm's-eye shot, the subject towering and powerful",
    "high_angle":   "high-angle bird's-eye shot, the subject small, trapped and overwhelmed",
    "close_up":     "extreme close-up shot on the eyes or clenched fist, intense emotion",
    "wide":         "wide establishing shot, epic scale of the place and the moment",
    "dutch":        "dutch-tilt canted shot, a sense of unease and wrongness",
    "montage":      "composed split-frame montage shot, two subjects facing off in one frame",
}
# Gatilhos de batida → shot (1º match vence). Casa a câmera ao momento da fala:
# revelação → close-up/POV; poder → low angle; tragédia/derrota → high angle;
# escala de lugar/frota → wide; reviravolta → dutch tilt.
_OP_SHOT_TRIGGERS = [
    (["towering", "powerful", "strongest", "unstoppable", "god", "conqueror", "dominat", "rises", "rising", "ruler"], "low_angle"),
    (["defeated", "fell", "fallen", "trapped", "doomed", "small", "buried", "crushed", "alone", "execution", "executed", "died", "death"], "high_angle"),
    (["realizes", "reveal", "revealed", "secret", "truth", "notice", "noticed", "eye", "fear", "shock", "stunned"], "close_up"),
    (["fleet", "island", "armada", "ocean", "across the", "whole world", "everywhere", "entire", "vast", "scale"], "wide"),
    (["wrong", "twist", "lie", "lied", "betray", "betrayed", "hidden agenda", "not what", "actually"], "dutch"),
    (["looking at", "stares at", "watches", "watching", "stands before", "faces", "sees"], "pov"),
    (["confront", "against", "faces off", "stares down", "stood against"], "over_shoulder"),
]


def _op_pick_shot(blob: str, vctx: dict) -> str:
    """
    Escolhe a cláusula de SHOT/CAMERA do v4 pra esta cena. Prioridade:
      1. vctx.shot_type explícito do roteirista (se mapear pra _OP_SHOT_LIBRARY);
      2. heurística pela batida da fala (_OP_SHOT_TRIGGERS);
      3. default 'close_up' (emoção do personagem — melhor que retrato neutro).
    Retorna "" se shot_type='none' (operador desliga a câmera para esta cena).
    Aceita também uma cláusula de câmera livre via vctx.camera (anexada à do shot).
    """
    vctx = vctx or {}
    requested = (vctx.get("shot_type") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if requested == "none":
        return ""
    shot_key = requested if requested in _OP_SHOT_LIBRARY else ""
    if not shot_key:
        for triggers, key in _OP_SHOT_TRIGGERS:
            if any(t in blob for t in triggers):
                shot_key = key
                break
    if not shot_key:
        shot_key = "close_up"
    clause = _OP_SHOT_LIBRARY[shot_key]
    camera = (vctx.get("camera") or "").strip()
    if camera:
        clause = f"{clause}, {camera}"
    return clause


def _op_clean_action(line_text: str) -> str:
    """
    Extrai a AÇÃO/EMOÇÃO da linha narrada para casar a imagem com o que a linha diz
    (requisito b: subject+action+emotion). Heurística leve: tira pontuação/aspas e
    encurta para ~14 palavras (o FLUX perde o fio em frases longas). Devolve "" se a
    linha for vazia — nesse caso o prompt usa só o subject estático da biblioteca.
    """
    t = re.sub(r"[\"“”'’]", "", (line_text or "")).strip()
    t = re.sub(r"\s+", " ", t)
    words = t.split()
    if not words:
        return ""
    return " ".join(words[:14])

# PURE-AI DEFAULT (v4): o b-roll do one-piece é render IA cinematográfico SOMENTE.
# Os stills reais baixa-res do Fandom (painel cru de mangá / emblema PNG sem contexto)
# ficam atrás de uma flag OPCIONAL — default OFF. Ligue com OP_USE_FANDOM_STILLS=1 se
# quiser voltar a misturar imagens reais do Fandom (ex.: símbolos exatos do WG/Marines).
def _op_use_fandom_stills() -> bool:
    """True só se o operador ligar OP_USE_FANDOM_STILLS=1. Default OFF = PURE-AI."""
    return os.environ.get("OP_USE_FANDOM_STILLS", "0").strip() == "1"


# ── LANE WEB (foto real PD/CC para shots de CENÁRIO/OBJETO) ──────────────────
# broll_kind='scenery'|'object' pode usar FOTO REAL de fonte livre (wikimedia/openverse/
# archive.org via image_providers lane "burn", que JÁ filtra licença p/ PD/CC0/CC-BY/CC-BY-SA).
# Em CIMA disso aplicamos um guardrail EXTRA anti-IP One Piece: descarta qualquer match que
# pareça still de anime / fanart (título/fonte/query citando "one piece", "anime", "fanart",
# nome de personagem, wiki de fã). Em QUALQUER dúvida → descarta → cai pro render IA.
# NUNCA é chamada para broll_kind='character'.

# Tokens que, no TÍTULO/fonte/atribuição de um resultado web, denunciam still/fanart de OP.
# Match por PALAVRA INTEIRA (\b) — não substring — pra não pegar "manga" em "mangabeira" etc.
_OP_WEB_IP_TOKENS = (
    "one piece", "onepiece", "anime", "manga", "fanart", "fan art", "fan-art",
    "cosplay", "shonen", "shounen", "shonen jump", "toei", "shueisha",
    "eiichiro oda", "crunchyroll", "funimation", "screencap", "doujin", "vtuber",
)
# Nomes de personagens DISTINTOS (improváveis em foto de cenário) → match por palavra
# inteira sozinho já descarta (é quase certo fanart/still daquele personagem).
_OP_WEB_CHARACTER_NAMES = (
    "luffy", "zoro", "roronoa", "sanji", "usopp", "chopper", "franky",
    "jinbe", "jimbei", "shanks", "buggy", "blackbeard", "kaido", "kaidou",
    "linlin", "whitebeard", "newgate", "sabo", "akainu",
    "aokiji", "kizaru", "sengoku", "mihawk", "doflamingo",
    "katakuri", "yamato", "hancock", "rayleigh",
    "joyboy", "joy boy", "nika", "gorosei", "vegapunk",
    "bonney", "harald", "arlong", "wapol", "bellamy", "bartolomeo",
    "gear 5", "gear fifth", "straw hat", "going merry", "thousand sunny", "jolly roger",
)
# Nomes que TAMBÉM são palavras comuns em inglês (boat≠boa, lawn≠law, space≠ace...). Mesmo
# como palavra inteira dão falso positivo em foto de cenário ("a kid", "the law", "a dragon").
# Só descartam se VIEREM ACOMPANHADOS de um sinal de anime/OP no haystack (cosplay/anime/etc).
_OP_WEB_AMBIGUOUS_NAMES = (
    "ace", "law", "kid", "robin", "brook", "boa", "dragon", "carrot", "vivi",
    "koby", "smoker", "moria", "kuro", "enel", "marco", "nami", "imu", "teach",
    "garp", "kuma", "loki", "perona", "tashigi",
)
# Fontes/domínios cujo conteúdo é IP / fan-made → nunca aceitar pela lane web.
_OP_WEB_BAD_SOURCE = (
    "fandom.com", "wikia", "deviantart", "pinterest", "artstation", "zerochan",
    "danbooru", "gelbooru", "safebooru", "myanimelist", "anilist", "crunchyroll",
    "reddit.com/r/onepiece", "tumblr", "pixiv",
)


def _op_word_hit(haystack: str, terms) -> Optional[str]:
    """True-ish: devolve o 1º termo de `terms` presente em `haystack` como PALAVRA INTEIRA
    (boundary não-alfanumérico dos dois lados). Evita o falso positivo de substring (ex.:
    'boa' em 'boat', 'ace' em 'space'). Termos com espaço/hífen casam como frase."""
    import re as _re
    for t in terms:
        if _re.search(r"(?<![a-z0-9])" + _re.escape(t) + r"(?![a-z0-9])", haystack):
            return t
    return None


def _op_web_burn_safe(meta: dict, query: str) -> tuple:
    """
    Guardrail EXTRA (além do filtro de licença do image_providers) p/ a lane WEB de
    cenário/objeto: aceita SÓ se a licença for de fato livre E o resultado NÃO parecer
    still de anime / fanart de One Piece. Qualquer dúvida → rejeita (cai pra IA).

    Checa: (1) licença explicitamente livre (PD/CC0/CC-BY/CC-BY-SA, sem NC/ND nem vazia) OU
    'pexels' (stock free-to-use explícito do 2º passe scenery/object — copyright afrouxado p/
    cenário genérico de mundo, NÃO é IP do One Piece); (2) título/atribuição/fonte/url não citam
    One Piece, anime, fanart, nem nome de personagem (match por PALAVRA INTEIRA — É O QUE de
    fato barra leak de IP, e roda também p/ pexels); (3) domínio da fonte não é wiki de fã/booru.

    Retorna (ok: bool, reason: str) — reason explica o descarte no log.
    """
    import image_providers as _ip
    license_str = str(meta.get("license", "")).strip().lower()
    # (1) licença: PD/CC0/CC-BY/CC-BY-SA (classificador do image_providers) OU 'pexels' (stock
    # free-to-use do 2º passe scenery/object). pexels NÃO é CC, mas é fonte stock EXPLÍCITA p/
    # cenário/objeto genérico (mar/ilha/ruína) — o gate de IP real é a heurística de texto abaixo,
    # que continua valendo p/ pexels. NUNCA chega aqui no caminho 'character' (guard no caller).
    if license_str != "pexels" and not _ip._license_is_burn_safe(license_str):
        return False, f"licença não-livre/vazia ({license_str!r})"
    if "ai-generated" in license_str or "unknown" in license_str:
        return False, f"licença suspeita ({license_str!r})"
    # (2)+(3) heurística anti-IP no TEXTO do resultado (título/atribuição/fonte/url+query).
    haystack = " ".join(str(meta.get(k, "")) for k in
                        ("attribution", "source_url", "provider", "query")).lower()
    haystack += " " + (query or "").lower()
    hit = _op_word_hit(haystack, _OP_WEB_IP_TOKENS)
    if hit:
        return False, f"token anime/OP {hit!r} no texto"
    hit = _op_word_hit(haystack, _OP_WEB_CHARACTER_NAMES)
    if hit:
        return False, f"nome de personagem {hit!r} no texto"
    # nome ambíguo (palavra comum) só descarta se acompanhado de sinal de anime/OP.
    amb = _op_word_hit(haystack, _OP_WEB_AMBIGUOUS_NAMES)
    if amb and _op_word_hit(haystack, _OP_WEB_IP_TOKENS + ("cosplay", "character", "anime")):
        return False, f"nome ambíguo {amb!r} + sinal de anime/OP"
    src = str(meta.get("source_url", "")).lower()
    for bad in _OP_WEB_BAD_SOURCE:
        if bad in src:
            return False, f"fonte IP/fan {bad!r}"
    return True, "ok"


def _op_find_sidecar(path: Path) -> Optional[dict]:
    """Lê o sidecar de metadata (<stem>.json) de um arquivo baixado pela lane web.
    Procura na pasta do arquivo, no img_cache e em IMG_CACHE_DIR (o .jpg é copiado pro
    out_dir, mas o .json fica no cache). Retorna o dict ou None."""
    stem = path.stem
    dirs = [path.parent]
    try:
        dirs.append(Path.cwd() / "out" / "img_cache")
    except Exception:
        pass
    env_cache = os.environ.get("IMG_CACHE_DIR", "").strip()
    if env_cache:
        dirs.append(Path(env_cache))
    for d in dirs:
        mp = d / f"{stem}.json"
        if mp.exists():
            try:
                return json.loads(mp.read_text(encoding="utf-8"))
            except Exception:
                return None
    return None


def _op_fetch_web_burn(query: str, n: int, broll_dir: Path,
                       kind: str = "scenery") -> list:
    """
    Tenta resolver até `n` FOTOS REAIS para um shot de CENÁRIO/OBJETO do One Piece via
    image_providers. 1º passe: lane "burn" CC-only (wikimedia/openverse/archive.org, PD/CC).
    2º passe (anti-preto): se o pool CC vier < n, completa com pexels_photo (stock free-to-use)
    — cenário do One Piece (mar/ilha/ruína/navio) NÃO é IP da Toei/Shueisha, e o copyright
    afrouxado cobre lugar/objeto genérico. O 2º passe é gateado por OP_SCENERY_ALLOW_PEXELS
    (default "1" = ligado). EM AMBOS os passes o guardrail _op_web_burn_safe roda e DESCARTA
    (apagando o arquivo) qualquer imagem que falhe a heurística anti-OP (still de anime/fanart/
    nome de personagem) — pexels não bypassa ESSE filtro, só o gate de licença CC.

    Retorna lista de Paths ACEITOS (pode ser menor que n, ou vazia → caller cai pra IA).
    NUNCA deve ser chamada para broll_kind='character' (assert por código abaixo).
    """
    import image_providers as _ip
    # SALVAGUARDA DURA: cenário/objeto SÓ. Personagem (Content ID Toei/Shueisha) jamais passa
    # por foto web — nem CC nem pexels. Se algum caller errar o kind, retorna vazio (cai pra IA).
    kind = (kind or "scenery").strip().lower()
    if kind not in ("scenery", "object"):
        log.warning("[one-piece][web] kind=%r não é scenery/object — recusando foto web (cai pra IA).", kind)
        return []
    q = (query or "").strip()
    if not q:
        return []

    accepted: list = []

    def _harvest(paths: list, src_label: str) -> None:
        """Aplica o guardrail anti-OP e acumula em `accepted` (compartilhado entre os passes)."""
        for p in paths:
            if len(accepted) >= n:
                # excedente do pool que não vamos usar — apaga pra não poluir broll_dir/créditos
                try:
                    p.unlink()
                except Exception:
                    pass
                continue
            meta = _op_find_sidecar(p) or {}
            ok, reason = _op_web_burn_safe(meta, q)
            if ok:
                accepted.append(p)
                log.info("[one-piece][web] ACEITA (%s, %s): %s license=%s src=%s",
                         kind, src_label, p.name, meta.get("license", "?"),
                         str(meta.get("source_url", ""))[:60])
            else:
                log.info("[one-piece][web] DESCARTADA (%s, %s): %s license=%s src=%s",
                         reason, src_label, p.name, meta.get("license", "?"),
                         str(meta.get("source_url", ""))[:60])
                try:
                    p.unlink()  # não deixa imagem reprovada vazar pra timeline/créditos
                except Exception:
                    pass

    # ── 1º passe: lane burn CC-only ──────────────────────────────────────────────
    # Pede um POOL maior que n: find_images para assim que junta `count`, então se a 1ª
    # imagem é boa-de-licença mas o NOSSO guardrail anti-OP reprova, ainda sobram opções.
    pool = max(n, min(n * 3, 8))
    try:
        cc_paths = _ip.find_images(q, "one-piece-theories-and-stories", "burn",
                                   count=pool, out_dir=broll_dir,
                                   providers=_ip.BURN_CC_PROVIDERS)
    except Exception as exc:
        log.warning("[one-piece][web] lane burn (CC) falhou p/ '%s': %s — segue.", q, exc)
        cc_paths = []
    _harvest(cc_paths, "CC")

    # ── 2º passe: pexels_photo (anti-preto) — SÓ scenery/object, gateado por env ──
    # kind já garantido scenery/object acima → inalcançável p/ character.
    allow_pexels = os.environ.get("OP_SCENERY_ALLOW_PEXELS", "1").strip().lower() not in ("0", "false", "no")
    if len(accepted) < n and allow_pexels:
        need = n - len(accepted)
        pex_pool = max(need, min(need * 3, 8))
        try:
            pex_paths = _ip.find_images(q, "one-piece-theories-and-stories", "burn",
                                        count=pex_pool, out_dir=broll_dir,
                                        providers=["pexels_photo"])
        except Exception as exc:
            log.warning("[one-piece][web] passe pexels falhou p/ '%s': %s — cai pra IA.", q, exc)
            pex_paths = []
        if pex_paths:
            log.info("[one-piece][web] pool CC insuficiente (%d/%d) → completando com pexels (%s).",
                     len(accepted), n, kind)
        _harvest(pex_paths, "pexels")

    return accepted


# Subjects que SÃO símbolos/bandeiras com imagem REAL limpa no Fandom: para esses,
# busca a imagem real (o símbolo EXATO, ex.: a bandeira do World Government) ANTES de
# gerar por IA — porque o FLUX faz um emblema genérico, não o símbolo canônico.
# (Só usado quando OP_USE_FANDOM_STILLS=1; no PURE-AI default tudo vira render IA.)
_OP_SUBJECT_FANDOM = {
    "world_government": "World_Government",
    "marines": "Marines",
}

# Personagens cuja imagem REAL do Fandom é fraca para Shorts (painel cru de mangá P&B,
# com balão em japonês) → preferimos um render IA colorido em estilo One Piece, coeso com
# o resto. Mapa: título do Fandom → subject_key da biblioteca. (Ex.: Imu só tem painel.)
_OP_PREFER_AI_SUBJECT = {
    "Nerona_Imu": "imu",
}

# Título do Fandom → subject_key, para descrever uma entidade nomeada na MONTAGEM "A vs B"
# (e em qualquer lugar que precise da descrição rica do subject a partir da entidade real).
_OP_TITLE_SUBJECT = {
    "Joy_Boy": "joy_boy_nika",
    "Monkey_D._Luffy": "luffy",
    "Nerona_Imu": "imu",
    "World_Government": "world_government",
    "Marines": "marines",
    "Marshall_D._Teach": "blackbeard",
    "Shanks": "shanks",
    "Loki": "loki",
    "Elbaf": "elbaf",
    "Roronoa_Zoro": "zoro",
    "Nami": "nami",
}


def _op_pick_subject(text: str, query: str) -> tuple:
    """
    Mapeia o CONCEITO de um beat (fala + query) para um subject One Piece on-brand.
    Retorna (subject_key, subject_description). Nunca devolve None — beat sem match
    cai no default (jolly_roger se o tom é sombrio; senão luffy).
    """
    blob = f"{text} {query}".lower()
    for triggers, subj in _OP_CONCEPT_MAP:
        if any(t in blob for t in triggers):
            return subj, _OP_SUBJECT_LIBRARY[subj]
    is_dark = any(w in blob for w in _OP_DARK_WORDS)
    subj = _OP_DARK_DEFAULT_SUBJECT if is_dark else _OP_DEFAULT_SUBJECT
    return subj, _OP_SUBJECT_LIBRARY[subj]


def _op_build_image_prompt(subject_desc: str, vctx: dict, action: str = "",
                           blob: str = "") -> str:
    """Monta o prompt final em estilo One Piece (template Controller v4) seguindo a fórmula
    SHOT/CAMERA + SUBJECT + ACTION + EMOTION + SETTING, com mood/palette do visual_context
    (defaults One Piece se vierem vazios).

    `action` = a fala da linha (subject+action+emotion). Quando presente, o prompt vira
    "{subject} — {ação da linha}, {traços do subject}" pra a imagem CASAR com o que a linha
    narra. `blob` (fala+query em minúsculas) escolhe o SHOT/CAMERA pela batida da cena.
    Sempre anexa o ART STYLE LOCK (faz a imagem PARECER o anime) e a cláusula de termos a
    evitar. O SHOT vem PRIMEIRO no prompt (o FLUX dá mais peso ao começo) pra enquadrar de
    fato a cena, em vez de cair num retrato neutro."""
    vctx = vctx or {}
    mood = (vctx.get("mood") or "epic, mysterious, hype").strip()
    palette = (vctx.get("palette") or "deep ocean blues, weathered gold, dramatic high-contrast light").strip()
    setting = (vctx.get("setting") or "the One Piece world of pirate seas, ancient ruins and the Holy Land").strip()
    action = (action or "").strip()
    # SHOT/CAMERA da batida (v4): enquadra a cena ANTES do subject.
    shot = _op_pick_shot(blob or action.lower(), vctx)
    shot_clause = f"{shot}: " if shot else ""
    # Subject + ação da linha (o que está acontecendo) ANTES dos traços de assinatura.
    subject_clause = f"{subject_desc}, depicting: {action}" if action else subject_desc
    # _OP_FACE_CLARITY_LOCK (positivo) suprime a "gota azul/lágrima/suor" — FLUX schnell
    # ignora negative, então descrevemos a pele limpa e seca em vez de proibir a gota.
    return (
        f"{shot_clause}{subject_clause}, {_OP_FACE_CLARITY_LOCK}, {_OP_ART_STYLE_LOCK}, {mood} mood, "
        f"{palette} palette, {setting} in the background, "
        f"vertical 9:16 composition, dynamic dramatic shonen lighting, no text, no watermark. "
        f"{_op_avoid_clause()}"
    )


def _estimate_n_imgs(text: str, cap: int = 3) -> int:
    """
    Estima QUANTAS imagens distintas uma linha deve ter (~1 imagem a cada
    CANAL_DARK_SEC_PER_IMAGE segundos de fala), p/ não segurar uma só imagem por
    muito tempo. Estima a duração por contagem de palavras (~2.5 palavras/s).
    """
    sec_per_img = float(os.environ.get("CANAL_DARK_SEC_PER_IMAGE", "4.0"))
    if sec_per_img <= 0:
        return 1
    words = max(1, len((text or "").split()))
    est_dur = words / 2.5
    n = int(est_dur / sec_per_img) + (1 if (est_dur % sec_per_img) > 0.1 else 0)
    return max(1, min(cap, n))


def _op_entry_desc(entry: dict) -> str:
    """Descrição rica de uma entidade validada (p/ montagem A-vs-B). Usa o subject da
    biblioteca quando a entidade mapeia para um (via ai_subject ou título), senão o nome."""
    ai_subj = entry.get("ai_subject")
    if ai_subj and ai_subj in _OP_SUBJECT_LIBRARY:
        return _OP_SUBJECT_LIBRARY[ai_subj]
    subj = _OP_TITLE_SUBJECT.get(entry.get("title", ""))
    if subj and subj in _OP_SUBJECT_LIBRARY:
        return _OP_SUBJECT_LIBRARY[subj]
    return f"{entry.get('label', 'a One Piece character')}, a One Piece character"


def _op_build_montage_prompt(desc_a: str, desc_b: str, vctx: dict) -> str:
    """Prompt de MONTAGEM 'A vs B' — confronto dos dois em um único frame dividido."""
    vctx = vctx or {}
    mood = (vctx.get("mood") or "epic, tense, hype").strip()
    palette = (vctx.get("palette") or "deep ocean blues, weathered gold, dramatic high-contrast light").strip()
    return (
        f"dramatic split-screen face-off composition: on the left {desc_a}; "
        f"on the right {desc_b}; the two locked in confrontation, a glowing divide between them, "
        f"{_OP_FACE_CLARITY_LOCK}, {_OP_ART_STYLE_LOCK}, {mood} mood, {palette} palette, "
        f"vertical 9:16 composition, dynamic dramatic shonen lighting, no text, no watermark. "
        f"{_op_avoid_clause()}"
    )


def _op_plan_scene(line_text: str, query: str, matched_entries: list, n: int,
                   vctx: dict, broll_dir: Path, broll_kind: str = "character") -> list:
    """
    PLANEJA até `n` slots de imagem on-brand para uma cena One Piece, SEM rodar a IA lenta.
    Resolve imagens RÁPIDAS (foto web livre p/ cenário/objeto; personagem/símbolo do Fandom)
    inline; deixa as imagens de IA como specs ('ai', prompt) para execução PARALELA depois.

    `broll_kind` ROTEIA a fonte:
      • 'character' → SÓ render IA (NUNCA web — Content ID Toei/Shueisha). Caminho v4 abaixo.
      • 'scenery'|'object' → tenta FOTO REAL livre (lane WEB burn, PD/CC) ANTES; o que sobrar
        cai pro render IA. Em qualquer dúvida/falha → IA (nunca imagem não-livre / anime still).

    Retorna lista de specs, cada uma: ('ready', Path)  ou  ('ai', prompt_str).
    Prioridade IA: (0) montagem A-vs-B se a fala é de confronto e há ≥2 entidades; (1) entidades
    nomeadas (real, ou IA-preferida p/ Imu); (2) subjects por conceito (símbolo→flag real,
    senão IA); (3) pad com o subject default. Nunca paisagem vazia / Pexels.
    """
    import image_providers as _ip
    blob = f"{line_text} {query}".lower()
    # Ação/emoção desta linha — injetada nos prompts de IA pra a imagem CASAR com a fala.
    action = _op_clean_action(line_text)
    specs: list = []
    used_subjects: set = set()
    used_real: set = set()

    # ── ROTEAMENTO POR SHOT: cenário/objeto tenta FOTO REAL livre (lane WEB) primeiro ──
    # broll_kind já vem promovido a 'character' pelo validador quando a query nomeia um
    # ícone IP (Poneglyph, Devil Fruit, navio nomeado...) → aqui scenery/object NÃO nomeia
    # IP. Buscamos foto PD/CC genérica (lugar/objeto/atmosfera), com guardrail anti-OP.
    kind = (broll_kind or "character").strip().lower()
    if kind in ("scenery", "object") and n >= 1:
        # kind já é scenery/object aqui (o ramo character cai no caminho IA abaixo) → o passe
        # pexels do _op_fetch_web_burn é inalcançável p/ character por construção.
        web_imgs = _op_fetch_web_burn(query, n, broll_dir, kind=kind)
        for wp in web_imgs:
            if len(specs) >= n:
                break
            specs.append(("ready", wp))
        if len(specs) >= n:
            log.info("[one-piece] Cena (%s): %d/%d slot(s) por FOTO WEB livre (sem IA).",
                     kind, len(specs), n)
            return specs[:n]
        # Faltou foto livre → completa o restante com render IA de CENÁRIO/OBJETO (não
        # força personagem nomeado nem montagem A-vs-B; usa o concept-map / pad de cenário).
        # BUG 3: a cena de CENÁRIO/OBJETO nunca pode virar uma PESSOA nomeada. O concept-map
        # casa por substring no blob inteiro (fala+query) → numa narração que cita o herói
        # do vídeo (ex.: "Loki"), o 1º gatilho a casar era um personagem e o cenário (Elbaf)
        # virava o personagem. Aqui (a) pulamos subjects de personagem (_OP_CHARACTER_SUBJECTS)
        # e (b) priorizamos o sinal da QUERY do shot (o que o roteirista disse que a cena
        # MOSTRA) sobre menções de passagem na narração.
        scenery_blob = (query or line_text or "").lower()
        for triggers, subj in _OP_CONCEPT_MAP:
            if len(specs) >= n:
                break
            if subj in used_subjects or subj in _OP_CHARACTER_SUBJECTS:
                continue  # cenário/objeto: pula personagem nomeado
            if any(t in scenery_blob for t in triggers):
                used_subjects.add(subj)
                specs.append(("ai", _op_build_image_prompt(_OP_SUBJECT_LIBRARY[subj], vctx, action, blob),
                              _op_char_seed(subj)))
        pad_subj = _OP_SCENERY_PAD_SUBJECT
        while len(specs) < n:
            # pad de CENÁRIO neutro → seed livre (não é identidade de personagem a congelar).
            specs.append(("ai", _op_build_image_prompt(_OP_SUBJECT_LIBRARY[pad_subj], vctx, action, blob),
                          None))
        log.info("[one-piece] Cena (%s): %d slot(s) web + %d render(s) IA de fallback.",
                 kind, len(web_imgs), n - len(web_imgs))
        return specs[:n]

    # ── broll_kind == 'character' (ou default seguro): SÓ render IA, NUNCA web ──
    # (0) MONTAGEM A vs B — confronto entre dois atores nomeados.
    confrontation = any(k in blob for k in (
        " vs ", "vs.", "versus", " against ", " between ", "fought", "rivalry",
        "enemy", "enemies", "clashed", "war between", "faced off", "stood against",
    ))
    if confrontation and len(matched_entries) >= 2 and n >= 1:
        # montagem A-vs-B → DOIS personagens num frame; não há um seed único de identidade.
        specs.append(("ai", _op_build_montage_prompt(
            _op_entry_desc(matched_entries[0]), _op_entry_desc(matched_entries[1]), vctx), None))

    # (1) entidades nomeadas citadas na fala (real, ou IA-preferida p/ Imu).
    for entry in matched_entries:
        if len(specs) >= n:
            break
        ai_subj = entry.get("ai_subject")
        if ai_subj:
            if ai_subj in used_subjects:
                continue
            used_subjects.add(ai_subj)
            specs.append(("ai", _op_build_image_prompt(_OP_SUBJECT_LIBRARY[ai_subj], vctx, action, blob),
                          _op_char_seed(ai_subj)))
        else:
            img = entry["img"]
            if id(img) in used_real:
                continue
            used_real.add(id(img))
            specs.append(("ready", img))

    # (2) subjects por conceito presentes na fala.
    # BUG 3: para SUBJECTS DE PERSONAGEM, só aceitamos o gatilho se ele aparecer na QUERY do
    # shot (o que a cena MOSTRA) — assim um nome de OUTRO personagem citado de passagem na
    # narração (ex.: "Loki" numa cena cujo foco é Luffy) não fura um slot como sub-shot. Para
    # conceitos de LUGAR/OBJETO/EMBLEMA (temáticos, não identidade) seguimos casando no blob
    # inteiro, que dá variedade on-brand sem trocar o personagem da cena.
    q_low = (query or "").lower()
    for triggers, subj in _OP_CONCEPT_MAP:
        if len(specs) >= n:
            break
        if subj in used_subjects:
            continue
        hay = q_low if subj in _OP_CHARACTER_SUBJECTS else blob
        if any(t in hay for t in triggers):
            used_subjects.add(subj)
            # PURE-AI por padrão (v4): o b-roll do one-piece é render IA cinematográfico.
            # O still real do Fandom (baixa-res / emblema cru) só entra atrás da flag
            # opcional OP_USE_FANDOM_STILLS=1 (default OFF). Sem a flag, mesmo símbolos
            # (WG/Marines) viram render IA on-brand, coeso com o resto.
            if subj in _OP_SUBJECT_FANDOM and _op_use_fandom_stills():
                try:
                    ri = _ip._prov_fandom_pageimage(_OP_SUBJECT_FANDOM[subj], broll_dir)
                except Exception:
                    ri = None
                if ri is not None:
                    specs.append(("ready", ri))
                    continue
            specs.append(("ai", _op_build_image_prompt(_OP_SUBJECT_LIBRARY[subj], vctx, action, blob),
                          _op_char_seed(subj)))

    # (3) pad com o subject default até atingir n (nunca deixa cena sem subject).
    pad_subj = _OP_DARK_DEFAULT_SUBJECT if any(w in blob for w in _OP_DARK_WORDS) else _OP_DEFAULT_SUBJECT
    while len(specs) < n:
        specs.append(("ai", _op_build_image_prompt(_OP_SUBJECT_LIBRARY[pad_subj], vctx, action, blob),
                      _op_char_seed(pad_subj)))

    return specs[:n]


def _op_execute_plans(plans: list, broll_dir: Path) -> list:
    """
    Executa os planos de cena. As specs ('ai', prompt) são geradas no Pollinations EM
    PARALELO (rede-bound → threads), o que derruba bastante o tempo total de render.
    Specs ('ready', Path) já estão prontas. Devolve lista (por cena) de listas de Paths.
    Workers via CANAL_DARK_AI_WORKERS (padrão 5).
    """
    from concurrent.futures import ThreadPoolExecutor

    tasks = []  # (cena_i, slot_j, prompt, unique_index, seed)
    for ci, specs in enumerate(plans):
        for sj, spec in enumerate(specs or []):
            if spec[0] == "ai":
                # spec é ("ai", prompt[, seed]); seed é o seed estável por personagem
                # (None p/ montagem/cenário). 3-tupla nova é retrocompatível.
                seed = spec[2] if len(spec) > 2 else None
                tasks.append((ci, sj, spec[1], ci * 100 + sj, seed))

    results: dict = {}
    if tasks:
        # Concorrência: com Cloudflare (estável, sem rate-limit anônimo) usamos 6 por
        # padrão → render rápido. Sem ele, no Pollinations grátis, caímos p/ 3 (ele 402a
        # sob rajada; 3 + retry/backoff é o equilíbrio). Override por CANAL_DARK_AI_WORKERS.
        _has_cf = bool(os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
                       and os.environ.get("CLOUDFLARE_API_TOKEN", "").strip())
        workers = max(1, int(os.environ.get("CANAL_DARK_AI_WORKERS", "6" if _has_cf else "3")))
        log.info("[one-piece] Gerando %d imagem(ns) por IA em PARALELO (workers=%d)...",
                 len(tasks), workers)

        def _run(t):
            ci, sj, prompt, idx, seed = t
            # Negative prompt One Piece: honrado só onde o motor respeita (AI Horde);
            # _fetch_ai_image repassa pra cada provider o que ele suporta (seed/negative).
            # ORDEM One Piece: AI Horde (Anything Diffusion) PRIMEIRO — só ele honra o
            # negative e dá traço de anime; Cloudflare/Pollinations viram fallback. Vale
            # SÓ aqui (lane do nicho); os outros nichos seguem _AI_ORDER_DEFAULT.
            return ci, sj, _fetch_ai_image(prompt, broll_dir, idx,
                                           seed=seed, negative=_OP_NEGATIVE_PROMPT,
                                           order=_AI_ORDER_ONE_PIECE)

        with ThreadPoolExecutor(max_workers=workers) as ex:
            for ci, sj, path in ex.map(_run, tasks):
                results[(ci, sj)] = path

    out = []
    for ci, specs in enumerate(plans):
        imgs = []
        for sj, spec in enumerate(specs or []):
            imgs.append(spec[1] if spec[0] == "ready" else results.get((ci, sj)))
        imgs = [im for im in imgs if im is not None]
        out.append(imgs or [None])
    return out


def _build_op_broll(all_queries: list, all_texts: list, all_kinds: list,
                    op_char_entries: list, ref_images: list, vctx: dict,
                    broll_dir: Path) -> list:
    """
    Monta o broll_files inteiro do canal One Piece: planeja cada cena (rápido), gera todas
    as imagens de IA EM PARALELO e remonta na ordem. Cada item de broll_files é um Path,
    None, ou uma LISTA de Paths (várias imagens distintas por cena).

    `all_kinds` (paralelo a all_queries) roteia a FONTE por shot: 'character' → render IA;
    'scenery'/'object' → tenta foto real livre (lane WEB burn) ANTES, com fallback IA.
    """
    plans = []
    for i, query in enumerate(all_queries):
        line_text = all_texts[i] if i < len(all_texts) else ""
        kind = all_kinds[i] if i < len(all_kinds) else "character"
        is_cta = (i == len(all_queries) - 1)
        if i < len(ref_images):
            plans.append([("ready", ref_images[i])])
            continue
        if is_cta:
            plans.append(None)   # CTA reusa o último (resolvido após montar)
            continue
        # BUG 3: o match de entidade era SUBSTRING no blob inteiro (fala+query) e tratava toda
        # menção igual. Numa narração que cita o herói do vídeo (ex.: "Loki") em CADA cena, o
        # Loki (entidade global, do topic/título) casava em cenas que eram de OUTRO personagem
        # (ex.: Imu) e, por vir antes na lista, era renderizado no lugar do subject certo.
        # Fix: (a) match por PALAVRA INTEIRA (_op_word_hit — evita 'loki' colando onde não é);
        # (b) PRIORIZA entidades nomeadas na QUERY do shot (o que o roteirista disse que a cena
        # MOSTRA) sobre as que só aparecem de passagem na narração. Assim a cena de Imu rende
        # Imu, e o Loki incidental não fura a fila.
        q_text = (query or "").lower()
        line_low = (line_text or "").lower()
        in_query, in_line = [], []
        for e in op_char_entries:
            terms = e["terms"]
            if _op_word_hit(q_text, terms):
                in_query.append(e)
            elif _op_word_hit(line_low, terms):
                in_line.append(e)
        matched = in_query + in_line  # subject declarado da cena primeiro
        n = _estimate_n_imgs(line_text)
        plans.append(_op_plan_scene(line_text, query or "", matched, n, vctx, broll_dir, kind))

    resolved = _op_execute_plans([p if p is not None else [] for p in plans], broll_dir)

    broll_files = []
    for i, p in enumerate(plans):
        if p is None:  # CTA
            broll_files.append(broll_files[-1] if broll_files else None)
            log.info("[one-piece] Cena %d (CTA): reusa o b-roll anterior.", i)
        else:
            imgs = resolved[i]
            broll_files.append(imgs if len(imgs) > 1 else (imgs[0] if imgs else None))
            log.info("[one-piece] Cena %d: %d imagem(ns) distinta(s).", i, len(imgs))
    return broll_files


# ════════════════════════════════════════════════════════════════════════════
# FUTEBOL-HISTÓRIA — ROTEAMENTO DE FONTE POR broll_kind
# ════════════════════════════════════════════════════════════════════════════
# Política completa: nichos/futebol-historia/06-visual-broll.md. Por linha o roteirista
# emite broll_kind ∈ {scene, object, player_real, caricature} (validado/defaultado pra
# "scene" em _parse_and_validate_script). Aqui roteamos a FONTE da imagem por kind:
#   scene / object  → FOTO/VÍDEO real genérico do Pexels (fetch_broll, o que já existia).
#   player_real     → SÓ foto livre PD/CC do jogador (lane WEB "burn": wikimedia/openverse/
#                     internetarchive — find_images já filtra licença e grava crédito em
#                     out/CREDITS.jsonl + sidecar .json). Sem foto livre → fallback SEGURO
#                     pra 'scene' (NUNCA foto qualquer da web, NUNCA IA fotorrealista de pessoa).
#   caricature      → render IA em estilo CARICATO/cartoon NÃO-fotorrealista (paródia).
#
# VETOS DUROS deste nicho (06-visual-broll.md): nunca footage de transmissão, nunca IA
# fotorrealista de jogador real, nunca Getty/AP. O default 'scene' + os caminhos abaixo
# respeitam isso por construção (player_real só foto livre; caricature força cartoon).

# Estilo CARICATO injetado no prompt de IA do broll_kind='caricature'. Front-load o estilo
# (FLUX/Pollinations dão mais peso ao começo) pra a imagem sair claramente cartoon — JAMAIS
# fotorrealista (é o que protege a lane como paródia e evita likeness/deepfake).
_FB_CARICATURE_STYLE = (
    "stylized caricature, cartoon, non-photorealistic, exaggerated features, "
    "bold ink outlines, flat cel shading, comic illustration"
)
# Negative do caricature: o oposto duro do estilo (alguns providers honram negative —
# AI Horde sim; FLUX schnell ignora, por isso o estilo positivo acima é o que manda).
_FB_CARICATURE_NEGATIVE = (
    "photorealistic, realistic photo, photograph, real face, deepfake, "
    "3d render, cgi, live action, broadcast footage, getty image, ap photo, "
    "watermark, signature, text, logo, blurry, lowres"
)

# ANTI-PEOPLE POSITIVO p/ scene/object na lane de IA (Bug 2 / causa-raiz 2). O scorer do
# Pexels veta rosto via subject_mode='places', mas a lane de GERAÇÃO (FLUX/Cloudflare) não
# tem scoring — a IA pode desenhar gente/rosto numa cena de cenário/objeto e o pipeline
# aceita a única imagem. E o FLUX schnell IGNORA negative prompt. Por isso descrevemos o
# estado DESEJADO no prompt POSITIVO (mesma tática do One Piece _OP_FACE_CLARITY_LOCK):
# estádio vazio / close do objeto, sem jogadores/pessoas/rostos. Coerente com "se a linha
# não pede o rosto, não chame rosto" (06-visual-broll.md). NÃO se aplica a player_real/
# caricature (essas PODEM ter pessoa) nem a queries que pedem crowd/silhouette/hands/back.
_FB_SCENE_ANTI_PEOPLE = (
    "empty stadium, close-up of the ball and the turf and the boots, "
    "no players, no people, no faces, no crowd in focus, "
    "plain unbranded ball, no text, no lettering, no numbers, no logos, no watermark"
)
# Whitelist: se a query PEDE pessoa anônima/multidão explicitamente, NÃO injeta anti-people
# (espelha _ALLOWED_PEOPLE do scorer do Pexels — mantém os dois caminhos coerentes).
_FB_PEOPLE_ALLOWED = ("crowd", "silhouette", "hands", "back", "fans", "supporters", "terraces")


def _fb_build_caricature_prompt(query: str, vctx: dict) -> str:
    """Prompt de IA do broll_kind='caricature': caricatura cartoon de jogador reconhecível,
    NÃO-fotorrealista. `query` traz o nome próprio + contexto (ex.: 'maradona caricature
    cartoon stylized'); aqui garantimos o estilo caricato e a paleta/era do visual_context."""
    vctx = vctx or {}
    q = (query or "").strip()
    mood = (vctx.get("mood") or "nostalgic, warm, reverent").strip()
    palette = (vctx.get("palette") or "warm sepia and stadium green").strip()
    return (
        f"{_FB_CARICATURE_STYLE} of {q}, {mood} mood, {palette} palette, "
        f"vertical 9:16 composition, no text, no watermark. "
        f"Avoid: photorealistic face, real photo, broadcast footage, league overlay, club logo."
    )


def _fb_fetch_player_real(query: str, n: int, broll_dir: Path) -> list:
    """
    Resolve até `n` FOTOS REAIS de licença livre (PD/CC) de um JOGADOR REAL nomeado, via
    image_providers lane "burn" (wikimedia/openverse/internetarchive). find_images já filtra
    a licença (PD/CC0/CC-BY/CC-BY-SA) e GRAVA o crédito (autor/licença/link) no sidecar
    <stem>.json e em out/CREDITS.jsonl — esse crédito entra depois no fim da descrição.

    Retorna lista de Paths aceitos (pode ser MENOR que n, ou VAZIA → caller cai pro
    fallback seguro 'scene'). NUNCA usa fonte sem licença nem IA fotorrealista.
    """
    import image_providers as _ip
    q = (query or "").strip()
    if not q:
        return []
    try:
        # GATE DE IDENTIDADE: player_real exige foto LIVRE do jogador NOMEADO. Forçamos os
        # providers CC-only (wikimedia/openverse/internetarchive) e NUNCA pexels_photo —
        # mesmo que o operador o tenha listado em IMG_PROVIDERS_BURN. pexels_photo é stock
        # free-to-use e bypassa o gate CC, então preencheria o slot que a foto livre do
        # jogador não cobriu com o ROSTO DE UM TERCEIRO qualquer (bug do "homem sorrindo"
        # na fala "The story of Garrincha"). Sem foto livre → caller cai no fallback SEGURO
        # 'scene' (genérico/atmosférico), nunca outra pessoa real nem IA fotorrealista.
        paths = _ip.find_images(q, "futebol-historia", "burn", count=n, out_dir=broll_dir,
                                providers=_ip.BURN_CC_PROVIDERS)
    except Exception as exc:
        log.warning("[futebol][player_real] lane burn falhou p/ '%s': %s — cai pra 'scene'.", q, exc)
        return []
    for p in paths:
        meta = _op_find_sidecar(p) or {}
        log.info("[futebol][player_real] ACEITA: %s license=%s src=%s",
                 p.name, meta.get("license", "?"), str(meta.get("source_url", ""))[:60])
    return paths[:n]


def _fb_plan_scene(query: str, line_text: str, kind: str, n: int,
                   vctx: dict, broll_dir: Path, used_broll_ids: set,
                   broll_source: str = "pexels") -> list:
    """
    Resolve até `n` imagens/clipes de UMA cena de futebol-historia segundo o broll_kind.
    Retorna lista de Paths (pode ser vazia → caller deixa cor sólida).
    """
    kind = (kind or "scene").strip().lower()

    # caricature → render IA cartoon (NÃO-fotorrealista). Estilo caricato força a paródia.
    if kind == "caricature":
        imgs = []
        for k in range(n):
            bf = _fetch_ai_image(_fb_build_caricature_prompt(query, vctx), broll_dir,
                                 k, negative=_FB_CARICATURE_NEGATIVE)
            if bf is not None:
                imgs.append(bf)
        if imgs:
            return imgs
        # IA não respondeu → fallback seguro pra 'scene' (genérico), NÃO foto de pessoa.
        log.info("[futebol][caricature] IA não retornou p/ '%s' — fallback 'scene'.", query)
        kind = "scene"

    # player_real → SÓ foto livre PD/CC do jogador. VETO DURO: se a fonte livre não acha
    # nada, o fallback é 'scene'/atmosférico genérico — NUNCA uma foto qualquer da web,
    # NUNCA IA fotorrealista de pessoa real (likeness/deepfake/desinformação).
    if kind == "player_real":
        imgs = _fb_fetch_player_real(query, n, broll_dir)
        if imgs:
            return imgs
        log.warning(
            "[futebol][player_real] sem foto livre p/ '%s' — VETO: fallback SEGURO 'scene' "
            "(genérico/atmosférico, NUNCA foto da web sem licença nem IA fotorrealista).",
            query,
        )
        kind = "scene"

    # scene / object → caminho de FOTO/VÍDEO real genérico (Pexels/image_providers), o que
    # já existe hoje via broll_query. É o lado seguro: zero likeness, zero IP.
    #
    # ANTI-BUG "rosto onde a linha não pede rosto" (cena ~13s do Garrincha — a linha fala do
    # DRIBLE/das pernas, não do rosto; o teste trouxe "um corpo com bola no lugar da cabeça"):
    # o visual_context do futebol é subject_mode='mixed', e com 'mixed' o scorer do Pexels
    # NÃO aplica o veto-de-pessoa. Resultado: um shot 'scene'/'object' podia puxar um clipe
    # com gente/rosto. Aqui forçamos subject_mode='places' SÓ p/ estes dois kinds (cópia
    # local, não muta o vctx global) → o veto-de-pessoa do scorer liga e o shot fica em
    # ATMOSFERA/OBJETO (estádio, gramado, bola, chuteira), sem rosto. Quem QUER pessoa usa
    # explicitamente player_real/caricature; quem quer multidão/silhueta escreve crowd/
    # silhouette/hands/back na query (whitelist _ALLOWED_PEOPLE do scorer). 06-visual-broll.md:
    # "se a linha não pede o ROSTO, não chame rosto — mostre o que a linha DIZ".
    broll_source = (broll_source or "pexels").strip() or "pexels"
    faceless_vctx = dict(vctx or {})
    faceless_vctx["subject_mode"] = "places"

    # ANTI-PEOPLE na lane de IA (Bug 2 / causa-raiz 2): o subject_mode='places' acima só liga
    # o veto-de-rosto do scorer do PEXELS. Na lane de GERAÇÃO (broll-source image → FLUX, sem
    # scoring) nada impede a IA de desenhar rosto — e o FLUX ignora negative. Então injetamos
    # o anti-people no prompt POSITIVO (a query é o que vira o prompt de geração). NÃO se
    # aplica se a query JÁ pede pessoa anônima/multidão (whitelist, espelha _ALLOWED_PEOPLE).
    eff_query = query
    q_lower = (query or "").lower()
    if not any(w in q_lower for w in _FB_PEOPLE_ALLOWED):
        eff_query = f"{query}, {_FB_SCENE_ANTI_PEOPLE}" if query else _FB_SCENE_ANTI_PEOPLE

    imgs = []
    for k in range(n):
        bf = fetch_broll(eff_query, broll_source, broll_dir, k,
                         vctx=faceless_vctx, used_ids=used_broll_ids)
        if bf is not None:
            imgs.append(bf)
    return imgs


def _build_fb_broll(all_queries: list, all_texts: list, all_kinds: list,
                    ref_images: list, vctx: dict, broll_dir: Path,
                    used_broll_ids: set, broll_source: str = "pexels") -> list:
    """
    Monta o broll_files inteiro do canal futebol-historia roteando a FONTE por broll_kind
    (paralelo a all_queries; +1 p/ o CTA). Cada item é um Path, None, ou LISTA de Paths.
    """
    broll_files: list = []
    for i, query in enumerate(all_queries):
        line_text = all_texts[i] if i < len(all_texts) else ""
        kind = all_kinds[i] if i < len(all_kinds) else "scene"
        is_cta = (i == len(all_queries) - 1)
        if i < len(ref_images):
            broll_files.append(ref_images[i])
            log.info("[futebol] Cena %d: imagem de referência '%s'.", i, ref_images[i].name)
            continue
        if is_cta and broll_files:
            broll_files.append(broll_files[-1])
            log.info("[futebol] Cena %d (CTA): reusa o b-roll anterior.", i)
            continue
        n = 1 if is_cta else _estimate_n_imgs(line_text)
        imgs = _fb_plan_scene(query or "", line_text, kind, n, vctx, broll_dir,
                              used_broll_ids, broll_source)
        if not imgs:
            broll_files.append(None)
        else:
            broll_files.append(imgs if len(imgs) > 1 else imgs[0])
        log.info("[futebol] Cena %d (%s): %d imagem(ns).", i, kind, len(imgs))
    return broll_files


def _fetch_pexels(
    query: str,
    out_dir: Path,
    index: int,
    vctx: Optional[dict] = None,
    used_ids: Optional[set] = None,
) -> Optional[Path]:
    """
    Busca e baixa 1 vídeo vertical do Pexels usando a API gratuita.

    Estratégia:
    1. Enriquece a query com anchor_terms do visual_context (sem mood/palette — evitam natureza)
    2. Busca 15 candidatos e rankeia por score determinístico (coerência de slug)
    3. Aplica veto duro de avoid_terms e veto de pessoas (se subject_mode != atmosphere)
    4. Fallback escalonado: termos legados → setting → anchor night → empty interior night
    5. Piso: retorna None → create_solid_color_clip no chamador

    vctx:  dict com setting/anchor_terms/avoid_terms/subject_mode/mood (pode ser None → {})
    used_ids: set mutável de video_ids já usados (dedup entre linhas do mesmo vídeo)
    """
    import requests  # já garantido no requirements.txt

    api_key = os.environ.get("PEXELS_API_KEY")
    if not api_key:
        log.error(
            "PEXELS_API_KEY não definida.\n"
            "Obtenha gratuitamente em: https://www.pexels.com/api/\n"
            "Defina: set PEXELS_API_KEY=sua-chave"
        )
        sys.exit(1)

    if vctx is None:
        vctx = {}
    if used_ids is None:
        used_ids = set()

    headers = {"Authorization": api_key}

    # ── helpers de tokenização ────────────────────────────────────────────────
    _TOKEN_RE = re.compile(r"[^a-z0-9]+")

    def _tokenize(text: str) -> set:
        """Divide por separadores e retorna tokens em minúsculas. Match INTEIRO, não substring."""
        return set(t for t in _TOKEN_RE.split(text.lower()) if t)

    # Stopwords simples para não contar preposições/artigos no score
    _STOPWORDS = {"a", "an", "the", "at", "in", "on", "of", "to", "and", "or", "for", "with"}

    # ── veto sets ─────────────────────────────────────────────────────────────
    avoid_from_vctx = [str(t).lower() for t in (vctx.get("avoid_terms") or []) if t]
    effective_avoid = set(DEFAULT_AVOID_TERMS) | set(avoid_from_vctx)
    subject_mode = vctx.get("subject_mode") or "places"
    mood_tokens = _tokenize(str(vctx.get("mood") or ""))

    # Termos que permitem persona mesmo em mode places/objects (silhueta anônima)
    _ALLOWED_PEOPLE = {"crowd", "silhouette", "hands", "back"}

    # ── enriquecimento de query ───────────────────────────────────────────────
    # Pega apenas a 1ª cena (defesa contra query legada com vírgulas)
    base = query.split(",")[0].strip()
    anchor_terms = list(vctx.get("anchor_terms") or [])
    anchor_short = " ".join(anchor_terms[:2])  # máx 2 no enriched — não zera resultados
    setting = str(vctx.get("setting") or "").strip()

    enriched = " ".join(t for t in [base, anchor_short] if t)

    # ── _search: busca + ranking ──────────────────────────────────────────────
    def _search(q: str) -> Optional[dict]:
        """
        Chama a API do Pexels, rankeia os 15 candidatos por score determinístico
        e retorna o melhor vídeo (url + video_id) ou None.

        Score por candidato:
          +3 por token da query (base+anchor, sem stopwords) presente como token no slug
          +1 se portrait (height > width)
          +1 se duração no intervalo 4–20s
          +1 por token de mood presente como token no slug

        Veto duro (descarta):
          - qualquer token do slug ∈ effective_avoid
          - se subject_mode ∈ {places, objects} e token do slug ∈ PEOPLE_WORDS
            (exceto se base pede explicitamente crowd/silhouette/hands/back)
          - video_id já em used_ids
        """
        url = "https://api.pexels.com/videos/search"
        params = {
            "query": q,
            "orientation": "portrait",
            "size": "medium",
            "per_page": 15,
        }
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=20)
        except requests.RequestException as exc:
            log.warning("Erro de rede ao acessar Pexels: %s", exc)
            return None

        if resp.status_code == 403:
            log.error(
                "PEXELS_API_KEY inválida ou sem permissão (HTTP 403).\n"
                "Verifique a chave em: https://www.pexels.com/api/"
            )
            sys.exit(1)

        if resp.status_code != 200:
            log.warning("Pexels retornou HTTP %d para query '%s'.", resp.status_code, q)
            return None

        videos = resp.json().get("videos", [])
        if not videos:
            return None

        # Tokens da query (sem stopwords) para score
        q_tokens = _tokenize(q) - _STOPWORDS

        # Verifica se a base pede explicitamente silhueta/mãos/multidão
        base_tokens = _tokenize(base)
        base_allows_people = bool(base_tokens & _ALLOWED_PEOPLE)

        best_score = -1
        best_video = None
        best_file_info = None

        for video in videos:
            vid_id = video.get("id")
            if vid_id in used_ids:
                continue  # dedup

            slug_raw = video.get("url", "").lower()
            slug_tokens = _tokenize(slug_raw)

            # ── veto duro: avoid_terms ──
            if slug_tokens & effective_avoid:
                continue

            # ── veto de pessoa ──
            if subject_mode in ("places", "objects") and not base_allows_people:
                if slug_tokens & set(PEOPLE_WORDS):
                    continue

            # ── score ──
            score = 0
            score += 3 * len(q_tokens & slug_tokens)
            w = video.get("width", 1)
            h = video.get("height", 1)
            if h > w:
                score += 1
            dur = video.get("duration", 0)
            if 4 <= dur <= 20:
                score += 1
            score += len(mood_tokens & slug_tokens)

            if score <= best_score:
                continue

            # ── seleciona arquivo mp4 hd ──
            best_file = None
            for vf in video.get("video_files", []):
                if vf.get("quality") == "hd" and vf.get("link", "").endswith(".mp4"):
                    best_file = vf
                    break
            if best_file is None:
                for vf in video.get("video_files", []):
                    if vf.get("link", "").endswith(".mp4"):
                        best_file = vf
                        break
            if best_file is None:
                continue  # sem mp4 usável

            best_score = score
            best_video = video
            best_file_info = best_file

        if best_video is None or best_score <= 0:
            return None

        return {
            "url": best_file_info["link"],
            "video_id": best_video["id"],
            "slug": best_video.get("url", ""),
            "score": best_score,
        }

    # ── busca primária ────────────────────────────────────────────────────────
    log.info("Buscando b-roll no Pexels: '%s' (enriched: '%s')", query, enriched)
    result = _search(enriched)

    # ── fallback escalonado ───────────────────────────────────────────────────
    if result is None:
        # Degrau 1: se havia vírgulas na query original, tentar cada cena i>0 + setting+anchor
        extra_scenes = [p.strip() for p in query.split(",") if p.strip()][1:]
        for scene in extra_scenes:
            candidate = " ".join(t for t in [scene, anchor_short] if t)
            log.warning("Fallback degrau 1: '%s'", candidate)
            result = _search(candidate)
            if result is not None:
                break

    if result is None and setting:
        # Degrau 2: setting puro
        log.warning("Fallback degrau 2 (setting): '%s'", setting)
        result = _search(setting)

    if result is None:
        # Degrau 3a: anchor + night
        anchor_night = f"{anchor_short} night".strip() if anchor_short else "dark night"
        log.warning("Fallback degrau 3a (anchor night): '%s'", anchor_night)
        result = _search(anchor_night)

    if result is None:
        # Degrau 3b: empty interior + substantivo do setting + night
        setting_word = setting.split()[0] if setting else "room"
        last_resort = f"empty interior {setting_word} night"
        log.warning("Fallback degrau 3b (last resort): '%s'", last_resort)
        result = _search(last_resort)

    # ── piso ─────────────────────────────────────────────────────────────────
    if result is None:
        log.warning("Sem b-roll mesmo após fallbacks para '%s'. Cor sólida.", query)
        return None

    # Registra o id para dedup nas próximas linhas
    used_ids.add(result["video_id"])
    log.info(
        "B-roll escolhido [score=%d]: %s",
        result.get("score", 0), result.get("slug") or result["url"]
    )

    # Download do vídeo
    import requests as req
    out_file = out_dir / f"broll_{index:02d}.mp4"
    log.info("Baixando b-roll %d: %s", index, result["url"])
    try:
        r = req.get(result["url"], stream=True, timeout=60)
        r.raise_for_status()
        with open(out_file, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
    except Exception as exc:
        log.warning("Falha ao baixar b-roll %d: %s", index, exc)
        return None

    log.info(
        "B-roll %d salvo: %s (%.1f MB)",
        index, out_file.name, out_file.stat().st_size / 1e6
    )
    return out_file


def _last_resort_broll_image(duration: float, out_path: Path,
                             query: Optional[str] = None) -> Path:
    """
    REDE ANTI-PRETO (sem chave): antes de cair na cor sólida (tela preta), tenta baixar UMA
    foto real atmosférica de fonte livre/stock (pexels_photo/wikimedia) e transformá-la num
    clipe Ken Burns 9:16. Só se ISSO falhar é que chama create_solid_color_clip (preto = o
    último dos últimos). Conserta o sintoma "o vídeo vira PRETO quando os geradores caem".

    `query`: a query da cena (quando houver) OU um genérico atmosférico SEGURO. A foto é só
    fundo — qualquer cena marítima/tempestade genérica é melhor que preto.

    REGRA DURA ONE PIECE: o fallback é GENÉRICO (mar/tempestade/ruína) — NUNCA nome de
    personagem, NUNCA provider anime/fandom. A query passada aqui pelo caminho One Piece já
    deve ser genérica; mesmo assim, o provider é fixado em pexels_photo/wikimedia (jamais
    fandom/civitai), então não há como vazar frame de anime por aqui.
    """
    import image_providers as _ip
    # Genérico atmosférico SEGURO (zero IP, zero personagem): serve a todos os nichos.
    q = (query or "").strip() or "dark stormy ocean horizon cinematic"
    try:
        imgs = _ip.find_images(
            q, os.environ.get("CANAL_DARK_NICHE", "").strip().lower() or "generic",
            "burn", count=1, providers=["pexels_photo", "wikimedia"],
        )
    except Exception as exc:
        log.warning("[anti-preto] busca de foto atmosférica falhou (%s) — caindo na cor sólida.", exc)
        imgs = []
    if imgs:
        img = imgs[0]
        if isinstance(img, Path) and img.exists():
            log.info("[anti-preto] foto atmosférica p/ último recurso: %s (q=%r)", img.name, q)
            # Casa 9:16 + movimento (mesmo tratamento de qualquer imagem do pipeline).
            return _ken_burns_clip(img, duration, out_path)
    # Piso final: até a foto falhou → cor sólida (preto). create_solid_color_clip tem sys.exit(1)
    # se nem o preto sair — preservado de propósito (é o último piso real).
    log.warning("[anti-preto] sem foto atmosférica — usando cor sólida (%s).", FALLBACK_BG_COLOR)
    return create_solid_color_clip(FALLBACK_BG_COLOR, duration, out_path)


def create_solid_color_clip(color: str, duration: float, out_path: Path) -> Path:
    """
    Cria um clipe de cor sólida via FFmpeg como fallback quando não há b-roll.
    Usado para não quebrar a montagem quando o Pexels não retorna resultado.
    """
    log.info(
        "Gerando clipe fallback de cor sólida (%s, %.1fs) → %s",
        color, duration, out_path.name
    )
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        # lavfi testsrc color gera vídeo sólido; usamos 'color' source
        "-i", f"color=c={color}:s={OUTPUT_WIDTH}x{OUTPUT_HEIGHT}:r=30",
        "-t", str(duration),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "stillimage",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        log.error("FFmpeg falhou ao gerar clipe fallback:\n%s", result.stderr[-1000:])
        sys.exit(1)
    return out_path


# ═════════════════════════════════════════════════════════════════════════════
# (d) MONTAGEM COM FFMPEG
# ═════════════════════════════════════════════════════════════════════════════

def _punch_offset_for_window(words, win_start: float, win_dur: float):
    """
    Acha o offset (em segundos, relativo a win_start) da palavra de MAIOR carga
    dentro da janela [win_start, win_start + win_dur] — alvo do punch-in.

    Heurística simples (sem NLP): entre as content-words da janela (descarta
    stop-words curtas), escolhe a de maior "peso" = nº de letras + bônus se começar
    com maiúscula (nome próprio) ou tiver dígito (número). Empate → a mais à frente.

    Retorna float (0 ≤ offset < win_dur) ou None se nenhuma palavra cair na janela.
    """
    win_end = win_start + win_dur
    best = None  # (peso, offset)
    for (s, _e, txt) in words:
        if s < win_start or s >= win_end:
            continue
        raw = txt.strip()
        norm = _norm_token(raw)
        if not norm:
            continue
        if norm in _PUNCH_STOPWORDS and not any(c.isdigit() for c in raw):
            continue
        peso = len(norm)
        if raw[:1].isupper():
            peso += 3        # provável nome próprio → ênfase
        if any(c.isdigit() for c in raw):
            peso += 4        # número → ênfase forte
        offset = s - win_start
        # >= favorece a palavra mais à frente em empate (clímax tende a vir no fim).
        if best is None or peso >= best[0]:
            best = (peso, offset)
    if best is None:
        return None
    # Recua ~0.1s p/ o zoom começar JUNTO com a sílaba tônica, não depois dela.
    return max(0.0, best[1] - 0.1)


def _ken_burns_clip(image_path: Path, duration: float, out_path: Path,
                    variant: int = 0, punch_at: Optional[float] = None) -> Path:
    """
    Transforma uma imagem em clipe 9:16 com zoom/pan (Ken Burns) mais expressivo.

    variant (int): variante de movimento para evitar shots idênticos quando a
    mesma imagem é reutilizada em sub-shots (E5 — cap de duração).
      0 → zoom-in suave a partir do centro (push-in)
      1 → zoom-in com pan diagonal p/ esquerda+baixo
      2 → zoom-in arrancando do canto superior-direito (pan p/ centro)
      3 → pull-back suave (z inicia grande e decresce) com leve pan lateral à direita

    punch_at (float|None): offset em segundos (relativo ao início do clipe) onde
    aplicar um zoom rápido extra (punch-in na palavra-chave). SUTIL. None = sem punch.

    A VELOCIDADE do zoom/pan deriva da DURAÇÃO (frames): o increment por frame é
    calculado p/ atingir o alvo (z_end / amplitude do pan) no ÚLTIMO frame — assim o
    movimento não "morre" em shots longos nem fica violento em shots curtos.

    EASING (Frente 3): em vez de progresso LINEAR (que dá "arranque" no 1º frame e
    parada seca no último), o zoom e o pan seguem um ease-in-out (smoothstep
    3t²-2t³): aceleram do repouso e desaceleram até parar. O movimento entra e sai
    suave — leitura de "história", não de slideshow mecânico.
    """
    frames = max(1, int(round(duration * 30)))

    # Amplitude tunável. Default REDUZIDO (Frente 3): zoom mais contido p/ não "puxar"
    # a imagem com força — combina com o tom-história. Nichos podem sobrepor via env.
    z_end = float(os.environ.get("CANAL_DARK_KB_ZOOM_END", "1.12"))   # antes 1.22
    pan_amp = float(os.environ.get("CANAL_DARK_KB_PAN", "0.06"))      # antes 0.08 (fração de iw/ih)

    # Progresso com EASING ease-in-out (smoothstep) 0→1 ao longo do shot. Usa o nº de
    # frames conhecido (não a var 'in' do libass). t = on/frames clampado em [0,1];
    # e = 3t²-2t³ acelera do repouso e desacelera até parar (derivada 0 nas pontas).
    t = f"min(max(on/{frames}\\,0)\\,1)"
    ease = f"(3*({t})*({t})-2*({t})*({t})*({t}))"

    # Zoom-in com easing: parte de 1.0 e chega EXATO em z_end no último frame, mas pela
    # curva suave (não linear). Zoom-out: o inverso (de z_end até 1.0).
    z_in = f"(1.0+({z_end}-1.0)*{ease})"
    z_out = f"({z_end}-({z_end}-1.0)*{ease})"
    # Pan também segue o easing (mesma curva → começa/termina parado, sem solavanco).
    prog = ease

    if variant == 1:
        # Pan diagonal p/ esquerda+baixo enquanto dá zoom-in (easing).
        zoom_expr = z_in
        x_expr = f"iw/2-(iw/zoom/2)-iw*{pan_amp}*{prog}"
        y_expr = f"ih/2-(ih/zoom/2)+ih*{pan_amp*0.75:.4f}*{prog}"
    elif variant == 2:
        # Arranca do canto superior-direito e caminha p/ o centro com zoom-in (easing).
        zoom_expr = z_in
        x_expr = f"iw*0.60-(iw/zoom/2)+iw*{pan_amp}*{prog}"
        y_expr = f"ih*0.20-(ih/zoom/2)+ih*{pan_amp:.4f}*{prog}"
    elif variant == 3:
        # Pull-back suave: começa em z_end e afasta até ~1.0 (easing) + leve pan p/ direita.
        zoom_expr = z_out
        x_expr = f"iw/2-(iw/zoom/2)+iw*{pan_amp*0.6:.4f}*{prog}"
        y_expr = "ih/2-(ih/zoom/2)"
    else:
        # Padrão (variant 0): zoom-in centralizado SUAVE (easing).
        zoom_expr = z_in
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"

    # Punch-in: pulso de zoom adicional, triangular e curto, centrado no frame da
    # palavra-chave (sobe ~0.2s e desce ~0.2s). Aditivo ao zoom base → SUTIL.
    if punch_at is not None and punch_at >= 0:
        pf = int(round(punch_at * 30))                      # frame central do punch
        pf = max(0, min(frames - 1, pf))
        pw = max(2, int(round(0.20 * 30)))                  # meia-largura do pulso (~6 frames)
        punch_amp = float(os.environ.get("CANAL_DARK_PUNCH_AMP", "0.06"))
        # max(0, 1 - |on-pf|/pw): 1 no centro, 0 nas bordas do pulso.
        pulse = f"{punch_amp}*max(0\\,1-abs(on-{pf})/{pw})"
        # Soma o pulso e re-clampa no teto p/ não estourar o crop.
        zoom_expr = f"min(({zoom_expr})+{pulse},{z_end + punch_amp:.4f})"

    vf = (
        f"scale={OUTPUT_WIDTH*2}:{OUTPUT_HEIGHT*2}:force_original_aspect_ratio=increase,"
        f"crop={OUTPUT_WIDTH*2}:{OUTPUT_HEIGHT*2},"
        f"zoompan=z='{zoom_expr}':d={frames}:"
        f"x='{x_expr}':y='{y_expr}':"
        f"s={OUTPUT_WIDTH}x{OUTPUT_HEIGHT}:fps=30,"
        "format=yuv420p"
    )
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(image_path),
        "-t", str(duration),
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        "-an",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        log.warning("Ken Burns falhou no b-roll de IA: %s. Cor sólida.", result.stderr[-400:])
        return create_solid_color_clip(FALLBACK_BG_COLOR, duration, out_path)
    return out_path


def prepare_broll_segment(
    broll_path: Optional[Path],
    duration: float,
    index: int,
    work_dir: Path,
) -> Path:
    """
    Prepara um segmento de b-roll para o 9:16: escala/crop para 1080x1920 e
    ajusta para a duração exata do segmento de fala correspondente.

    Matemática do crop para 9:16:
      - Se o vídeo fonte for landscape (16:9): cortamos a faixa central vertical.
        crop = largura_final = altura_fonte; offset_x = (w - h) / 2
        scale = 1080:1920
      - Se já for portrait: apenas redimensiona.
      - Fórmula segura para qualquer proporção:
        crop=ih*9/16:ih:(iw-ih*9/16)/2:0, scale=1080:1920
        (garante que nunca peça crop maior que a largura real)
    """
    out_path = work_dir / f"broll_ready_{index:02d}.mp4"

    if broll_path is None:
        # Nenhum b-roll disponível — REDE ANTI-PRETO: tenta foto atmosférica real (sem chave)
        # antes de cair na cor sólida. Sem a query da cena aqui → genérico seguro do helper.
        return _last_resort_broll_image(duration, out_path)

    # Imagem (ex: gerada por IA no Pollinations) → clipe 9:16 com movimento Ken Burns.
    if broll_path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
        return _ken_burns_clip(broll_path, duration, out_path)

    # Filtro de vídeo robusto p/ 9:16 (corrige erro -22 do libx264 com dimensão ímpar):
    #   scale=...force_original_aspect_ratio=increase → cobre o frame todo (sem barras)
    #   crop=1080:1920 exato (sempre PAR, libx264 feliz)
    #   fps=30 CFR → concat -c copy consistente
    vf = (
        f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={OUTPUT_WIDTH}:{OUTPUT_HEIGHT},"
        "fps=30,"
        "setpts=PTS-STARTPTS"
    )

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1",        # loop infinito caso o b-roll seja mais curto que a fala
        "-i", str(broll_path),
        "-t", str(duration),         # corta exatamente na duração da fala
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",       # pixel format uniforme p/ concat -c copy
        "-r", "30",                  # CFR 30fps (reforça o filtro fps=30)
        "-an",                       # sem áudio no b-roll (o áudio vem da narração)
        str(out_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        log.warning(
            "FFmpeg falhou ao preparar b-roll %d. Usando fallback de cor sólida.\n%s",
            index, result.stderr[-500:]
        )
        return create_solid_color_clip(FALLBACK_BG_COLOR, duration, out_path)

    return out_path


def compute_line_durations(
    script: dict,
    total_audio_duration: float,
) -> list[float]:
    """
    Distribui a duração total do áudio proporcionalmente entre as linhas do roteiro
    com base no número de palavras de cada linha (incluindo o CTA como última linha).

    Isso é uma aproximação: a distribuição real de tempo seria obtida pelo SubMaker,
    mas precisamos das durações antes de montar o vídeo.

    Retorna lista de floats com a duração de cada segmento (linhas + cta).
    """
    all_texts = [line["text"] for line in script["lines"]] + [script["cta"]]
    word_counts = [len(t.split()) for t in all_texts]
    total_words = sum(word_counts)

    if total_words == 0:
        # Distribui igualmente se não houver palavras (edge case)
        n = len(all_texts)
        return [total_audio_duration / n] * n

    durations = [
        (wc / total_words) * total_audio_duration
        for wc in word_counts
    ]
    return durations


def _norm_token(tok: str) -> str:
    """
    Normaliza um token p/ casar o texto da linha com a palavra do edge-tts:
    minúsculas e sem pontuação nas bordas. Mantém apenas alfanuméricos internos
    (apóstrofo/hífen viram nada) — basta p/ alinhamento, não p/ exibição.
    """
    return re.sub(r"[^0-9a-z]+", "", tok.lower())


def compute_line_durations_from_words(
    script: dict,
    words: list,
    total_audio_duration: float,
):
    """
    Casa o FIM de cada linha do roteiro com o `end` REAL da última palavra dela,
    usando os word-timestamps do edge-tts (lista de tuplas (start, end, texto)).

    Por que: `compute_line_durations` só aproxima por contagem de palavras, o que
    dessincroniza o corte da fala (ex.: pausas de pontuação, palavras longas). Aqui
    a duração de cada shot bate com a fala de verdade → cadência real.

    Estratégia (tolerante a tokenização/pontuação):
      - A narração é " ".join(textos das linhas + cta), então os tokens das linhas
        aparecem NA MESMA ORDEM na lista `words`.
      - Para cada linha, consumimos da lista `words` tantos tokens quanto a linha
        tem (casando por token normalizado quando possível; se um token não casar,
        avançamos mesmo assim p/ não travar — alinhamento é aproximado mas monotônico).
      - O fim da linha = `end` da última palavra consumida; a duração = fim atual −
        fim anterior.

    Retorna (durations, line_bounds):
      durations   = lista de floats (duração de cada segmento: linhas + cta)
      line_bounds = lista de (start_sec, end_sec) absolutos de cada segmento
    Levanta ValueError se o casamento for inseguro → caller cai no fallback.
    """
    all_texts = [line["text"] for line in script["lines"]] + [script["cta"]]
    if not words:
        raise ValueError("sem word-timestamps")

    n_words = len(words)
    cursor = 0          # índice na lista global de palavras
    bounds = []         # (start, end) por linha
    prev_end = 0.0
    matched_tokens = 0  # nº de tokens que casaram (p/ aferir confiança)
    total_tokens = 0

    for li, text in enumerate(all_texts):
        line_toks = [_norm_token(t) for t in text.split()]
        line_toks = [t for t in line_toks if t]  # descarta tokens que viram vazio
        total_tokens += len(line_toks)

        if cursor >= n_words:
            # Acabaram as palavras antes das linhas → casamento falhou.
            raise ValueError("word-timestamps acabaram antes das linhas do roteiro")

        line_start = words[cursor][0] if cursor < n_words else prev_end
        consumed = 0
        for lt in line_toks:
            if cursor >= n_words:
                break
            # Tenta casar o token da linha com a palavra atual; se não casar,
            # tolera um pequeno deslize procurando até 2 palavras à frente.
            wt = _norm_token(words[cursor][2])
            if wt == lt:
                matched_tokens += 1
                cursor += 1
                consumed += 1
            else:
                look = 1
                hit = False
                while look <= 2 and cursor + look < n_words:
                    if _norm_token(words[cursor + look][2]) == lt:
                        cursor += look + 1
                        consumed += look + 1
                        matched_tokens += 1
                        hit = True
                        break
                    look += 1
                if not hit:
                    # Sem casamento: consome 1 palavra mesmo assim (mantém monotônico).
                    cursor += 1
                    consumed += 1

        if consumed == 0:
            # Linha não consumiu nenhuma palavra (ex.: linha só de pontuação).
            # Usa o tempo corrente como ponto sem duração — evita divisão estranha.
            line_end = prev_end
        else:
            line_end = words[cursor - 1][1]

        # Garante monotonicidade (o end nunca pode recuar).
        line_end = max(line_end, prev_end)
        bounds.append((line_start if line_start >= prev_end else prev_end, line_end))
        prev_end = line_end

    # A última linha deve ir até o fim real do áudio (cobre cauda de silêncio/respiração).
    if bounds:
        last_start, _ = bounds[-1]
        bounds[-1] = (last_start, max(prev_end, total_audio_duration))

    # Confiança: se quase nada casou, o alinhamento é lixo → fallback.
    if total_tokens > 0 and (matched_tokens / total_tokens) < 0.5:
        raise ValueError(
            f"casamento fraco ({matched_tokens}/{total_tokens} tokens) — fallback p/ aproximação"
        )

    # Converte os bounds (start,end) absolutos em durações por segmento.
    durations = []
    cum = 0.0
    for (_s, e) in bounds:
        d = max(0.0, e - cum)
        durations.append(d)
        cum = e

    return durations, bounds


def assemble_short(
    script: dict,
    broll_files: list[Optional[Path]],
    narration_path: Path,
    srt_path: Path,
    out_dir: Path,
    music_path: Optional[Path],
    work_dir: Path,
) -> Path:
    """
    Monta o short final 9:16 via FFmpeg:
      1. Prepara cada segmento de b-roll na duração proporcional à fala
      2. Concatena todos os segmentos de b-roll em um único vídeo de fundo
      3. Adiciona a narração como trilha de áudio principal
      4. Se --music: mixa trilha de fundo em volume reduzido (-20dB em relação à voz)
      5. Queima as legendas do SRT sobre o vídeo
      6. Salva o short.mp4 no out_dir

    Retorna o Path do arquivo final.
    """
    total_duration = get_audio_duration(narration_path)
    if total_duration <= 0:
        log.warning("Não foi possível determinar duração do áudio. Usando estimativa de 60s.")
        total_duration = 60.0

    if total_duration > MAX_SHORT_DURATION:
        log.warning(
            "Narração tem %.1fs (acima dos %ds recomendados para Shorts). "
            "Considere encurtar o roteiro.",
            total_duration, MAX_SHORT_DURATION
        )

    # Duração de cada segmento de b-roll.
    #   CANAL_DARK_CADENCE=1 (padrão): casa o FIM de cada linha com o `end` real da
    #     última palavra dela (cadência na fala) lendo words.json do edge-tts.
    #   =0 ou casamento falho: cai na aproximação por contagem de palavras.
    # line_bounds = (start,end) absolutos por linha — usado pelo punch-in.
    line_bounds: Optional[list] = None
    use_cadence = os.environ.get("CANAL_DARK_CADENCE", "1").strip().lower() not in ("0", "false", "no")
    words_ts = _load_word_timestamps(work_dir) if use_cadence else []
    if words_ts:
        try:
            line_durations, line_bounds = compute_line_durations_from_words(
                script, words_ts, total_duration
            )
            log.info("Cadência: durações casadas à fala real (words.json, %d palavras).", len(words_ts))
        except Exception as exc:  # noqa: BLE001 — qualquer falha → aproximação
            log.warning("Cadência por fala falhou (%s); usando aproximação por palavras.", exc)
            line_durations = compute_line_durations(script, total_duration)
            line_bounds = None
    else:
        line_durations = compute_line_durations(script, total_duration)

    # Duração máxima por shot — evita imagem congelada por períodos longos.
    # Se uma cena durar mais que MAX_SHOT_SEC, ela é dividida em sub-shots com
    # variações de Ken Burns (para imagens) ou cortes (para vídeos).
    max_shot_sec = float(os.environ.get("CANAL_DARK_MAX_SHOT", str(DEFAULT_MAX_SHOT_SEC)))

    # HOOK mais rápido: nos primeiros ~3s do vídeo, usa um max_shot menor (cortes mais
    # ágeis prendem a atenção logo no começo). Configurável por CANAL_DARK_HOOK_MAX_SHOT
    # (segundos) e CANAL_DARK_HOOK_WINDOW (janela em segundos a partir do 0).
    hook_max_shot = float(os.environ.get("CANAL_DARK_HOOK_MAX_SHOT", "2.2"))
    hook_window = float(os.environ.get("CANAL_DARK_HOOK_WINDOW", "3.0"))

    # Cada sub-shot abaixo deste piso vira "piscada" (zoompan precisa de frames).
    min_sub_sec = float(os.environ.get("CANAL_DARK_MIN_SUB", "0.6"))

    log.info(
        "Montando %d segmentos de b-roll (duração total: %.1fs, max_shot=%.1fs)",
        len(broll_files), total_duration, max_shot_sec
    )

    # Start ABSOLUTO (em segundos) de cada linha na timeline. Vem dos bounds reais
    # (cadência) quando disponíveis; senão é o acumulado das durações aproximadas.
    line_starts: list[float] = []
    _acc = 0.0
    for _li in range(len(line_durations)):
        if line_bounds and _li < len(line_bounds):
            line_starts.append(line_bounds[_li][0])
        else:
            line_starts.append(_acc)
        _acc += line_durations[_li]

    # Punch-in na palavra-chave (Frente C): SUTIL, atrás de flag p/ A/B testar.
    punch_on = os.environ.get("CANAL_DARK_PUNCH", "0").strip().lower() in ("1", "true", "yes")
    if punch_on and not words_ts:
        log.info("CANAL_DARK_PUNCH ligado mas sem words.json; punch-in desativado nesta run.")
        punch_on = False

    # 1. Prepara cada clipe de b-roll na resolução e duração corretas.
    #    Quando duration > max_shot_sec, divide em sub-shots com variação de movimento.
    ready_clips: list[Path] = []
    ready_durs: list[float] = []  # duração real de cada ready_clip (p/ a chain de xfade)
    clip_counter = 0  # índice global de clips (para nomes únicos de arquivo)

    for i, (broll, duration) in enumerate(zip(broll_files, line_durations)):
        # Normaliza para LISTA de imagens (suporta 1 imagem, várias distintas, ou None).
        imgs = broll if isinstance(broll, list) else [broll]
        if not imgs:
            imgs = [None]

        line_start = line_starts[i] if i < len(line_starts) else 0.0

        # max_shot efetivo: no HOOK (início do vídeo) corta mais rápido.
        eff_max_shot = max_shot_sec
        if hook_window > 0 and line_start < hook_window and hook_max_shot > 0:
            eff_max_shot = min(max_shot_sec, hook_max_shot) if max_shot_sec > 0 else hook_max_shot

        # nº de sub-shots = o MAIOR entre o split por tempo (eff_max_shot) e o nº de imagens.
        # Assim cada imagem distinta da cena ganha ao menos um sub-shot (mais variação).
        if eff_max_shot and eff_max_shot > 0 and duration > eff_max_shot:
            n_by_time = int(duration / eff_max_shot) + (1 if duration % eff_max_shot > 0.1 else 0)
        else:
            n_by_time = 1
        n_sub = max(n_by_time, len(imgs))
        # Piso anti-"piscada": não deixa o sub-shot cair abaixo de min_sub_sec.
        # (mas respeita o nº de imagens distintas — cada uma merece ao menos 1 shot.)
        if min_sub_sec > 0 and duration > 0:
            max_subs_by_floor = max(len(imgs), int(duration / min_sub_sec))
            n_sub = min(n_sub, max(1, max_subs_by_floor))
        sub_dur = duration / n_sub
        if n_sub > 1:
            log.info(
                "B-roll cena %d (%.1fs, t0=%.1fs): %d sub-shots de ~%.1fs, %d imagem(ns) distinta(s)",
                i, duration, line_start, n_sub, sub_dur, len([x for x in imgs if x is not None]) or 1,
            )

        prev_src = object()   # sentinela p/ detectar repetição do mesmo arquivo
        run_count = 0
        for sub_i in range(n_sub):
            # Distribui as imagens distintas igualmente pelos sub-shots.
            cur = imgs[(sub_i * len(imgs)) // n_sub]
            run_count = run_count + 1 if (cur is not None and cur == prev_src) else 0
            prev_src = cur
            variant = (i + sub_i) % 4  # varia o Ken Burns por cena e por sub-shot
            out_path_sub = work_dir / f"broll_ready_{clip_counter:03d}.mp4"

            # Punch-in: offset (em segundos, relativo ao início do sub-shot) da palavra
            # de maior carga dentro da janela [sub_start, sub_start+sub_dur]. None = sem punch.
            punch_at = None
            if punch_on and cur is not None and cur.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                sub_start_abs = line_start + sub_i * sub_dur
                punch_at = _punch_offset_for_window(words_ts, sub_start_abs, sub_dur)

            if cur is None:
                # REDE ANTI-PRETO: b-roll desta cena veio VAZIO → tenta foto atmosférica real
                # antes da cor sólida. Decide a query do último recurso pelo broll_kind da linha.
                _line = script["lines"][i] if i < len(script["lines"]) else {}
                _kind = str(_line.get("broll_kind", "") or "").strip().lower()
                _niche = os.environ.get("CANAL_DARK_NICHE", "").strip().lower()
                if _niche == "one-piece-theories-and-stories" and _kind == "character":
                    # One Piece CHARACTER virou preto: fidelidade perdida. NUNCA frame de anime
                    # nem nome de personagem na query → força o genérico atmosférico do helper.
                    log.warning("[one-piece] anti-black atmosférico p/ CHARACTER — "
                                "fidelidade perdida, sem frame de anime")
                    _lr_query = None
                elif _niche == "one-piece-theories-and-stories":
                    # OP cenário/objeto: query genérica de MUNDO (mar/ilha/ruína), nunca personagem.
                    _lr_query = "open pirate sea, distant islands, dramatic sky, cinematic"
                else:
                    # Demais nichos: pode usar a query da cena (foto atmosférica coerente).
                    _lr_query = str(_line.get("broll_query", "") or "").strip() or None
                ready = _last_resort_broll_image(sub_dur, out_path_sub, query=_lr_query)
            elif cur.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                ready = _ken_burns_clip(cur, sub_dur, out_path_sub, variant=variant, punch_at=punch_at)
            else:
                # Vídeo: offset avança só enquanto o MESMO arquivo se repete em sub-shots.
                offset_sec = run_count * sub_dur
                vf_sub = (
                    f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=increase,"
                    f"crop={OUTPUT_WIDTH}:{OUTPUT_HEIGHT},"
                    "fps=30,"
                    "setpts=PTS-STARTPTS"
                )
                cmd_sub = [
                    "ffmpeg", "-y",
                    "-ss", str(offset_sec),
                    "-stream_loop", "-1",
                    "-i", str(cur),
                    "-t", str(sub_dur),
                    "-vf", vf_sub,
                    "-c:v", "libx264",
                    "-preset", "fast",
                    "-crf", "23",
                    "-pix_fmt", "yuv420p",
                    "-r", "30",
                    "-an",
                    str(out_path_sub),
                ]
                res_sub = subprocess.run(cmd_sub, capture_output=True, text=True, timeout=120)
                if res_sub.returncode != 0:
                    log.warning(
                        "Sub-shot %d/%d da cena %d falhou; usando fallback.\n%s",
                        sub_i + 1, n_sub, i, res_sub.stderr[-400:]
                    )
                    ready = create_solid_color_clip(FALLBACK_BG_COLOR, sub_dur, out_path_sub)
                else:
                    ready = out_path_sub
            ready_clips.append(ready)
            ready_durs.append(sub_dur)
            clip_counter += 1

    # 2. Concatena todos os clipes de b-roll.
    #
    # Dois caminhos (Frente 3.2):
    #   (a) CROSSFADE (default p/ tom-história): xfade curto (~0.25s) entre planos. Como
    #       o xfade do FFmpeg é PAR-A-PAR e SOBREPÕE os clipes, ele ENCURTA a timeline
    #       (cada transição come `fade` segundos). O áudio/legenda são montados depois sobre
    #       este background, então um background mais curto dessincroniza tudo. Solução:
    #       o xfade encadeado é feito direto no filter_complex e o resultado é re-medido —
    #       NÃO usamos o demuxer concat (que não faz crossfade).
    #   (b) CORTE SECO: demuxer concat com -c copy (rápido, comportamento antigo).
    #
    # Todos os ready_clips JÁ estão em 30fps CFR + yuv420p + libx264 (re-encodados no passo
    # 1), então o xfade não esbarra no bug de fps variável do Pexels.
    bg_video_path = work_dir / "background.mp4"
    xfade_dur = float(os.environ.get("CANAL_DARK_XFADE", "0.25"))  # 0 = corte seco

    # Só faz sentido crossfade com ≥2 clipes e se cada par tem folga p/ a sobreposição.
    can_xfade = (
        xfade_dur > 0
        and len(ready_clips) >= 2
        and all(d > xfade_dur + 0.05 for d in ready_durs)
    )

    if can_xfade:
        # Encadeia xfade: v0 ⨉ v1 → v01; v01 ⨉ v2 → v012; ...
        # offset de cada transição = (soma das durações já encadeadas) - (xfade_dur)*(nº de
        # transições já feitas) - xfade_dur. Como cada xfade sobrepõe `xfade_dur`, o tempo
        # acumulado do encadeado cresce por (dur - xfade_dur) a cada clipe novo.
        inputs: list = []
        for clip in ready_clips:
            inputs += ["-i", str(clip.resolve())]
        filt = []
        prev_label = "0:v"
        acc = ready_durs[0]  # duração acumulada do stream encadeado até aqui
        for idx in range(1, len(ready_clips)):
            offset = acc - xfade_dur
            out_label = f"x{idx}"
            filt.append(
                f"[{prev_label}][{idx}:v]xfade=transition=fade:"
                f"duration={xfade_dur}:offset={offset:.4f}[{out_label}]"
            )
            prev_label = out_label
            acc = acc + ready_durs[idx] - xfade_dur  # sobreposição encurta a timeline
        filter_complex = ";".join(filt)
        cmd_concat = [
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", f"[{prev_label}]",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            "-an",
            str(bg_video_path),
        ]
        result = subprocess.run(cmd_concat, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            log.warning(
                "Crossfade (xfade) falhou; caindo p/ corte seco (concat -c copy).\n%s",
                result.stderr[-1200:],
            )
            can_xfade = False  # cai no caminho de corte seco abaixo
        else:
            log.info(
                "Background com crossfade (%d planos, xfade=%.2fs): %s",
                len(ready_clips), xfade_dur, bg_video_path.name,
            )

    if not can_xfade:
        # Caminho de CORTE SECO (demuxer concat). Gera lista p/ o demuxer.
        concat_list_path = work_dir / "concat_list.txt"
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for clip in ready_clips:
                # O demuxer concat resolve paths RELATIVOS à pasta do concat_list.txt.
                # Se --out-dir for relativo, clip.as_posix() também é relativo e o FFmpeg
                # acaba duplicando (out/_work/out/_work/...). Por isso gravamos ABSOLUTO.
                f.write(f"file '{clip.resolve().as_posix()}'\n")
        cmd_concat = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list_path),
            "-c", "copy",
            str(bg_video_path),
        ]
        result = subprocess.run(cmd_concat, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            log.error("FFmpeg falhou na concatenação do b-roll:\n%s", result.stderr[-2000:])
            sys.exit(1)
        log.info("Background concatenado (corte seco): %s", bg_video_path.name)

    # 3. Monta o short final: vídeo de fundo + narração + (música opcional) + legenda
    out_path = out_dir / "short.mp4"

    # No Windows o filtro 'subtitles' não lida bem com 'C:' no path.
    # Solução robusta: rodar o ffmpeg final com cwd na pasta do SRT e referenciar só o nome.
    srt_name = srt_path.name

    # === ESTILO DE LEGENDA DO CANAL — configurável por env ===
    #
    # Abordagem de resolução fixa: injetamos PlayResX/PlayResY no force_style.
    # O FFmpeg/libass, ao processar um SRT com force_style, usa esses campos para
    # definir o "canvas" virtual de escala — garantindo que FontSize=18 signifique
    # 18/1920 da altura do frame em QUALQUER máquina, seja ela 720p ou 4K.
    # (Referência: libass docs, "Script Info" section; testado no ffmpeg 6.x+)
    #
    # SUB_POS  (env): 'lower' (padrão) = terço inferior, longe da UI do TikTok/Shorts
    #                 'center'          = centro vertical da tela
    # SUB_STYLE (env): 'clean' (padrão) = legível, 2 linhas, ~30 chars/linha
    #                  'punchy'         = 1-3 palavras grandes por cue, impacto máximo

    sub_pos = os.environ.get("SUB_POS", "lower").strip().lower()
    sub_style_env = os.environ.get("SUB_STYLE", "clean").strip().lower()

    # Detecta fonte disponível (Montserrat ou fallback Arial)
    font_name = _resolve_subtitle_font()

    # fontsdir: instrui o libass a achar o .ttf local. Vale p/ QUALQUER fonte custom
    # (Montserrat OU Bebas) — antes só Montserrat, então a Bebas caía no fallback.
    #
    # ⚠️ ARMADILHA WINDOWS: um caminho ABSOLUTO ('C:/Users/...') no fontsdir QUEBRA o
    # parser do filtro subtitles (o 'C:' é lido como separador de opção). Solução
    # (mesmo padrão do .srt): o ffmpeg roda com cwd na pasta do .ass, então COPIAMOS
    # o .ttf p/ essa pasta e usamos 'fontsdir=.' — relativo, sem dois-pontos.
    fontsdir_opt = ""
    if font_name != SUBTITLE_FONT_FALLBACK and ASSETS_FONTS_DIR.exists():
        import shutil as _shutil
        _ttf_key = "montserrat" if font_name == SUBTITLE_FONT_PREFERRED else "bebas"
        for _ttf in ASSETS_FONTS_DIR.glob("*.ttf"):
            if _ttf_key in _ttf.name.lower():
                try:
                    _shutil.copy2(_ttf, srt_path.parent / _ttf.name)
                    fontsdir_opt = ":fontsdir=."
                except Exception as _exc:
                    log.warning("Não consegui copiar a fonte p/ cwd (%s); libass pode usar fallback.", _exc)
                break

    # Configuração por posição
    if sub_pos == "center":
        # Alignment=5 = centralizado horizontalmente E verticalmente (ASS numpad layout)
        alignment = 5
        margin_v = 0  # MarginV sem efeito em Alignment=5; mantido 0 por clareza
        log.info("SUB_POS=center: legenda centralizada verticalmente")
    else:
        # lower (padrão): Alignment=2 = centralizado na base; MarginV afasta da borda
        # 220px em 1920 = ~11% — margem segura acima da UI do TikTok/Shorts/Reels
        alignment = 2
        margin_v = 220
        log.info("SUB_POS=lower: legenda no terço inferior (MarginV=%d)", margin_v)

    # Configuração por estilo
    #
    # FontSize PROPORCIONAL à altura do vídeo (PlayResY=1920):
    #   alvo ~7.5% da altura → 7.5% * 1920 ≈ 144px
    #   No ASS/force_style o FontSize é em "script points" onde 1pt ≈ 1px quando
    #   PlayResY=1920. Por isso usamos 144 diretamente.
    #   "punchy" usa 1.15× (~165px) para impacto extra sem esconder o b-roll.
    #
    # Referência: libass docs, "Script Info", campos PlayResX/Y e ScaledBorderAndShadow.
    _SUB_FONT_RATIO = float(os.environ.get("SUB_FONT_RATIO", "0.075"))
    _base_font_size = int(round(SUBTITLE_PLAY_RES_Y * _SUB_FONT_RATIO))  # ≈ 144

    # O .ass do karaokê é construído com 1 evento/cue + \kf. Como o force_style do
    # libass SOBRESCREVE o FontSize embutido no .ass, definimos aqui um tamanho
    # menor (≈ 6% da altura ≈ 115px) p/ Bebas Neue caber em 1–2 linhas sem virar
    # parede de texto. Configurável por SUB_KARAOKE_FONT_RATIO.
    is_ass = srt_name.lower().endswith(".ass")
    if is_ass:
        _kara_ratio = float(os.environ.get("SUB_KARAOKE_FONT_RATIO", "0.060"))
        font_size = int(round(SUBTITLE_PLAY_RES_Y * _kara_ratio))
        bold = 1
        log.info("Legenda KARAOKE (.ass): FontSize=%d (~%.0f%% PlayResY), \\kf nativo, 1 evento/cue",
                 font_size, _kara_ratio * 100)
    elif sub_style_env == "punchy":
        # Punchy: ~15% maior que clean, poucas palavras por cue
        font_size = int(round(_base_font_size * 1.15))
        bold = 1
        log.info("SUB_STYLE=punchy: FontSize=%d (~%.0f%% PlayResY), cues curtos de 1-3 palavras",
                 font_size, _SUB_FONT_RATIO * 115)
        # NOTA PENDENTE: realce karaokê por palavra (\k) não implementado.
        # O edge-tts retorna timestamps por PALAVRA (WordBoundary), mas não por
        # fragmento subword. Implementar \k exigiria converter cada cue num ASS
        # nativo com eventos \k{duracao} por token — possível, mas fora do escopo
        # desta entrega. Os cues curtos já dão ritmo visual similar ao karaokê.
    else:
        # Clean: proporcional 7.5% → legível sem dominar o frame
        font_size = _base_font_size
        bold = 1
        log.info("SUB_STYLE=clean: FontSize=%d (~%.0f%% PlayResY), até 2 linhas de ~%d chars",
                 font_size, _SUB_FONT_RATIO * 100, SUBTITLE_MAX_CHARS_PER_LINE)

    subtitle_style = (
        f"FontName={font_name},"
        f"FontSize={font_size},"
        f"Bold={bold},"
        "PrimaryColour=&H00FFFFFF,"      # texto branco
        "OutlineColour=&HC0000000,"      # contorno preto ~75% opaco
        "BackColour=&H80000000,"         # sombra translúcida
        "BorderStyle=1,"                 # 1 = contorno+sombra (NÃO caixa cheia)
        "Outline=3,"                     # contorno espesso p/ legibilidade forte sobre b-roll
        "Shadow=2,"                      # sombra mais pronunciada p/ destacar do fundo
        f"Alignment={alignment},"
        f"MarginV={margin_v},"
        # PlayResX/Y fixam o "canvas" virtual do libass para que FontSize seja
        # idêntico em qualquer máquina independentemente da resolução de saída.
        f"PlayResX={SUBTITLE_PLAY_RES_X},"
        f"PlayResY={SUBTITLE_PLAY_RES_Y}"
    )
    vf_subtitles = f"subtitles='{srt_name}'{fontsdir_opt}:force_style='{subtitle_style}'"

    if music_path and music_path.exists():
        # Com trilha musical: mixa narração (volume normal) + música (-20dB relativo)
        # amerge + volume garante que a voz nunca fica abafada pela música
        cmd_final = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",         # loopa o fundo p/ cobrir toda a narração
            "-i", str(bg_video_path.resolve()),  # entrada 0: vídeo de fundo (abs: cwd=_work p/ legenda)
            "-i", str(narration_path.resolve()),  # entrada 1: narração (abs)
            "-stream_loop", "-1",
            "-i", str(music_path.resolve()),  # entrada 2: música (abs)
            "-filter_complex",
            # Normaliza volumes: narração 100%, música 15%
            "[1:a]volume=1.0[narration];"
            "[2:a]volume=0.15[music];"
            "[narration][music]amix=inputs=2:duration=first[audio_mix]",
            "-map", "0:v",               # vídeo do background
            "-map", "[audio_mix]",       # áudio mixado
            "-vf", vf_subtitles,
            "-t", str(total_duration),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            str(out_path.resolve()),
        ]
    else:
        # Sem música: apenas narração como trilha de áudio
        cmd_final = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",         # loopa o fundo p/ cobrir toda a narração
            "-i", str(bg_video_path.resolve()),  # entrada 0: vídeo de fundo (abs: cwd=_work p/ legenda)
            "-i", str(narration_path.resolve()),  # entrada 1: narração (abs)
            "-map", "0:v",
            "-map", "1:a",
            "-vf", vf_subtitles,
            "-t", str(total_duration),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            str(out_path.resolve()),
        ]

    log.info("Renderizando short.mp4 (pode demorar alguns minutos)...")
    # cwd na pasta do SRT → o filtro subtitles abre o arquivo pelo nome, sem o 'C:' problemático
    result = subprocess.run(cmd_final, capture_output=True, text=True, timeout=600, cwd=str(srt_path.parent))
    if result.returncode != 0:
        log.error("FFmpeg falhou na montagem final:\n%s", result.stderr[-3000:])
        sys.exit(1)

    size_mb = out_path.stat().st_size / 1e6
    log.info("short.mp4 gerado: %s (%.1f MB, %.1fs)", out_path, size_mb, total_duration)
    return out_path


# ═════════════════════════════════════════════════════════════════════════════
# (e) METADATA DE PUBLICAÇÃO
# ═════════════════════════════════════════════════════════════════════════════

def _flatten_broll_files(broll_files: list) -> list:
    """
    Achata a lista de b-roll da timeline em Paths únicos, NA ORDEM de aparição.
    Cada item de broll_files pode ser None, um Path, ou uma LISTA de Paths
    (várias imagens distintas numa cena). Dedupa preservando ordem — o CTA
    reusa o b-roll anterior, então o mesmo arquivo aparece 2x e não deve gerar
    crédito duplicado.
    """
    seen: set = set()
    flat: list = []
    for item in broll_files or []:
        if item is None:
            continue
        candidates = item if isinstance(item, (list, tuple)) else [item]
        for p in candidates:
            if p is None:
                continue
            key = str(p)
            if key in seen:
                continue
            seen.add(key)
            flat.append(Path(p))
    return flat


def _credit_for_broll_file(path: Path) -> Optional[str]:
    """
    Resolve UMA linha de crédito honesta para 1 arquivo de b-roll que ENTROU
    na timeline. Fonte de verdade = o próprio arquivo (não um log acumulado).

    Estratégia, na ordem:
      1) Sidecar de metadata do image_providers ('<stem>.json' no out_dir ou no
         img_cache) → usa attribution/license/source_url reais. Cobre os bancos
         (Wikimedia/Openverse/archive.org/Civitai) do fluxo híbrido.
      2) Sem sidecar → classifica pelo prefixo do nome do arquivo:
           - 'broll_ai_*'   → imagem AI-generated (Pollinations/Cloudflare/ImageRouter FLUX)
           - 'fandom_char_*'→ One Piece Wiki (Fandom) — IP Toei/Shueisha
           - 'broll_*.mp4'  → vídeo de stock Pexels
    Retorna None se o arquivo não rende crédito relevante (não deveria ocorrer).
    """
    stem = path.stem
    name = path.name.lower()

    # (1) Sidecar de metadata (mesmo stem). O .jpg é copiado pro out_dir, mas o
    #     .json fica no img_cache → procura nos dois lugares.
    sidecar_dirs = [path.parent]
    try:
        sidecar_dirs.append(Path.cwd() / "out" / "img_cache")
    except Exception:
        pass
    env_cache = os.environ.get("IMG_CACHE_DIR", "").strip()
    if env_cache:
        sidecar_dirs.append(Path(env_cache))

    for d in sidecar_dirs:
        meta_path = d / f"{stem}.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                break
            provider = meta.get("provider", "")
            license_str = meta.get("license", "")
            attribution = meta.get("attribution", "")
            source_url = meta.get("source_url", "")
            if attribution:
                return f"  {attribution} ({license_str}) — {source_url}"
            if source_url:
                return f"  {provider.capitalize()}: {source_url} ({license_str})"
            return None

    # (2) Classificação por prefixo (arquivos gerados sem sidecar).
    if name.startswith("broll_ai_"):
        return "  AI-generated image (FLUX via Pollinations/Cloudflare/ImageRouter)"
    if name.startswith("fandom_char_"):
        return "  One Piece Wiki (Fandom) — https://onepiece.fandom.com/"
    if name.startswith("broll_") and name.endswith(".mp4"):
        return "  Stock footage from Pexels (https://www.pexels.com/)"
    return None


def _build_image_credits_block(broll_files: list) -> str:
    """
    Formata o bloco "Image credits:" da descrição a partir das imagens de b-roll
    que ENTRARAM na timeline final DESTE vídeo, na ordem, 1:1.

    NÃO lê mais o CREDITS.jsonl acumulado (que misturava runs/temas antigos e
    creditava imagens que nem aparecem no vídeo). Cada crédito vem do arquivo
    realmente usado: sidecar de metadata p/ bancos, ou classificação por prefixo
    p/ AI/Fandom/Pexels. Retorna string vazia se nada render crédito.
    """
    lines: list = []
    seen_lines: set = set()
    for path in _flatten_broll_files(broll_files):
        line = _credit_for_broll_file(path)
        if not line or line in seen_lines:
            continue
        seen_lines.add(line)
        lines.append(line)

    if not lines:
        return ""

    return "Image credits:\n" + "\n".join(lines)


def build_publication_metadata(script: dict, short_path: Path,
                               broll_files: Optional[list] = None) -> dict:
    """
    Gera o metadata.json com título, descrição e hashtags formatados
    para cada plataforma alvo. Este arquivo é lido pelo n8n para publicar via Postiz.

    O bloco de créditos reflete EXATAMENTE as imagens de b-roll que entraram na
    timeline final deste vídeo (via broll_files), 1:1 — sem vazamento de cache
    de runs/temas antigos.
    """
    title = script["title"]
    hashtags_str = " ".join(script.get("hashtags", []))

    # Descrição base: hook + cta + hashtags
    base_description = f"{script['hook']}\n\n{script['cta']}\n\n{hashtags_str}"

    # Aviso obrigatório sobre conteúdo de IA (boa prática; obrigatório no YouTube se "realistic")
    ai_disclosure = "This video was created with AI assistance (voice & visuals). #AIContent"

    # Bloco de créditos: SÓ o b-roll efetivamente usado nesta run.
    credits_block = _build_image_credits_block(broll_files or [])
    credits_section = f"\n\n{credits_block}" if credits_block else ""

    metadata = {
        "short_path": str(short_path),
        "youtube": {
            "title": title,
            "description": (
                f"{base_description}\n\n"
                f"{ai_disclosure}"
                f"{credits_section}"
            ),
            "tags": [tag.lstrip("#") for tag in script.get("hashtags", [])],
            # IMPORTANTE: marque "altered_or_synthetic" se a voz for gerada por IA
            "made_for_kids": False,
            "ai_disclosure_required": True,
            "note": "Marque 'Altered or synthetic content' no YouTube Studio antes de publicar.",
        },
        "tiktok": {
            "caption": f"{title} {hashtags_str}",
            "note": "Adicione sticker 'AI Generated' no TikTok Creator Tools.",
        },
        "instagram": {
            "caption": f"{title}\n\n{script['cta']}\n\n{hashtags_str}",
            "note": "Use a label 'Created with AI' nas configurações do Reels.",
        },
    }

    return metadata


# ═════════════════════════════════════════════════════════════════════════════
# PIPELINE PRINCIPAL
# ═════════════════════════════════════════════════════════════════════════════

def run_pipeline(
    topic: Optional[str],
    script_file: Optional[str],
    out_dir: Path,
    tts_voice: str,
    tts_engine: str,
    broll_source: str,
    music_path: Optional[Path],
    script_only: bool = False,
) -> dict:
    """
    Orquestra as etapas (a) → (b) → (c) → (d) → (e) do pipeline.
    Retorna o dict de metadados de saída.

    script_only:
        Se True, executa apenas a etapa (a) — gera/carrega o roteiro, salva o
        script_draft.json em out_dir, imprime o caminho no stdout e retorna.
        Nenhuma voz, b-roll ou montagem é realizada.
        Útil para o bot do Telegram mostrar o roteiro para aprovação antes de
        gastar créditos de TTS e tempo de render.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # Diretório temporário de trabalho (intermediários que não precisam ser entregues)
    work_dir = out_dir / "_work"
    work_dir.mkdir(exist_ok=True)

    # ── (a) ROTEIRO ────────────────────────────────────────────────────────────
    if script_file:
        log.info("Carregando roteiro aprovado: %s", script_file)
        script = load_script_from_file(script_file)
    elif topic:
        log.info("Gerando roteiro via Gemini para o tema: %s", topic)
        script = generate_script_via_gemini(topic)
        # Salva o roteiro gerado para que o humano possa revisar/reusar
        draft_path = out_dir / "script_draft.json"
        draft_path.write_text(
            json.dumps(script, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        log.info(
            "Roteiro salvo em: %s\n"
            "DICA: revise o roteiro antes de usar em producao com --script-file.",
            draft_path,
        )
    else:
        log.error("Forneça --topic ou --script-file.")
        sys.exit(1)

    # ── MODO --script-only: sai aqui após salvar o rascunho do roteiro ────────
    if script_only:
        draft_path = out_dir / "script_draft.json"
        draft_path.write_text(
            json.dumps(script, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        log.info("[script-only] Roteiro salvo. Saindo sem gerar voz/render.")
        # Imprime o caminho absoluto no stdout para que o bot possa ler
        print(str(draft_path.resolve()))
        return {"script_only": True, "script_draft": str(draft_path.resolve()), "script": script}

    # ── (b) VOZ (TTS) + SRT ────────────────────────────────────────────────────
    full_text = build_full_narration(script)
    narration_path = work_dir / "narration.mp3"
    srt_path = work_dir / "subtitles.srt"

    if tts_engine == "edge":
        # edge-tts pode gerar mp3 + SRT numa única passagem via SubMaker
        srt_path = generate_srt_from_edge_tts(full_text, tts_voice, srt_path, narration_path)
        # Se o mp3 não foi gerado pelo SubMaker (comportamento de versões antigas), gera separado
        if not narration_path.exists() or narration_path.stat().st_size < 1000:
            log.info("Gerando narração separada (SubMaker não gerou mp3)...")
            narration_path = synthesize_voice(full_text, tts_engine, tts_voice, narration_path)
    else:
        # Outros engines: gera mp3 primeiro, depois SRT com fallback
        narration_path = synthesize_voice(full_text, tts_engine, tts_voice, narration_path)
        srt_path.write_text(
            _generate_fallback_srt(full_text),
            encoding="utf-8"
        )
        log.info("SRT de fallback gerado (sem timestamps exatos).")

    # ── (c) B-ROLL ─────────────────────────────────────────────────────────────
    broll_dir = work_dir / "broll"
    broll_dir.mkdir(exist_ok=True)

    # ── Carrega imagens de referência forçadas (env REF_DIR) ──────────────────
    # Se REF_DIR aponta para uma pasta com imagens (.jpg/.png/.webp), elas são
    # usadas como b-roll nas primeiras N linhas (uma por linha, em ordem).
    # Linhas sem imagem de referência seguem o fluxo normal (Pexels).
    ref_images: list[Path] = []
    ref_dir_env = os.environ.get("REF_DIR", "").strip()
    if ref_dir_env:
        ref_dir = Path(ref_dir_env)
        if ref_dir.is_dir():
            img_exts = {".jpg", ".jpeg", ".png", ".webp"}
            ref_images = sorted(
                p for p in ref_dir.iterdir()
                if p.suffix.lower() in img_exts
            )
            if ref_images:
                log.info(
                    "REF_DIR: %d imagem(ns) de referência encontrada(s) em '%s': %s",
                    len(ref_images), ref_dir,
                    [p.name for p in ref_images]
                )
            else:
                log.warning("REF_DIR='%s' existe mas não contém imagens. Ignorando.", ref_dir)
        else:
            log.warning("REF_DIR='%s' não é uma pasta válida. Ignorando.", ref_dir_env)

    # Baixa b-roll para cada linha + para o CTA (última posição)
    all_queries = [line["broll_query"] for line in script["lines"]] + [script.get("cta", "")]
    broll_files: list[Optional[Path]] = []
    # Set compartilhado entre todas as chamadas do mesmo vídeo para deduplicar clipes
    used_broll_ids: set = set()
    vctx = script.get("visual_context") or {}

    # ── One Piece: tenta personagem certo via Fandom/Safebooru ────────────────
    # Mapa de apelidos comuns para o título canônico do Fandom One Piece Wiki.
    _OP_ALIAS: dict = {
        "imu": "Nerona_Imu",
        "joy boy": "Joy_Boy",
        "joyboy": "Joy_Boy",
        "luffy": "Monkey_D._Luffy",
        "monkey d luffy": "Monkey_D._Luffy",
        "robin": "Nico_Robin",
        "nico robin": "Nico_Robin",
        "zoro": "Roronoa_Zoro",
        "roronoa zoro": "Roronoa_Zoro",
        "shanks": "Shanks",
        "blackbeard": "Marshall_D._Teach",
        "marshall d teach": "Marshall_D._Teach",
        "loki": "Loki",
        "elbaf": "Elbaf",
    }

    def _extract_entities(texts: list) -> list:
        """
        Extrai entidades nomeadas simples (sequências de palavras Capitalizadas)
        dos textos fornecidos. Retorna lista deduplicada, ordem de aparição.
        """
        combined = " ".join(texts)
        # Captura sequências de 1 a 4 palavras capitalizadas (não stopwords puras)
        raw_matches = re.findall(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})\b', combined)
        # Dedup preservando ordem
        seen: set = set()
        entities: list = []
        for m in raw_matches:
            key = m.lower()
            if key not in seen and len(m) > 2:
                seen.add(key)
                entities.append(m)
        return entities

    def _op_fandom_title(entity: str) -> str:
        """Resolve apelido → título canônico do Fandom; senão converte espaços em _."""
        return _OP_ALIAS.get(entity.lower(), entity.replace(" ", "_"))

    # Stopwords: sequências capitalizadas que NÃO são personagem (evita gastar
    # fetch com "The", "Erased", "Year War World Gov" que a regex gulosa captura).
    _OP_STOPWORDS = {
        "the", "a", "an", "and", "but", "war", "world", "year", "secret", "erased",
        "the secret war between", "year war world gov", "world gov", "secret war",
    }

    # Pré-VALIDA as entidades do canal one-piece: só entra quem resolve numa página
    # real do Fandom (com imagem). Para cada entidade válida guardamos os TERMOS de
    # match (nome + apelidos) para depois casar com a FALA de cada cena — assim,
    # quando a narração fala "World Government", entra o SÍMBOLO do WG; quando fala
    # "Imu", entra o Imu. Imagem segue a fala (contextual), não a ordem.
    niche_env = os.environ.get("CANAL_DARK_NICHE", "").strip().lower()
    _op_char_entries: list = []   # [{terms:set, img:Path, label:str}] em ordem de validação
    # CONTROLADOR ONE-PIECE = DEFAULT do nicho (desacoplado de ALLOW_ANIME). Ele faz:
    #   • personagens (broll_kind='character') → render IA FLUX on-brand (NUNCA web/anime real);
    #   • cenário/objeto (broll_kind='scenery'|'object') → tenta FOTO REAL livre (lane WEB
    #     burn: wikimedia/openverse/archive.org, só PD/CC0/CC-BY/CC-BY-SA) e, em qualquer
    #     dúvida/falha, cai pro render IA.
    # ALLOW_ANIME volta a significar SÓ o caminho de FRAMES BOORU/anime REAIS (Content ID
    # Toei/Shueisha) — que segue OFF/indesejado e NÃO é necessário pra ligar o controlador.
    _op_mode = (niche_env == "one-piece-theories-and-stories")
    _fb_mode = (niche_env == "futebol-historia")

    # ── CADÊNCIA DE CORTE do canal "Stoppage Time" (futebol-historia) ──────────────
    # Achado da pesquisa de ritmo (etapa 2): ~1 troca de imagem a cada 2,5-3s no corpo,
    # apertando p/ 1,5-2s no clímax; HOOK mais RESPIRADO (1 plano forte 3-4s, NÃO o corte
    # rápido genérico). Os defaults globais (MAX_SHOT/SEC_PER_IMAGE=4.0, HOOK_MAX_SHOT=2.2)
    # são lentos demais no corpo e rápidos demais no hook p/ o tom-história do futebol.
    # Aqui ajustamos SÓ os DEFAULTS deste nicho via setdefault — qualquer env explícito do
    # operador/.env ainda vence, e os outros 3 nichos ficam INTOCADOS. Transições (Frente 3):
    # crossfade CURTO entre planos (xfade ~0.25s, CANAL_DARK_XFADE) — perceptível mas discreto
    # (tom-história), NÃO whip/flash — somado ao Ken Burns com easing dentro do plano.
    if _fb_mode:
        os.environ.setdefault("CANAL_DARK_MAX_SHOT", "2.8")       # corpo: troca ~2,5-3s
        os.environ.setdefault("CANAL_DARK_SEC_PER_IMAGE", "2.8")  # nº de imgs distintas/linha casa o corpo
        os.environ.setdefault("CANAL_DARK_HOOK_MAX_SHOT", "3.5")  # hook RESPIRADO: 1 plano forte 3-4s
        os.environ.setdefault("CANAL_DARK_HOOK_WINDOW", "4.0")    # janela do hook respirado
        os.environ.setdefault("CANAL_DARK_KB_ZOOM_END", "1.10")   # Ken Burns push-in SUAVE (~10%), foto-história não estoura
        os.environ.setdefault("CANAL_DARK_KB_PAN", "0.05")        # pan lento e contido (tom reverente)
        log.info(
            "[futebol] Cadência Stoppage Time: corpo ~%ss, hook %ss/janela %ss, "
            "Ken Burns zoom_end=%s pan=%s (defaults do nicho; env explícito sobrepõe).",
            os.environ["CANAL_DARK_MAX_SHOT"], os.environ["CANAL_DARK_HOOK_MAX_SHOT"],
            os.environ["CANAL_DARK_HOOK_WINDOW"], os.environ["CANAL_DARK_KB_ZOOM_END"],
            os.environ["CANAL_DARK_KB_PAN"],
        )

    if _op_mode:
        entity_texts = [topic or "", script.get("title", "")]
        raw_entities = _extract_entities(entity_texts)
        log.info("[one-piece] Entidades brutas: %s", raw_entities)
        use_fandom = _op_use_fandom_stills()
        import image_providers as _ip
        seen_titles: set = set()

        def _op_entity_terms(entity: str, fandom_title: str) -> set:
            """Termos de match do entry: nome extraído + título Fandom + apelidos."""
            terms = {entity.lower(), fandom_title.replace("_", " ").lower()}
            for alias_k, alias_v in _OP_ALIAS.items():
                if alias_v == fandom_title:
                    terms.add(alias_k)
            return terms

        for entity in raw_entities:
            if entity.lower() in _OP_STOPWORDS:
                continue
            fandom_title = _op_fandom_title(entity)
            if fandom_title in seen_titles:
                continue
            seen_titles.add(fandom_title)

            if not use_fandom:
                # PURE-AI DEFAULT (v4): NÃO baixa still real do Fandom. Resolve a entidade
                # pra um subject da biblioteca (traços de assinatura, inclusive Loki via
                # 'loki') e gera um render IA cinematográfico. Entidade que não mapeia pra
                # nenhum subject conhecido é ignorada (o _OP_CONCEPT_MAP da cena cobre).
                subj = _OP_TITLE_SUBJECT.get(fandom_title)
                if not subj:
                    subj, _ = _op_pick_subject(entity.lower(), "")
                if subj not in _OP_SUBJECT_LIBRARY:
                    log.info("[one-piece] '%s' não mapeia subject — coberto pelo concept map.", entity)
                    continue
                _op_char_entries.append({
                    "terms": _op_entity_terms(entity, fandom_title), "img": None,
                    "label": entity, "title": fandom_title, "ai_subject": subj,
                })
                log.info("[one-piece] Entidade '%s' → render IA on-brand (subject '%s').", entity, subj)
                continue

            # Caminho OPCIONAL (OP_USE_FANDOM_STILLS=1): valida via imagem real do Fandom.
            try:
                img = _ip._prov_fandom_pageimage(fandom_title, broll_dir)
            except Exception as exc:
                log.warning("[one-piece] Erro ao validar '%s': %s", entity, exc)
                img = None
            if img is not None:
                _op_char_entries.append({
                    "terms": _op_entity_terms(entity, fandom_title), "img": img,
                    "label": entity, "title": fandom_title,
                    # ai_subject: se o personagem tem imagem real fraca (ex.: Imu = painel cru
                    # de mangá), preferimos um render IA estilizado no lugar do .png real.
                    "ai_subject": _OP_PREFER_AI_SUBJECT.get(fandom_title),
                })
                log.info("[one-piece] Entidade VÁLIDA: '%s' (match: %s) → %s%s",
                         entity, sorted(_op_entity_terms(entity, fandom_title)), img.name,
                         " [render IA preferido]" if _OP_PREFER_AI_SUBJECT.get(fandom_title) else "")
            else:
                log.info("[one-piece] '%s' não resolveu no Fandom — descartado.", entity)
        log.info("[one-piece] %d entidade(s) p/ casar com a narração (pure-ai=%s).",
                 len(_op_char_entries), not use_fandom)

    # Texto da fala de cada cena (paralelo a all_queries) p/ o match contextual.
    all_texts = [line["text"] for line in script["lines"]] + [script.get("cta", "")]
    # broll_kind por cena, paralelo a all_queries; +1 p/ o CTA (default seguro do nicho).
    # Roteia a FONTE da imagem por shot. one-piece: 'character'→IA, 'scenery'/'object'→foto
    # livre/IA. futebol-historia: 'scene'/'object'→Pexels, 'player_real'→foto livre PD/CC,
    # 'caricature'→IA cartoon. O default do CTA acompanha o lado seguro de cada nicho.
    _kind_default = "scene" if _fb_mode else "character"
    all_kinds = [str(line.get("broll_kind", _kind_default)).strip().lower()
                 for line in script["lines"]] + [_kind_default]

    if _op_mode:
        # Canal One Piece: planeja todas as cenas e gera as imagens de IA EM PARALELO
        # (mais rápido). Cada cena já vem com 1+ imagens DISTINTAS on-brand. Personagens
        # = render IA; cenário/objeto pode ser FOTO REAL livre (PD/CC) da lane WEB.
        broll_files = _build_op_broll(
            all_queries, all_texts, all_kinds, _op_char_entries, ref_images, vctx, broll_dir,
        )
    elif _fb_mode:
        # Canal futebol-historia: roteia a FONTE por broll_kind (scene/object → Pexels;
        # player_real → foto livre PD/CC com crédito; caricature → IA cartoon). VETO duro:
        # player_real sem foto livre cai em 'scene' (nunca foto da web sem licença / IA real).
        broll_files = _build_fb_broll(
            all_queries, all_texts, all_kinds, ref_images, vctx, broll_dir,
            used_broll_ids, broll_source,
        )
    else:
        for i, query in enumerate(all_queries):
            line_text = all_texts[i] if i < len(all_texts) else ""
            is_cta = (i == len(all_queries) - 1)
            # nº de imagens DISTINTAS desta linha (~1 a cada CANAL_DARK_SEC_PER_IMAGE s).
            n_imgs = 1 if is_cta else _estimate_n_imgs(line_text)

            if i < len(ref_images):
                ref_img = ref_images[i]
                log.info("B-roll linha %d: usando imagem de referência '%s'", i, ref_img.name)
                broll_files.append(ref_img)
            elif is_cta and len(broll_files) > 0:
                broll_files.append(broll_files[-1])
                log.info("B-roll do CTA: reutilizando b-roll anterior.")
            else:
                # Demais nichos: busca n imagens DISTINTAS (seed/índice varia → composições
                # diferentes na lane de IA do híbrido; dedup de clipes no Pexels).
                imgs = []
                for k in range(n_imgs):
                    bf = fetch_broll(query, broll_source, broll_dir, i * 10 + k,
                                     vctx=vctx, used_ids=used_broll_ids)
                    if bf is not None:
                        imgs.append(bf)
                if not imgs:
                    broll_files.append(None)
                else:
                    broll_files.append(imgs if len(imgs) > 1 else imgs[0])
                log.info("B-roll cena %d: %d imagem(ns) distinta(s).", i, max(1, len(imgs)))

    # ── (d) MONTAGEM ───────────────────────────────────────────────────────────
    short_path = assemble_short(
        script=script,
        broll_files=broll_files,
        narration_path=narration_path,
        srt_path=srt_path,
        out_dir=out_dir,
        music_path=music_path,
        work_dir=work_dir,
    )

    # ── (e) METADATA DE PUBLICAÇÃO ─────────────────────────────────────────────
    metadata = build_publication_metadata(script, short_path, broll_files=broll_files)
    metadata["script"] = script  # inclui o roteiro completo para rastreabilidade

    metadata_path = out_dir / "metadata.json"
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    log.info("Metadata salvo: %s", metadata_path)

    log.info(
        "\n========================================\n"
        "Pipeline concluido com sucesso!\n"
        "  short.mp4  : %s\n"
        "  metadata   : %s\n"
        "  titulo     : %s\n"
        "========================================",
        short_path, metadata_path, script["title"]
    )

    return metadata


# ═════════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Canal Dark — Short Factory: roteiro narrado → vídeo 9:16 pronto pra publicar",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
  # Gera roteiro via Gemini (para teste; revise antes de usar em producao):
  python short_factory.py --topic "Why ancient Stoics slept on the floor"

  # Apenas gera o roteiro e sai (sem voz/render) — ideal pro checkpoint do Telegram:
  python short_factory.py --script-only --topic "Why ancient Stoics slept on the floor"

  # Usa roteiro ja aprovado pelo humano (fluxo normal de producao):
  python short_factory.py --script-file ./approved_script.json

  # Com musica de fundo e voz masculina:
  python short_factory.py --topic "..." --tts-voice en-US-GuyNeural --music ./bg_music.mp3

  # Usando ElevenLabs (requer ELEVENLABS_API_KEY):
  python short_factory.py --script-file ./script.json --tts-engine elevenlabs --tts-voice 21m00Tcm4TlvDq8ikWAM

Variaveis de ambiente:
  GEMINI_API_KEY     — necessaria para --topic (https://aistudio.google.com/app/apikey)
  PEXELS_API_KEY     — necessaria para --broll-source pexels (https://www.pexels.com/api/)
  ELEVENLABS_API_KEY — necessaria para --tts-engine elevenlabs (opcional)
  SUB_POS            — posicao da legenda: 'lower' (padrao) ou 'center'
  SUB_STYLE          — estilo da legenda: 'clean' (padrao) ou 'punchy'
  REF_DIR            — pasta com imagens forcadas como b-roll nas primeiras linhas
        """,
    )

    # Entrada: roteiro gerado ou aprovado
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--topic", "-t",
        metavar="TOPIC",
        help="Tema do short — Gemini gera o roteiro (bom para testes rápidos).",
    )
    source_group.add_argument(
        "--script-file", "-s",
        metavar="PATH",
        help="Path para o script.json ja aprovado pelo humano (fluxo de producao).",
    )

    # Saída
    parser.add_argument(
        "--out-dir", "-o",
        default="./out",
        help="Diretório de saída (padrão: ./out).",
    )

    # Modo script-only (checkpoint do Telegram)
    parser.add_argument(
        "--script-only",
        action="store_true",
        default=False,
        help=(
            "Apenas gera o roteiro JSON e sai (sem voz, b-roll ou render). "
            "Salva script_draft.json em --out-dir e imprime o caminho no stdout. "
            "Requer --topic (ou --script-file para revalidar um roteiro existente)."
        ),
    )

    # TTS
    parser.add_argument(
        "--tts-voice",
        default=DEFAULT_TTS_VOICE,
        help=(
            f"Voz do TTS (padrão: {DEFAULT_TTS_VOICE}). "
            "Para edge-tts, veja vozes disponíveis: python -m edge_tts --list-voices. "
            "Para ElevenLabs, use o voice_id da API."
        ),
    )
    parser.add_argument(
        "--tts-engine",
        choices=["edge", "elevenlabs"],
        default="edge",
        help="Engine TTS: 'edge' (grátis, padrão) ou 'elevenlabs' (requer chave).",
    )

    # B-roll
    parser.add_argument(
        "--broll-source",
        choices=["pexels", "ai", "image"],
        default="pexels",
        help=(
            "Fonte de b-roll: 'pexels' (grátis, padrão), 'ai' (Pollinations.ai, grátis) "
            "ou 'image' (image_providers: Wikimedia/Openverse/IA, grátis, sem chave paga). "
            "Com IMG_PROVIDERS setada, image_providers é tentado primeiro em qualquer source."
        ),
    )

    # Música
    parser.add_argument(
        "--music",
        metavar="PATH",
        default=None,
        help=(
            "Path para arquivo de áudio de fundo opcional (.mp3/.wav). "
            "Volume reduzido automaticamente para não cobrir a narração."
        ),
    )

    args = parser.parse_args()

    # Valida caminhos opcionais
    out_dir = Path(args.out_dir)
    music_path: Optional[Path] = None
    if args.music:
        music_path = Path(args.music)
        if not music_path.exists():
            parser.error(f"Arquivo de música não encontrado: {args.music}")

    # Pré-check do sistema — no modo --script-only FFmpeg não é necessário
    if not args.script_only:
        check_ffmpeg()

    result = run_pipeline(
        topic=args.topic,
        script_file=args.script_file,
        out_dir=out_dir,
        tts_voice=args.tts_voice,
        tts_engine=args.tts_engine,
        broll_source=args.broll_source,
        music_path=music_path,
        script_only=args.script_only,
    )

    # Em modo --script-only o caminho do JSON já foi impresso dentro do pipeline;
    # aqui imprimimos o JSON completo de resultado apenas no modo normal.
    if not args.script_only:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
