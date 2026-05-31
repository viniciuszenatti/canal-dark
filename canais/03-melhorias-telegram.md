---
projeto: canal-dark
tipo: canal
tema: melhorias-telegram
atualizado: 2026-05-30
---

# 📲 Canal 03 — Melhorias do Telegram

> **Objetivo:** transformar o bot no painel de controle de TODO o pipeline — status ao vivo,
> opções, aprovação por botão e envio de referências. O Telegram é o controle remoto do canal.

**Quando usar:** qualquer coisa do `telegram_bot.py` — comandos, status, botões, fila, envio de mídia.

## Estado atual (`telegram_bot.py`)

- Long-polling (`getUpdates`), bot privado (só responde ao `TELEGRAM_CHAT_ID`).
- Comandos: `/gerar <nicho> <tema>`, `/start`, `/ajuda`. Aliases de nicho (crimes/misterios/onepiece).
- Manda o `short.mp4` pronto + caption do `metadata.json`.

## ⚠️ Pré-requisito (bug que bloqueia tudo)

A geração roda **síncrona dentro do loop de polling** (`subprocess.run` em `handle`). Enquanto gera
(2-4 min), o bot fica **surdo** — não responde nada. **Sem tornar a geração assíncrona (thread/processo),
`/status` e `/cancel` nascem mortos.** Esta é a primeira tarefa.

## Backlog

- [ ] **Async**: rodar a geração em thread/processo; loop de polling segue respondendo.
- [ ] **Progresso ao vivo**: editar UMA mensagem com as etapas (✅ roteiro → ✅ voz → ⏳ b-roll → 🎬 montagem)
      via `editMessageText`. Hoje fica mudo.
- [ ] **`/status`** (job atual + fila) e **`/cancel`** (aborta).
- [ ] **Checkpoints humanos com botões inline** (os 2 do plano):
      (a) aprovar/regerar ROTEIRO antes de gastar voz/render (já existe `--script-only` no factory!);
      (b) guardrail "publicar / descartar" no fim.
- [ ] **Enviar REFERÊNCIAS**: aceitar foto/imagem ou link → salvar em `out/refs/<topic>/` e exportar
      `REF_DIR` pro `short_factory.py` (a feature `REF_DIR` JÁ existe — só falta o bot alimentá-la).
      Ex.: mando a foto do julgamento da Suzane → entra no vídeo. Imagem de web aberta exige OK humano.
- [ ] **Fila simples** (1 job por vez; recusa concorrente com aviso).
- [ ] `/ajuda` e `/nichos` atualizados.

## Amarração com o resto

- `--script-only` do `short_factory.py` já entrega só o roteiro (pro checkpoint #1). → canal **02**.
- `REF_DIR` já consome imagens forçadas. → canal **01**.
- No n8n, os checkpoints são nós Telegram com Wait → evoluir pra webhook + botões. → canal **06**.

## Links

[[01-melhorias-video]] · [[02-melhorias-prompt]] · [[06-infra-n8n-servidor]] · [[00-pesquisa]]
