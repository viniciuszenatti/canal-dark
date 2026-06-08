# -*- coding: utf-8 -*-
"""
tools/op_web_lane_test.py — JUIZ da metade 2 do one-piece: roteamento por broll_kind +
lane WEB (foto real PD/CC p/ cenário/objeto) + guardrail de licença/anti-OP.

NÃO depende de LLM. Monta shots sintéticos com broll_kind variado e prova:
  (1) gating: o controlador _op_mode liga com CANAL_DARK_NICHE=one-piece e ALLOW_ANIME=0;
  (2) roteamento: shot 'character' -> SÓ IA (specs 'ai'); 'scenery'/'object' -> tenta WEB;
  (3) lane WEB real (wikimedia/openverse/archive.org) numa query de cenário;
  (4) guardrail: licença não-livre / título de anime/fanart/personagem -> descarta -> IA;
  (5) atribuição: sidecar real (licença+fonte) das fotos web aceitas;
  (6) gear5 anti-louro na _OP_SUBJECT_LIBRARY.

USO:
  C:/Users/aless/canal-dark/.venv/Scripts/python.exe tools/op_web_lane_test.py
"""
import os
import sys
import json
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # paths previsíveis (out/img_cache)
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

# Força o cenário exato do contrato ANTES de importar o módulo.
os.environ["CANAL_DARK_NICHE"] = "one-piece-theories-and-stories"
os.environ["ALLOW_ANIME"] = "0"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
import short_factory as sf  # noqa: E402

OUT = ROOT / "out" / "_op_web_lane_test"
OUT.mkdir(parents=True, exist_ok=True)


def hr(t):
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)


def spec_kind(spec):
    if spec[0] == "ready":
        return f"WEB/READY -> {Path(spec[1]).name}"
    return "AI -> " + (spec[1][:70] + "...")


# ── (1) GATING: controlador liga com ALLOW_ANIME=0 ──────────────────────────
hr("(1) GATING do controlador one-piece (ALLOW_ANIME=0)")
niche = os.environ.get("CANAL_DARK_NICHE", "").strip().lower()
op_mode = (niche == "one-piece-theories-and-stories")
print(f"CANAL_DARK_NICHE={niche!r}  ALLOW_ANIME={os.environ.get('ALLOW_ANIME')!r}")
print(f"_op_mode (controlador liga?) = {op_mode}  -> {'OK' if op_mode else 'FALHOU'}")
# lane anime deve estar OFF mesmo com IMG_PROVIDERS_ANIME setado
import image_providers as ip  # noqa: E402
anime_providers = ip._resolve_providers("anime")
print(f"lane ANIME providers (ALLOW_ANIME=0) = {anime_providers}  "
      f"-> {'OK (vazia)' if not anime_providers else 'FALHOU (deveria estar vazia)'}")
# gate duro: find_images da lane anime deve devolver [] mesmo com IMG_PROVIDERS_ANIME setado
anime_imgs = ip.find_images("luffy", "one-piece-theories-and-stories", "anime", count=1, out_dir=OUT)
print(f"find_images(lane=anime, ALLOW_ANIME=0) = {anime_imgs}  "
      f"-> {'OK (vazio, fandom/civitai OFF)' if not anime_imgs else 'FALHOU (baixou anime!)'}")
print(f"lane BURN providers = {ip._resolve_providers('burn')}")

# ── (6) GEAR5 anti-louro ────────────────────────────────────────────────────
hr("(6) GEAR5 anti-louro na _OP_SUBJECT_LIBRARY")
for key in ("gear5_nika", "gear5_luffy"):
    desc = sf._OP_SUBJECT_LIBRARY[key]
    ok = "NOT blond" in desc and "pure white" in desc.lower()
    print(f"{key}: {'OK' if ok else 'FALHOU'} | trecho: ...{desc[:95]}...")

# ── (4) GUARDRAIL unit: licença + heurística anti-OP ────────────────────────
hr("(4) GUARDRAIL _op_web_burn_safe (unit, metadata sintética)")
cases = [
    ("ACEITA: foto PD de farol", {"license": "public domain", "attribution": "Lighthouse, photo by J. Doe", "source_url": "https://commons.wikimedia.org/wiki/File:Lighthouse.jpg", "provider": "wikimedia"}, "old lighthouse on a cliff", True),
    ("ACEITA: CC0 mar tempestuoso", {"license": "cc0", "attribution": "", "source_url": "https://commons.wikimedia.org/wiki/File:Storm.jpg", "provider": "wikimedia"}, "stormy sea", True),
    ("ACEITA: CC-BY-SA navio antigo", {"license": "cc-by-sa 4.0", "attribution": "Tall ship by A. Photographer", "source_url": "https://commons.wikimedia.org/wiki/File:Tallship.jpg", "provider": "wikimedia"}, "old sailing ship", True),
    ("DESCARTA: licença NC", {"license": "cc-by-nc 4.0", "attribution": "x", "source_url": "https://example.org/x.jpg", "provider": "openverse"}, "harbor", False),
    ("DESCARTA: licença vazia", {"license": "", "attribution": "", "source_url": "https://example.org/y.jpg", "provider": "openverse"}, "harbor", False),
    ("DESCARTA: título cita One Piece", {"license": "cc0", "attribution": "One Piece Luffy fanart", "source_url": "https://example.org/op.jpg", "provider": "openverse"}, "pirate", False),
    ("DESCARTA: título cita personagem (Zoro)", {"license": "cc-by 4.0", "attribution": "Zoro cosplay at con", "source_url": "https://example.org/z.jpg", "provider": "openverse"}, "swordsman", False),
    ("DESCARTA: fonte fandom wiki", {"license": "cc-by-sa 4.0", "attribution": "scene", "source_url": "https://onepiece.fandom.com/wiki/Foo", "provider": "openverse"}, "island", False),
    ("DESCARTA: query contamina (anime)", {"license": "cc0", "attribution": "clouds", "source_url": "https://commons.wikimedia.org/wiki/File:Clouds.jpg", "provider": "wikimedia"}, "one piece anime sky", False),
    ("DESCARTA: ai-generated", {"license": "ai-generated", "attribution": "", "source_url": "", "provider": "pollinations"}, "x", False),
]
passed = 0
for name, meta, q, expected in cases:
    got, reason = sf._op_web_burn_safe(meta, q)
    ok = (got == expected)
    passed += ok
    print(f"  [{'OK' if ok else 'FALHOU'}] {name}: got={got} expected={expected}" + (f"  ({reason})" if not got else ""))
print(f"\nGuardrail unit: {passed}/{len(cases)} OK")

# ── (3) LANE WEB real: várias queries de cenário/objeto FOTOGRAFÁVEIS ────────
hr("(3) LANE WEB real (foto PD/CC) — queries de cenário/objeto")
WEB_QUERIES = [
    "old wooden sailing ship at sea",
    "stormy ocean waves dark clouds",
    "old lighthouse on a rocky cliff",
    "ancient stone ruins overgrown",
    "antique nautical compass",
    "tropical island beach aerial",
]
all_web = []
for q in WEB_QUERIES:
    paths = sf._op_fetch_web_burn(q, 1, OUT)
    all_web += [(q, p) for p in paths]
    print(f"  query={q!r:42s} -> {len(paths)} foto(s) livre(s) aceita(s)")
print(f"\n>>> {len(all_web)} foto(s) WEB livre(s) aceita(s) no total. Detalhe p/ inspeção:")
for q, p in all_web:
    meta = sf._op_find_sidecar(p) or {}
    print(f"\n  - {p}")
    print(f"      query={q!r}")
    print(f"      license={meta.get('license')!r} provider={meta.get('provider')!r}")
    print(f"      source_url={meta.get('source_url')!r}")
    print(f"      attribution={meta.get('attribution','')[:90]!r}")
    print(f"      CRED (entra na descrição): {sf._credit_for_broll_file(p)}")
# invariante: TODA foto web aceita tem licença livre registrada
all_free = all(sf._op_find_sidecar(p) and
               ip._license_is_burn_safe(sf._op_find_sidecar(p).get("license", ""))
               for _, p in all_web)
print(f"\nInvariante 'toda foto web tem licença livre registrada': "
      + ("OK" if all_free else "FALHOU"))

# ── (2) ROTEAMENTO POR SHOT via _op_plan_scene ──────────────────────────────
hr("(2) ROTEAMENTO POR SHOT (_op_plan_scene por broll_kind)")
shots = [
    ("Luffy clenches his fist and grins at the horizon", "Luffy grinning close up", "character"),
    ("Gear 5 Luffy laughs as reality bends", "gear 5 awakening white hair", "character"),
    ("The sea raged under a black and brooding sky", "stormy ocean waves dark clouds", "scenery"),
    ("On the deck rested a single weathered ship's wheel", "antique nautical compass", "object"),
    ("No one alive could read the ancient stone", "ancient carved stone tablet", "scenery"),
]
vctx = {}
summary = []
for text, query, kind in shots:
    n = 2
    plan = sf._op_plan_scene(text, query, [], n, vctx, OUT, kind)
    kinds = [("WEB" if s[0] == "ready" else "AI") for s in plan]
    print(f"\nkind={kind:8s} n={n} query={query!r}")
    for s in plan:
        print("   " + spec_kind(s))
    # invariante: character NUNCA web
    if kind == "character":
        assert all(s[0] == "ai" for s in plan), "VIOLACAO: character foi pra WEB!"
    summary.append((kind, kinds))

hr("RESUMO ROTEAMENTO")
for kind, kinds in summary:
    print(f"  {kind:8s} -> {kinds}")
print("\nInvariante 'character SEMPRE IA, nunca WEB': "
      + ("OK" if all(all(k == 'AI' for k in ks) for kd, ks in summary if kd == 'character') else "FALHOU"))

# ── (5) _build_op_broll END-TO-END (ponto de produção real) com IA mockada ──
# Prova o roteamento no caminho REAL (all_kinds → _build_op_broll), sem depender da
# infra de IA fragilizada (402/cota): mocka _fetch_ai_image p/ devolver um marcador.
hr("(5) _build_op_broll END-TO-END (all_kinds, IA mockada)")
AI_MARK = OUT / "_AI_PLACEHOLDER.txt"
AI_MARK.write_text("ai", encoding="utf-8")
_orig = sf._fetch_ai_image
sf._fetch_ai_image = lambda prompt, out_dir, index: AI_MARK  # type: ignore
try:
    all_queries = ["stormy ocean waves dark clouds", "Luffy grins at the camera",
                   "antique nautical compass", "the ancient stone could not be read"]
    all_texts = ["The sea raged black and cruel", "Luffy laughed at the danger",
                 "A compass spun on the old deck", "No one alive could read it"]
    all_kinds = ["scenery", "character", "object", "scenery"]
    bf = sf._build_op_broll(all_queries, all_texts, all_kinds, [], [], {}, OUT)
    print("\nbroll_files (por cena):")
    web_used = ai_used = 0
    for i, item in enumerate(bf):
        cands = item if isinstance(item, (list, tuple)) else [item]
        labels = []
        for c in cands:
            if c is None:
                labels.append("None")
            elif Path(c) == AI_MARK:
                labels.append("AI"); ai_used += 1
            else:
                labels.append(f"WEB:{Path(c).name}"); web_used += 1
        print(f"  cena {i} kind={all_kinds[i]:8s} -> {labels}")
    # invariante no caminho real: a cena 'character' (idx 1) não pode ter nenhum WEB
    char_cands = bf[1] if isinstance(bf[1], (list, tuple)) else [bf[1]]
    char_ok = all((c is None or Path(c) == AI_MARK) for c in char_cands)
    print(f"\n  cena 'character' (idx 1) sem WEB: {'OK' if char_ok else 'FALHOU'}")
    print(f"  total slots: {web_used} WEB (foto livre) + {ai_used} IA")
finally:
    sf._fetch_ai_image = _orig

print(f"\nImagens web p/ inspecao: {OUT}")
