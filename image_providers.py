"""
image_providers.py — Provedores de imagens livres/geradas para o Canal Dark
============================================================================
Busca imagens de domínio público, CC0 ou geradas por IA a partir de múltiplas
fontes, classifica por lane de copyright e salva com sidecar de atribuição.

Lanes:
  burn     — só aceita licenças ∈ BURN_ALLOWLIST (PD, CC0, CC-BY, CC-BY-SA)
  generate — imagens geradas por IA (sem restrição de copyright, uso livre)
  anime    — fandom/civitai (IP protegida; exige ALLOW_ANIME=1 + revisão humana)
  ref      — modo referência: baixa para out/img_ref/, NÃO entra no vídeo

Variáveis de ambiente:
  IMG_PROVIDERS          — CSV de providers default (ex: "wikimedia,openverse")
  IMG_PROVIDERS_BURN     — CSV de providers para lane burn
  IMG_PROVIDERS_GENERATE — CSV de providers para lane generate
  IMG_PROVIDERS_ANIME    — CSV de providers para lane anime
  IMG_PROVIDERS_REF      — CSV de providers para lane ref
  IMG_LANE               — lane default (default: "burn")
  ALLOW_ANIME            — "1" para habilitar lane anime
  IMG_CACHE_DIR          — pasta de cache (default: <cwd>/out/img_cache)
  AIHORDE_API_KEY        — chave AI Horde (default: "0000000000" = fila pública anônima)
  AIHORDE_WIDTH/HEIGHT   — resolução AI Horde (default 512x768; enxuto p/ caber no free-tier)
  AIHORDE_STEPS          — passos AI Horde (default 12)
  AIHORDE_UPSCALE        — "1" liga RealESRGAN_x4plus (opt-in; encarece → 403 no grátis)
  AIHORDE_MODELS         — CSV de modelos AI Horde (default "stable_diffusion")
  PEXELS_API_KEY         — chave Pexels (provider 'pexels_photo': foto stock real grátis)
  IMG_HTTP_TIMEOUT       — timeout HTTP geral em segundos (default: 30)

Providers de imagem REAL grátis (lane burn / stock):
  wikimedia, openverse, internetarchive — CC/PD (passam pelo gate de licença)
  pexels_photo                          — stock free-to-use (NÃO-CC; fora do gate, opt-in)
Geradores de IA (lane generate, vivo-primeiro 2026-06-08):
  cloudflare (vivo) → aihorde (vivo, lento) → pollinations (402) → imagerouter (403)
"""

import hashlib
import inspect
import json
import logging
import os
import re
import time
import urllib.parse
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("short_factory")

# ---------------------------------------------------------------------------
# Constantes de licença
# ---------------------------------------------------------------------------

BURN_ALLOWLIST = {
    "public domain",
    "public domain mark",
    "pdm",
    "no restrictions",
    "cc0",
    "cc0 1.0",
    "cc-by 4.0",
    "cc-by-sa 4.0",
    "cc-by-sa 3.0",
    "cc-by-sa 3.0 de",
    "cc-by 2.0",
    "cc-by-sa 2.0",
}

# Providers de licença LIVRE confirmada (PD/CC) que passam pelo gate de licença burn.
# pexels_photo é stock free-to-use NÃO-CC e fica DE FORA: lanes sensíveis a IDENTIDADE
# (ex.: futebol player_real, que precisa da foto livre do jogador NOMEADO) devem usar só
# esta lista — pexels_photo preencheria o slot com o rosto de um TERCEIRO qualquer.
BURN_CC_PROVIDERS = ["wikimedia", "openverse", "internetarchive"]

# Extensões de imagem aceitas (excluir SVG, TIFF, PDF, audio, video etc.)
_IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# Timeout padrão para HTTP geral; AI generators usam 120s
_HTTP_TIMEOUT = int(os.environ.get("IMG_HTTP_TIMEOUT", "30"))
_GEN_TIMEOUT = 120

# User-Agent é OBRIGATÓRIO na API da Wikimedia (sem ele → HTTP 403) e é boa
# prática nas demais. Política da Wikimedia pede um contato identificável.
_HEADERS = {
    "User-Agent": "CanalDark/1.0 (Shorts pipeline; contato: canal-dark local) python-requests",
}

# ---------------------------------------------------------------------------
# AI Horde — throttle global de submissão (BUG 2)
# ---------------------------------------------------------------------------
# A fila ANÔNIMA do AI Horde (apikey "0000000000") limita a 2 submissões por
# segundo POR CHAVE. O controlador One Piece dispara vários shots em PARALELO
# (ThreadPoolExecutor) → estoura o limite e a API responde 429 "2 per 1 second",
# derrubando a geração. Para não serializar o pipeline inteiro, serializamos SÓ
# o momento da submissão ao Horde: um lock global garante o espaçamento mínimo
# entre dois POST /generate/async. O poll de status (que é por-geração) segue
# paralelo normalmente — só a porta de ENTRADA é estreitada.
import threading

_AIHORDE_SUBMIT_LOCK = threading.Lock()
_AIHORDE_LAST_SUBMIT = [0.0]  # mutável p/ compartilhar entre threads (list = ref)
# Espaçamento mínimo entre submissões (s). 0.6 → ~1.6 req/s, com folga sob o teto
# de 2/s mesmo com jitter de rede. Tunável por env p/ chaves com prioridade maior.
_AIHORDE_MIN_SUBMIT_GAP = float(os.environ.get("AIHORDE_MIN_SUBMIT_GAP", "0.6"))
# Tempo máximo (s) que o poll de status do AI Horde espera a geração concluir antes
# de desistir e cair pro próximo gerador. A fila ANÔNIMA é compartilhada e pode demorar:
# 180s (default antigo) estourava em horário de pico e jogava tudo pro Cloudflare. 300s
# dá mais fôlego sem travar a fábrica. Tunável por env AIHORDE_POLL_TIMEOUT.
_AIHORDE_POLL_TIMEOUT = float(os.environ.get("AIHORDE_POLL_TIMEOUT", "300"))

# ---------------------------------------------------------------------------
# Cloudflare Workers AI — throttle global de submissão (anti-preto)
# ---------------------------------------------------------------------------
# A conta grátis do Cloudflare (10.000 neurons/dia) tem rate-limit por janela curta: sob
# rajada de submissões paralelas (One Piece dispara vários shots ao mesmo tempo) ela devolve
# HTTP 429, e o caminho atual caía direto pra IA seguinte/preto sem retry. Mesmo padrão do
# AI Horde: serializamos SÓ o momento do POST (espaçamento mínimo entre submissões) + 1 retry
# no 429 transitório. O gate é COMPARTILHADO entre _prov_cloudflare (lane generate) e
# short_factory._fetch_cloudflare (caminho do One Piece) — uma casa só pra constante, pra o
# throttle valer nos DOIS caminhos. Se a cota DIÁRIA estourou (429 persistente), o 1 retry
# falha e ele cai rápido pro próximo gerador (não trava).
_CLOUDFLARE_SUBMIT_LOCK = threading.Lock()
_CLOUDFLARE_LAST_SUBMIT = [0.0]  # mutável p/ compartilhar entre threads (list = ref)
_CF_MIN_SUBMIT_GAP = float(os.environ.get("CF_MIN_SUBMIT_GAP", "1.5"))


# ---------------------------------------------------------------------------
# ImageResult
# ---------------------------------------------------------------------------

class ImageResult:
    """
    Representa uma imagem candidata retornada por um provider.

    Campos:
      url                 — URL direto para download da imagem
      license             — string de licença normalizada (ex: "cc0 1.0")
      attribution         — texto de atribuição (vazio em PD/CC0, obrigatório em CC-BY*)
      source_provider     — nome do provider (ex: "wikimedia")
      source_url          — URL da página de origem (para créditos)
      needs_human_approval — True quando a lane exige revisão manual (anime/UNKNOWN-IP)
      _bytes              — conteúdo binário (opcional, None = baixar depois)
    """

    __slots__ = (
        "url", "license", "attribution", "source_provider",
        "source_url", "needs_human_approval", "_bytes",
    )

    def __init__(
        self,
        url: str,
        license: str,
        attribution: str,
        source_provider: str,
        source_url: str = "",
        needs_human_approval: bool = False,
        bytes_data: Optional[bytes] = None,
    ):
        self.url = url
        self.license = license.strip().lower()
        self.attribution = attribution.strip()
        self.source_provider = source_provider
        self.source_url = source_url
        self.needs_human_approval = needs_human_approval
        self._bytes = bytes_data


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _strip_html(text: str) -> str:
    """Remove tags HTML de uma string (usado em atribuições do Wikimedia)."""
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _cache_dir() -> Path:
    """Retorna (e cria) o diretório de cache de imagens."""
    env_dir = os.environ.get("IMG_CACHE_DIR", "").strip()
    if env_dir:
        d = Path(env_dir)
    else:
        d = Path.cwd() / "out" / "img_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _license_is_burn_safe(license_str: str) -> bool:
    """
    Decide se a licença é segura pra QUEIMAR no vídeo (domínio público/CC livre).
    Normaliza variações: 'cc by-sa 4.0' / 'cc-by-sa 4.0' / 'CC BY-SA 4.0' → iguais;
    'no restrictions', 'pd', 'pd-us' → domínio público. Rejeita NC e ND sempre.
    """
    s = (license_str or "").strip().lower()
    if not s:
        return False
    # rejeitar non-commercial / no-derivatives explicitamente
    if "nc" in s.split() or "-nc" in s or "noncommercial" in s or "nd" in s.split() or "-nd" in s:
        return False
    # domínio público em várias formas
    if any(k in s for k in ("public domain", "no restrictions", "pd-us", "pdm")) or s in ("pd", "cc0", "cc0 1.0"):
        return True
    # normaliza "cc by" / "cc-by" / "ccby" → tokens
    norm = s.replace("-", " ").replace("_", " ")
    norm = " ".join(norm.split())
    # aceita CC0, CC-BY*, CC-BY-SA* (sem NC/ND, já filtrado acima)
    if norm.startswith("cc0"):
        return True
    if norm.startswith("cc by"):  # cobre 'cc by 4.0', 'cc by sa 4.0', etc.
        return True
    return False


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


# Providers que GERAM a imagem a partir do prompt (não buscam foto pronta). Só nestes o
# prompt/style/avoid muda a imagem — então só nestes a cache key precisa de prompt_sig.
# Fontes de FOTO REAL (wikimedia/openverse/internetarchive/fandom/civitai/pexels_photo)
# ignoram o prompt → mantêm cache estável (mesma query = mesma foto, cache hit preservado).
_GENERATOR_PROVIDERS = frozenset({"cloudflare", "nvidia", "aihorde", "together",
                                  "pollinations", "imagerouter"})


def _prompt_signature(query: str, style: Optional[dict]) -> str:
    """
    Assinatura curta do que afeta a imagem GERADA: o prompt final (que já embute query +
    visual_context) MAIS os avoid_terms. Bug 2 / causa-raiz 1: sem isto, mudar o prompt de
    geração NÃO invalidava o cache — re-renders serviam a imagem velha (frame com 'bola no
    lugar da cabeça' voltava do cache mesmo com o fix do prompt aplicado).
    """
    avoid = [str(t).strip() for t in ((style or {}).get("avoid") or []) if str(t).strip()]
    raw = _build_gen_prompt(query, style) + "|avoid=" + ",".join(sorted(avoid))
    return hashlib.sha1(raw.encode()).hexdigest()[:10]


def _cache_key(provider: str, query: str, lane: str, index: int,
               prompt_sig: str = "") -> str:
    # prompt_sig só vem preenchido para providers geradores (ver _GENERATOR_PROVIDERS):
    # pra eles a imagem depende do prompt, então a assinatura entra na key e re-renders com
    # prompt diferente NÃO colidem com o cache antigo. Pra fontes de foto real fica vazio →
    # key idêntica à de antes (cache hit estável, sem invalidação desnecessária).
    raw = f"{provider}|{query}|{lane}|{index}|{prompt_sig}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _build_gen_prompt(query: str, style: Optional[dict]) -> str:
    """
    Monta o prompt de GERAÇÃO de imagem combinando a query da cena com a 'bíblia
    visual' (visual_context) do roteiro. É o que dá COESÃO: todo shot do vídeo
    compartilha paleta/mood/era → parece dirigido, não stock genérico.
    """
    style = style or {}
    bits = [query.strip()]
    for k in ("palette", "mood", "era", "style"):
        v = str(style.get(k) or "").strip()
        if v and v.lower() not in (query or "").lower():
            bits.append(v)
    bits += ["cinematic", "dramatic volumetric lighting", "highly detailed",
             "vertical 9:16 composition", "no text", "no watermark"]
    prompt = ", ".join(b for b in bits if b)
    # COERÊNCIA (Bug 2): o visual_context do roteiro traz avoid_terms (tópicos que NUNCA
    # podem aparecer — ex.: futebol "modern stadium", "skyline"). Os geradores FLUX honram
    # mal um negative separado, mas respeitam bem uma cláusula "avoid:" no fim do prompt
    # positivo. Anexamos os avoid_terms aqui pra a cena gerada não contradizer o próprio
    # roteiro (estádio moderno/skyline num tópico dos anos 50). Best-effort, custo zero.
    avoid = [str(t).strip() for t in (style.get("avoid") or []) if str(t).strip()]
    if avoid:
        prompt = f"{prompt}. Avoid: {', '.join(avoid)}"
    return prompt


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

def _prov_wikimedia(query: str, count: int) -> list:
    """
    Wikimedia Commons — imagens de domínio público e licenças livres.
    API: MediaWiki generator=search + imageinfo + extmetadata.
    """
    limit = max(count * 3, 5)
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": "6",  # namespace File
        "gsrlimit": str(limit),
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "format": "json",
    }
    url = "https://commons.wikimedia.org/w/api.php"
    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("[wikimedia] Erro na busca '%s': %s", query, exc)
        return []

    pages = data.get("query", {}).get("pages", {})
    results = []
    for page in pages.values():
        ii_list = page.get("imageinfo", [])
        if not ii_list:
            continue
        ii = ii_list[0]
        img_url = ii.get("url", "")

        # Aceitar só extensões de imagem bitmap
        if not any(img_url.lower().endswith(ext) for ext in _IMG_EXTS):
            continue

        extmeta = ii.get("extmetadata", {})
        raw_license = extmeta.get("LicenseShortName", {}).get("value", "")
        raw_attr = extmeta.get("Artist", {}).get("value", "")
        attribution = _strip_html(raw_attr)
        license_norm = raw_license.strip().lower()

        source_url = page.get("imageinfo", [{}])[0].get("descriptionurl", img_url)

        results.append(ImageResult(
            url=img_url,
            license=license_norm,
            attribution=attribution,
            source_provider="wikimedia",
            source_url=source_url,
        ))

    log.debug("[wikimedia] '%s' → %d candidatos", query, len(results))
    return results


def _prov_openverse(query: str, count: int) -> list:
    """
    Openverse (WordPress Foundation) — busca CC0 e PDM.
    API pública, sem chave necessária.
    """
    limit = max(count * 3, 5)
    params = {
        "q": query,
        "license": os.environ.get("OPENVERSE_LICENSES", "cc0,pdm"),
        "page_size": str(limit),
    }
    url = "https://api.openverse.org/v1/images/"
    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("[openverse] Erro na busca '%s': %s", query, exc)
        return []

    results = []
    for item in data.get("results", []):
        img_url = item.get("url", "")
        if not img_url:
            continue
        license_str = item.get("license", "").strip().lower()
        attribution = item.get("attribution", "").strip()
        source_url = item.get("foreign_landing_url", img_url)

        results.append(ImageResult(
            url=img_url,
            license=license_str,
            attribution=attribution,
            source_provider="openverse",
            source_url=source_url,
        ))

    log.debug("[openverse] '%s' → %d candidatos", query, len(results))
    return results


def _prov_internetarchive(query: str, count: int) -> list:
    """
    Internet Archive — imagens com licença livre.
    Rejeita NC (non-commercial) e ND (no-derivatives).
    """
    limit = max(count * 3, 5)
    # Filtra apenas itens com licenseurl preenchida
    ia_query = f"mediatype:image AND licenseurl:* AND {query}"
    params = {
        "q": ia_query,
        "fl[]": ["identifier", "licenseurl"],
        "rows": str(limit),
        "output": "json",
    }
    url = "https://archive.org/advancedsearch.php"
    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("[internetarchive] Erro na busca '%s': %s", query, exc)
        return []

    results = []
    for doc in data.get("response", {}).get("docs", []):
        identifier = doc.get("identifier", "")
        license_url = (doc.get("licenseurl") or "").lower()

        if not identifier:
            continue

        # Rejeitar NC (non-commercial) ou ND (no-derivatives)
        if "nc" in license_url or "nd" in license_url:
            continue

        # Inferir license string da URL
        if "publicdomain" in license_url or "zero" in license_url:
            license_str = "cc0"
        elif "/by-sa/" in license_url:
            license_str = "cc-by-sa 4.0"
        elif "/by/" in license_url:
            license_str = "cc-by 4.0"
        else:
            license_str = license_url  # mantém bruto para filtragem posterior

        # Montar URL candidata e validar via HEAD
        img_url = f"https://archive.org/download/{identifier}/{identifier}.jpg"
        try:
            head = requests.head(img_url, timeout=_HTTP_TIMEOUT, allow_redirects=True)
            if head.status_code == 404:
                continue
        except Exception:
            continue

        source_url = f"https://archive.org/details/{identifier}"
        results.append(ImageResult(
            url=img_url,
            license=license_str,
            attribution="",
            source_provider="internetarchive",
            source_url=source_url,
        ))

    log.debug("[internetarchive] '%s' → %d candidatos", query, len(results))
    return results


def _prov_aihorde(query: str, count: int, style: Optional[dict] = None,
                  seed: Optional[int] = None, negative: Optional[str] = None) -> list:
    """
    AI Horde — geração gratuita de imagens via fila pública.
    Usa AIHORDE_API_KEY (padrão "0000000000" = fila anônima de baixa prioridade, grátis).

    IMPORTANTE (free-tier anônimo): a fila grátis com apikey "0000000000" tem DOIS limites
    que derrubavam a geração:
      1) RESOLUÇÃO: pedidos com QUALQUER lado > 626px são barrados com HTTP 403 "this
         generation is in a state of heavy demand ... requests over 626x626 need kudos".
         O default ANTIGO (512x768) tinha o lado 768 > 626 → 100% rejeitado, e tudo caía no
         Cloudflare-FLUX (sem anime, sem negative, sem seed) — toda a melhoria de fidelidade
         ficava INERTE. Por isso o default agora é 320x576: ambos os lados ≤626, múltiplos de
         64, razão ~9:16 (320/576 ≈ 0.555 vs 9:16 = 0.5625). A imagem é b-roll de FUNDO que o
         pipeline reescala pra 1080x1920, então a resolução menor no gerador é aceitável.
      2) KUDOS/custo: pedidos "caros" (upscale, muitos steps) também 403am. Por isso steps
         enxutos e upscale OFF por default.
    Quem tiver chave/kudos (AIHORDE_API_KEY != "0000000000") pode subir AIHORDE_WIDTH/HEIGHT
    acima de 626 sem mexer no código — o teto de 626 só vale pra fila anônima.

    Knobs:
      AIHORDE_WIDTH   — largura (default 320; múltiplo de 64; anônimo: ≤626)
      AIHORDE_HEIGHT  — altura  (default 576; múltiplo de 64; anônimo: ≤626)
      AIHORDE_STEPS   — passos de sampling (default 12)
      AIHORDE_UPSCALE — "1" liga RealESRGAN_x4plus (opt-in; encarece → 403 no grátis). Default OFF.
      AIHORDE_MODELS  — CSV de modelos (default "stable_diffusion"; ex.: "stable_diffusion,Deliberate")
      AIHORDE_POLL_TIMEOUT — segundos que o poll espera a geração antes de desistir (default 300;
                       define perto do bloco de throttle). Subir ajuda em horário de pico da fila
                       anônima; baixar faz cair mais rápido pro próximo gerador.
    """
    api_key = os.environ.get("AIHORDE_API_KEY", "0000000000")
    prompt = _build_gen_prompt(query, style)
    # NEGATIVE PROMPT (One Piece fidelidade): o AI Horde honra negative via a sintaxe
    # "<prompt> ### <negative>" (Stable Diffusion, CFG>1). Diferente do FLUX schnell, AQUI
    # o negative DE FATO corta off-model/3d/western-cartoon/deformações. Só anexa se vier.
    if negative:
        prompt = f"{prompt} ### {negative.strip()}"
    # Resolução/steps enxutos por default pra caber no free-tier anônimo. O teto ANÔNIMO é
    # 626px POR LADO (acima disso → 403 "heavy demand ... over 626x626 need kudos"). 320x576
    # é vertical ~9:16, ambos os lados ≤626, múltiplos de 64. Tunável por env.
    _AIHORDE_ANON_MAX_SIDE = 626  # teto da fila anônima (apikey "0000000000")
    width = int(os.environ.get("AIHORDE_WIDTH", "320"))
    height = int(os.environ.get("AIHORDE_HEIGHT", "576"))  # ~9:16 vertical, lado ≤626
    # Rede de segurança: na fila anônima, clampa qualquer lado > 626 (e arredonda p/ baixo a
    # múltiplo de 64) pra não voltar a cair no 403. Com chave/kudos, respeita o que veio.
    if api_key == "0000000000":
        def _clamp_anon(v: int) -> int:
            v = min(v, _AIHORDE_ANON_MAX_SIDE)
            return max(64, (v // 64) * 64)  # múltiplo de 64, ≤626 (=> ≤576)
        if width > _AIHORDE_ANON_MAX_SIDE or height > _AIHORDE_ANON_MAX_SIDE:
            log.warning("[aihorde] dims %dx%d acima do teto anônimo (≤%d/lado) → clampando.",
                        width, height, _AIHORDE_ANON_MAX_SIDE)
        width, height = _clamp_anon(width), _clamp_anon(height)
    steps = int(os.environ.get("AIHORDE_STEPS", "12"))
    # MODELO anime-tuned por default (item 4): um checkpoint anime renderiza traço de
    # anime MUITO melhor que o "stable_diffusion" base. "Anything Diffusion" é o anime
    # mais comum/disponível na fila. Mantém CSV → quem quiser variar/voltar ao base ajusta
    # AIHORDE_MODELS. A redundância multi-fonte segue intacta (Cloudflare/Pollinations 1º).
    models_env = os.environ.get("AIHORDE_MODELS", "Anything Diffusion").strip()
    models = [m.strip() for m in models_env.split(",") if m.strip()] or ["Anything Diffusion"]
    params = {
        "width": width,
        "height": height,
        "steps": steps,
    }
    # seed ESTÁVEL por personagem (consistência entre shots). AI Horde aceita seed como
    # STRING em params. Sem seed → fila sorteia (cenário/objeto, sem identidade travada).
    if seed is not None:
        params["seed"] = str(int(seed))
    # post_processing (upscale) é OPT-IN: encarece o pedido e estoura o KudosUpfront da
    # fila anônima. Só liga com AIHORDE_UPSCALE=1 (quando há chave/kudos).
    if os.environ.get("AIHORDE_UPSCALE", "0") == "1":
        params["post_processing"] = ["RealESRGAN_x4plus"]
    payload = {
        "prompt": prompt,
        "params": params,
        "models": models,
        "r2": True,  # retorna URL em vez de base64 (quando suportado)
    }
    headers = {"apikey": api_key, "Content-Type": "application/json", **_HEADERS}

    # Submeter geração. NÃO usamos raise_for_status às cegas: em 403 (KudosUpfront/teto do
    # free-tier) queremos LOGAR a mensagem EXATA da API pra saber o teto real, não um
    # "403 Client Error" genérico.
    #
    # THROTTLE (BUG 2): a fila anônima limita a 2 submissões/s por chave; o controlador One
    # Piece dispara shots em paralelo e estourava o 429. Serializamos SÓ o POST de submissão
    # (não o poll): pegamos o lock, esperamos o gap mínimo desde a última submissão, marcamos
    # o timestamp e SÓ AÍ liberamos o lock — assim duas threads nunca submetem coladas. O
    # sleep acontece DENTRO do lock de propósito (é o que espaça as submissões); como é um
    # gap curto (~0.6s) e o trabalho pesado (geração + poll) roda fora do lock, não vira
    # gargalo nem deadlock. Só vale pra fila anônima (chave com kudos não tem esse teto).
    if api_key == "0000000000":
        with _AIHORDE_SUBMIT_LOCK:
            elapsed = time.time() - _AIHORDE_LAST_SUBMIT[0]
            if elapsed < _AIHORDE_MIN_SUBMIT_GAP:
                time.sleep(_AIHORDE_MIN_SUBMIT_GAP - elapsed)
            _AIHORDE_LAST_SUBMIT[0] = time.time()
    try:
        resp = requests.post(
            "https://aihorde.net/api/v2/generate/async",
            json=payload,
            headers=headers,
            timeout=_HTTP_TIMEOUT,
        )
    except Exception as exc:
        log.warning("[aihorde] Erro de rede ao submeter geração: %s", exc)
        return []

    if resp.status_code != 202:
        # A API responde o motivo em JSON {"message": "..."} (ex.: KudosUpfront).
        try:
            api_msg = resp.json().get("message", "")
        except Exception:
            api_msg = resp.text[:200]
        log.warning("[aihorde] Submissão recusada (HTTP %s, w=%d h=%d steps=%d upscale=%s): %s",
                    resp.status_code, width, height, steps,
                    os.environ.get("AIHORDE_UPSCALE", "0"), api_msg[:200])
        return []

    gen_id = resp.json().get("id")
    if not gen_id:
        log.warning("[aihorde] Sem ID de geração para '%s'", query)
        return []

    log.info("[aihorde] Geração submetida (id=%s, %dx%d steps=%d), aguardando fila...",
             gen_id, width, height, steps)

    # Poll até concluir ou timeout. A fila ANÔNIMA é compartilhada → o endpoint de status
    # devolve 429 (Too Many Requests) com frequência, ainda mais quando o controlador One
    # Piece dispara vários pedidos em paralelo. 429 NÃO é fatal: esperamos mais e seguimos
    # no poll (a geração continua viva no servidor). Só desistimos no timeout global.
    # Timeout configurável (AIHORDE_POLL_TIMEOUT, default 300s) — ver bloco de throttle.
    deadline = time.time() + _AIHORDE_POLL_TIMEOUT
    while time.time() < deadline:
        time.sleep(5)
        try:
            status_resp = requests.get(
                f"https://aihorde.net/api/v2/generate/status/{gen_id}",
                headers={"apikey": api_key, **_HEADERS},
                timeout=_HTTP_TIMEOUT,
            )
            if status_resp.status_code == 429:
                log.debug("[aihorde] status 429 (fila ocupada) — aguardando e seguindo o poll.")
                time.sleep(5)
                continue
            status_resp.raise_for_status()
            status = status_resp.json()
        except Exception as exc:
            log.warning("[aihorde] Erro ao consultar status: %s — segue no poll.", exc)
            time.sleep(5)
            continue

        if status.get("done"):
            generations = status.get("generations", [])
            if not generations:
                log.warning("[aihorde] done=true mas sem generations")
                return []

            # Descarta geração censurada pelo filtro NSFW (AI Horde devolve um cartão
            # "CENSORED / potentially spam content blocked" que passaria no filtro de tamanho).
            if generations[0].get("censored"):
                log.warning("[aihorde] geração censurada (filtro NSFW) — descartando placeholder.")
                return []

            img_data = generations[0].get("img", "")
            if not img_data:
                log.warning("[aihorde] Campo img vazio")
                return []

            # img pode ser URL ou base64 webp
            if img_data.startswith("http"):
                img_url = img_data
                bytes_data = None
            else:
                # base64 — decodifica para bytes
                import base64
                try:
                    raw = img_data.split(",", 1)[-1]  # remove "data:image/...;base64,"
                    bytes_data = base64.b64decode(raw)
                    img_url = f"aihorde://generated/{gen_id}"
                except Exception as exc:
                    log.warning("[aihorde] Falha ao decodificar base64: %s", exc)
                    return []

            return [ImageResult(
                url=img_url,
                license="ai-generated",
                attribution="",
                source_provider="aihorde",
                source_url=f"https://aihorde.net/",
                bytes_data=bytes_data,
            )]

        wait_time = status.get("wait_time", "?")
        log.debug("[aihorde] Aguardando... wait_time=%s", wait_time)

    log.warning("[aihorde] Timeout aguardando geração para '%s'", query)
    return []


def _prov_pollinations(query: str, count: int, style: Optional[dict] = None) -> list:
    """
    Pollinations.ai (FLUX) — geração grátis de imagens sem chave.
    Best-effort: retorna [] se falhar ou imagem for muito pequena.
    """
    prompt = _build_gen_prompt(query, style)
    encoded = urllib.parse.quote(prompt, safe="")
    url = f"https://image.pollinations.ai/prompt/{encoded}?width=1080&height=1920&model=flux&nologo=true"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_GEN_TIMEOUT)
        ct = resp.headers.get("content-type", "")
        if resp.status_code != 200:
            log.warning("[pollinations] HTTP %s para '%s'", resp.status_code, query)
            return []
        if not ct.startswith("image/"):
            log.warning("[pollinations] Content-type inesperado: %s", ct)
            return []
        if len(resp.content) < 2000:
            log.warning("[pollinations] Imagem muito pequena (%d bytes)", len(resp.content))
            return []
    except Exception as exc:
        log.warning("[pollinations] Erro para '%s': %s", query, exc)
        return []

    return [ImageResult(
        url=url,
        license="ai-generated",
        attribution="",
        source_provider="pollinations",
        source_url="https://pollinations.ai/",
        bytes_data=resp.content,
    )]


def _prov_cloudflare(query: str, count: int, style: Optional[dict] = None) -> list:
    """
    Cloudflare Workers AI (FLUX) — geração grátis com conta (10.000 neurons/dia).
    Requer CLOUDFLARE_ACCOUNT_ID + CLOUDFLARE_API_TOKEN no .env; sem eles, pula.
    Qualidade alta e estável → provider preferido da lane 'generate' quando configurado.
    """
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not account or not token:
        log.info("[cloudflare] sem CLOUDFLARE_ACCOUNT_ID/API_TOKEN — pulando (opcional).")
        return []

    model = os.environ.get("CLOUDFLARE_IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell")
    prompt = _build_gen_prompt(query, style)
    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", **_HEADERS}

    def _cf_throttle() -> None:
        # Mesmo padrão do AI Horde: serializa SÓ o momento da submissão (espaçamento mínimo
        # desde o último POST). Gate COMPARTILHADO com short_factory._fetch_cloudflare.
        with _CLOUDFLARE_SUBMIT_LOCK:
            elapsed = time.time() - _CLOUDFLARE_LAST_SUBMIT[0]
            if elapsed < _CF_MIN_SUBMIT_GAP:
                time.sleep(_CF_MIN_SUBMIT_GAP - elapsed)
            _CLOUDFLARE_LAST_SUBMIT[0] = time.time()

    try:
        _cf_throttle()
        resp = requests.post(url, json={"prompt": prompt, "steps": 6},
                             headers=headers, timeout=_GEN_TIMEOUT)
        # 429 transitório (rajada de submissões paralelas): 1 retry após esperar o gap (ou o
        # Retry-After da resposta) converte o 429 em sucesso. Se for a COTA DIÁRIA estourada,
        # o retry também 429a e cai pro próximo gerador — rápido, sem travar.
        if resp.status_code == 429:
            try:
                wait = float(resp.headers.get("Retry-After", "") or _CF_MIN_SUBMIT_GAP)
            except (TypeError, ValueError):
                wait = _CF_MIN_SUBMIT_GAP
            log.warning("[cloudflare] HTTP 429 — aguardando %.1fs e tentando 1x mais.", wait)
            time.sleep(max(wait, _CF_MIN_SUBMIT_GAP))
            _cf_throttle()
            resp = requests.post(url, json={"prompt": prompt, "steps": 6},
                                 headers=headers, timeout=_GEN_TIMEOUT)
        if resp.status_code != 200:
            log.warning("[cloudflare] HTTP %s: %s", resp.status_code, resp.text[:200])
            return []
        ct = resp.headers.get("content-type", "")
        if ct.startswith("image/"):
            img_bytes = resp.content
        else:
            data = resp.json()
            b64 = (data.get("result") or {}).get("image", "")
            if not b64:
                log.warning("[cloudflare] resposta sem result.image")
                return []
            import base64
            img_bytes = base64.b64decode(b64)
    except Exception as exc:
        log.warning("[cloudflare] erro: %s", exc)
        return []

    if len(img_bytes) < 2000:
        return []

    return [ImageResult(
        url=f"cloudflare://flux/{_sha1(img_bytes)[:12]}",
        license="ai-generated",
        attribution="",
        source_provider="cloudflare",
        source_url="https://developers.cloudflare.com/workers-ai/",
        bytes_data=img_bytes,
    )]


def _prov_imagerouter(query: str, count: int, style: Optional[dict] = None) -> list:
    """
    ImageRouter — geração de imagens via roteador OpenAI-compatible.
    Usa IMAGEROUTER_API_KEY do env. Sem chave, pula silenciosamente.

    Modelo padrão: black-forest-labs/FLUX-1-schnell:free (preço $0, mas exige
    depósito único de qualquer valor na conta — ver https://imagerouter.io/pricing).
    O modelo pode ser sobrescrito via IMAGEROUTER_MODEL no env.

    ATENÇÃO: o modelo :free retorna HTTP 403 "access_denied" se a conta não
    tiver feito o depósito inicial. O provider retorna [] nesses casos e a
    cascata segue para o próximo (aihorde).
    """
    api_key = os.environ.get("IMAGEROUTER_API_KEY", "").strip()
    if not api_key:
        log.info("[imagerouter] IMAGEROUTER_API_KEY não definida — pulando.")
        return []

    model = os.environ.get("IMAGEROUTER_MODEL", "black-forest-labs/FLUX-1-schnell:free").strip()
    prompt = _build_gen_prompt(query, style)

    payload = {
        "prompt": prompt,
        "model": model,
        # 1024x1024 é o tamanho padrão seguro; verticais não são suportados por todos os modelos
        "size": "1024x1024",
        "n": 1,
    }
    url = "https://api.imagerouter.io/v1/openai/images/generations"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        **_HEADERS,
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=_GEN_TIMEOUT)
    except Exception as exc:
        log.warning("[imagerouter] Erro de rede: %s", exc)
        return []

    if resp.status_code == 403:
        try:
            err_msg = resp.json().get("error", {}).get("message", "")
        except Exception:
            err_msg = resp.text[:200]
        log.warning("[imagerouter] HTTP 403 (access_denied) — %s", err_msg[:120])
        return []

    if resp.status_code != 200:
        log.warning("[imagerouter] HTTP %s para '%s': %s",
                    resp.status_code, query, resp.text[:200])
        return []

    try:
        data = resp.json()
        images = data.get("data", [])
        if not images:
            log.warning("[imagerouter] Resposta sem 'data' para '%s'", query)
            return []
    except Exception as exc:
        log.warning("[imagerouter] Falha ao parsear resposta: %s", exc)
        return []

    img_entry = images[0]
    img_url = img_entry.get("url", "")
    b64_data = img_entry.get("b64_json", "")

    if b64_data:
        import base64
        try:
            img_bytes = base64.b64decode(b64_data)
        except Exception as exc:
            log.warning("[imagerouter] Falha ao decodificar b64_json: %s", exc)
            return []
        return [ImageResult(
            url=f"imagerouter://generated/{_sha1(img_bytes)[:12]}",
            license="ai-generated",
            attribution="",
            source_provider="imagerouter",
            source_url="https://imagerouter.io/",
            bytes_data=img_bytes,
        )]

    if img_url:
        # URL retornada — baixa o conteúdo
        try:
            dl = requests.get(img_url, headers=_HEADERS, timeout=_GEN_TIMEOUT)
            dl.raise_for_status()
            img_bytes = dl.content
        except Exception as exc:
            log.warning("[imagerouter] Falha ao baixar imagem gerada: %s", exc)
            return []
        if len(img_bytes) < 2000:
            log.warning("[imagerouter] Imagem muito pequena (%d bytes)", len(img_bytes))
            return []
        return [ImageResult(
            url=img_url,
            license="ai-generated",
            attribution="",
            source_provider="imagerouter",
            source_url="https://imagerouter.io/",
            bytes_data=img_bytes,
        )]

    log.warning("[imagerouter] Nenhum url nem b64_json na resposta para '%s'", query)
    return []


def _prov_nvidia(query: str, count: int, style: Optional[dict] = None,
                 seed: Optional[int] = None, negative: Optional[str] = None) -> list:
    """
    NVIDIA NIM (Stable Diffusion 3.5 Large) — geração de imagens via API hospedada.
    Requer NVIDIA_API_KEY no .env; sem ela, pula silenciosamente (igual cloudflare/imagerouter).
    3º gerador da rede anti-preto: entra como fallback rápido quando Cloudflare cai.

    POST https://ai.api.nvidia.com/v1/genai/stabilityai/stable-diffusion-3-5-large
    Resposta JSON: artifacts[0].base64 → bytes. Guard de tamanho mínimo (< 2000 bytes = lixo).
    Knobs: NVIDIA_API_KEY (chave). aspect_ratio 9:16 nativo (sai vertical).
    """
    api_key = os.environ.get("NVIDIA_API_KEY", "").strip()
    if not api_key:
        log.info("[nvidia] NVIDIA_API_KEY não definida — pulando (opcional).")
        return []

    prompt = _build_gen_prompt(query, style)
    neg = (negative or "").strip() or "text, watermark, logo, blurry, deformed, low quality, extra limbs"
    url = "https://ai.api.nvidia.com/v1/genai/stabilityai/stable-diffusion-3-5-large"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        **_HEADERS,
    }
    payload = {
        "prompt": prompt,
        "cfg_scale": 5,
        "aspect_ratio": "9:16",
        "steps": 30,
        "negative_prompt": neg,
    }
    if seed is not None:
        payload["seed"] = int(seed)

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=_GEN_TIMEOUT)
    except Exception as exc:
        log.warning("[nvidia] Erro de rede: %s", exc)
        return []

    if resp.status_code != 200:
        log.warning("[nvidia] HTTP %s para '%s': %s", resp.status_code, query, resp.text[:200])
        return []

    try:
        data = resp.json()
        b64 = (data.get("artifacts") or [{}])[0].get("base64", "")
        if not b64:
            log.warning("[nvidia] resposta sem artifacts[0].base64 para '%s'", query)
            return []
        import base64
        img_bytes = base64.b64decode(b64)
    except Exception as exc:
        log.warning("[nvidia] Falha ao parsear/decodificar resposta: %s", exc)
        return []

    if len(img_bytes) < 2000:
        log.warning("[nvidia] Imagem muito pequena (%d bytes), ignorando", len(img_bytes))
        return []

    return [ImageResult(
        url=f"nvidia://sd35/{_sha1(img_bytes)[:12]}",
        license="ai-generated",
        attribution="",
        source_provider="nvidia",
        source_url="https://build.nvidia.com/stabilityai/stable-diffusion-3-5-large",
        bytes_data=img_bytes,
    )]


def _prov_together(query: str, count: int, style: Optional[dict] = None) -> list:
    """
    Together AI (FLUX.1-schnell-Free) — geração grátis de imagens (tier free, requer chave).
    Requer TOGETHER_API_KEY no .env; sem ela, pula silenciosamente. 4º gerador da rede
    anti-preto: entra após o aihorde (fallback grátis quando os demais caem).

    POST https://api.together.xyz/v1/images/generations
    Resposta JSON: data[i].b64_json → bytes. 768x1344 ≈ 9:16 vertical, steps=4 (schnell).
    """
    api_key = os.environ.get("TOGETHER_API_KEY", "").strip()
    if not api_key:
        log.info("[together] TOGETHER_API_KEY não definida — pulando (opcional).")
        return []

    prompt = _build_gen_prompt(query, style)
    model = os.environ.get("TOGETHER_IMAGE_MODEL", "black-forest-labs/FLUX.1-schnell-Free")
    url = "https://api.together.xyz/v1/images/generations"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        **_HEADERS,
    }
    payload = {
        "model": model,
        "prompt": prompt,
        "width": 768,
        "height": 1344,   # ≈ 9:16 vertical
        "steps": 4,       # FLUX schnell: poucos passos
        "n": count,
        "response_format": "b64_json",
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=_GEN_TIMEOUT)
    except Exception as exc:
        log.warning("[together] Erro de rede: %s", exc)
        return []

    if resp.status_code != 200:
        log.warning("[together] HTTP %s para '%s': %s", resp.status_code, query, resp.text[:200])
        return []

    try:
        data = resp.json()
        items = data.get("data", []) or []
    except Exception as exc:
        log.warning("[together] Falha ao parsear resposta: %s", exc)
        return []

    if not items:
        log.warning("[together] resposta sem 'data' para '%s'", query)
        return []

    import base64
    results = []
    for item in items:
        b64 = item.get("b64_json", "")
        if not b64:
            continue
        try:
            img_bytes = base64.b64decode(b64)
        except Exception as exc:
            log.warning("[together] Falha ao decodificar b64_json: %s", exc)
            continue
        if len(img_bytes) < 2000:
            continue
        results.append(ImageResult(
            url=f"together://flux/{_sha1(img_bytes)[:12]}",
            license="ai-generated",
            attribution="",
            source_provider="together",
            source_url="https://api.together.xyz/",
            bytes_data=img_bytes,
        ))

    if not results:
        log.warning("[together] nenhuma imagem válida na resposta para '%s'", query)
    return results


def _prov_fandom_pageimage(title: str, out_dir: Path) -> Optional[Path]:
    """
    Fandom (One Piece wiki) — infobox do personagem via prop=pageimages.

    Tenta baixar a imagem principal da página do personagem a partir de variações
    do título: como veio, com espaços→"_", e Title_Case.
    Retorna Path do arquivo salvo em out_dir, ou None se não encontrar.

    ATENÇÃO: IP protegida (Toei/Shueisha). Usar apenas com ALLOW_ANIME=1 e
    revisão humana obrigatória.
    """
    if os.environ.get("ALLOW_ANIME", "0") != "1":
        return None

    # Monta variações do título para tentar
    def _variants(t: str):
        seen = set()
        for v in (
            t,
            t.replace(" ", "_"),
            t.title().replace(" ", "_"),
            t.title(),
        ):
            if v and v not in seen:
                seen.add(v)
                yield v

    api_url = "https://onepiece.fandom.com/api.php"

    for variant in _variants(title):
        params = {
            "action": "query",
            "prop": "pageimages",
            "titles": variant,
            "piprop": "original|thumbnail",
            "pithumbsize": "800",
            "format": "json",
            "redirects": "1",
        }
        try:
            resp = requests.get(api_url, params=params, headers=_HEADERS, timeout=_HTTP_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.debug("[fandom_pageimage] Erro ao buscar '%s': %s", variant, exc)
            continue

        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            # Pula página inexistente (missing=-1)
            if page.get("missing") is not None:
                continue
            img_url = ""
            original = page.get("original", {})
            thumbnail = page.get("thumbnail", {})
            img_url = original.get("source") or thumbnail.get("source") or ""
            if not img_url:
                continue
            # URLs do Fandom têm formato: .../Char.png/revision/latest?cb=...
            # A extensão pode não estar no fim — verificamos se aparece em qualquer
            # parte do path (antes do primeiro "?").
            url_path = img_url.lower().split("?")[0]
            if not any(ext in url_path for ext in _IMG_EXTS):
                log.debug("[fandom_pageimage] URL ignorada (não imagem): %s", img_url[:80])
                continue

            # Baixa a imagem
            try:
                dl = requests.get(img_url, headers=_HEADERS, timeout=_HTTP_TIMEOUT)
                dl.raise_for_status()
                img_bytes = dl.content
            except Exception as exc:
                log.warning("[fandom_pageimage] Falha ao baixar '%s': %s", img_url[:80], exc)
                continue

            if len(img_bytes) < 2048:
                continue

            # Salva no out_dir
            out_dir.mkdir(parents=True, exist_ok=True)
            slug = re.sub(r"[^a-zA-Z0-9_-]", "_", variant)[:40]
            dest = out_dir / f"fandom_char_{slug}.jpg"
            dest.write_bytes(img_bytes)
            log.info(
                "[fandom_pageimage] Personagem '%s' → %s (%.1f KB)",
                variant, dest.name, len(img_bytes) / 1024,
            )
            return dest

    log.debug("[fandom_pageimage] Sem imagem para '%s' (e variações)", title)
    return None


def _prov_safebooru(tag: str, out_dir: Path) -> Optional[Path]:
    """
    Safebooru (Danbooru) — imagens de anime por tag (lane anime).

    GET https://safebooru.donmai.us/posts.json?tags={tag}&limit=5
    tag = nome em minúsculo com "_" (ex: "nerona_imu").
    Retorna o primeiro post com file_url de imagem (jpg/png/webp).

    ATENÇÃO: Safebooru usa Cloudflare e pode retornar 403 com requests simples
    sem cookies. O provider retorna None nesses casos e o pipeline cai no fluxo
    atual (civitai/pollinations).

    Conteúdo é fanart — IP protegida. needs_human_approval=True implícito.
    Usar apenas com ALLOW_ANIME=1.
    """
    if os.environ.get("ALLOW_ANIME", "0") != "1":
        return None

    slug = tag.lower().replace(" ", "_")
    params = {"tags": slug, "limit": "5"}
    api_url = "https://safebooru.donmai.us/posts.json"

    headers_sb = {
        **_HEADERS,
        "Accept": "application/json",
    }
    try:
        resp = requests.get(api_url, params=params, headers=headers_sb, timeout=_HTTP_TIMEOUT)
        if resp.status_code in (403, 503):
            log.warning("[safebooru] HTTP %s (Cloudflare bloqueou) para tag '%s'", resp.status_code, slug)
            return None
        resp.raise_for_status()
        posts = resp.json()
    except Exception as exc:
        log.warning("[safebooru] Erro para tag '%s': %s", slug, exc)
        return None

    if not isinstance(posts, list):
        log.debug("[safebooru] Resposta inesperada para '%s'", slug)
        return None

    for post in posts:
        file_url = post.get("file_url", "")
        if not file_url:
            continue
        ext = file_url.lower().split("?")[0].rsplit(".", 1)[-1]
        if f".{ext}" not in _IMG_EXTS:
            continue

        try:
            dl = requests.get(file_url, headers=headers_sb, timeout=_HTTP_TIMEOUT)
            dl.raise_for_status()
            img_bytes = dl.content
        except Exception as exc:
            log.warning("[safebooru] Falha ao baixar '%s': %s", file_url[:80], exc)
            continue

        if len(img_bytes) < 2048:
            continue

        out_dir.mkdir(parents=True, exist_ok=True)
        safe_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", slug)[:40]
        dest = out_dir / f"safebooru_{safe_slug}.jpg"
        dest.write_bytes(img_bytes)
        log.info(
            "[safebooru] Tag '%s' → %s (%.1f KB, post_id=%s)",
            slug, dest.name, len(img_bytes) / 1024, post.get("id", "?"),
        )
        return dest

    log.debug("[safebooru] Sem imagem para tag '%s'", slug)
    return None


def _prov_fandom(query: str, count: int) -> list:
    """
    Fandom wikis — imagens de anime/franquias (lane anime).
    ATENÇÃO: IP protegida. Marcada com needs_human_approval=True e license=UNKNOWN-IP.

    Usa generator=search&gsrnamespace=6&gsrsearch=<query> (busca semântica por File:)
    em vez de list=allimages (que era alfabética e irrelevante).
    """
    limit = max(count * 3, 5)
    # Busca semântica por arquivos de imagem (namespace 6 = File:)
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": "6",  # namespace File
        "gsrlimit": str(limit),
        "prop": "imageinfo",
        "iiprop": "url",
        "format": "json",
    }
    url = "https://onepiece.fandom.com/api.php"
    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("[fandom] Erro na busca '%s': %s", query, exc)
        return []

    pages = data.get("query", {}).get("pages", {})
    results = []
    for page in pages.values():
        ii_list = page.get("imageinfo", [])
        if not ii_list:
            continue
        img_url = ii_list[0].get("url", "")
        if not img_url or not any(img_url.lower().endswith(ext) for ext in _IMG_EXTS):
            continue
        results.append(ImageResult(
            url=img_url,
            license="unknown-ip",
            attribution="",
            source_provider="fandom",
            source_url=img_url,
            needs_human_approval=True,
        ))

    log.debug("[fandom] '%s' → %d candidatos (todos needs_human_approval)", query, len(results))
    return results


def _prov_civitai(query: str, count: int) -> list:
    """
    CivitAI — imagens geradas por modelos da comunidade (lane anime).
    ATENÇÃO: modelos podem ter IP de personagens protegidos.
    Marcado com needs_human_approval=True e license=UNKNOWN-IP.
    """
    limit = max(count * 3, 5)
    params = {
        "query": query,
        "limit": str(limit),
        "nsfw": "false",
        "sort": "Most Reactions",
    }
    url = "https://civitai.com/api/v1/images"
    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("[civitai] Erro na busca '%s': %s", query, exc)
        return []

    results = []
    for item in data.get("items", []):
        img_url = item.get("url", "")
        if not img_url:
            continue
        results.append(ImageResult(
            url=img_url,
            license="unknown-ip",
            attribution="",
            source_provider="civitai",
            source_url=img_url,
            needs_human_approval=True,
        ))

    log.debug("[civitai] '%s' → %d candidatos (todos needs_human_approval)", query, len(results))
    return results


def _prov_pexels_photo(query: str, count: int) -> list:
    """
    Pexels (FOTO) — banco de fotos reais grátis, sem chave paga (requer PEXELS_API_KEY).
    Decisão do Vinicius: "pegue da internet, use como está". Licença Pexels = uso livre +
    modificação, SEM atribuição obrigatória (não é CC, é stock free-to-use).

    Fonte de imagem REAL grátis nº 4 (Pexels segue vivo: ~24k requests sobrando). Bom
    fallback rápido para shots de CENÁRIO/OBJETO de true-crime/conspiracy quando os bancos
    CC (wikimedia/openverse/archive) não trazem nada — preserva a cota escassa do Cloudflare.

    license="pexels" (NÃO é burn-safe/CC). É fonte STOCK EXPLÍCITA: só entra quando o
    operador a lista em IMG_PROVIDERS/IMG_PROVIDERS_BURN; o gate de licença da lane burn é
    PULADO para ela em find_images (igual aos geradores de IA, que também não são CC).
    NUNCA usar para personagem One Piece (o controlador OP nem chama esta lane).
    """
    api_key = os.environ.get("PEXELS_API_KEY", "").strip()
    if not api_key:
        log.info("[pexels_photo] PEXELS_API_KEY não definida — pulando.")
        return []

    q = (query or "").split(",")[0].strip()
    if not q:
        return []
    per_page = max(count * 3, 5)
    params = {"query": q, "per_page": str(per_page), "orientation": "portrait"}
    url = "https://api.pexels.com/v1/search"
    headers = {"Authorization": api_key, **_HEADERS}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=_HTTP_TIMEOUT)
        if resp.status_code != 200:
            log.warning("[pexels_photo] HTTP %s para '%s': %s",
                        resp.status_code, q, resp.text[:160])
            return []
        data = resp.json()
    except Exception as exc:
        log.warning("[pexels_photo] Erro na busca '%s': %s", q, exc)
        return []

    results = []
    for photo in data.get("photos", []):
        src = photo.get("src", {}) or {}
        # Preferimos um tamanho grande o suficiente p/ 9:16 sem upscale feio.
        img_url = src.get("portrait") or src.get("large2x") or src.get("large") or src.get("original")
        if not img_url:
            continue
        page_url = photo.get("url", "")
        photographer = photo.get("photographer", "")
        results.append(ImageResult(
            url=img_url,
            license="pexels",                       # stock free-to-use (NÃO CC) — fora do gate burn
            attribution=f"Photo by {photographer} on Pexels" if photographer else "Pexels",
            source_provider="pexels_photo",
            source_url=page_url or img_url,
        ))

    log.debug("[pexels_photo] '%s' → %d candidatos", q, len(results))
    return results


# ---------------------------------------------------------------------------
# Registro de providers
# ---------------------------------------------------------------------------

_PROVIDER_REGISTRY = {
    "wikimedia": _prov_wikimedia,
    "openverse": _prov_openverse,
    "internetarchive": _prov_internetarchive,
    "cloudflare": _prov_cloudflare,
    "nvidia": _prov_nvidia,
    "aihorde": _prov_aihorde,
    "together": _prov_together,
    "pollinations": _prov_pollinations,
    "imagerouter": _prov_imagerouter,
    "fandom": _prov_fandom,
    "civitai": _prov_civitai,
    "pexels_photo": _prov_pexels_photo,
}

# Providers padrão por lane
_LANE_DEFAULTS = {
    "burn": ["wikimedia", "openverse", "internetarchive"],
    # generate: cascata VIVO-PRIMEIRO (medido 2026-06-08), mortos por último como best-effort:
    #   cloudflare    — gerador vivo (FLUX schnell, token CF, alta qualidade) → 1º
    #   nvidia        — NIM SD3.5 (gateado em NVIDIA_API_KEY; skip sem chave) → 2º fallback rápido
    #   aihorde       — grátis público (fila anônima lenta, mas vivo) → sobrevive ao CF/NVIDIA cair
    #   together      — FLUX schnell free (gateado em TOGETHER_API_KEY; skip sem chave) → após aihorde
    #   pollinations  — HTTP 402 paywall x402 (morto) → FORA da cascata viva
    #   imagerouter   — HTTP 400/403 "deposit" (morto) → FORA da cascata viva
    # nvidia/together pulam sozinhos sem chave → a cascata grátis (cloudflare→aihorde→...) segue
    # idêntica pra quem não configurou as chaves novas. Override por env IMG_PROVIDERS_GENERATE.
    # pollinations/imagerouter saíram (retry 4x desperdiçava ~1min/cena); _prov_* seguem no
    # registry → reativáveis listando-os em IMG_PROVIDERS_GENERATE se reviverem.
    "generate": ["cloudflare", "nvidia", "aihorde", "together"],
    "anime": [],   # só ativado via ALLOW_ANIME=1
    "ref": ["wikimedia", "internetarchive"],
}


def _resolve_providers(lane: str) -> list:
    """
    Determina a lista de providers a usar, respeitando a hierarquia de env vars:
      IMG_PROVIDERS_{LANE} > IMG_PROVIDERS > default da lane
    """
    lane_upper = lane.upper()
    lane_env = os.environ.get(f"IMG_PROVIDERS_{lane_upper}", "").strip()
    if lane_env:
        names = [n.strip() for n in lane_env.split(",") if n.strip()]
    else:
        generic = os.environ.get("IMG_PROVIDERS", "").strip()
        if generic:
            names = [n.strip() for n in generic.split(",") if n.strip()]
        else:
            names = list(_LANE_DEFAULTS.get(lane, []))

    # lane anime: HARD GATE. Só existe com ALLOW_ANIME=1; caso contrário a lane é VAZIA
    # mesmo que IMG_PROVIDERS_ANIME esteja setado no .env (defesa em profundidade — frames
    # booru/anime reais são Content ID Toei/Shueisha e ficam OFF por padrão). One-piece usa
    # a lane 'burn' (foto livre p/ cenário) + render IA, nunca esta.
    if lane == "anime":
        if os.environ.get("ALLOW_ANIME", "0") != "1":
            return []
        if not names:
            names = ["fandom", "civitai"]

    return names


# ---------------------------------------------------------------------------
# find_images — função principal
# ---------------------------------------------------------------------------

def find_images(
    query: str,
    niche: str,
    lane: str,
    count: int = 1,
    out_dir: Optional[Path] = None,
    style: Optional[dict] = None,
    providers: Optional[list] = None,
) -> list:
    """
    Busca, filtra e baixa imagens livres ou geradas para uso como b-roll.

    Parâmetros:
      query     — termo de busca (broll_query do roteiro)
      niche     — nicho do canal (ex: "true-crimes") — usado para logging/sidecar
      lane      — "burn", "generate", "anime" ou "ref"
      count     — número de imagens desejadas
      out_dir   — pasta de destino (default: img_cache)
      providers — lista EXPLÍCITA de providers, sobrepondo a resolução por env/lane.
                  Usado por lanes que NÃO podem aceitar fontes stock não-CC mesmo que o
                  operador as tenha listado em IMG_PROVIDERS_BURN (ex.: futebol player_real,
                  que exige foto LIVRE do jogador NOMEADO e nunca um rosto de terceiro via
                  pexels_photo). Quando None, mantém o comportamento antigo (_resolve_providers).

    Retorna list[Path] com até count caminhos de imagem salvas.
    Retorna [] quando:
      - lane=="anime" e ALLOW_ANIME!=1
      - nenhum provider encontrou imagem válida
    """
    # Gate: anime exige opt-in
    if lane == "anime" and os.environ.get("ALLOW_ANIME", "0") != "1":
        log.info("[image_providers] lane=anime bloqueada (ALLOW_ANIME não está setada como 1)")
        return []

    # Diretório de saída
    if lane == "ref":
        ref_base = Path.cwd() / "out" / "img_ref"
        ref_base.mkdir(parents=True, exist_ok=True)
        effective_out = ref_base
    else:
        effective_out = out_dir or _cache_dir()
        effective_out.mkdir(parents=True, exist_ok=True)

    cache_base = _cache_dir()
    credits_path = Path.cwd() / "out" / "CREDITS.jsonl"
    credits_path.parent.mkdir(parents=True, exist_ok=True)

    # providers EXPLÍCITO (override do caller) tem precedência sobre a resolução por env/lane.
    # Caso contrário, resolve normalmente (IMG_PROVIDERS_{LANE} > IMG_PROVIDERS > default).
    if providers is not None:
        providers = [str(p).strip() for p in providers if str(p).strip()]
    else:
        providers = _resolve_providers(lane)
    if not providers:
        log.info("[image_providers] Nenhum provider para lane='%s'", lane)
        return []

    log.info(
        "[image_providers] query='%s' lane='%s' providers=%s count=%d",
        query, lane, providers, count,
    )

    collected: list = []       # Paths de imagens salvas nesta run
    seen_urls: set = set()     # Dedupe por source_url
    seen_hashes: set = set()   # Dedupe por sha1(bytes)

    for provider_name in providers:
        if len(collected) >= count:
            break

        fn = _PROVIDER_REGISTRY.get(provider_name)
        if fn is None:
            log.warning("[image_providers] Provider desconhecido: '%s'", provider_name)
            continue

        # Assinatura de prompt: SÓ pra providers geradores (a imagem depende do prompt). Pra
        # fontes de foto real fica "" → cache key idêntica ao comportamento antigo.
        prompt_sig = (_prompt_signature(query, style)
                      if provider_name in _GENERATOR_PROVIDERS else "")

        # Índice POR-PROVIDER: conta só as imagens aceitas DESTE provider (substitui o antigo
        # run_index global). Garante que a LEITURA e a GRAVAÇÃO do cache usem o MESMO espaço de
        # índices (0,1,2…), e que cada imagem lógica receba sempre a mesma cache key entre runs
        # — cache determinístico, independente de quantas imagens os providers anteriores
        # trouxeram. (Bug corrigido: a leitura chumbava índice 0 e a gravação usava run_index
        # global → com count>1 a verificação só achava o slot 0, copiava 1 imagem e dava
        # `continue`, pulando o resto do provider; o vídeo recebia menos imagens que count em
        # silêncio. Atinge One Piece [count=pool] e futebol [count=n]; count=1 ficava ok por
        # coincidência.)
        provider_index = 0

        # Drena o cache ANTES de chamar o provider: varre os slots 0,1,2… deste provider até
        # suprir o count ou achar o primeiro buraco (slots são gravados em sequência, então um
        # ausente = fim do que está cacheado). Cada hit ALIMENTA os sets de dedupe (hash dos
        # bytes + source_url do sidecar) pra a busca fresca a seguir NÃO re-baixar/re-servir a
        # mesma imagem que já veio do cache (furo apontado na revisão).
        while len(collected) < count:
            ck = _cache_key(provider_name, query, lane, provider_index, prompt_sig)
            cached_img = cache_base / f"{ck}.jpg"
            cached_meta = cache_base / f"{ck}.json"
            if not (cached_img.exists() and cached_meta.exists()):
                break
            log.info("[image_providers] Cache hit: %s (%s, idx=%d)",
                     ck, provider_name, provider_index)
            cached_bytes = cached_img.read_bytes()
            seen_hashes.add(_sha1(cached_bytes))
            try:
                _meta = json.loads(cached_meta.read_text(encoding="utf-8"))
                if _meta.get("source_url"):
                    seen_urls.add(_meta["source_url"])
            except Exception:
                pass
            # Copia para effective_out se for diferente
            dest = effective_out / cached_img.name
            if not dest.exists():
                dest.write_bytes(cached_bytes)
            collected.append(dest)
            provider_index += 1

        # Cache já supriu o count? Encerra (o for externo também sairia no topo).
        if len(collected) >= count:
            break

        # Busca via provider. Geradores (cloudflare/pollinations/aihorde) aceitam
        # 'style' (visual_context) p/ prompt coeso; providers de banco ignoram.
        try:
            if "style" in inspect.signature(fn).parameters:
                candidates = fn(query, count, style)
            else:
                candidates = fn(query, count)
        except Exception as exc:
            log.warning("[image_providers] Erro no provider '%s': %s", provider_name, exc)
            continue

        for result in candidates:
            if len(collected) >= count:
                break

            # Gate de licença para lane burn (usa normalização tolerante a formato).
            # Pode ser afrouxado via IMG_LICENSE_LAX=1 (aceita licença desconhecida/vazia
            # também) — MAIS imagens, porém MAIS risco de copyright. Default = seguro.
            # EXCEÇÃO: pexels_photo é fonte STOCK free-to-use EXPLÍCITA (não-CC). Não passa
            # pelo gate CC — quem listou "pexels_photo" em IMG_PROVIDERS já optou pela fonte
            # stock conscientemente (decisão do Vinicius: "pegue da internet, use como está").
            if (lane == "burn" and os.environ.get("IMG_LICENSE_LAX", "") != "1"
                    and result.source_provider != "pexels_photo"):
                if not _license_is_burn_safe(result.license):
                    log.debug(
                        "[image_providers] Descartado (licença '%s' não-livre): %s",
                        result.license, result.url[:80],
                    )
                    continue

                # CC-BY/CC-BY-SA sem attribution: em vez de descartar (regra antiga,
                # restritiva demais), geramos um crédito automático a partir da fonte.
                # Mais imagens aproveitadas, e o crédito ainda fica registrado.
                norm_lic = result.license.replace("-", " ").lower()
                if norm_lic.startswith("cc by") and not result.attribution:
                    result.attribution = f"Source: {result.source_url or result.url} ({result.license})"

            # Dedupe por URL
            url_key = result.url
            if url_key in seen_urls:
                continue
            seen_urls.add(url_key)

            # Download (ou usa bytes já obtidos pelo provider)
            if result._bytes:
                img_bytes = result._bytes
            else:
                timeout = _GEN_TIMEOUT if result.source_provider in ("aihorde", "pollinations") else _HTTP_TIMEOUT
                img_bytes = None
                # Wikimedia/upload.wikimedia.org responde 429 (rate limit) em downloads
                # rápidos em sequência. Retry com backoff curto resolve na maioria.
                for attempt in range(3):
                    try:
                        dl = requests.get(result.url, headers=_HEADERS, timeout=timeout)
                        if dl.status_code == 429:
                            wait = 1.5 * (attempt + 1)
                            log.debug("[image_providers] 429 em %s; aguardando %.1fs (tentativa %d/3)",
                                      result.url[:60], wait, attempt + 1)
                            time.sleep(wait)
                            continue
                        dl.raise_for_status()
                        img_bytes = dl.content
                        break
                    except Exception as exc:
                        if attempt == 2:
                            log.warning("[image_providers] Falha ao baixar %s: %s", result.url[:80], exc)
                        else:
                            time.sleep(1.0)
                if img_bytes is None:
                    continue

            # Validação: content-type e tamanho mínimo
            if len(img_bytes) < 2048:
                log.debug("[image_providers] Imagem muito pequena (%d bytes), ignorando", len(img_bytes))
                continue

            # Dedupe por hash de conteúdo
            content_hash = _sha1(img_bytes)
            if content_hash in seen_hashes:
                continue
            seen_hashes.add(content_hash)

            # Salva no cache
            cache_key_i = _cache_key(provider_name, query, lane, provider_index, prompt_sig)
            cache_img_path = cache_base / f"{cache_key_i}.jpg"
            cache_meta_path = cache_base / f"{cache_key_i}.json"

            cache_img_path.write_bytes(img_bytes)

            meta = {
                "provider": result.source_provider,
                "source_url": result.source_url,
                "license": result.license,
                "attribution": result.attribution,
                "query": query,
                "lane": lane,
                "needs_human_approval": result.needs_human_approval,
                "ts": int(time.time()),
            }
            cache_meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

            # Copia para effective_out
            dest = effective_out / f"{cache_key_i}.jpg"
            if effective_out != cache_base:
                dest.write_bytes(img_bytes)
            else:
                dest = cache_img_path

            # Appenda ao CREDITS.jsonl
            with open(credits_path, "a", encoding="utf-8") as cf:
                cf.write(json.dumps({
                    "file": dest.name,
                    "provider": result.source_provider,
                    "source_url": result.source_url,
                    "license": result.license,
                    "attribution": result.attribution,
                    "query": query,
                    "lane": lane,
                    "needs_human_approval": result.needs_human_approval,
                    "ts": int(time.time()),
                }, ensure_ascii=False) + "\n")

            log.info(
                "[image_providers] Imagem salva: %s (provider=%s license=%s)",
                dest.name, result.source_provider, result.license,
            )
            collected.append(dest)
            provider_index += 1

    # Lane ref: baixou para img_ref, mas NÃO retorna paths pro vídeo
    if lane == "ref":
        if collected:
            log.info(
                "[image_providers] lane=ref: %d imagem(ns) salva(s) em %s (fora do vídeo)",
                len(collected), effective_out,
            )
        return []

    return collected


# ---------------------------------------------------------------------------
# Bloco de teste
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Muda cwd para a pasta do script para que os paths de saída sejam previsíveis
    os.chdir(Path(__file__).resolve().parent)

    print("\n=== Teste: find_images('courtroom trial', 'true-crimes', 'burn', 1) ===\n")
    paths = find_images("courtroom trial", "true-crimes", "burn", 1)

    if not paths:
        print("RESULTADO: nenhuma imagem baixada.")
        sys.exit(1)

    for p in paths:
        print(f"Imagem: {p}")
        meta_path = p.parent / (p.stem + ".json")
        if meta_path.exists():
            print("Sidecar:")
            print(json.dumps(json.loads(meta_path.read_text(encoding="utf-8")), indent=2))
        else:
            print("(sidecar não encontrado)")
