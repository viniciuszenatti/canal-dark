# 📋 Solicitação à Equipe de Infraestrutura — Deploy "Canal Dark" (Hetzner compartilhado)

> **Solicitante:** Vinicius Zenatti · **Projeto:** Canal Dark (pessoal) · **Data:** 2026-05-31
> **Servidor alvo:** VPS Hetzner **já em uso** — Docker + Portainer + Traefik (proxy reverso) já instalados,
> com outros containers em produção.

---

## 🚀 Resumo (leiam isto primeiro)

Preciso subir um projeto pessoal meu nesse VPS, **sem encostar em nada que já roda** (é add-only). Em resumo:

- **O que sobe:** 1 stack isolada no Portainer chamada `canal-dark`, em rede própria, containers `canaldark-*`.
- **Fase 1 (prioridade):** 1 container custom `canaldark-n8n` (n8n + Python + FFmpeg + meu código), publicado pelo
  Traefik **via labels** em `n8n.canal-dark`. Nada instalado no host.
- **Fase 2 (depois, se a RAM permitir):** Postiz + Postgres + Redis em `postiz.canal-dark`.
- **De vocês eu preciso de 4 respostas:** (1) rede do Traefik, (2) entrypoint HTTPS + certresolver, (3) ok nos
  subdomínios `n8n.canal-dark`/`postiz.canal-dark`, (4) `nproc` + `free -h` + `df -h /` do host. → seção "❓ Informações".
- **Eu entrego à parte (canal seguro):** a chave privada de leitura do repo + os valores do `.env`. → seção "📦 Pacote".
- **Código:** repo privado `github.com/viniciuszenatti/canal-dark`, com deploy key **read-only** já criada.

O resto do documento detalha cada ponto, com Dockerfile (Anexo A) e a stack pronta pro Portainer (Anexo B).

---

## 📦 Pacote que o Vinicius entrega à parte (por canal seguro, fora deste doc)

Estes dois itens **não vão neste arquivo** por serem segredos — chegam por mensagem segura/cofre:

1. **Chave privada de leitura** (`canaldark_deploy_ro`) → vai em `/opt/canal-dark/canaldark_deploy_ro` no host (`chmod 600`).
2. **Valores do `.env`** → as chaves de API listadas na seção "🔐 Segredos / variáveis de ambiente".

Sem esses dois, a fase 1 **builda mas não roda** (o n8n sobe, mas o pipeline não chama as APIs). Com eles, está completo.

---

## ⚠️ Restrições duras (ler antes de tudo)

Este servidor é **compartilhado** e já tem serviços em produção. A regra é **ADD-ONLY**:

1. **NÃO** parar, alterar, recriar ou remover nenhum container, rede, volume ou config que **já existe**.
2. **NÃO** alterar a configuração do **Traefik** (arquivo estático/dinâmico). Vamos publicar **apenas via labels**
   nos nossos próprios containers — o jeito nativo do Traefik, sem tocar no resto.
3. Tudo do Canal Dark entra como **uma stack isolada no Portainer** (nome sugerido: `canal-dark`), em
   **rede Docker própria**, com containers prefixados `canaldark-*` — pra ser removível com 1 clique sem afetar ninguém.
4. **NÃO** instalar nada no host (Python, FFmpeg etc.). Tudo roda **dentro de container** (imagem custom), pra não
   contaminar o ambiente dos outros serviços.

---

## 🎯 Objetivo (o que é o projeto, em 3 linhas)

Canal faceless de YouTube Shorts: um agente de IA escreve um roteiro, uma voz de IA narra, o sistema monta um
vídeo vertical 9:16 (~90s) com b-roll + legenda e publica em YouTube/TikTok/Reels — **com 2 aprovações humanas
via Telegram** no meio do caminho. Hoje roda **local** na máquina do Vinicius; queremos rodá-lo **24/7** no servidor.

**Componente que faz o trabalho pesado:** um script Python (`short_factory.py`) que chama **FFmpeg** + `edge-tts`
(voz grátis, sem chave) + APIs (Gemini, Pexels). **Orquestrador:** **n8n** (dispara, espera aprovação, chama o
script, publica). **Publicador (fase 2):** **Postiz**.

---

## 🧱 Arquitetura solicitada

Pra não instalar Python/FFmpeg no host, a fábrica de vídeo roda **dentro do próprio container do n8n** (imagem
custom = n8n + Python + FFmpeg + o código). O n8n usa o nó *Execute Command* pra rodar o script localmente.

```
internet → Traefik (já existe) → n8n.canal-dark     → container canaldark-n8n  (orquestra + gera o vídeo)
                                → postiz.canal-dark  → container canaldark-postiz (fase 2 — publica)
```

### Fase 1 — Pipeline núcleo (prioridade)
| Container | Imagem | Função | Ingress Traefik |
|---|---|---|---|
| `canaldark-n8n` | **custom** (ver Anexo A) | n8n + Python 3.11 + FFmpeg + repo. Orquestra e **gera o vídeo dentro de si**. | `n8n.canal-dark` (também recebe o webhook do Telegram) |

Volumes: `canaldark-n8n-data` (persistência do n8n) · `canaldark-out` (vídeos gerados; compartilhado com o Postiz na fase 2).

### Fase 2 — Publicação (fazer só depois da fase 1 validada e **se a RAM permitir**)
| Container | Imagem | Função | Ingress |
|---|---|---|---|
| `canaldark-postiz` | `ghcr.io/gitroomhq/postiz-app:latest` | posta nas 3 plataformas | `postiz.canal-dark` |
| `canaldark-postiz-pg` | `postgres:16-alpine` | banco do Postiz | interno |
| `canaldark-postiz-redis` | `redis:7-alpine` | fila do Postiz | interno |

> **Crítico:** Postiz + Postgres + Redis comem RAM. Só subir a fase 2 se sobrar folga (ver "Recursos" abaixo).
> Se o servidor estiver apertado, a fase 1 já entrega o vídeo pronto — a publicação pode ficar manual no começo.

---

## 📊 Recursos estimados (pra vocês validarem contra o que sobra)

| Item | Fase 1 (n8n+FFmpeg) | Fase 2 (+Postiz) |
|---|---|---|
| RAM em repouso | ~400–600 MB | +1–1.5 GB |
| RAM em pico (encode de vídeo) | +500 MB–1 GB por ~1–2 min/dia | — |
| CPU | pico curto no encode 1x/dia | baixo |
| Disco | ~2 GB (imagem) + ~200 MB/vídeo no `out` | +1 GB (Postiz/banco) |

**Por favor confirmem:** `nproc`, `free -h`, `df -h /` do host pra dizermos se cabe a fase 2 ou só a fase 1.

---

## ❓ Informações que precisamos de vocês (pra finalizar a stack)

Como vocês conhecem o setup do Traefik daí, precisamos destes valores pra preencher as labels corretamente:

1. **Nome da rede Docker do Traefik** (a que os serviços roteados entram). Ex.: `traefik`, `proxy`, `web`.
   - `docker network ls`
2. **Nome do entrypoint HTTPS** (ex.: `websecure`) e do **certresolver** Let's Encrypt (ex.: `letsencrypt`, `myresolver`).
   - Mais fácil: copiem as **labels de um serviço que já é roteado** → `docker inspect <container> --format '{{json .Config.Labels}}'`
3. **Subdomínios** (DNS apontando pra esse servidor): `n8n.canal-dark` (fase 1) e `postiz.canal-dark` (fase 2).
   - Obs.: se o roteamento exigir um FQDN público com TLD, completem com o domínio-base que vocês gerenciam
     (ex.: `n8n.canal-dark.<domínio-de-vocês>`) — o rótulo do projeto é `canal-dark`.
4. Confirmação dos **recursos** (item acima) pra decidir fase 1 vs fase 1+2.

---

## 🔐 Segredos / variáveis de ambiente

O Canal Dark usa um arquivo `.env` (NÃO versionado). **Os valores das chaves serão entregues pelo Vinicius
por canal seguro — não estão neste documento.** A stack deve consumir via `env_file` ou variáveis do Portainer.

Variáveis necessárias (nomes apenas):

```
GEMINI_API_KEY          # roteiro + guardrail (Google AI Studio)
PEXELS_API_KEY          # b-roll de fundo
TELEGRAM_BOT_TOKEN      # bot dos checkpoints (@CanalDark_bot)
TELEGRAM_CHAT_ID        # chat que recebe roteiro/alertas
POSTIZ_API_KEY          # fase 2
POSTIZ_URL              # fase 2 (ex.: http://canaldark-postiz:3000)
GOOGLE_SHEET_ID         # log (opcional)
TTS_VOICE=en-US-AriaNeural
CANAL_DARK_PATH=/app/canal-dark/short_factory.py
CANAL_DARK_OUTPUT_DIR=/app/canal-dark/out
N8N_DEFAULT_TIMEZONE=America/Sao_Paulo
N8N_HOST=n8n.canal-dark
N8N_PROTOCOL=https
WEBHOOK_URL=https://n8n.canal-dark/
N8N_BASIC_AUTH_ACTIVE=true
N8N_BASIC_AUTH_USER=<definir>
N8N_BASIC_AUTH_PASSWORD=<definir senha forte>
```

> `edge-tts` (voz) e os providers de imagem grátis (`wikimedia`, `pollinations` etc.) **não precisam de chave**.

### Acesso ao código (já provisionado)

- **Repositório (privado):** `git@github.com:viniciuszenatti/canal-dark.git` (clone HTTPS: `https://github.com/viniciuszenatti/canal-dark.git`)
- **Credencial de leitura:** uma **deploy key SSH read-only** (id `153107639`, título `canaldark-infra-readonly`)
  já está anexada ao repo. A **chave privada** correspondente será entregue pelo Vinicius por **canal seguro**
  (não está neste documento). Coloquem em `/opt/canal-dark/canaldark_deploy_ro` no host, `chmod 600`, e o
  `build.ssh` do Anexo B a usa no build (a chave **não** fica gravada na imagem).
- É **read-only** e amarrada **só a este repo** — pode ser revogada a qualquer momento sem afetar a conta do Vinicius.

---

## ✅ Critério de pronto (aceite)

**Fase 1**
- [ ] Stack `canal-dark` criada no Portainer, em rede própria, sem tocar em nada existente.
- [ ] `https://n8n.canal-dark` abre o n8n com HTTPS válido (cadeado) e basic-auth pedindo senha.
- [ ] Dentro do container: `ffmpeg -version` e `/app/venv/bin/python /app/canal-dark/short_factory.py --help` funcionam.
- [ ] Webhook do Telegram acessível em `https://n8n.canal-dark/webhook/...` (teste responde).
- [ ] **Nenhum** container pré-existente foi reiniciado/alterado (conferir `docker ps` antes e depois).

**Fase 2 (se aprovada)**
- [ ] `https://postiz.canal-dark` abre o Postiz com HTTPS.
- [ ] Postiz enxerga o volume `canaldark-out` (acesso aos vídeos gerados).

---

## 🚫 O que NÃO fazer (resumo)

- ❌ Mexer no Traefik, em outros containers, redes ou volumes existentes.
- ❌ Instalar Python/FFmpeg/n8n **no host** — tudo em container.
- ❌ Expor o n8n **sem** basic-auth/HTTPS.
- ❌ Colocar segredos em arquivo versionado ou em log.

---

## 📎 Anexo A — Dockerfile da imagem custom do n8n

```dockerfile
# Base oficial do n8n (Alpine). Adiciona Python + FFmpeg + o código do Canal Dark.
FROM n8nio/n8n:latest

USER root

# FFmpeg (motor de vídeo) + Python + git
RUN apk add --no-cache python3 py3-pip ffmpeg git

# Código do projeto — clone READ-ONLY via deploy key SSH (BuildKit, a chave NÃO fica na imagem).
# Build com BuildKit:  DOCKER_BUILDKIT=1 docker build --ssh canaldark=/caminho/canaldark_deploy_ro ...
# (no compose, ver Anexo B → build.ssh)
WORKDIR /app
RUN --mount=type=ssh,id=canaldark \
    mkdir -p ~/.ssh && ssh-keyscan github.com >> ~/.ssh/known_hosts && \
    git clone git@github.com:viniciuszenatti/canal-dark.git /app/canal-dark

# venv isolado com as dependências Python
RUN python3 -m venv /app/venv \
 && /app/venv/bin/pip install --no-cache-dir -r /app/canal-dark/requirements.txt

# pasta de saída dos vídeos
RUN mkdir -p /app/canal-dark/out && chown -R node:node /app

USER node
```

> O n8n chama o script via nó *Execute Command*: `/app/venv/bin/python /app/canal-dark/short_factory.py ...`

## 📎 Anexo B — Stack do Portainer (docker-compose) — preencher `<<...>>`

```yaml
# Stack: canal-dark  (Portainer > Stacks > Add stack)
services:
  canaldark-n8n:
    build:
      context: .              # pasta com o Dockerfile do Anexo A
      ssh:
        - canaldark=/opt/canal-dark/canaldark_deploy_ro   # chave privada read-only no host (ver "Acesso ao código")
    image: canaldark-n8n:latest
    container_name: canaldark-n8n
    restart: unless-stopped
    env_file: .env
    environment:
      - N8N_HOST=n8n.canal-dark
      - N8N_PROTOCOL=https
      - WEBHOOK_URL=https://n8n.canal-dark/
      - N8N_DEFAULT_TIMEZONE=America/Sao_Paulo
    volumes:
      - canaldark-n8n-data:/home/node/.n8n
      - canaldark-out:/app/canal-dark/out
    networks:
      - <<REDE_DO_TRAEFIK>>      # ex.: traefik / proxy / web
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.canaldark-n8n.rule=Host(`n8n.canal-dark`)"
      - "traefik.http.routers.canaldark-n8n.entrypoints=<<ENTRYPOINT_HTTPS>>"   # ex.: websecure
      - "traefik.http.routers.canaldark-n8n.tls.certresolver=<<CERTRESOLVER>>"  # ex.: letsencrypt
      - "traefik.http.services.canaldark-n8n.loadbalancer.server.port=5678"

volumes:
  canaldark-n8n-data:
  canaldark-out:

networks:
  <<REDE_DO_TRAEFIK>>:
    external: true
```

> A stack da **Fase 2 (Postiz)** será entregue em documento separado, só depois da Fase 1 validada e da
> confirmação de recursos.

---

## 📌 Observação do solicitante (contexto, não bloqueia o deploy)

O **agendamento automático** (postar 1 short/dia sozinho) só será **ligado** depois que o nicho do canal estiver
definido. **Isso não impede a montagem da infra** — podem provisionar tudo da Fase 1 normalmente; o n8n fica
pronto e o workflow é ativado depois. Ou seja: o entregável de vocês é **a casa pronta e segura**, não "no ar postando".
