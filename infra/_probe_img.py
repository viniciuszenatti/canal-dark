import sys, os
sys.path.insert(0, r"C:\Users\aless\canal-dark")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\aless\canal-dark\.env")
import google.generativeai as genai
genai.configure(api_key=os.environ["GEMINI_API_KEY"])
found = []
for x in genai.list_models():
    n = x.name.lower()
    if "image" in n or "imagen" in n:
        found.append(x.name)
if found:
    print("MODELOS DE IMAGEM:")
    for x in found:
        print("  ", x)
else:
    print("NENHUM modelo de imagem nesta chave")
