"""Testa geração REAL de imagem com a chave Gemini (sem custo se cair no free tier)."""
import sys, os
sys.path.insert(0, r"C:\Users\aless\canal-dark")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\aless\canal-dark\.env")
import google.generativeai as genai
genai.configure(api_key=os.environ["GEMINI_API_KEY"])

out = r"C:\Users\aless\canal-dark\out\_gem_test.png"
prompt = "dark stormy ocean at night, cinematic, dramatic lighting, vertical 9:16"

for model_name in ["gemini-2.5-flash-image", "imagen-4.0-fast-generate-001"]:
    print(f"\n=== Testando {model_name} ===")
    try:
        model = genai.GenerativeModel(model_name)
        resp = model.generate_content(prompt)
        saved = False
        for cand in resp.candidates:
            for part in cand.content.parts:
                if getattr(part, "inline_data", None) and part.inline_data.data:
                    with open(out, "wb") as f:
                        f.write(part.inline_data.data)
                    kb = round(len(part.inline_data.data) / 1024, 1)
                    print(f"  OK! imagem salva ({kb} KB) -> {out}")
                    saved = True
        if not saved:
            print("  Sem dados de imagem na resposta:", str(resp)[:200])
        if saved:
            break
    except Exception as e:
        print(f"  ERRO: {type(e).__name__}: {str(e)[:300]}")
