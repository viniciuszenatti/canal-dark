# CLAUDE.md — Canal Dark

> Este arquivo é o "manual de operação" do Claude para este projeto. Ele é carregado
> automaticamente quando você abre o Claude Code dentro de `C:\Users\aless\canal-dark\`.
> Migrado do chat "canal" (que rodava dentro do projeto S4S) em 2026-05-30 para separar
> definitivamente este projeto pessoal do trabalho. Histórico completo em
> [docs/historico-chat-canal.md](docs/historico-chat-canal.md).

## Quem sou eu / escopo

Projeto **PESSOAL do Vinicius Zenatti**. **Sem nenhuma relação com CRM / S4S / Staff4Solutions.**
Não misturar com os outros projetos da máquina. Canal **faceless de YouTube Shorts narrados**:
roteiro próprio (teoria/curiosidade/fatos de um nicho) escrito por **agente de IA + revisão humana**,
narração por **voz de IA**, **b-roll** de fundo + **legenda automática**, publicado em YouTube Shorts +
TikTok + Reels de forma semi-automática. Stack 100% grátis no MVP.

## ⚖️ Regras de interação (valem sempre)

1. **TER SENSO CRÍTICO e ser proativo.** Não concordar/obedecer só porque o Vinicius falou. Se a ideia,
   o raciocínio ou o pedido dele estiver errado, frágil ou tiver um trade-off escondido, **apontar isso
   claramente** — inclusive contrariando a linha dele. Destrinchar as ideias, não bajular.
2. **Explicar do jeito mais simples possível.** Linguagem direta, sem jargão desnecessário; quando usar
   um termo técnico, explicar em uma linha.

## 📁 Onde as coisas vivem (regra de sincronização)

O trabalho vive em 3 lugares e **os espelhos devem ficar sempre atualizados**:

1. **Código que RODA** (fonte de trabalho): `C:\Users\aless\canal-dark\` — home, sem espaço, FORA do
   OneDrive. É onde o `.venv` e os scripts executam.
2. **Docs no Obsidian**: `C:\Users\aless\obsidian-vault-1\Canal Dark\` — notas `.md`/`.txt` com frontmatter
   `projeto: canal-dark` e wikilink pra `[[Canal Dark — MOC]]`. ⚠️ **O vault tem remote PÚBLICO no GitHub
   e faz auto-commit** — NUNCA escrever segredo (chave/token/chave privada) em arquivo do vault.
3. **Cópia navegável**: `C:\Users\aless\OneDrive\Desktop\canal-dark\` — cópia que o Vinicius abre na Área de
   Trabalho. Atualizar via `robocopy` excluindo segredos/pesados: `/XF .env /XD .venv out __pycache__`.

**Cuidados:** NUNCA copiar `.env`, `.venv`, `out/` ou temp pro Desktop/OneDrive (segredo na nuvem = ruim;
venv é pesado e regenerável). O código não fica direto no OneDrive porque a sincronização do venv (milhares
de arquivos) trava/conflita.

## 🔒 Segredos (AÇÃO PENDENTE de segurança)

`.env` é gitignored e contém as chaves reais — nunca versionar, nunca colar em doc, nunca mandar pro OneDrive.

⚠️ **Várias chaves foram coladas no chat e DEVEM SER REVOGADAS/ROTACIONADAS pelo Vinicius:** a API key do
n8n (hml-editor.staff4solutions.com.br), a API key da Oracle Cloud (OCI), o token do bot Telegram, e as
chaves Gemini/Pexels/YouTube. O histórico em `docs/` já foi redigido (segredos removidos), mas os transcripts
crus do Claude (`.claude/projects/.../*.jsonl`) ainda contêm as chaves — por isso a recomendação é rotacionar.

## 🎯 Decisões travadas (sessão 2026-05-29)

- **Mercado**: inglês / global. **Formato**: só Shorts verticais 9:16, até ~90s.
- **Modelo**: faceless narrado — roteiro próprio ÚNICO (agente pesquisa+rascunha, humano revisa/ajusta) +
  voz de IA + b-roll + legenda. **NÃO é clipping, NÃO é reupload.** (O `legacy/clip_engine.py` é o modelo
  antigo de clipping e **não serve** — foi reescrito como `short_factory.py`.)
- **Voz**: IA. Grátis = **Edge-TTS** (Microsoft, sem chave) ou Piper local. Paga = ElevenLabs (só ao escalar).
- **B-roll**: a decidir junto com o nicho. Suporta IA-gerado e/ou bancos grátis (Pexels/Pixabay). Manter
  fonte LIMPA (sem IP de terceiros) pra evitar Content ID.
- **Distribuição**: Postiz self-hosted (grátis, evita app review do TikTok). **Orquestração**: n8n self-hosted.
- **LLM grátis** (roteirista/trend/guardrail): Gemini (e MetaGPT já instalado em `C:\Users\aless\metagpt-pm`).
- **Semi-auto**: 2 checkpoints humanos via Telegram → (1) revisar o roteiro, (2) guardrail de risco antes de postar.
- **Escala mês 1**: 1 canal, ~1 short/dia, conta própria.

### 🚨 RISCO #1 (crítico, sustenta o modelo)
Voz de IA + b-roll automático + Shorts em série = EXATAMENTE o perfil "inauthentic content" que o YouTube
**desmonetizou em massa (jan/2026)**. O que salva: **roteiros ÚNICOS revisados pelo humano** + persona
nomeada + variação de formato. Qualidade do roteiro = mecanismo de sobrevivência, não luxo. Por isso a
revisão humana do roteiro é **obrigatória, não opcional**.

## 🧩 Nicho (gargalo principal — ainda NÃO definido)

3 candidatos, cada um com base de conhecimento completa em `nichos/` (5 docs cada: 01-conteúdo+pesquisa /
02-roteiro+linguagem / 03-riscos / 04-roteiro-de-pesquisa / 05-linguagem-e-referências) + `00-tecnicas-shorts-comum.md`:
- **true-crimes** — voz respeitosa sem gore (ref. MrBallen/JCS; Charley Project/Doe Network/NamUs). Mais limpo p/ monetizar.
- **conspiracy-theories** — empurrar pra "unexplained mysteries" honesto (ref. LEMMiNO/Barely Sociable/Nexpo; Snopes/SIFT). Limpo.
- **one-piece-theories-and-stories** — MAIOR risco de copyright (Content ID Toei/Shueisha → tende a alcance/TikTok, não AdSense). Ref. Ohara/GrandLineReview; onepiece.fandom.com.

## ✅ Status atual (2026-05-30)

- Pipeline **LOCAL funciona**: `short_factory.py` gera o vídeo ponta-a-ponta (9:16, voz Edge-TTS + b-roll
  Pexels + legenda). Vários testes em `video testes/`. Bot Telegram (@CanalDark_bot) já gera vídeo sob comando.
- n8n: workflow "Canal Dark — MVP Shorts Narrados" **importado** no n8n de HML da S4S (id `dz3ehGcD3srs7vtQ`,
  **inativo**). ⚠️ RESTRIÇÃO DURA nesse n8n: **só ADICIONAR, nunca alterar/excluir nada existente.** Não roda
  lá ainda (sem Python/FFmpeg/chaves no servidor).
- Servidor 24/7: **EM ESPERA.** Oracle Cloud free (ARM A1, SP) ficou "out of capacity" a noite toda; rota
  pausada. Se retomar, recomendado Hetzner (~R$25/mês) em vez de brigar com a Oracle grátis. Por enquanto: LOCAL.

### Próximas tarefas
- [ ] **DEFINIR O NICHO** (destrava fonte de b-roll, tom e tudo o mais) — gargalo nº 1.
- [ ] Testar voz Edge-TTS (grátis) vs ElevenLabs e ouvir a diferença.
- [ ] Subir Docker + Postiz e ligar o workflow ponta-a-ponta (publicação).
- [ ] Melhorar b-roll contextual (ver "qualidade de imagem" abaixo) e legendas (fonte melhor, menor).

## ⚙️ Armadilhas técnicas do pipeline no Windows (não-óbvias)

- **Edge-TTS**: usar **≥ 7.2.x** (a 6.1.19 dá HTTP 403). Na 7.x o `SubMaker.get_srt()` gera SRT **vazio sem
  erro** → montar a legenda direto dos word-timestamps, com proteção anti-vazio. Voz é grátis e sem chave.
- **FFmpeg filtro `subtitles`**: o `C:` do caminho **quebra o parser**. Rodar o FFmpeg com **cwd na pasta do
  .srt** e referenciar **só o nome do arquivo** (sem caminho absoluto).
- **B-roll do Pexels tem fps variável**. Concatenar com `-c copy` embaralha os tempos → **re-encodar cada
  clipe pra 30fps constante + pixfmt uniforme ANTES do concat**.
- **`load_dotenv()`**: garantir a chamada no início do script (mesmo com `python-dotenv` instalado).
- **n8n nesta máquina**: rodar **nativo via `npx n8n`** (Docker pode não estar instalado). Nativo é melhor
  aqui porque o n8n precisa chamar o **Python/FFmpeg do host**.
- **Qualidade do b-roll** (bug conhecido): o `short_factory.py` pegava `videos[0]` cego do Pexels e tinha
  fallback atmosférico ("moody fog night") que trazia imagem sem contexto (ex.: lago de peixes no vídeo da
  Suzane). Correção em andamento: roteirista emite um `visual_context` global (bíblia visual) + queries de
  2–4 keywords coerentes; seleção do Pexels deixa de ser cega. Ver `docs/historico-chat-canal.md`.

## 🗺️ Ponteiros

- **Repositório (privado):** github.com/viniciuszenatti/canal-dark — `git`/`origin` já configurados. `.env` é gitignored (nunca versionar segredo). `clipradar/` é repo separado do Caio, ignorado aqui.
- **🛰️ Centro de comando: [canais/_COMANDO.md](canais/_COMANDO.md)** — índice dos 8 "canais" de conversa por
  tema (00 pesquisa · 01 vídeo · 02 prompt · 03 telegram · 04 nicho · 05 publicação · 06 infra/n8n · 07 clipradar).
  **Ao abrir uma sessão nova aqui, comece carregando este arquivo.** Cada canal é um chat-tema separado.
- Visão geral e setup: [README.md](README.md)
- Histórico completo do projeto (redigido): [docs/historico-chat-canal.md](docs/historico-chat-canal.md)
- Só os pedidos do Vinicius: [docs/historico-pedidos.md](docs/historico-pedidos.md)
- Bases de nicho: [nichos/](nichos/) · técnicas comuns: [nichos/00-tecnicas-shorts-comum.md](nichos/00-tecnicas-shorts-comum.md)
- Prompts dos agentes: [prompts/](prompts/) (roteirista, trend_scout, guardrail)
- MOC no Obsidian: `obsidian-vault-1\Canal Dark\Canal Dark — MOC.md`
- Linha paralela (cortes de podcast, do amigo Caio): [clipradar/](clipradar/) — repo github.com/Caiorasuckkk/clipradar
