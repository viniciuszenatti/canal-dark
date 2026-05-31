---
projeto: canal-dark
tipo: canal
tema: publicacao-distribuicao
atualizado: 2026-05-30
---

# 🚀 Canal 05 — Publicação & Distribuição

> **Objetivo:** levar o `short.mp4` pronto às plataformas com segurança de política —
> Postiz, contas, metadata, AI disclosure.

**Quando usar:** publicar, configurar Postiz/contas, ajustar metadata por plataforma, dúvidas de política.

## Estado atual

- **`build_publication_metadata`** já gera `metadata.json` com presets por plataforma
  (YouTube/TikTok/Instagram): título, descrição, hashtags + **avisos de AI disclosure** embutidos.
- **Distribuição planejada**: **Postiz self-hosted** (grátis, evita app review do TikTok), acionado pelo n8n.
- Contas sociais: ainda **não criadas**.

## AI disclosure (obrigatório — não opcional)

- YouTube: marcar "Altered or synthetic content" no Studio.
- TikTok: sticker "AI Generated" no Creator Tools.
- Instagram: label "Created with AI" no Reels.
> O `metadata.json` já lembra disso em `note`. Ignorar = risco de penalização.

## Backlog

- [ ] Subir Postiz (docker-compose) e conectar YouTube/TikTok/Instagram.
- [ ] Criar as contas/canal coerentes com a persona (depois do canal **04**).
- [ ] Fechar o loop: n8n → Postiz → log no Google Sheets (já desenhado no workflow). → canal **06**.
- [ ] Política por plataforma do nicho escolhido (ex.: one-piece tende a TikTok).

## Links

Nicho/plataforma-foco → [[04-nicho-decisao]]. Orquestração da publicação → [[06-infra-n8n-servidor]].
Guardrail antes de publicar → [[03-melhorias-telegram]].
