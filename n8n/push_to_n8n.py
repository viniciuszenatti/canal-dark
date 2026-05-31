"""
Importa UM workflow novo num n8n remoto via API pública (apenas POST/criar).
NUNCA lista, edita ou apaga nada existente — só adiciona.

Uso:
    set N8N_API_KEY=eyJ...        (a chave fica no ambiente, nunca em arquivo)
    python push_to_n8n.py --base https://SEU-n8n --file caminho/workflow.json
"""
import argparse
import json
import os
import sys

import requests


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="URL base do n8n (sem barra final)")
    ap.add_argument("--file", required=True, help="Path do workflow .json a importar")
    args = ap.parse_args()

    key = os.environ.get("N8N_API_KEY")
    if not key:
        print("ERRO: defina a variável de ambiente N8N_API_KEY")
        sys.exit(1)

    with open(args.file, encoding="utf-8") as f:
        wf = json.load(f)

    # Payload mínimo aceito pela API de criação. Só estes campos →
    # evita 'additional properties' e NÃO mexe em active/id/tags existentes.
    payload = {
        "name": wf.get("name", "Imported workflow"),
        "nodes": wf["nodes"],
        "connections": wf["connections"],
        "settings": {},  # vazio: a API rejeita chaves extras em settings; n8n usa defaults
    }

    url = args.base.rstrip("/") + "/api/v1/workflows"
    headers = {
        "X-N8N-API-KEY": key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    print(f"POST {url}")
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    print("HTTP", r.status_code)

    try:
        data = r.json()
    except Exception:
        print("Resposta (texto):", r.text[:1500])
        sys.exit(0 if r.status_code < 300 else 1)

    if r.status_code in (200, 201):
        print("WORKFLOW CRIADO COM SUCESSO:")
        print("  id    :", data.get("id"))
        print("  name  :", data.get("name"))
        print("  active:", data.get("active"))
    else:
        print("FALHOU:", json.dumps(data, ensure_ascii=False)[:1500])
        sys.exit(1)


if __name__ == "__main__":
    main()
