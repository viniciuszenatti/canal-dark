# LEGADO — modelo antigo de clipping, substituído por short_factory.py
"""
clip_engine.py — Motor de Clipping do Canal Dark
=================================================
Recebe uma URL de vídeo do YouTube e:
  1. Verifica se a licença é Creative Commons (aborta se não for)
  2. Baixa via yt-dlp
  3. Transcreve com faster-whisper (modelo 'base')
  4. Chama Gemini para selecionar os melhores trechos para Shorts
  5. Corta + reenquadra 9:16 + queima legenda com FFmpeg
  6. Salva metadados em JSON de saída

Uso:
    python clip_engine.py --url "https://youtube.com/watch?v=..." --out-dir ./output --num-clips 3

Dependências do sistema (não pip):
    ffmpeg — instale via https://ffmpeg.org/download.html e adicione ao PATH
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("clip_engine")

# ── Constantes ────────────────────────────────────────────────────────────────
GEMINI_MODEL = "gemini-2.5-flash-lite"
WHISPER_MODEL = "base"
# Crop 9:16: a partir de um vídeo landscape (w > h), cortamos uma faixa central
# de largura = ih * (9/16) e altura total ih, depois escalamos para 1080x1920.
# Fórmula FFmpeg: crop=iw*(9/16):ih:(iw-(iw*9/16))/2:0,scale=1080:1920
# Nota: usamos iw*(9/16) para manter aspect ratio correto; ih permanece intacto.
FFMPEG_CROP_FILTER = "crop=iw*9/16:ih:(iw-iw*9/16)/2:0,scale=1080:1920"


# ─────────────────────────────────────────────────────────────────────────────
# 1. VERIFICAÇÃO E DOWNLOAD
# ─────────────────────────────────────────────────────────────────────────────

def check_ffmpeg() -> None:
    """Verifica se o FFmpeg está disponível no PATH. Aborta se não estiver."""
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
            "Instale em https://ffmpeg.org/download.html e adicione ao PATH."
        )
        sys.exit(1)


def get_video_info(url: str) -> dict:
    """
    Obtém metadados do vídeo via yt-dlp (sem baixar).
    Retorna dict com 'license', 'title', 'id', etc.
    Aborta se o vídeo não for acessível.
    """
    log.info("Consultando metadados do vídeo: %s", url)
    try:
        result = subprocess.run(
            ["yt-dlp", "--dump-json", "--no-playlist", url],
            capture_output=True, text=True, timeout=60, encoding="utf-8"
        )
    except FileNotFoundError:
        log.error("yt-dlp não encontrado. Instale: pip install yt-dlp")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        log.error("Timeout ao consultar metadados do vídeo.")
        sys.exit(1)

    if result.returncode != 0:
        log.error("yt-dlp falhou ao obter metadados:\n%s", result.stderr)
        sys.exit(1)

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        log.error("Resposta inesperada do yt-dlp: %s", exc)
        sys.exit(1)


def assert_creative_commons(info: dict) -> None:
    """
    Verifica se a licença do vídeo é Creative Commons.
    O campo 'license' do yt-dlp retorna strings como 'Creative Commons Attribution license'
    para vídeos CC-BY. Aborta com mensagem clara se não for CC.
    """
    license_str: str = info.get("license") or ""
    if "creative commons" not in license_str.lower():
        title = info.get("title", "desconhecido")
        log.error(
            "GUARDRAIL: Vídeo NÃO é Creative Commons.\n"
            "  Título  : %s\n"
            "  Licença : %s\n"
            "Download cancelado. Use apenas conteúdo CC.",
            title,
            license_str or "(não informada)",
        )
        sys.exit(2)  # código 2 = falha de licença (distinguível em scripts)
    log.info("Licença OK: %s", license_str)


def download_video(url: str, out_dir: Path) -> Path:
    """
    Baixa o melhor vídeo+áudio disponível em formato mp4.
    Retorna o Path do arquivo baixado.
    """
    log.info("Baixando vídeo em: %s", out_dir)
    out_template = str(out_dir / "%(id)s.%(ext)s")
    result = subprocess.run(
        [
            "yt-dlp",
            "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/mp4",
            "--merge-output-format", "mp4",
            "--no-playlist",
            "--output", out_template,
            "--print", "filename",  # imprime o path final no stdout
            url,
        ],
        capture_output=True, text=True, timeout=600, encoding="utf-8"
    )
    if result.returncode != 0:
        log.error("Erro no download:\n%s", result.stderr)
        sys.exit(1)

    # yt-dlp --print filename imprime o path na última linha
    downloaded_path = result.stdout.strip().splitlines()[-1]
    path = Path(downloaded_path)
    if not path.exists():
        # Fallback: procura o primeiro .mp4 no diretório
        candidates = list(out_dir.glob("*.mp4"))
        if not candidates:
            log.error("Arquivo baixado não encontrado em %s", out_dir)
            sys.exit(1)
        path = candidates[0]

    log.info("Download concluído: %s (%.1f MB)", path.name, path.stat().st_size / 1e6)
    return path


# ─────────────────────────────────────────────────────────────────────────────
# 2. TRANSCRIÇÃO COM FASTER-WHISPER
# ─────────────────────────────────────────────────────────────────────────────

def transcribe_video(video_path: Path) -> list[dict]:
    """
    Transcreve o áudio do vídeo com faster-whisper (modelo WHISPER_MODEL).
    Retorna lista de segments: [{start, end, text}, ...]
    O modelo roda localmente — sem custo, sem API.
    """
    log.info("Transcrevendo com faster-whisper (modelo '%s')...", WHISPER_MODEL)
    try:
        from faster_whisper import WhisperModel  # importação tardia para mensagem de erro clara
    except ImportError:
        log.error("faster-whisper não instalado. Execute: pip install faster-whisper")
        sys.exit(1)

    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    segments_iter, info = model.transcribe(str(video_path), beam_size=5)

    log.info(
        "Idioma detectado: %s (probabilidade %.0f%%)",
        info.language,
        info.language_probability * 100,
    )

    segments = []
    for seg in segments_iter:
        segments.append({
            "start": round(seg.start, 2),
            "end": round(seg.end, 2),
            "text": seg.text.strip(),
        })

    log.info("Transcrição concluída: %d segmentos", len(segments))
    return segments


def segments_to_srt(segments: list[dict], srt_path: Path) -> None:
    """
    Gera um arquivo .srt a partir dos segmentos do Whisper.
    O SRT será usado pelo FFmpeg para burn-in de legenda.
    """
    def fmt_time(seconds: float) -> str:
        """Converte segundos para o formato HH:MM:SS,mmm do SRT."""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    for i, seg in enumerate(segments, start=1):
        lines.append(str(i))
        lines.append(f"{fmt_time(seg['start'])} --> {fmt_time(seg['end'])}")
        lines.append(seg["text"])
        lines.append("")  # linha em branco obrigatória entre entradas SRT

    srt_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("SRT gerado: %s", srt_path)


# ─────────────────────────────────────────────────────────────────────────────
# 3. SELEÇÃO DE TRECHOS VIA GEMINI
# ─────────────────────────────────────────────────────────────────────────────

def build_gemini_prompt(segments: list[dict], num_clips: int, video_title: str) -> str:
    """
    Monta o prompt para o Gemini selecionar os melhores trechos para Shorts.
    O modelo deve retornar JSON puro (sem markdown).
    """
    # Formata a transcrição como texto corrido com timestamps para o Gemini
    transcript_lines = [
        f"[{seg['start']:.1f}s–{seg['end']:.1f}s] {seg['text']}"
        for seg in segments
    ]
    transcript_text = "\n".join(transcript_lines)

    prompt = f"""You are an expert YouTube Shorts editor specializing in viral, transformative clips for a global English-speaking audience.

Video title: "{video_title}"

Below is the full transcript with timestamps (in seconds):
---
{transcript_text}
---

Task: Identify the {num_clips} best continuous segments (30–60 seconds each) to cut as viral YouTube Shorts. Prioritize segments that:
1. Start with a strong hook (surprising fact, bold claim, or compelling question)
2. Tell a complete micro-story or insight without needing prior context
3. Are highly shareable and work for international audiences

Return ONLY a valid JSON array (no markdown, no explanation) in this exact format:
[
  {{
    "clip_number": 1,
    "start": <float seconds>,
    "end": <float seconds>,
    "hook": "<first 10 words of the hook>",
    "reason": "<one sentence explaining why this segment will go viral>"
  }}
]

Rules:
- start and end must be exact float values from the transcript timestamps
- end - start must be between 30 and 60 seconds
- No overlapping segments
- Return exactly {num_clips} items"""

    return prompt


def call_gemini(prompt: str) -> str:
    """
    Chama a API do Gemini via SDK google-generativeai.
    Retorna o texto da resposta.
    Trata erros de quota (ResourceExhausted) com mensagem clara.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        log.error(
            "GEMINI_API_KEY não definida.\n"
            "Exporte: export GEMINI_API_KEY='sua-chave-aqui'\n"
            "Obtenha em: https://aistudio.google.com/app/apikey"
        )
        sys.exit(1)

    try:
        import google.generativeai as genai  # importação tardia
    except ImportError:
        log.error(
            "SDK do Gemini não instalado. Execute: pip install google-generativeai"
        )
        sys.exit(1)

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(GEMINI_MODEL)

    try:
        log.info("Chamando Gemini (%s)...", GEMINI_MODEL)
        response = model.generate_content(prompt)
        return response.text
    except Exception as exc:
        error_str = str(exc)
        if "ResourceExhausted" in error_str or "429" in error_str:
            log.error(
                "Quota do Gemini esgotada (ResourceExhausted).\n"
                "Aguarde até o reset diário ou use uma chave diferente.\n"
                "Detalhes: %s",
                error_str,
            )
        elif "InvalidArgument" in error_str or "400" in error_str:
            log.error("Prompt inválido ou vídeo muito longo para o contexto: %s", error_str)
        else:
            log.error("Erro inesperado ao chamar Gemini: %s", error_str)
        sys.exit(1)


def parse_gemini_clips(raw_response: str) -> list[dict]:
    """
    Faz parse da resposta JSON do Gemini.
    Remove blocos de markdown (```json ... ```) se o modelo os incluir mesmo sendo instruído a não.
    """
    text = raw_response.strip()

    # Remove cerca markdown que o modelo às vezes inclui
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

    try:
        clips = json.loads(text)
    except json.JSONDecodeError as exc:
        log.error(
            "Gemini retornou resposta que não é JSON válido.\n"
            "Erro: %s\n"
            "Resposta bruta:\n%s",
            exc, raw_response
        )
        sys.exit(1)

    if not isinstance(clips, list):
        log.error("Gemini retornou JSON mas não é uma lista: %s", type(clips))
        sys.exit(1)

    # Valida campos obrigatórios
    required_fields = {"clip_number", "start", "end", "hook", "reason"}
    for clip in clips:
        missing = required_fields - set(clip.keys())
        if missing:
            log.warning("Clip com campos faltando (%s): %s", missing, clip)

    return clips


# ─────────────────────────────────────────────────────────────────────────────
# 4. CORTE + REENQUADRAMENTO 9:16 + LEGENDA COM FFMPEG
# ─────────────────────────────────────────────────────────────────────────────

def cut_and_reframe_clip(
    source_video: Path,
    srt_path: Path,
    clip_info: dict,
    out_dir: Path,
    video_id: str,
) -> Path:
    """
    Corta o trecho indicado por clip_info, reenquadra para 9:16 e queima a legenda.

    Filtro FFmpeg explicado:
      crop=iw*9/16:ih:(iw-iw*9/16)/2:0
        - largura do crop = largura original * 9/16  (para landscape 16:9, isso dá exatamente h)
        - altura do crop  = ih (altura total)
        - offset x        = (iw - largura_crop) / 2  (centraliza horizontalmente)
        - offset y        = 0
      scale=1080:1920
        - escala para resolução padrão de Shorts

      subtitles=<srt>:force_style='...'
        - burn-in do SRT; force_style define fonte e tamanho
    """
    clip_num = clip_info["clip_number"]
    start = float(clip_info["start"])
    end = float(clip_info["end"])
    duration = end - start

    if duration < 1:
        log.warning("Clip %d com duração muito curta (%.1fs), pulando.", clip_num, duration)
        return None

    out_path = out_dir / f"{video_id}_clip{clip_num:02d}.mp4"

    # Escapa o path do SRT para o filtro FFmpeg (barras e dois-pontos precisam de escape)
    srt_escaped = str(srt_path).replace("\\", "/").replace(":", "\\:")

    # Filtro composto: crop → scale → burn-in de legenda
    vf_filter = (
        f"crop=iw*9/16:ih:(iw-iw*9/16)/2:0,"
        f"scale=1080:1920,"
        f"subtitles='{srt_escaped}':"
        f"force_style='FontName=Arial,FontSize=18,PrimaryColour=&Hffffff,OutlineColour=&H000000,BorderStyle=3'"
    )

    cmd = [
        "ffmpeg",
        "-y",                        # sobrescreve sem perguntar
        "-ss", str(start),           # seek de entrada (rápido, antes do decode)
        "-i", str(source_video),
        "-to", str(duration),        # duração a partir do ponto de seek
        "-vf", vf_filter,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",                # qualidade razoável, tamanho moderado
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",   # otimiza para streaming
        str(out_path),
    ]

    log.info(
        "Cortando clip %d [%.1fs → %.1fs] (%.0fs)...",
        clip_num, start, end, duration
    )

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        log.error("FFmpeg falhou no clip %d:\n%s", clip_num, result.stderr[-2000:])
        return None

    size_mb = out_path.stat().st_size / 1e6
    log.info("Clip %d salvo: %s (%.1f MB)", clip_num, out_path.name, size_mb)
    return out_path


# ─────────────────────────────────────────────────────────────────────────────
# 5. ORQUESTRAÇÃO PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def process_video(url: str, out_dir: Path, num_clips: int) -> dict:
    """
    Função principal: executa o pipeline completo para uma URL de vídeo.
    Retorna um dict com os paths dos clips gerados e metadados.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # 0. Pré-check do sistema
    check_ffmpeg()

    # 1. Metadados + guardrail de licença
    info = get_video_info(url)
    assert_creative_commons(info)

    video_id = info.get("id", "unknown")
    video_title = info.get("title", "Unknown Title")
    log.info("Processando: '%s' (ID: %s)", video_title, video_id)

    # 2. Download
    video_path = download_video(url, out_dir)

    # 3. Transcrição + geração do SRT
    segments = transcribe_video(video_path)
    if not segments:
        log.error("Transcrição vazia — o vídeo tem áudio em inglês?")
        sys.exit(1)

    srt_path = out_dir / f"{video_id}.srt"
    segments_to_srt(segments, srt_path)

    # 4. Seleção de trechos via Gemini
    prompt = build_gemini_prompt(segments, num_clips, video_title)
    raw_response = call_gemini(prompt)
    clips = parse_gemini_clips(raw_response)
    log.info("Gemini selecionou %d trechos.", len(clips))

    # 5. Corte + reenquadramento
    output_clips = []
    for clip_info in clips:
        clip_path = cut_and_reframe_clip(
            source_video=video_path,
            srt_path=srt_path,
            clip_info=clip_info,
            out_dir=out_dir,
            video_id=video_id,
        )
        if clip_path:
            output_clips.append({
                "clip_number": clip_info["clip_number"],
                "path": str(clip_path),
                "start": clip_info["start"],
                "end": clip_info["end"],
                "hook": clip_info.get("hook", ""),
                "reason": clip_info.get("reason", ""),
            })

    # 6. Salva metadados em JSON de saída
    output_metadata = {
        "source_url": url,
        "video_id": video_id,
        "video_title": video_title,
        "license": info.get("license", ""),
        "source_video_path": str(video_path),
        "srt_path": str(srt_path),
        "clips": output_clips,
    }

    metadata_path = out_dir / f"{video_id}_metadata.json"
    metadata_path.write_text(
        json.dumps(output_metadata, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    log.info("Metadados salvos: %s", metadata_path)
    log.info("Pipeline concluído. %d clip(s) gerado(s) em %s", len(output_clips), out_dir)

    return output_metadata


# ─────────────────────────────────────────────────────────────────────────────
# 6. CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Canal Dark — Motor de Clipping de Shorts Transformativos",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
  python clip_engine.py --url "https://youtube.com/watch?v=dQw4w9WgXcQ"
  python clip_engine.py --url "https://youtube.com/watch?v=..." --out-dir ./output --num-clips 2

Variáveis de ambiente necessárias:
  GEMINI_API_KEY  — chave da Google AI Studio (https://aistudio.google.com/app/apikey)
        """,
    )
    parser.add_argument(
        "--url", "-u",
        required=True,
        help="URL do vídeo no YouTube (deve ser Creative Commons)",
    )
    parser.add_argument(
        "--out-dir", "-o",
        default="./output",
        help="Diretório de saída para os clips (padrão: ./output)",
    )
    parser.add_argument(
        "--num-clips", "-n",
        type=int,
        default=3,
        help="Número de clips a gerar (padrão: 3)",
    )

    args = parser.parse_args()

    if args.num_clips < 1 or args.num_clips > 10:
        parser.error("--num-clips deve ser entre 1 e 10")

    result = process_video(
        url=args.url,
        out_dir=Path(args.out_dir),
        num_clips=args.num_clips,
    )

    # Imprime o JSON de saída no stdout para integração com n8n/outros scripts
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
