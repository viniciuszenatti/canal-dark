---
projeto: canal-dark
tipo: canal
tema: infra-n8n-servidor
atualizado: 2026-05-30
---

# ⚙️ Canal 06 — Infra · n8n · Servidor

> **Objetivo:** orquestrar o pipeline ponta-a-ponta e rodar 24/7. Cola roteiro→vídeo→guardrail→publicação.

**Quando usar:** n8n, automação, agendamento, VPS, deploy, systemd.

## Estado atual

- **n8n MVP 2.0** (`n8n/workflow-mvp.json`, "Canal Dark — MVP Shorts Narrados"):
  Schedule → Trend Scout → Roteirista → **Telegram #1 (aprovar roteiro)** → `short_factory.py`
  → Guardrail → IF risco → **Telegram #2 (se risco)** → Postiz → Google Sheets (log).
  **Importado e INATIVO**, todos os nós Code em **modo simulação** (código real comentado, pronto pra ligar).
- Hospedado no n8n de **HML da S4S** (`hml-editor.staff4solutions.com.br`), workflow id `dz3ehGcD3srs7vtQ`.
  **RESTRIÇÃO DURA: só ADICIONAR, nunca alterar/excluir nada que já existe nesse n8n.**
- **Servidor 24/7**: Oracle Cloud free (ARM A1) **pausado** — "out of capacity" a noite toda.
  Rodando **local** por enquanto (o bot já gera vídeo). Se retomar 24/7: recomendado **Hetzner (~R$25/mês)**.
- Script de auto-retry da VM: `infra/oci_retry_launch.py` (loop até pegar vaga, avisa no Telegram).

## Backlog

- [ ] Ligar o workflow real: env vars (GEMINI/PEXELS/TELEGRAM/POSTIZ/SHEETS) + descomentar nós Code.
- [ ] Montar volume `canal-dark/` no container n8n + instalar Python/FFmpeg lá.
- [ ] Evoluir os checkpoints Telegram de Wait → **webhook + botões inline**. → canal **03**.
- [ ] Decidir servidor: insistir Oracle free vs migrar Hetzner.
- [ ] Postiz no mesmo host. → canal **05**.

## Segredos a revogar (foram colados no chat)

- API key do n8n (HML) e API key da Oracle → **revogar**.

## Links

Bot/checkpoints → [[03-melhorias-telegram]]. Publicação → [[05-publicacao-distribuicao]].
Guardrail/roteirista → [[02-melhorias-prompt]].
