---
projeto: canal-dark
tipo: canal
tema: infra-n8n-servidor
atualizado: 2026-06-08
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
- **Servidor 24/7**: **Hetzner contratado** (substitui a rota Oracle, que ficou "out of capacity"). ⚠️ É um VPS
  **compartilhado/em produção**: já tem Docker + **Portainer** + **Traefik** (proxy reverso) e outros containers.
  → regra **ADD-ONLY** (igual ao n8n de HML): só adicionar stack isolada, nunca tocar no que existe.
- **Rota Oracle (OCI) ENCERRADA (2026-06-08)**: o auto-retry foi **morto de vez** — o processo
  `oci_retry_launch.py` (loop detached que tentava criar a VM ARM A1 grátis) está **parado**, **sem tarefa
  agendada nem entrada de inicialização religando**. A Oracle free seguiu "out of capacity" e a rota foi
  oficialmente abandonada. Servidor 24/7 = **Hetzner**. O script `infra/oci_retry_launch.py` fica no repo só
  como referência histórica (não roda mais).
- **Solicitação à equipe de infra criada**: [`infra/SOLICITACAO-infra-hetzner.md`](../infra/SOLICITACAO-infra-hetzner.md)
  — Fase 1: imagem custom `canaldark-n8n` (n8n+Python+FFmpeg+repo, gera o vídeo dentro de si) publicada via labels
  Traefik em `n8n.<DOMINIO>`. Fase 2 (se a RAM permitir): Postiz. Inclui Dockerfile + stack do Portainer.
  **Pendente da infra:** nome da rede do Traefik, entrypoint/certresolver, subdomínios e `nproc/free -h/df -h`.
- Script de auto-retry da VM (rota Oracle, **DESLIGADO DE VEZ em 2026-06-08** — não roda mais, mantido só como
  referência histórica): `infra/oci_retry_launch.py`.

## Backlog

- [ ] Ligar o workflow real: env vars (GEMINI/PEXELS/TELEGRAM/POSTIZ/SHEETS) + descomentar nós Code.
- [ ] Montar volume `canal-dark/` no container n8n + instalar Python/FFmpeg lá.
- [ ] Evoluir os checkpoints Telegram de Wait → **webhook + botões inline**. → canal **03**.
- [x] ~~Decidir servidor: insistir Oracle free vs migrar Hetzner~~ → DECIDIDO: Hetzner (Oracle encerrada 08/06).
- [ ] Postiz no mesmo host. → canal **05**.

## Chaves / Segredos

**Decisão do Vinicius (2026-05-31): NÃO rotacionar** as chaves vazadas. Sobrepõe a "ação pendente de
segurança" do CLAUDE.md. ⚠️ Risco aceito: a chave do **n8n de HML da S4S** é de infra da empresa — se vazar
de fato, é problema corporativo, não só pessoal. Mantido o registro caso mude de ideia.

> Onde os valores reais vivem: `.env` (gitignored, NÃO versionado) e, no repo de trabalho
> `C:\Users\aless\canal-dark`, também `_segredos/` (pasta gitignored). **Nunca** colar o valor de nenhuma
> chave em doc, repo, OneDrive ou vault público.

As 5 chaves em uso (todas **NÃO rotacionadas — decisão 31/05**):

| # | Chave | Pra que serve | Variável no `.env` | Status |
|---|-------|---------------|--------------------|--------|
| 1 | **n8n HML (S4S)** | Autenticar na API do n8n de HML (`hml-editor.staff4solutions.com.br`) p/ subir/ler workflow. Infra da **empresa**. | (não está no `.env.example`; usada pelo `n8n/push_to_n8n.py`) | NÃO rotacionada (31/05) |
| 2 | **OCI / Oracle Cloud** | Autenticava na API da Oracle p/ tentar criar a VM ARM A1 (rota de servidor **ENCERRADA 08/06** — não mais usada). | credenciais OCI (`~/.oci`, fingerprint + chave privada) — não no `.env` | NÃO rotacionada (31/05) |
| 3 | **Telegram bot** | Token do `@CanalDark_bot` p/ enviar/receber os 2 checkpoints humanos (roteiro + guardrail). | `TELEGRAM_BOT_TOKEN` | NÃO rotacionada (31/05) |
| 4 | **Gemini** | Google AI Studio; LLM do roteirista/trend/guardrail. | `GEMINI_API_KEY` | NÃO rotacionada (31/05) |
| 5 | **Pexels** | API de b-roll grátis usada pelo `short_factory.py`. | `PEXELS_API_KEY` | NÃO rotacionada (31/05) |

## Links

Bot/checkpoints → [[03-melhorias-telegram]]. Publicação → [[05-publicacao-distribuicao]].
Guardrail/roteirista → [[02-melhorias-prompt]].
