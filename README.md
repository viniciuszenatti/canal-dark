# Canal Dark — Shorts Narrados (Faceless)

Pipeline semi-automático para criar e publicar YouTube Shorts faceless: **roteiro original gerado por IA + revisão humana → voz narrada → b-roll de fundo → legenda queimada → publicação automática** no YouTube Shorts, TikTok e Instagram Reels.

Formato: vídeos verticais 9:16, até ~90 segundos. Mercado global em inglês. Stack 100% gratuita.

> **AVISO — Risco "Inauthentic Content"**: canais faceless com voz de IA correm risco de ban se o conteúdo for genérico demais ou disfarçar que é IA. Mitigue com: (1) roteiro único com ângulo original — não genérico; (2) persona consistente do canal; (3) variação entre vídeos; (4) sempre marque como "conteúdo de IA" em todas as plataformas (ver instruções de compliance abaixo). O pipeline foi desenhado para forçar revisão humana do roteiro antes da produção.

---

## Pipeline

```
Schedule Trigger (08h)
        ↓
  Trend Scout (Gemini)        → gera 5 ideias de tópicos
        ↓
  Roteirista (Gemini)         → gera roteiro JSON: title/hook/lines/cta/hashtags
        ↓
  ═══ CHECKPOINT #1 ══════════════════════════════════════════
  Telegram: roteiro enviado para revisão humana
  Humano: edita se necessário, aprova para produção
  ════════════════════════════════════════════════════════════
        ↓
  short_factory.py
    ├── (b) edge-tts      → narração .mp3 + SRT com timestamps por palavra
    ├── (c) Pexels API    → b-roll vertical por query de cada linha
    └── (d) FFmpeg        → monta 9:16 1080×1920: b-roll + voz + legenda queimada
        ↓
  Guardrail (Gemini)          → avalia: b-roll license? AI disclosure? hook forte? fact-check?
        ↓
  ═══ CHECKPOINT #2 (se risco) ════════════════════════════════
  Telegram: short enviado para revisão (apenas se guardrail flagou)
  Humano: aprova ou rejeita
  ════════════════════════════════════════════════════════════
        ↓
  Postiz                      → publica YouTube Shorts + TikTok + Reels
        ↓
  Google Sheets               → log: título, hook, risco, status, hashtags
```

---

## Setup Passo a Passo

### Pré-requisitos

- Python 3.10+
- Docker Desktop (para n8n e Postiz)
- Windows 10/11, macOS ou Linux

---

### Passo 1 — Instalar FFmpeg

FFmpeg é o motor de edição de vídeo. **Não está no pip** — é um binário do sistema.

**Windows** (recomendado):
```powershell
winget install ffmpeg
```

Ou baixe manualmente em https://ffmpeg.org/download.html, extraia e adicione a pasta `bin/` ao PATH.

Verifique:
```bash
ffmpeg -version
```

---

### Passo 2 — Instalar Dependências Python

```bash
cd C:\Users\aless\canal-dark

# Crie um ambiente virtual
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux

# Instale as dependências
pip install -r requirements.txt
```

Dependências principais:
- **edge-tts**: TTS gratuito sem chave de API (usa infraestrutura Microsoft Edge)
- **requests**: download de b-roll do Pexels
- **google-generativeai**: SDK Gemini para geração de roteiro e guardrail
- **python-dotenv**: carrega `.env` automaticamente em dev local

---

### Passo 3 — Gerar Chave da API Gemini (grátis)

1. Acesse https://aistudio.google.com/app/apikey
2. Clique em **Create API Key**
3. Copie e guarde a chave

```powershell
# Windows (sessão atual)
$env:GEMINI_API_KEY = "AIza..."

# Ou adicione ao .env (recomendado):
# GEMINI_API_KEY=AIza...
```

**Limites gratuitos (maio 2026):** gemini-2.5-flash-lite: 1.500 req/dia, 15 req/min. Suficiente para 1 short/dia com margem.

---

### Passo 4 — Obter Chave da API Pexels (grátis)

1. Acesse https://www.pexels.com/api/ e crie uma conta
2. Acesse **Your API Key** e copie a chave
3. Uso comercial permitido gratuitamente; limite: 200 req/hora

```powershell
$env:PEXELS_API_KEY = "sua-chave-aqui"
```

---

### Passo 5 — Criar Telegram Bot (para checkpoints)

1. Abra o Telegram, converse com **@BotFather**
2. Envie `/newbot`, escolha nome e username
3. Copie o token (formato: `123456:AAF...`)
4. Para descobrir seu CHAT_ID:
   - Envie uma mensagem ao bot
   - Acesse: `https://api.telegram.org/bot<TOKEN>/getUpdates`
   - O campo `chat.id` aparece no JSON da resposta

```powershell
$env:TELEGRAM_BOT_TOKEN = "123456:AAF..."
$env:TELEGRAM_CHAT_ID   = "123456789"
```

---

### Passo 6 — Subir n8n via Docker

```bash
docker volume create n8n_data

docker run -d \
  --name canal-dark-n8n \
  -p 5678:5678 \
  -v n8n_data:/home/node/.n8n \
  -v C:/Users/aless/canal-dark:/app/canal-dark \
  -e N8N_DEFAULT_TIMEZONE=America/Sao_Paulo \
  -e N8N_BASIC_AUTH_ACTIVE=true \
  -e N8N_BASIC_AUTH_USER=admin \
  -e N8N_BASIC_AUTH_PASSWORD=mude-esta-senha \
  -e GEMINI_API_KEY=${GEMINI_API_KEY} \
  -e PEXELS_API_KEY=${PEXELS_API_KEY} \
  -e TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN} \
  -e TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID} \
  -e POSTIZ_URL=http://host.docker.internal:3000 \
  -e POSTIZ_API_KEY=${POSTIZ_API_KEY} \
  -e CANAL_DARK_PATH=/app/canal-dark/short_factory.py \
  -e CANAL_DARK_OUTPUT_DIR=/app/canal-dark/out \
  -e TTS_VOICE=en-US-AriaNeural \
  n8nio/n8n:latest
```

Acesse: http://localhost:5678

O volume `-v C:/Users/aless/canal-dark:/app/canal-dark` monta o projeto dentro do n8n. O Python + deps precisam estar instalados dentro do container ou via imagem customizada.

---

### Passo 7 — Subir Postiz via Docker

```bash
git clone https://github.com/gitroomhq/postiz-app.git
cd postiz-app
cp .env.example .env
# Edite o .env com credenciais de cada plataforma
docker compose up -d
```

Acesse: http://localhost:3000

No painel do Postiz:
1. **Settings > Integrations**: conecte YouTube, TikTok e Instagram
2. **Settings > API Keys**: gere uma chave para o n8n

---

### Passo 8 — Importar o Workflow n8n

1. Acesse http://localhost:5678
2. **Workflows > Import from File**
3. Selecione `n8n/workflow-mvp.json`
4. Configure as credenciais nos nós:
   - **Telegram**: credencial com `TELEGRAM_BOT_TOKEN`
   - **Google Sheets**: Service Account (crie em Google Cloud Console > IAM > Service Accounts)
   - **Postiz**: URL e chave de API
5. Ative o workflow com o toggle superior direito

---

### Passo 9 — Testar o short_factory.py

```bash
# Ative o venv primeiro
.venv\Scripts\activate

# Exemplo com --topic (gera roteiro via Gemini, bom para teste):
python short_factory.py --topic "Why ancient Stoics slept on the floor" --out-dir ./out

# Exemplo com roteiro já aprovado (fluxo de produção):
python short_factory.py --script-file ./out/script_draft.json --out-dir ./out

# Com voz masculina e música de fundo:
python short_factory.py --topic "..." --tts-voice en-US-GuyNeural --music ./assets/bg.mp3

# Ver vozes disponíveis do edge-tts:
python -m edge_tts --list-voices
```

O pipeline irá:
1. Gerar (ou carregar) o roteiro JSON
2. Sintetizar narração via edge-tts (sem chave de API)
3. Gerar SRT com timestamps por palavra
4. Baixar b-roll vertical do Pexels para cada linha
5. Montar o vídeo 9:16 com FFmpeg: b-roll + voz + legenda queimada
6. Salvar `short.mp4` + `metadata.json` em `./out/`

---

## Estrutura do Projeto

```
canal-dark/
├── short_factory.py        # Pipeline principal: roteiro → voz → b-roll → montagem
├── requirements.txt        # Dependências Python (edge-tts, requests, google-generativeai)
├── .env.example            # Template de variáveis de ambiente
├── README.md               # Este arquivo
├── prompts/
│   ├── roteirista.md       # System prompt para geração de roteiro via Gemini
│   ├── trend_scout.md      # Prompt para geração de ideias de tópicos
│   └── guardrail.md        # Prompt para avaliação de risco pré-publicação
├── n8n/
│   └── workflow-mvp.json   # Workflow n8n importável (v2 — Shorts narrados)
├── out/                    # Saída dos shorts gerados (criado automaticamente)
│   ├── short.mp4
│   ├── metadata.json
│   ├── script_draft.json   # Roteiro gerado (modo --topic) — revise antes de usar
│   └── _work/              # Arquivos intermediários (b-roll, narração, srt)
└── legacy/
    └── clip_engine.py      # LEGADO — modelo antigo de clipping CC (não usar)
```

---

## Compliance e Disclosure de IA

### OBRIGATORIO em todas as plataformas

O canal usa voz de IA e b-roll de stock. Isso exige disclosure explícito:

**YouTube Studio** (ao publicar):
- Marque **"Altered or synthetic content"** na opção de disclosure do upload
- Inclua na descrição: `This video was created with AI assistance (voice & visuals).`

**TikTok**:
- Ative o label **"AI Generated"** nas configurações do vídeo ao publicar

**Instagram Reels**:
- Ative a label **"Created with AI"** nas configurações de publicação

O campo `metadata.json > youtube.ai_disclosure_required: true` lembra você disso. O Postiz pode ser configurado para incluir a descrição com o texto de disclosure automaticamente.

### B-roll Pexels

Todos os vídeos do Pexels são licenciados para uso comercial gratuito, incluindo em conteúdo de IA, **sem necessidade de atribuição** (mas atribuição é boa prática). Veja: https://www.pexels.com/license/

### Risco "Inauthentic Content"

Canais faceless de IA correm risco de penalização se:
- O conteúdo for excessivamente genérico (passível de ser de qualquer canal)
- A voz de IA for usada sem disclosure
- O mesmo roteiro for reutilizado com pequenas variações

Mitigações implementadas no pipeline:
1. O roteiro exige ângulo específico e original (anti-genérico, via system prompt)
2. Checkpoint humano #1 revisa o roteiro antes da produção
3. O guardrail avalia o hook_strength (score baixo bloqueia publicação)
4. O metadata.json inclui disclosure de IA para todas as plataformas

---

## Próximos Passos (pós-MVP)

1. **Ativar código real no n8n**: descomentar os blocos `execSync` e chamadas Gemini nos Code Nodes
2. **Webhook de aprovação**: substituir os Wait Nodes por Wait on Webhook com botões inline no Telegram
3. **Guardrail real**: conectar o prompt de `prompts/guardrail.md` ao Gemini via HTTP Request no n8n
4. **ElevenLabs**: testar `--tts-engine elevenlabs` com créditos grátis para comparar qualidade de voz
5. **B-roll AI**: implementar o stub `fetch_broll(..., source="ai")` com RunwayML ou Kling
6. **Múltiplos nichos**: parametrizar `NICHES_THIS_WEEK` no Trend Scout para rotacionar temas
7. **Analytics**: monitorar views/retention dos primeiros 10 shorts para ajustar nicho e formato
