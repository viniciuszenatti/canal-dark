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
  AIHORDE_API_KEY        — chave AI Horde (default: "0000000000" = fila pública)
  IMG_HTTP_TIMEOUT       — timeout HTTP geral em segundos (default: 30)
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
    "cc0",
    "cc0 1.0",
    "cc-by 4.0",
    "cc-by-sa 4.0",
    "cc-by-sa 3.0",
    "cc-by 2.0",
    "cc-by-sa 2.0",
}

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


def _cache_key(provider: str, query: str, lane: str, index: int) -> str:
    raw = f"{provider}|{query}|{lane}|{index}"
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
    return ", ".join(b for b in bits if b)


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


def _prov_aihorde(query: str, count: int, style: Optional[dict] = None) -> list:
    """
    AI Horde — geração gratuita de imagens via fila pública.
    Usa AIHORDE_API_KEY (padrão "0000000000" = fila de baixa prioridade, grátis).
    """
    api_key = os.environ.get("AIHORDE_API_KEY", "0000000000")
    prompt = _build_gen_prompt(query, style)
    payload = {
        "prompt": prompt,
        "params": {
            "width": 512,
            "height": 512,
            "steps": 20,
        },
        "models": ["stable_diffusion"],
        "r2": True,  # retorna URL em vez de base64 (quando suportado)
    }
    headers = {"apikey": api_key, "Content-Type": "application/json", **_HEADERS}

    # Submeter geração
    try:
        resp = requests.post(
            "https://aihorde.net/api/v2/generate/async",
            json=payload,
            headers=headers,
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        gen_id = resp.json().get("id")
        if not gen_id:
            log.warning("[aihorde] Sem ID de geração para '%s'", query)
            return []
    except Exception as exc:
        log.warning("[aihorde] Falha ao submeter geração: %s", exc)
        return []

    log.info("[aihorde] Geração submetida (id=%s), aguardando fila...", gen_id)

    # Poll até concluir ou timeout
    deadline = time.time() + 180
    while time.time() < deadline:
        time.sleep(5)
        try:
            status_resp = requests.get(
                f"https://aihorde.net/api/v2/generate/status/{gen_id}",
                headers={"apikey": api_key, **_HEADERS},
                timeout=_HTTP_TIMEOUT,
            )
            status_resp.raise_for_status()
            status = status_resp.json()
        except Exception as exc:
            log.warning("[aihorde] Erro ao consultar status: %s", exc)
            break

        if status.get("done"):
            generations = status.get("generations", [])
            if not generations:
                log.warning("[aihorde] done=true mas sem generations")
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

    try:
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


def _prov_fandom(query: str, count: int) -> list:
    """
    Fandom wikis — imagens de anime/franquias (lane anime).
    ATENÇÃO: IP protegida. Marcada com needs_human_approval=True e license=UNKNOWN-IP.
    """
    limit = max(count * 3, 5)
    # Tenta busca genérica no onepiece.fandom.com como exemplo; query determina contexto
    params = {
        "action": "query",
        "list": "allimages",
        "aifrom": query.replace(" ", "_"),
        "ailimit": str(limit),
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

    results = []
    for item in data.get("query", {}).get("allimages", []):
        img_url = item.get("url", "")
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


# ---------------------------------------------------------------------------
# Registro de providers
# ---------------------------------------------------------------------------

_PROVIDER_REGISTRY = {
    "wikimedia": _prov_wikimedia,
    "openverse": _prov_openverse,
    "internetarchive": _prov_internetarchive,
    "cloudflare": _prov_cloudflare,
    "aihorde": _prov_aihorde,
    "pollinations": _prov_pollinations,
    "fandom": _prov_fandom,
    "civitai": _prov_civitai,
}

# Providers padrão por lane
_LANE_DEFAULTS = {
    "burn": ["wikimedia", "openverse", "internetarchive"],
    # generate: Cloudflare FLUX primeiro (grátis com token, alta qualidade);
    # cai pro Pollinations (grátis sem chave) e AI Horde (grátis, lento) se faltar/falhar.
    "generate": ["cloudflare", "pollinations", "aihorde"],
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

    # lane anime: adiciona fandom/civitai apenas se ALLOW_ANIME=1
    if lane == "anime" and os.environ.get("ALLOW_ANIME", "0") == "1":
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
) -> list:
    """
    Busca, filtra e baixa imagens livres ou geradas para uso como b-roll.

    Parâmetros:
      query   — termo de busca (broll_query do roteiro)
      niche   — nicho do canal (ex: "true-crimes") — usado para logging/sidecar
      lane    — "burn", "generate", "anime" ou "ref"
      count   — número de imagens desejadas
      out_dir — pasta de destino (default: img_cache)

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
    run_index = 0              # Índice sequencial para cache key

    for provider_name in providers:
        if len(collected) >= count:
            break

        fn = _PROVIDER_REGISTRY.get(provider_name)
        if fn is None:
            log.warning("[image_providers] Provider desconhecido: '%s'", provider_name)
            continue

        # Verifica cache antes de chamar o provider
        cache_key = _cache_key(provider_name, query, lane, 0)
        cached_img = cache_base / f"{cache_key}.jpg"
        cached_meta = cache_base / f"{cache_key}.json"

        if cached_img.exists() and cached_meta.exists():
            log.info("[image_providers] Cache hit: %s (%s)", cache_key, provider_name)
            # Copia para effective_out se for diferente
            dest = effective_out / cached_img.name
            if not dest.exists():
                dest.write_bytes(cached_img.read_bytes())
            collected.append(dest)
            continue

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
            if lane == "burn" and os.environ.get("IMG_LICENSE_LAX", "") != "1":
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
            cache_key_i = _cache_key(provider_name, query, lane, run_index)
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
            run_index += 1

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
