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
# Se não encontrada, cai para SUBTITLE_FONT_FALLBACK sem erro silencioso.
SUBTITLE_FONT_PREFERRED = "Montserrat SemiBold"
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
    Verifica se Montserrat SemiBold está disponível em assets/fonts/.
    Loga qual fonte foi escolhida para que seja fácil auditar em CI/produção.
    Retorna o nome da fonte para uso no force_style do FFmpeg.

    Busca por qualquer .ttf com 'montserrat' no nome (case-insensitive) em
    ASSETS_FONTS_DIR. Se encontrar, instrui o filtro subtitles a usar
    fontsdir=<pasta>; se não, usa Arial e loga aviso claro.
    """
    if ASSETS_FONTS_DIR.exists():
        ttf_files = list(ASSETS_FONTS_DIR.glob("*.ttf")) + list(ASSETS_FONTS_DIR.glob("*.TTF"))
        for ttf in ttf_files:
            if "montserrat" in ttf.name.lower():
                log.info("Fonte de legenda: %s (Montserrat encontrada em assets/fonts/)", ttf.name)
                return SUBTITLE_FONT_PREFERRED
    log.warning(
        "Montserrat NAO encontrada em %s. "
        "Usando fallback '%s'. "
        "Para usar Montserrat: baixe Montserrat-SemiBold.ttf de "
        "https://fonts.google.com/specimen/Montserrat e salve em assets/fonts/.",
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
    for rel in (f"{niche}/02-roteiro-e-linguagem.md",
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

    # subject_mode: apenas valores válidos
    valid_modes = {"places", "objects", "atmosphere"}
    if vctx.get("subject_mode") not in valid_modes:
        vctx["subject_mode"] = "places"

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

    # ── Agrupa palavras em cues (lógica 'clean') ────────────────────────────────
    max_total_chars = SUBTITLE_MAX_CHARS_PER_LINE * SUBTITLE_MAX_LINES
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

    # ── Emite eventos ASS ────────────────────────────────────────────────────────
    dialogue_lines = []

    for cue in cues:
        cue_start = fmt_ass_time(cue[0][0])
        cue_end   = fmt_ass_time(cue[-1][1])
        # Texto do cue com quebra de linha ASS (\N) mas sem inline color
        # (layer 0 = base, tudo branco)
        base_text = _wrap_subtitle_text(" ".join(w[2] for w in cue)).replace("\n", "\\N")

        # Layer 0: base — texto branco todo o cue
        dialogue_lines.append(
            f"Dialogue: 0,{cue_start},{cue_end},Default,,0,0,0,,{base_text}"
        )

        # Layer 1: um evento por palavra — realça somente a palavra ativa
        for k, (w_start, w_end, w_text) in enumerate(cue):
            # Reconstrói o texto do cue com a palavra k em destaque
            parts = []
            for j, (_, _, wt) in enumerate(cue):
                if j == k:
                    parts.append(f"{{\\c{hl_color}}}{wt}{{\\c&H00FFFFFF&}}")
                else:
                    parts.append(wt)
            cue_with_hl = " ".join(parts)
            # Mantém quebras de linha do cue original (usa _wrap_subtitle_text
            # sem os overrides, depois reaplica nos mesmos pontos de quebra)
            # Abordagem simples: wrap no texto sem tags, depois reinsere as tags
            # pelo alinhamento de palavras (sem risco de quebrar tags inline).
            raw_wrapped_words = _wrap_subtitle_text(" ".join(w[2] for w in cue)).split("\n")
            # Remonta linha-a-linha com inline colors
            lines_hl = []
            word_cursor = 0
            for line_text in raw_wrapped_words:
                line_words_count = len(line_text.split())
                line_parts = []
                for ji in range(word_cursor, word_cursor + line_words_count):
                    _, _, wt = cue[ji]
                    if ji == k:
                        line_parts.append(f"{{\\c{hl_color}}}{wt}{{\\c&H00FFFFFF&}}")
                    else:
                        line_parts.append(wt)
                lines_hl.append(" ".join(line_parts))
                word_cursor += line_words_count
            hl_text = "\\N".join(lines_hl)

            w_start_fmt = fmt_ass_time(w_start)
            w_end_fmt   = fmt_ass_time(w_end)
            dialogue_lines.append(
                f"Dialogue: 1,{w_start_fmt},{w_end_fmt},Default,,0,0,0,,{hl_text}"
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
    # 1. Modo cinematográfico (IA) tem PRECEDÊNCIA: gera imagens coesas a partir do
    #    visual_context em vez de banco genérico. Ligado por IMG_STYLE=cinematic.
    #    (Vem antes de IMG_LANE pra não ser sobreposto pelo IMG_LANE=burn do .env.)
    if os.environ.get("IMG_STYLE", "").strip().lower() == "cinematic":
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
        except ImportError:
            log.warning("image_providers não disponível — seguindo para Pexels.")

    # source=="image" sem resultado → fallback Pexels (se PEXELS_API_KEY existir)
    if source == "image":
        pexels_key = os.environ.get("PEXELS_API_KEY", "").strip()
        if not pexels_key:
            log.warning("Nenhuma imagem encontrada via image_providers e PEXELS_API_KEY não definida.")
            return None
        log.info("image_providers sem resultado — fallback para Pexels.")
        return _fetch_pexels(query, out_dir, index, vctx=vctx, used_ids=used_ids)

    if source == "pexels":
        return _fetch_pexels(query, out_dir, index, vctx=vctx, used_ids=used_ids)
    elif source == "ai":
        return _fetch_pollinations(query, out_dir, index)
    else:
        log.error("Fonte de b-roll desconhecida: '%s'. Use 'pexels', 'ai' ou 'image'.", source)
        sys.exit(1)


def _fetch_pollinations(query: str, out_dir: Path, index: int) -> Optional[Path]:
    """Gera uma imagem 9:16 grátis no Pollinations.ai (sem chave de API) para a query."""
    import urllib.parse
    import requests
    prompt = f"{query}, cinematic, dramatic lighting, highly detailed"
    encoded = urllib.parse.quote(prompt, safe="")
    seed = (index * 7919 + len(query)) % 1000000  # determinístico, varia por linha
    url = (f"https://image.pollinations.ai/prompt/{encoded}"
           f"?width=1080&height=1920&model=flux&nologo=true&seed={seed}")
    out_path = out_dir / f"broll_ai_{index:02d}.jpg"
    log.info("Gerando b-roll por IA (Pollinations): '%s'", query[:60])
    try:
        r = requests.get(url, timeout=120)
        if r.status_code != 200 or len(r.content or b"") < 1000:
            log.warning("Pollinations falhou (status %s). Fallback.", r.status_code)
            return None
        out_path.write_bytes(r.content)
        return out_path
    except Exception as e:
        log.warning("Erro no Pollinations: %s. Fallback.", e)
        return None


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

def _ken_burns_clip(image_path: Path, duration: float, out_path: Path,
                    variant: int = 0) -> Path:
    """
    Transforma uma imagem em clipe 9:16 com zoom/pan lento (Ken Burns).

    variant (int): variante de movimento para evitar shots idênticos quando a
    mesma imagem é reutilizada em sub-shots (E5 — cap de duração).
      0 → zoom-in a partir do centro (padrão)
      1 → zoom-in deslocando levemente para a esquerda/baixo (pan sutil)
      2 → zoom-in a partir do canto superior-direito
      3 → zoom-out leve (z inicia em 1.12 e decresce para 1.0)
    """
    frames = max(1, int(round(duration * 30)))

    if variant == 1:
        # Pan: parte do centro-esquerdo e caminha para o centro
        zoom_expr = "min(zoom+0.0010,1.12)"
        x_expr = "iw/2-(iw/zoom/2)+iw*0.02*(1-on/in)"
        y_expr = "ih/2-(ih/zoom/2)+ih*0.015*(1-on/in)"
    elif variant == 2:
        # Canto superior-direito
        zoom_expr = "min(zoom+0.0012,1.15)"
        x_expr = "iw*0.65-(iw/zoom/2)"
        y_expr = "ih*0.15-(ih/zoom/2)"
    elif variant == 3:
        # Zoom-out: começa grande e afasta levemente
        zoom_expr = "max(1.12-0.0012*on,1.0)"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
    else:
        # Padrão (variant 0): zoom-in a partir do centro
        zoom_expr = "min(zoom+0.0012,1.15)"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"

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
        # Nenhum b-roll disponível — gera cor sólida para este segmento
        return create_solid_color_clip(FALLBACK_BG_COLOR, duration, out_path)

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

    # Calcula duração de cada segmento de b-roll proporcional ao número de palavras
    line_durations = compute_line_durations(script, total_duration)

    # Duração máxima por shot — evita imagem congelada por períodos longos.
    # Se uma cena durar mais que MAX_SHOT_SEC, ela é dividida em sub-shots com
    # variações de Ken Burns (para imagens) ou cortes (para vídeos).
    max_shot_sec = float(os.environ.get("CANAL_DARK_MAX_SHOT", str(DEFAULT_MAX_SHOT_SEC)))

    log.info(
        "Montando %d segmentos de b-roll (duração total: %.1fs, max_shot=%.1fs)",
        len(broll_files), total_duration, max_shot_sec
    )

    # 1. Prepara cada clipe de b-roll na resolução e duração corretas.
    #    Quando duration > max_shot_sec, divide em sub-shots com variação de movimento.
    ready_clips: list[Path] = []
    clip_counter = 0  # índice global de clips (para nomes únicos de arquivo)

    for i, (broll, duration) in enumerate(zip(broll_files, line_durations)):
        if duration <= max_shot_sec or max_shot_sec <= 0:
            # Shot curto: comportamento normal.
            # Para imagens, chama _ken_burns_clip com variante baseada no índice da cena.
            # Para vídeos e None, delega a prepare_broll_segment (lógica original).
            if broll is not None and broll.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                out_path_clip = work_dir / f"broll_ready_{clip_counter:03d}.mp4"
                ready = _ken_burns_clip(broll, duration, out_path_clip, variant=i % 4)
            else:
                # prepare_broll_segment cria broll_ready_{clip_counter:02d}.mp4
                ready = prepare_broll_segment(broll, duration, clip_counter, work_dir)
            ready_clips.append(ready)
            clip_counter += 1
        else:
            # Shot longo: divide em sub-shots de até max_shot_sec segundos
            n_sub = int(duration / max_shot_sec) + (1 if duration % max_shot_sec > 0.1 else 0)
            sub_dur = duration / n_sub  # distribui igualmente (pode ser ligeiramente > max)
            log.info(
                "B-roll cena %d (%.1fs > %.1fs): dividido em %d sub-shots de ~%.1fs",
                i, duration, max_shot_sec, n_sub, sub_dur
            )
            for sub_i in range(n_sub):
                variant = (i * 4 + sub_i) % 4  # alterna 4 variantes de Ken Burns
                out_path_sub = work_dir / f"broll_ready_{clip_counter:03d}.mp4"
                if broll is None:
                    ready = create_solid_color_clip(FALLBACK_BG_COLOR, sub_dur, out_path_sub)
                elif broll.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                    # Imagem: cada sub-shot usa uma variante diferente de Ken Burns
                    ready = _ken_burns_clip(broll, sub_dur, out_path_sub, variant=variant)
                else:
                    # Vídeo: usa offset temporal diferente para cada sub-shot
                    # (re-encodar trecho do vídeo original a partir de offset calculado)
                    offset_sec = sub_i * sub_dur
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
                        "-i", str(broll),
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
                clip_counter += 1

    # 2. Concatena todos os clipes de b-roll usando o filter_complex concat
    #    Gera um arquivo de lista para o demuxer concat do FFmpeg
    concat_list_path = work_dir / "concat_list.txt"
    with open(concat_list_path, "w", encoding="utf-8") as f:
        for clip in ready_clips:
            # O demuxer concat resolve paths RELATIVOS à pasta do concat_list.txt.
            # Se --out-dir for relativo, clip.as_posix() também é relativo e o FFmpeg
            # acaba duplicando (out/_work/out/_work/...). Por isso gravamos ABSOLUTO.
            f.write(f"file '{clip.resolve().as_posix()}'\n")

    bg_video_path = work_dir / "background.mp4"
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
    log.info("Background concatenado: %s", bg_video_path.name)

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

    # Dimensiona fontsdir se Montserrat foi encontrada localmente
    fontsdir_opt = ""
    if font_name == SUBTITLE_FONT_PREFERRED and ASSETS_FONTS_DIR.exists():
        # fontsdir instrui o libass a procurar .ttf nesta pasta
        fontsdir_opt = f":fontsdir='{ASSETS_FONTS_DIR.as_posix()}'"

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

    if sub_style_env == "punchy":
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

def _build_image_credits_block(out_dir: Path) -> str:
    """
    Lê o CREDITS.jsonl gerado pelo image_providers nesta run e formata
    um bloco de créditos para incluir na descrição do vídeo.

    Apenas entradas com attribution preenchida aparecem no bloco de texto —
    CC0/PD não exigem crédito mas são incluídas com URL de origem para transparência.
    Retorna string vazia se o arquivo não existir ou não tiver entradas.
    """
    credits_path = out_dir / "CREDITS.jsonl"
    # Também procura na pasta cwd/out (padrão do image_providers)
    if not credits_path.exists():
        credits_path = Path.cwd() / "out" / "CREDITS.jsonl"
    if not credits_path.exists():
        return ""

    lines = []
    try:
        for raw in credits_path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            entry = json.loads(raw)
            provider = entry.get("provider", "")
            license_str = entry.get("license", "")
            attribution = entry.get("attribution", "")
            source_url = entry.get("source_url", "")
            if attribution:
                lines.append(f"  {attribution} ({license_str}) — {source_url}")
            elif source_url:
                lines.append(f"  {provider.capitalize()}: {source_url} ({license_str})")
    except Exception as exc:
        log.warning("Não foi possível ler CREDITS.jsonl: %s", exc)
        return ""

    if not lines:
        return ""

    return "Image credits:\n" + "\n".join(lines)


def build_publication_metadata(script: dict, short_path: Path) -> dict:
    """
    Gera o metadata.json com título, descrição e hashtags formatados
    para cada plataforma alvo. Este arquivo é lido pelo n8n para publicar via Postiz.

    Se CREDITS.jsonl existir em out_dir (gerado pelo image_providers), o bloco
    de créditos é anexado à descrição do YouTube.
    """
    title = script["title"]
    hashtags_str = " ".join(script.get("hashtags", []))

    # Descrição base: hook + cta + hashtags
    base_description = f"{script['hook']}\n\n{script['cta']}\n\n{hashtags_str}"

    # Aviso obrigatório sobre conteúdo de IA (boa prática; obrigatório no YouTube se "realistic")
    ai_disclosure = "This video was created with AI assistance (voice & visuals). #AIContent"

    # Bloco de créditos de imagem (do image_providers, se houver)
    out_dir = short_path.parent
    credits_block = _build_image_credits_block(out_dir)
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

    for i, query in enumerate(all_queries):
        # Verifica se há imagem de referência forçada para esta posição
        if i < len(ref_images):
            ref_img = ref_images[i]
            log.info("B-roll linha %d: usando imagem de referência '%s'", i, ref_img.name)
            broll_files.append(ref_img)
        # CTA geralmente não precisa de b-roll específico — reutiliza o último
        elif i == len(all_queries) - 1 and len(broll_files) > 0:
            # Reutiliza o último b-roll para o CTA (economiza quota da API)
            broll_files.append(broll_files[-1])
            log.info("B-roll do CTA: reutilizando b-roll anterior.")
        else:
            bf = fetch_broll(query, broll_source, broll_dir, i, vctx=vctx, used_ids=used_broll_ids)
            broll_files.append(bf)

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
    metadata = build_publication_metadata(script, short_path)
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
