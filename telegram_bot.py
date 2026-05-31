"""
Bot do Canal Dark — comande o pipeline pelo Telegram.

Fluxo normal:
  /roteiro <nicho> <tema>  →  gera roteiro → aprovação inline → vídeo → publicar/descartar
  /gerar <nicho> <tema>    →  atalho: mesmo fluxo que /roteiro (mantém compatibilidade)

Envie uma foto ou link de imagem (.jpg/.png/.webp) antes do /gerar para forçar
imagens de referência no b-roll.

Nichos: crimes | misterios | onepiece

Comportamento:
  - Long-polling (getUpdates) — sem webhook, sem URL pública.
  - Só responde ao dono (TELEGRAM_CHAT_ID do .env) — bot privado.
  - 1 job por vez; geração roda em thread separada (loop nunca bloqueia).
"""

import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OWNER = str(os.environ.get("TELEGRAM_CHAT_ID", "")).strip()
API = f"https://api.telegram.org/bot{TOKEN}"

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "short_factory.py"
OUT = HERE / "out"
PYTHON = sys.executable  # mesmo python (venv) que está rodando este bot

# ---------------------------------------------------------------------------
# Mapeamento de apelidos → pasta do nicho (mantém exatamente o original)
# ---------------------------------------------------------------------------
NICHE_ALIASES = {
    "crimes": "true-crimes",
    "crime": "true-crimes",
    "true-crimes": "true-crimes",
    "truecrimes": "true-crimes",
    "misterios": "conspiracy-theories",
    "mistérios": "conspiracy-theories",
    "misterio": "conspiracy-theories",
    "conspiracy": "conspiracy-theories",
    "conspiracao": "conspiracy-theories",
    "mysteries": "conspiracy-theories",
    "onepiece": "one-piece-theories-and-stories",
    "one-piece": "one-piece-theories-and-stories",
    "op": "one-piece-theories-and-stories",
}

# ---------------------------------------------------------------------------
# Estado global do job (1 por vez — canal pessoal)
# ---------------------------------------------------------------------------
# Campos:
#   id          — UUID único do job
#   topic       — tema escolhido
#   niche       — pasta do nicho (ex: "true-crimes") ou None
#   stage       — "script" | "voice" | "broll" | "render" | "done" | "failed"
#   phase       — "A" (script-only) | "B" (render) | "C" (aprovação final)
#   proc        — subprocess.Popen ativo ou None
#   cancelled   — bool
#   chat        — chat_id do dono
#   status_msg_id — message_id da mensagem de progresso (editada ao vivo)
#   script_path — Path do script_draft.json (preenchido no fim da fase A)
#   ref_dir     — Path da pasta de refs ou None
#   started_at  — time.time() do início
current_job: dict | None = None
job_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Detecção de etapa via stdout do short_factory
# ---------------------------------------------------------------------------
# Mapeamento de substrings de log → nome de etapa interna.
# As strings abaixo foram extraídas dos log.info/log.warning reais do
# short_factory.py (grep por log.info / log.warning / log.error):
#
#  ROTEIRO:
#    "Gerando roteiro via Gemini"   → log.info linha ~379
#    "Roteiro carregado"            → log.info linha ~501 ("Roteiro carregado: ...")
#    "Carregando roteiro aprovado"  → log.info linha ~1564
#    "[script-only] Roteiro salvo"  → log.info linha ~1591
#
#  VOZ:
#    "Sintetizando voz com edge-tts" → log.info linha ~556
#    "Gerando SRT com timestamps"    → log.info linha ~621
#    "Narração gerada:"              → log.info linha ~572
#    "SRT gerado:"                   → log.info linha ~656
#
#  B-ROLL:
#    "Buscando b-roll no Pexels"    → log.info linha ~1051
#    "Baixando b-roll"              → log.info linha ~1098
#    "Gerando b-roll por IA"        → log.info linha ~854 (_fetch_pollinations)
#    "REF_DIR:"                     → log.info linha ~1636
#
#  MONTAGEM:
#    "Renderizando short.mp4"       → log.info linha ~1474
#    "Montando"                     → (não existe literalmente; coberto por "Renderizando")
#    "Background concatenado"       → log.info linha ~1341
#
#  FIM:
#    "short.mp4 gerado:"            → log.info linha ~1482
#    "Pipeline concluido com sucesso" → log.info linha ~1690

STAGE_PATTERNS = [
    # (substring_lowercase, nome_etapa)
    # Ordem importa: primeiro match ganha.
    ("gerando roteiro via gemini",    "script"),
    ("roteiro carregado:",            "script"),
    ("carregando roteiro aprovado",   "script"),
    ("[script-only] roteiro salvo",   "script"),
    ("sintetizando voz",              "voice"),
    ("gerando srt com timestamps",    "voice"),
    ("narração gerada:",              "voice"),
    ("srt gerado:",                   "voice"),
    ("buscando b-roll",               "broll"),
    ("baixando b-roll",               "broll"),
    ("b-roll via image_providers",    "broll"),
    ("b-roll linha",                  "broll"),
    ("gerando b-roll por ia",         "broll"),
    ("ref_dir:",                      "broll"),
    ("background concatenado",        "render"),
    ("renderizando short.mp4",        "render"),
    ("short.mp4 gerado:",             "done"),
    ("pipeline concluido com sucesso","done"),
]

STAGE_ORDER = ["script", "voice", "broll", "render", "done"]

# Checklist visual por etapa
STAGE_LABELS = {
    "script":  "Roteiro",
    "voice":   "Voz",
    "broll":   "B-roll",
    "render":  "Montagem",
    "done":    "Pronto",
}


def _detect_stage(line: str) -> str | None:
    """Retorna o nome da etapa detectada na linha de log, ou None."""
    ll = line.lower()
    for pattern, stage in STAGE_PATTERNS:
        if pattern in ll:
            return stage
    return None


def _build_progress_text(topic: str, niche: str | None, stage: str) -> str:
    """
    Monta o checklist de progresso que é editado na mensagem ao vivo.
    Etapas anteriores à atual: ✅; etapa atual: ⏳; demais: ⬜.
    """
    niche_label = f" [{niche}]" if niche else ""
    header = f"🎬 {topic}{niche_label}\n"
    if stage == "done":
        lines = [f"✅ {STAGE_LABELS[s]}" for s in STAGE_ORDER if s != "done"]
        lines.append("✅ Pronto!")
        return header + "\n".join(lines)
    if stage == "failed":
        lines = []
        for s in STAGE_ORDER[:-1]:  # sem "done"
            lines.append(f"❌ {STAGE_LABELS.get(s, s)}")
        return header + "\n".join(lines) + "\n❌ Falhou"
    current_idx = STAGE_ORDER.index(stage) if stage in STAGE_ORDER else 0
    lines = []
    for i, s in enumerate(STAGE_ORDER[:-1]):  # sem "done"
        label = STAGE_LABELS[s]
        if i < current_idx:
            lines.append(f"✅ {label}")
        elif i == current_idx:
            lines.append(f"⏳ {label}...")
        else:
            lines.append(f"⬜ {label}")
    return header + "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers de API Telegram
# ---------------------------------------------------------------------------

def _api(method: str, **kwargs) -> dict:
    """Faz POST na API do Telegram. Retorna o dict de resposta ou {} em erro."""
    try:
        r = requests.post(f"{API}/{method}", timeout=30, **kwargs)
        return r.json()
    except Exception as e:
        print(f"erro {method}: {e}")
        return {}


def send(chat: str, text: str, parse_mode: str = "") -> dict:
    data: dict = {"chat_id": chat, "text": text[:4096]}
    if parse_mode:
        data["parse_mode"] = parse_mode
    return _api("sendMessage", data=data)


def send_with_inline(chat: str, text: str, buttons: list[list[dict]]) -> dict:
    """Envia mensagem com InlineKeyboardMarkup."""
    data = {
        "chat_id": chat,
        "text": text[:4096],
        "reply_markup": json.dumps({"inline_keyboard": buttons}),
    }
    return _api("sendMessage", data=data)


def edit_message(chat: str, message_id: int, text: str) -> dict:
    data = {
        "chat_id": chat,
        "message_id": message_id,
        "text": text[:4096],
    }
    return _api("editMessageText", data=data)


def edit_message_with_inline(chat: str, message_id: int, text: str, buttons: list[list[dict]]) -> dict:
    data = {
        "chat_id": chat,
        "message_id": message_id,
        "text": text[:4096],
        "reply_markup": json.dumps({"inline_keyboard": buttons}),
    }
    return _api("editMessageText", data=data)


def answer_callback(callback_query_id: str, text: str = "") -> None:
    _api("answerCallbackQuery", data={"callback_query_id": callback_query_id, "text": text[:200]})


def send_video(chat: str, path: Path, caption: str = "") -> None:
    try:
        with open(path, "rb") as f:
            requests.post(
                f"{API}/sendVideo",
                data={
                    "chat_id": chat,
                    "caption": caption[:1000],
                    "supports_streaming": "true",
                },
                files={"video": f},
                timeout=600,
            )
    except Exception as e:
        print(f"erro sendVideo: {e}")


def get_file(file_id: str) -> str | None:
    """Retorna o file_path do Telegram para um file_id, ou None em erro."""
    resp = _api("getFile", data={"file_id": file_id})
    return resp.get("result", {}).get("file_path")


def download_file(file_path: str, dest: Path) -> bool:
    """Baixa um arquivo da CDN do Telegram. Retorna True em sucesso."""
    url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
    try:
        r = requests.get(url, timeout=60, stream=True)
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in r.iter_content(65536):
                f.write(chunk)
        return True
    except Exception as e:
        print(f"erro download_file: {e}")
        return False


def _caption_from_metadata(topic: str, out_dir: Path | None = None) -> str:
    meta = (out_dir or OUT) / "metadata.json"
    if meta.exists():
        try:
            d = json.loads(meta.read_text(encoding="utf-8"))
            title = d.get("title") or d.get("youtube", {}).get("title") or topic
            tags = d.get("hashtags") or []
            return f"🎬 {title}\n" + " ".join(tags[:6])
        except Exception:
            pass
    return f"🎬 {topic}"


# ---------------------------------------------------------------------------
# Parsing de nicho/tema
# ---------------------------------------------------------------------------

def _parse_niche(rest: str) -> tuple[str | None, str]:
    """Se a 1ª palavra for um nicho conhecido, separa nicho do tema."""
    parts = rest.split(maxsplit=1)
    if not parts:
        return None, ""
    first = parts[0].lower()
    if first in NICHE_ALIASES:
        niche = NICHE_ALIASES[first]
        topic = parts[1] if len(parts) > 1 else ""
        return niche, topic
    return None, rest


# Chaves aceitas no formato estruturado (mensagem-modelo copiável)
_NICHE_KEYS = ("nicho", "niche")
_TOPIC_KEYS = ("ideia de video", "ideia de vídeo", "ideia", "tema", "video", "vídeo", "assunto")


def _parse_structured_request(text: str) -> tuple[str | None, str] | None:
    """
    Entende a mensagem-modelo (campos 'chave: valor'), ex.:
        nicho: crimes
        ideia de video: a história da Suzane von Richthofen

    Aceita variações de chave/ordem e bullets (•, -, *) no começo da linha.
    Retorna (niche, topic) se reconhecer o formato (pelo menos uma chave válida
    presente); caso contrário None (aí o handle trata como 'não entendi').
    """
    niche_raw: str | None = None
    topic: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("•-*").strip()
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower()
        val = val.strip().strip('"').strip()
        if key in _NICHE_KEYS:
            niche_raw = val
        elif key in _TOPIC_KEYS:
            topic = val
    if topic is None and niche_raw is None:
        return None  # não é o formato estruturado

    niche: str | None = None
    if niche_raw:
        k = niche_raw.lower().strip()
        niche = NICHE_ALIASES.get(k) or (k if k in set(NICHE_ALIASES.values()) else None)
    return niche, (topic or "")


# ---------------------------------------------------------------------------
# Referências de imagem (pasta pending)
# ---------------------------------------------------------------------------

def _refs_dir(topic: str | None = None) -> Path:
    slug = re.sub(r"[^\w-]", "_", (topic or "pending").lower())[:40]
    return OUT / "refs" / slug


def _count_refs(topic: str | None = None) -> int:
    d = _refs_dir(topic)
    if not d.exists():
        return 0
    return len([p for p in d.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}])


def _next_ref_index(topic: str | None = None) -> int:
    d = _refs_dir(topic)
    if not d.exists():
        return 1
    existing = sorted(
        int(p.stem) for p in d.iterdir()
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} and p.stem.isdigit()
    )
    return (existing[-1] + 1) if existing else 1


def save_ref_image(chat: str, file_id: str) -> None:
    """Baixa imagem do Telegram e salva em out/refs/pending/NN.jpg."""
    file_path = get_file(file_id)
    if not file_path:
        send(chat, "❌ Não consegui baixar a imagem.")
        return
    idx = _next_ref_index(None)
    dest = _refs_dir(None) / f"{idx:02d}.jpg"
    ok = download_file(file_path, dest)
    if ok:
        total = _count_refs(None)
        send(chat, f"📎 Referência salva ({total} no total). Ela será usada no próximo /gerar.")
    else:
        send(chat, "❌ Falha ao salvar a referência.")


def save_ref_from_url(chat: str, url: str) -> None:
    """Baixa imagem de URL e salva em out/refs/pending/NN.jpg."""
    idx = _next_ref_index(None)
    dest = _refs_dir(None) / f"{idx:02d}.jpg"
    try:
        r = requests.get(url, timeout=60, stream=True)
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in r.iter_content(65536):
                f.write(chunk)
        total = _count_refs(None)
        send(chat, f"📎 Referência salva ({total} no total). Ela será usada no próximo /gerar.")
    except Exception as e:
        send(chat, f"❌ Falha ao baixar imagem da URL: {e}")


def _resolve_ref_dir(topic: str | None) -> Path | None:
    """
    Retorna o REF_DIR a usar para o job:
      - Se há pasta refs/<slug_do_topic>, usa ela.
      - Senão, se há pasta refs/pending com imagens, renomeia para o slug e usa.
      - Senão, None.
    """
    topic_dir = _refs_dir(topic) if topic else None
    pending_dir = _refs_dir(None)  # out/refs/pending

    if topic_dir and topic_dir != pending_dir and topic_dir.exists() and _count_refs(topic) > 0:
        return topic_dir

    if pending_dir.exists() and _count_refs(None) > 0:
        if topic:
            new_dir = _refs_dir(topic)
            if new_dir != pending_dir:
                pending_dir.rename(new_dir)
                return new_dir
        return pending_dir

    return None


# ---------------------------------------------------------------------------
# Thread de leitura de stdout (atualização de progresso ao vivo)
# ---------------------------------------------------------------------------

def _stdout_reader(job_id: str, proc, chat: str, status_msg_id: int,
                   topic: str, niche: str | None) -> None:
    """
    Lê stdout do subprocess linha a linha em thread separada.
    Atualiza a mensagem de progresso no Telegram via editMessageText,
    com throttle de ~3s para não floodar.
    """
    last_text = ""
    last_edit_time = 0.0

    def _maybe_edit(text: str) -> None:
        nonlocal last_text, last_edit_time
        now = time.time()
        if text == last_text:
            return
        if now - last_edit_time < 3.0:
            return
        edit_message(chat, status_msg_id, text)
        last_text = text
        last_edit_time = now

    try:
        for raw_line in proc.stdout:
            line = raw_line.rstrip()
            if not line:
                continue

            with job_lock:
                if current_job is None or current_job["id"] != job_id:
                    break  # job foi cancelado
                if current_job.get("cancelled"):
                    break

            detected = _detect_stage(line)
            if detected:
                with job_lock:
                    if current_job and current_job["id"] == job_id:
                        current_stage = current_job.get("stage", "script")
                        # só avança (nunca volta)
                        if STAGE_ORDER.index(detected) >= STAGE_ORDER.index(current_stage):
                            current_job["stage"] = detected
                            current_stage = detected

                progress = _build_progress_text(topic, niche, current_stage)
                _maybe_edit(progress)
    except Exception as e:
        print(f"stdout_reader error: {e}")


# ---------------------------------------------------------------------------
# Botões inline de aprovação
# ---------------------------------------------------------------------------

def _script_approval_buttons(job_id: str) -> list[list[dict]]:
    return [
        [
            {"text": "✅ Aprovar e gerar", "callback_data": f"appr:{job_id}"},
            {"text": "🔄 Refazer roteiro", "callback_data": f"redo:{job_id}"},
        ],
        [
            {"text": "❌ Cancelar", "callback_data": f"cxl:{job_id}"},
        ],
    ]


def _video_approval_buttons(job_id: str) -> list[list[dict]]:
    return [
        [
            {"text": "👍 Publicar",  "callback_data": f"pub:{job_id}"},
            {"text": "🗑 Descartar", "callback_data": f"del:{job_id}"},
        ],
    ]


def _format_script_for_chat(script: dict) -> str:
    """Formata o roteiro JSON como texto legível para o chat."""
    lines = []
    lines.append(f"*{script.get('title', '(sem título)')}*")
    lines.append("")
    lines.append(f"🪝 *Hook:* {script.get('hook', '')}")
    lines.append("")
    for i, line in enumerate(script.get("lines", []), 1):
        lines.append(f"{i}. {line['text']}")
    lines.append("")
    lines.append(f"📢 *CTA:* {script.get('cta', '')}")
    hashtags = " ".join(script.get("hashtags", []))
    if hashtags:
        lines.append(f"\n{hashtags}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Runners de job (executam em threads)
# ---------------------------------------------------------------------------

def _run_phase_a(job_id: str, topic: str, niche: str | None, chat: str,
                 status_msg_id: int, ref_dir: Path | None) -> None:
    """
    Fase A: roda --script-only, lê o roteiro gerado e manda para aprovação.
    Ao terminar, a thread encerra; o job fica em phase="A_waiting" aguardando callback.
    """
    import subprocess

    env = os.environ.copy()
    if niche:
        env["CANAL_DARK_NICHE"] = niche
    if ref_dir:
        env["REF_DIR"] = str(ref_dir)

    out_dir = OUT
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [PYTHON, str(SCRIPT), "--script-only", "--topic", topic, "--out-dir", str(out_dir)]

    with job_lock:
        if current_job is None or current_job["id"] != job_id:
            return
        if current_job.get("cancelled"):
            return

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        with job_lock:
            if current_job and current_job["id"] == job_id:
                current_job["proc"] = proc

        # Lê stdout ao vivo (fase A é rápida mas ainda progride no checklist)
        reader = threading.Thread(
            target=_stdout_reader,
            args=(job_id, proc, chat, status_msg_id, topic, niche),
            daemon=True,
        )
        reader.start()

        proc.wait()
        reader.join(timeout=5)

        with job_lock:
            if current_job is None or current_job["id"] != job_id:
                return
            if current_job.get("cancelled"):
                _clear_job()
                return

        stderr_tail = (proc.stderr.read() or "")[-800:]

        if proc.returncode != 0:
            with job_lock:
                if current_job and current_job["id"] == job_id:
                    current_job["stage"] = "failed"
                    _clear_job()
            progress = _build_progress_text(topic, niche, "failed")
            edit_message(chat, status_msg_id, progress)
            send(chat, f"❌ Falha ao gerar roteiro. Fim do log:\n{stderr_tail}")
            return

        # Lê o script_draft.json
        draft_path = out_dir / "script_draft.json"
        if not draft_path.exists():
            with job_lock:
                _clear_job()
            edit_message(chat, status_msg_id, _build_progress_text(topic, niche, "failed"))
            send(chat, "❌ script_draft.json não encontrado após geração.")
            return

        try:
            script = json.loads(draft_path.read_text(encoding="utf-8"))
        except Exception as e:
            with job_lock:
                _clear_job()
            edit_message(chat, status_msg_id, _build_progress_text(topic, niche, "failed"))
            send(chat, f"❌ Erro ao ler script_draft.json: {e}")
            return

        with job_lock:
            if current_job and current_job["id"] == job_id:
                current_job["phase"] = "A_waiting"
                current_job["script_path"] = draft_path
                current_job["stage"] = "script"
                current_job["proc"] = None

        # Edita a mensagem de progresso para indicar que está aguardando aprovação
        edit_message(chat, status_msg_id, _build_progress_text(topic, niche, "script")
                     + "\n\n_Aguardando aprovação do roteiro..._")

        # Envia o roteiro formatado com botões de aprovação
        script_text = _format_script_for_chat(script)
        send_with_inline(chat, script_text, _script_approval_buttons(job_id))

    except Exception as e:
        with job_lock:
            _clear_job()
        send(chat, f"❌ Erro inesperado na fase A: {e}")


def _run_phase_b(job_id: str, topic: str, niche: str | None, chat: str,
                 status_msg_id: int, script_path: Path, ref_dir: Path | None) -> None:
    """
    Fase B: monta o vídeo a partir do roteiro aprovado (--script-file).
    """
    import subprocess

    env = os.environ.copy()
    if niche:
        env["CANAL_DARK_NICHE"] = niche
    if ref_dir:
        env["REF_DIR"] = str(ref_dir)

    out_dir = OUT

    cmd = [
        PYTHON, str(SCRIPT),
        "--script-file", str(script_path),
        "--out-dir", str(out_dir),
    ]

    with job_lock:
        if current_job is None or current_job["id"] != job_id:
            return
        if current_job.get("cancelled"):
            return
        current_job["phase"] = "B"
        current_job["stage"] = "voice"

    # Atualiza progress para mostrar que avançou além do roteiro
    edit_message(chat, status_msg_id, _build_progress_text(topic, niche, "voice"))

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        with job_lock:
            if current_job and current_job["id"] == job_id:
                current_job["proc"] = proc

        reader = threading.Thread(
            target=_stdout_reader,
            args=(job_id, proc, chat, status_msg_id, topic, niche),
            daemon=True,
        )
        reader.start()

        proc.wait()
        reader.join(timeout=5)

        with job_lock:
            if current_job is None or current_job["id"] != job_id:
                return
            if current_job.get("cancelled"):
                _clear_job()
                return

        stderr_tail = (proc.stderr.read() or "")[-1200:]
        mp4 = out_dir / "short.mp4"

        if proc.returncode != 0 or not mp4.exists():
            with job_lock:
                _clear_job()
            edit_message(chat, status_msg_id, _build_progress_text(topic, niche, "failed"))
            send(chat, f"❌ Falha na geração do vídeo. Fim do log:\n{stderr_tail}")
            return

        # Atualiza progresso para "pronto"
        edit_message(chat, status_msg_id, _build_progress_text(topic, niche, "done"))

        with job_lock:
            if current_job and current_job["id"] == job_id:
                current_job["phase"] = "C_waiting"
                current_job["stage"] = "done"
                current_job["proc"] = None

        # Fase C: envia o vídeo + botões de publicação/descarte
        caption = _caption_from_metadata(topic, out_dir)
        send(chat, "✅ Vídeo pronto! Enviando...")
        send_video(chat, mp4, caption=caption)
        send_with_inline(
            chat,
            "O que quer fazer com este short?",
            _video_approval_buttons(job_id),
        )

    except Exception as e:
        with job_lock:
            _clear_job()
        send(chat, f"❌ Erro inesperado na fase B: {e}")


# ---------------------------------------------------------------------------
# Gerenciamento de estado
# ---------------------------------------------------------------------------

def _clear_job() -> None:
    """Limpa current_job. DEVE ser chamado com job_lock adquirido."""
    global current_job
    current_job = None


def _start_job(topic: str, niche: str | None, chat: str, ref_dir: Path | None) -> None:
    """
    Cria um novo job e dispara a fase A em thread.
    DEVE ser chamado com job_lock adquirido (por isso não adquire ele aqui).
    """
    global current_job

    job_id = str(uuid.uuid4())[:8]
    current_job = {
        "id": job_id,
        "topic": topic,
        "niche": niche,
        "stage": "script",
        "phase": "A",
        "proc": None,
        "cancelled": False,
        "chat": chat,
        "status_msg_id": None,
        "script_path": None,
        "ref_dir": ref_dir,
        "started_at": time.time(),
    }

    # Envia a mensagem inicial de progresso
    resp = send(chat, _build_progress_text(topic, niche, "script"))
    msg_id = resp.get("result", {}).get("message_id")
    if msg_id:
        current_job["status_msg_id"] = msg_id
    else:
        # Fallback: manda outro send e ignora edição ao vivo
        send(chat, "⏳ Iniciando geração (sem mensagem de progresso ao vivo).")

    thread = threading.Thread(
        target=_run_phase_a,
        args=(job_id, topic, niche, chat, current_job["status_msg_id"], ref_dir),
        daemon=True,
    )
    thread.start()


# ---------------------------------------------------------------------------
# Handlers de comandos
# ---------------------------------------------------------------------------

def _begin_generation(chat: str, niche: str | None, topic: str) -> None:
    """Inicia o fluxo de geração (1 job por vez, refs, confirmação).
    Compartilhado por /gerar, /roteiro e o formato estruturado (modelo)."""
    topic = (topic or "").strip()
    if not topic:
        send(chat, "Faltou a *ideia de vídeo*. Use /novo para pegar o modelo pronto.", parse_mode="Markdown")
        return

    with job_lock:
        if current_job is not None:
            send(
                chat,
                "Já tem um job rodando. Use /status para ver o progresso ou /cancel para cancelar.",
            )
            return

        ref_dir = _resolve_ref_dir(topic)
        _start_job(topic, niche, chat, ref_dir)

    ref_note = f"\n📎 {_count_refs(topic)} referência(s) de imagem carregada(s)." if ref_dir else ""
    send(
        chat,
        f"⚙️ Gerando roteiro para: \"{topic}\" [{niche or 'sem nicho'}]"
        f"{ref_note}\nVou te mostrar o roteiro para aprovação antes de gerar o vídeo.",
    )


def handle_gerar_or_roteiro(chat: str, text: str) -> None:
    """Trata /gerar e /roteiro — ambos disparam o fluxo de checkpoint."""
    # Decide qual prefixo foi usado
    if text.startswith("/gerar"):
        rest = text[len("/gerar"):].strip()
    else:
        rest = text[len("/roteiro"):].strip()

    if not rest:
        handle_novo(chat)  # sem argumentos → manda o modelo copiável
        return

    niche, topic = _parse_niche(rest)
    _begin_generation(chat, niche, topic)


def handle_status(chat: str) -> None:
    with job_lock:
        job = current_job

    if job is None:
        send(chat, "Nenhum job rodando no momento.")
        return

    elapsed = int(time.time() - job.get("started_at", time.time()))
    m, s = divmod(elapsed, 60)
    phase_label = {
        "A": "Gerando roteiro",
        "A_waiting": "Aguardando aprovação do roteiro",
        "B": "Gerando vídeo",
        "C_waiting": "Aguardando decisão de publicação",
    }.get(job.get("phase", ""), job.get("phase", "?"))

    send(
        chat,
        f"Job atual:\n"
        f"  Tema: {job['topic']}\n"
        f"  Nicho: {job.get('niche') or 'sem nicho'}\n"
        f"  Fase: {phase_label}\n"
        f"  Etapa: {job.get('stage', '?')}\n"
        f"  Tempo: {m}m {s}s",
    )


def handle_cancel(chat: str) -> None:
    global current_job

    with job_lock:
        job = current_job
        if job is None:
            send(chat, "Nenhum job para cancelar.")
            return

        job["cancelled"] = True
        proc = job.get("proc")
        msg_id = job.get("status_msg_id")
        topic = job.get("topic", "")
        niche = job.get("niche")
        _clear_job()

    if proc is not None:
        try:
            proc.terminate()
            time.sleep(1)
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass

    if msg_id:
        edit_message(chat, msg_id, _build_progress_text(topic, niche, "failed") + "\n\n🚫 Cancelado.")

    send(chat, "🚫 Job cancelado.")


def handle_refs(chat: str, args: str) -> None:
    args = args.strip().lower()
    if args == "limpar":
        pending = _refs_dir(None)
        if pending.exists():
            import shutil
            shutil.rmtree(pending, ignore_errors=True)
            send(chat, "🗑 Referências pendentes apagadas.")
        else:
            send(chat, "Nenhuma referência pendente para apagar.")
    else:
        count = _count_refs(None)
        if count == 0:
            send(chat, "Nenhuma referência pendente. Mande uma foto ou link de imagem para adicionar.")
        else:
            send(chat, f"📎 {count} referência(s) pendente(s) para o próximo /gerar.\nUse /refs limpar para remover.")


def handle_novo(chat: str) -> None:
    """Manda a mensagem-modelo copiável para um novo pedido de vídeo."""
    send(
        chat,
        "📋 *Novo vídeo* — copie o bloco, preencha a ideia e me mande de volta:\n\n"
        "```\n"
        "nicho: crimes\n"
        "ideia de video: \n"
        "```\n"
        "Nichos: *crimes* · *misterios* · *onepiece*  (use /nichos p/ detalhes)\n"
        "📎 Dica: mande uma foto ou link de imagem *antes* — entra como referência no vídeo.",
        parse_mode="Markdown",
    )


def handle_nichos(chat: str) -> None:
    send(
        chat,
        "Nichos disponíveis:\n\n"
        "• crimes — True crime: casos reais, mistérios policiais, crimes históricos\n"
        "• misterios — Conspiracy theories: teorias, mistérios inexplicados, conspirações\n"
        "• onepiece — One Piece: teorias, histórias e análises do universo One Piece\n\n"
        "Uso: /gerar crimes the Somerton Man",
    )


def handle_ajuda(chat: str) -> None:
    send(
        chat,
        "🤖 *Canal Dark Bot* — comandos:\n\n"
        "/novo — modelo pronto pra copiar e pedir um vídeo\n"
        "/gerar <nicho> <tema> — pedido em uma linha (atalho)\n"
        "/status — job em andamento (fase, etapa, tempo)\n"
        "/cancel — cancela o job atual\n"
        "/refs — referências de imagem pendentes (/refs limpar p/ apagar)\n"
        "/nichos — lista os nichos\n"
        "/ajuda — esta ajuda\n\n"
        "*Jeito mais fácil — mande assim (ou use /novo):*\n"
        "```\n"
        "nicho: crimes\n"
        "ideia de video: o caso do Homem de Somerton\n"
        "```\n"
        "*Fluxo:* peço o roteiro → você aprova ✅ (ou 🔄 refazer / ❌ cancela) → "
        "gero o vídeo → você publica 👍 ou descarta 🗑.\n"
        "📎 *Referências:* mande foto/link de imagem antes do pedido para forçar no b-roll.",
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# Handler de callback_query (botões inline)
# ---------------------------------------------------------------------------

def handle_callback(callback_query: dict) -> None:
    """
    Trata cliques nos botões inline: appr:, redo:, cxl:, pub:, del:.
    Sempre responde answerCallbackQuery para parar o "girando" no botão.
    """
    global current_job

    cq_id = callback_query.get("id", "")
    data = callback_query.get("data", "")
    chat = str(callback_query.get("from", {}).get("id", ""))
    message = callback_query.get("message") or {}
    message_id = message.get("message_id")

    if not data:
        answer_callback(cq_id)
        return

    # Ignora callbacks de chats que não são o dono
    if chat != OWNER:
        answer_callback(cq_id, "Bot privado.")
        return

    # Extrai ação e job_id do callback_data (formato "acao:jobid")
    parts = data.split(":", 1)
    if len(parts) != 2:
        answer_callback(cq_id, "Dado inválido.")
        return

    action, cb_job_id = parts

    # Verifica se o job ainda é o correto
    with job_lock:
        job = current_job
        if job is None or job["id"] != cb_job_id:
            answer_callback(cq_id, "Job expirado ou cancelado.")
            if message_id:
                edit_message_with_inline(chat, message_id, message.get("text", "") + "\n\n_(expirado)_", [])
            return

    # --- APROVAR ---
    if action == "appr":
        answer_callback(cq_id, "Aprovado! Iniciando geração...")
        if message_id:
            edit_message_with_inline(chat, message_id,
                                     message.get("text", "") + "\n\n✅ *Roteiro aprovado.*", [])

        with job_lock:
            if current_job is None or current_job["id"] != cb_job_id:
                return
            job = current_job
            script_path = job.get("script_path")
            topic = job["topic"]
            niche = job.get("niche")
            ref_dir = job.get("ref_dir")
            status_msg_id = job.get("status_msg_id")

        if not script_path or not script_path.exists():
            send(chat, "❌ Roteiro não encontrado para aprovação.")
            with job_lock:
                _clear_job()
            return

        thread = threading.Thread(
            target=_run_phase_b,
            args=(cb_job_id, topic, niche, chat, status_msg_id, script_path, ref_dir),
            daemon=True,
        )
        thread.start()
        send(chat, "⚙️ Gerando vídeo a partir do roteiro aprovado...")

    # --- REFAZER ---
    elif action == "redo":
        answer_callback(cq_id, "Refazendo roteiro...")
        if message_id:
            edit_message_with_inline(chat, message_id,
                                     message.get("text", "") + "\n\n🔄 *Refeito — aguarde novo roteiro.*", [])

        with job_lock:
            if current_job is None or current_job["id"] != cb_job_id:
                return
            job = current_job
            topic = job["topic"]
            niche = job.get("niche")
            ref_dir = job.get("ref_dir")
            status_msg_id = job.get("status_msg_id")
            # Reseta para fase A novamente com o mesmo job_id
            current_job["phase"] = "A"
            current_job["stage"] = "script"
            current_job["proc"] = None
            current_job["script_path"] = None

        if status_msg_id:
            edit_message(chat, status_msg_id, _build_progress_text(topic, niche, "script"))

        thread = threading.Thread(
            target=_run_phase_a,
            args=(cb_job_id, topic, niche, chat, status_msg_id, ref_dir),
            daemon=True,
        )
        thread.start()

    # --- CANCELAR (fase A) ---
    elif action == "cxl":
        answer_callback(cq_id, "Cancelado.")
        if message_id:
            edit_message_with_inline(chat, message_id,
                                     message.get("text", "") + "\n\n❌ *Cancelado.*", [])
        with job_lock:
            job = current_job
            if job and job["id"] == cb_job_id:
                job["cancelled"] = True
                proc = job.get("proc")
                status_msg_id = job.get("status_msg_id")
                topic = job["topic"]
                niche = job.get("niche")
                _clear_job()

        if job and job.get("proc"):
            try:
                job["proc"].terminate()
            except Exception:
                pass
        send(chat, "🚫 Job cancelado.")

    # --- PUBLICAR ---
    elif action == "pub":
        answer_callback(cq_id, "Marcado para publicar!")
        if message_id:
            edit_message_with_inline(
                chat, message_id,
                # TODO: quando integrar o Postiz, substituir este texto pelo trigger
                # de publicação. O metadata.json em out/metadata.json já contém
                # título, descrição e hashtags formatados para cada plataforma.
                # Plugar aqui: chamar a API do Postiz ou n8n webhook com o path do mp4.
                "📬 Marcado p/ publicar (Postiz ainda não conectado).\n"
                "O short.mp4 está em: " + str(OUT / "short.mp4"),
                [],
            )
        with job_lock:
            _clear_job()

    # --- DESCARTAR ---
    elif action == "del":
        answer_callback(cq_id, "Descartado.")
        mp4 = OUT / "short.mp4"
        deleted = False
        if mp4.exists():
            try:
                mp4.unlink()
                deleted = True
            except Exception as e:
                send(chat, f"❌ Erro ao apagar short.mp4: {e}")
        if message_id:
            edit_message_with_inline(
                chat, message_id,
                "🗑 Short descartado." + (" (arquivo apagado)" if deleted else " (arquivo não encontrado)"),
                [],
            )
        with job_lock:
            _clear_job()

    else:
        answer_callback(cq_id, "Ação desconhecida.")


# ---------------------------------------------------------------------------
# Handler de mensagens de texto/mídia
# ---------------------------------------------------------------------------

def handle(chat: str, update_msg: dict) -> None:
    """Trata uma mensagem recebida do dono (texto, foto ou documento de imagem)."""
    text = (update_msg.get("text") or "").strip()
    photo = update_msg.get("photo")
    document = update_msg.get("document")

    # --- Foto enviada diretamente ---
    if photo:
        # photo é lista ordenada por tamanho; usa o maior
        best = max(photo, key=lambda p: p.get("file_size", 0))
        save_ref_image(chat, best["file_id"])
        return

    # --- Documento com mime type de imagem ---
    if document:
        mime = document.get("mime_type", "")
        if mime.startswith("image/"):
            save_ref_image(chat, document["file_id"])
            return

    if not text:
        return

    # --- Link de imagem no texto ---
    if re.match(r"https?://\S+\.(jpg|jpeg|png|webp)(\?.*)?$", text, re.IGNORECASE):
        save_ref_from_url(chat, text)
        return

    # --- Comandos ---
    if text.startswith("/gerar") or text.startswith("/roteiro"):
        handle_gerar_or_roteiro(chat, text)

    elif text.startswith("/novo") or text.startswith("/modelo") or text.startswith("/template"):
        handle_novo(chat)

    elif text.startswith("/status"):
        handle_status(chat)

    elif text.startswith("/cancel"):
        handle_cancel(chat)

    elif text.startswith("/refs"):
        args = text[len("/refs"):].strip()
        handle_refs(chat, args)

    elif text.startswith("/nichos"):
        handle_nichos(chat)

    elif text.startswith("/ajuda") or text.startswith("/help") or text.startswith("/start"):
        handle_ajuda(chat)

    else:
        # Formato estruturado (mensagem-modelo): "nicho:" / "ideia de video:"
        parsed = _parse_structured_request(text)
        if parsed is not None:
            niche, topic = parsed
            _begin_generation(chat, niche, topic)
        else:
            send(
                chat,
                "Não entendi. Use /novo para pegar o modelo de pedido.\n"
                "Comandos: /novo · /gerar · /status · /cancel · /refs · /nichos · /ajuda",
            )


# ---------------------------------------------------------------------------
# Loop principal de polling
# ---------------------------------------------------------------------------

def _set_bot_commands() -> None:
    """Registra o menu de comandos (aparece no botão '/' do Telegram)."""
    commands = [
        {"command": "novo", "description": "Modelo pronto pra pedir um vídeo"},
        {"command": "gerar", "description": "Pedido em uma linha: <nicho> <tema>"},
        {"command": "status", "description": "Job em andamento (fase, tempo)"},
        {"command": "cancel", "description": "Cancela o job atual"},
        {"command": "refs", "description": "Referências de imagem pendentes"},
        {"command": "nichos", "description": "Lista os nichos"},
        {"command": "ajuda", "description": "Ajuda e fluxo"},
    ]
    _api("setMyCommands", data={"commands": json.dumps(commands)})


def main() -> None:
    if not TOKEN or not OWNER:
        print("ERRO: TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID precisam estar no .env")
        sys.exit(1)

    _set_bot_commands()
    send(
        OWNER,
        "🤖 Canal Dark Bot ONLINE.\n"
        "Manda /novo pra pegar o modelo de pedido (nicho + ideia de vídeo).\n"
        "/ajuda para ver todos os comandos.",
    )
    print("Bot ouvindo... (Ctrl+C pra parar)")

    offset = None
    net_fails = 0

    while True:
        try:
            params: dict = {"timeout": 50, "allowed_updates": ["message", "callback_query"]}
            if offset is not None:
                params["offset"] = offset

            resp = requests.get(f"{API}/getUpdates", params=params, timeout=60).json()
            net_fails = 0  # reset ao conseguir falar com o Telegram

            for upd in resp.get("result", []):
                offset = upd["update_id"] + 1

                # --- callback_query (clique em botão inline) ---
                cq = upd.get("callback_query")
                if cq:
                    handle_callback(cq)
                    continue

                # --- mensagem de texto / foto / documento ---
                msg = upd.get("message") or upd.get("edited_message") or {}
                if not msg:
                    continue

                chat = str(msg.get("chat", {}).get("id", ""))
                if not chat:
                    continue

                if chat != OWNER:
                    send(chat, "🔒 Este bot é privado.")
                    continue

                handle(chat, msg)

        except Exception as e:
            # Erro de rede: espera progressiva (5s→10s→...→máx 60s).
            # Só loga 1x por série de falhas consecutivas (a cada 10).
            net_fails += 1
            wait = min(60, 5 * net_fails)
            if net_fails == 1 or net_fails % 10 == 0:
                print(f"sem rede (falha #{net_fails}), aguardando {wait}s: {str(e)[:120]}")
            time.sleep(wait)


if __name__ == "__main__":
    main()
