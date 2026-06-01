---
name: cd-short
description: Cria um YouTube Short do Canal Dark ponta-a-ponta a partir de um tema, com os 2 checkpoints humanos obrigatórios. Use quando o Vinicius disser "faz um short sobre X", "gera um vídeo de Y", "novo short". Orquestra: (ideia→) roteiro→REVISÃO HUMANA→geração do vídeo→guardrail→REVISÃO HUMANA→pronto pra publicar. NÃO pula os checkpoints (eles são o que protege a monetização).
tools: Read, Edit, Write, Grep, Glob, Bash
---

# Canal Dark — Novo Short (ponta-a-ponta)

Envelopa o fluxo semi-automático do canal. O modelo é **semi-auto com 2 checkpoints humanos** — eles NÃO podem ser pulados: são o que mantém o canal monetizável (Risco #1: conteúdo de IA em série desmonetizado; o que salva é roteiro único + revisão humana).

## Pré-condições
- Tema definido (argumento). Se o nicho estiver setado (`CANAL_DARK_NICHE`), o playbook do nicho entra no roteiro.
- Chaves no `.env` (GEMINI_API_KEY pra gerar roteiro; PEXELS_API_KEY se b-roll = pexels). NUNCA imprima/cole segredo.

## Workflow
1. **Roteiro** — gere o rascunho. Delegue ao `cd-melhorias-roteiro` ou rode `python short_factory.py --topic "<tema>"` só pra obter o JSON (não publique direto). Salve em `roteiros/<slug>.json`.
2. **⛔ CHECKPOINT #1 — REVISÃO HUMANA DO ROTEIRO (obrigatório).** Rode o checklist (use a skill `cd-revisar-roteiro`): hook ≤3s? ângulo não-genérico? 180–220 palavras? `[FACT-CHECK]` resolvidos? `visual_context` coerente? **PARE e mostre o roteiro pro Vinicius aprovar/editar antes de gerar o vídeo.** Não prossiga sem o OK dele.
3. **Geração** — com o JSON aprovado: `python short_factory.py --script-file ./roteiros/<slug>.json --out-dir ./out`. Em caso de erro, acione `cd-desenvolvimento`/`cd-testes` (armadilhas Windows no CLAUDE.md).
4. **Validação técnica** — confira 9:16, duração, legenda sincronizada, b-roll coerente (skill/agent `cd-testes`).
5. **⛔ CHECKPOINT #2 — GUARDRAIL + REVISÃO (obrigatório).** Acione `cd-publicacao`: AI disclosure marcado? hook forte? política de plataforma ok? (Copyright NÃO é mais critério de bloqueio — decisão do Vinicius.) Risco médio/alto → mostrar pro Vinicius antes de publicar.
6. **Entrega** — relate: caminho do .mp4, título/hashtags, e os labels de IA a aplicar por plataforma. NÃO publique automaticamente — publicação é decisão dele (ou via `cd-publicacao`/Postiz quando ligado).

## Regras
- Os 2 checkpoints são paradas DURAS — pergunte e espere o humano. Semi-auto ≠ auto.
- Sincronize artefatos (roteiro/vídeo) conforme a regra do projeto; segredos nunca saem do `.env`.
