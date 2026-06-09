---
projeto: canal-dark
tipo: command-center
atualizado: 2026-06-08
---

# 🛰️ Centro de Comando — Canal Dark

> **Este chat é a plataforma de comando.** É daqui que a gente decide o quê fazer, e
> manda cada frente de trabalho pro seu **canal** específico. Cada canal abaixo é uma
> conversa separada, com contexto próprio carregado — pra não misturar assunto nem
> perder histórico.

## Como funciona (workflow dos canais)

1. **Comando** (este chat / `_COMANDO.md`): decisão, priorização, visão geral. Não implementa — direciona.
2. **Canais** (`canais/0X-*.md`): cada um é o briefing de uma conversa-tema. Pra trabalhar num tema:
   - Abra o Claude Code **nesta pasta** (`C:\Users\aless\canal-dark`).
   - Comece um chat e diga: **"carregar `canais/03-melhorias-telegram.md` e seguir"**.
   - O Claude lê o briefing (estado atual + backlog + conhecimento) e já entra no contexto certo.
3. Ao terminar algo num canal, **atualize o `## Estado atual` e o `## Backlog` daquele arquivo** — é a memória viva do tema.

## Canais

| # | Canal | Pra quê |
|---|-------|---------|
| 00 | [Pesquisa](00-pesquisa.md) | tendências, nichos, concorrentes, fontes de imagem/áudio, APIs grátis |
| 01 | [Melhorias de Vídeo](01-melhorias-video.md) | legenda, b-roll, montagem FFmpeg, voz, qualidade do `short_factory.py` |
| 02 | [Melhorias de Prompt](02-melhorias-prompt.md) | system prompts do roteirista/trend/guardrail, visual_context, voz por nicho |
| 03 | [Melhorias do Telegram](03-melhorias-telegram.md) | bot: status ao vivo, botões, envio de referências, fila, async |
| 04 | [Nicho (decidido)](04-nicho-decisao.md) | persona/voz por canal — 3 nichos rodando em paralelo |
| 05 | [Publicação & Distribuição](05-publicacao-distribuicao.md) | Postiz, contas, metadata, política de plataforma, AI disclosure |
| 06 | [Infra · n8n · Servidor](06-infra-n8n-servidor.md) | n8n MVP, orquestração, VPS 24/7, automação |
| 07 | [ClipRadar · Trends · ML](07-clipradar-trends.md) | scanner de tendências + analisador de cortes (futuro ML) |

## Regra de sincronização (vale pra TUDO daqui)

Todo artefato do Canal Dark vive em **3 lugares** e os 3 ficam em dia:
1. **Repo que roda**: `C:\Users\aless\canal-dark` (fonte).
2. **Obsidian**: `C:\Users\aless\obsidian-vault-1\Canal Dark\`. ⚠️ o vault é PÚBLICO no GitHub → NUNCA escrever segredo (chave/token/chave privada) ali.
3. **Cópia navegável**: `C:\Users\aless\OneDrive\Desktop\canal-dark` (sem `.env`/`.venv`/`out`).

## Snapshot do projeto (2026-05-30)

- **Pipeline local FUNCIONA**: `short_factory.py` (roteiro→voz→b-roll→legenda→mp4 9:16 + metadata).
  Já tem `SUB_POS` (lower/center), `SUB_STYLE` (clean/punchy), `REF_DIR` (imagens forçadas),
  b-roll por IA grátis (Pollinations) e Pexels com scoring/veto/dedup.
- **Bot Telegram** (`telegram_bot.py`): só `/gerar`, `/start`, `/ajuda`. **Trava** durante a geração
  (subprocess síncrono no loop) → precisa virar async antes de status/botões. → canal **03**.
- **n8n MVP 2.0**: importado e inativo, em modo simulação. → canal **06**.
- **Nicho**: DECIDIDO 31/05 — 3 canais em paralelo (true-crime / conspiracy / one-piece). → canal **04**.
- **Servidor 24/7**: rota Oracle **ENCERRADA** (out of capacity; auto-retry morto 08/06); servidor = **Hetzner** contratado. Local segue como base de trabalho. → canal **06**.
- **Roteiro de teste**: `roteiros/suzane.json` (true-crime, caso real — exemplo do trade-off de copyright).

## Risco #1 (sempre na mesa)

Voz IA + b-roll automático + Shorts em série = perfil "inauthentic content" que o YouTube
desmonetizou em massa (jan/2026). **O que salva:** roteiro ÚNICO revisado por humano + persona
nomeada + variação. Qualidade do roteiro = mecanismo de sobrevivência. Toda imagem real (julgamento,
painel de mangá) entra com a lente de copyright/Content ID.
