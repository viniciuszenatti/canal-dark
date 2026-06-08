# -*- coding: utf-8 -*-
"""
tools/op_fix_test.py — JUIZ das 2 correções da lane de imagem one-piece (PROBLEM 1 e 2).

Gera 6 imagens de teste cobrindo loki + luffy + imu + gear5 + joy_boy_nika + zoro/shanks,
com CENAS VARIADAS (ângulo/ação diferentes por shot), usando EXATAMENTE os helpers _op_*
do short_factory.py (mesma lane FLUX schnell via Cloudflare/Pollinations) — pra o
olho humano julgar (a) zero gota azul/lágrima e (b) fidelidade do personagem.

USO:
  C:/Users/aless/canal-dark/.venv/Scripts/python.exe tools/op_fix_test.py --out C:/Users/aless/canal-dark/out/_op_fix_test
"""
import argparse
import os
import sys
from pathlib import Path

# garante o cwd do repo no sys.path e carrega .env (Cloudflare/Pollinations keys)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

import short_factory as sf  # noqa: E402

# (subject_key, action/fala da cena, blob p/ escolher o SHOT — cenas VARIADAS)
SCENES = [
    ("loki",          "the chained giant prince roars, straining against his iron shackles", "loki towering powerful giant rises"),
    ("luffy",         "Luffy grins and throws a punch toward the camera", "luffy close up eye reveal"),
    ("imu",           "the shadowed ruler watches the world from the empty throne", "imu ruler the throne high above"),
    ("gear5_nika",    "Gear 5 Luffy laughs as reality bends around him, white halo glowing", "gear 5 awakening god powerful"),
    ("joy_boy_nika",  "the Sun God Nika dances and beats the drums of liberation", "joy boy nika liberation savior"),
    ("zoro",          "Zoro draws all three swords in a wide battle stance", "zoro swordsman against clash fight"),
    ("shanks",        "Shanks stands calm on the deck, his empty sleeve in the wind", "shanks emperor stands before authority"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "out" / "_op_fix_test"))
    ap.add_argument("--n", type=int, default=6, help="quantas imagens gerar (default 6)")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    vctx = {}  # usa os defaults One Piece do builder

    scenes = SCENES[: args.n]
    print(f"[op_fix_test] gerando {len(scenes)} imagem(ns) em {out_dir}")
    print(f"[op_fix_test] provider: {'Cloudflare FLUX' if os.environ.get('CLOUDFLARE_ACCOUNT_ID') else 'Pollinations'} (schnell, CFG=1)")

    ok = 0
    for i, (key, action, blob) in enumerate(scenes):
        desc = sf._OP_SUBJECT_LIBRARY[key]
        prompt = sf._op_build_image_prompt(desc, vctx, action=action, blob=blob)
        print(f"\n[{i}] {key}: {prompt[:140]}...")
        path = sf._fetch_ai_image(prompt, out_dir, i)
        if path:
            # renomeia pra ficar legível por personagem
            named = out_dir / f"op_{i:02d}_{key}.jpg"
            try:
                if Path(path) != named:
                    Path(path).replace(named)
            except Exception:
                named = Path(path)
            ok += 1
            print(f"    OK -> {named}")
        else:
            print("    FALHOU (provider nao retornou imagem)")

    print(f"\n[op_fix_test] {ok}/{len(scenes)} imagens geradas. Olho humano e o juiz: zero gota + personagem fiel.")
    sys.exit(0 if ok == len(scenes) else 1)


if __name__ == "__main__":
    main()
