---
projeto: canal-dark
tipo: canal
tema: pesquisa
atualizado: 2026-05-30
---

# 🔎 Canal 00 — Pesquisa

> **Objetivo:** alimentar o projeto com matéria-prima: tendências, temas, fontes de
> imagem/áudio grátis, concorrentes e validação de risco. Não implementa código — descobre e cura.

**Quando usar:** "que tema bombar essa semana?", "onde acho imagem real grátis de X?",
"que APIs existem pra Y?", "esse caso passa no filtro de risco?".

## Estado atual

- 3 nichos com base de conhecimento completa em `nichos/` (cada um: conteúdo, roteiro, riscos,
  roteiro-de-pesquisa, linguagem). Fontes reais já mapeadas por nicho (ver abaixo).
- `clipradar/` tem scanners de tendência (Google Trends RSS, YouTube trending, RSS news). → canal **07**.
- Pesquisa de mercado da S4S existe, mas é de OUTRO projeto — não confundir.

## Fontes de imagem REAL grátis (mapeado nesta sessão)

Separadas por risco de copyright (decisão de queimar no vídeo vs só referência):

**Pode QUEIMAR (livre/CC):**
- Wikimedia Commons API · Openverse API · Internet Archive · Flickr (filtro CC). Sem chave (Flickr tem chave grátis).

**Anime / One Piece:**
- Jikan (api.jikan.moe) · AniList GraphQL · Fandom/MediaWiki API (onepiece.fandom.com) · Safebooru. Atenção: IP Toei/Shueisha.

**Só REFERÊNCIA / atrás de aprovação humana (web aberta = risco de direitos):**
- Google Programmable Search (100/dia) · SerpApi (100/mês) · lib `duckduckgo_search`.

**Gerar quando não existe imagem real:**
- Pollinations.ai (já integrado no `short_factory.py`!) · AI Horde · Cloudflare Workers AI · HuggingFace Inference.

> Trade-off central: footage de julgamento real e painel de mangá = perfil Content ID. Preferir
> lane "pode queimar" + IA; web aberta só como referência aprovada via Telegram (canal **03**).

## Fontes por nicho (já documentadas em `nichos/`)

- **true-crime**: Charley Project, Doe Network, NamUs, Wikipedia. Benchmarks: MrBallen, JCS, Bailey Sarian.
- **conspiracy**: filtro Snopes/FactCheck/SIFT obrigatório. Benchmarks: LEMMiNO, Barely Sociable, Nexpo.
- **one-piece**: mangá/anime canon, SBS, databooks, Fandom. Benchmarks: Ohara, GrandLineReview.

## Backlog

- [ ] Validar 2-3 fontes de imagem real grátis e dizer quais valem integrar (handoff p/ canal **01**).
- [ ] Rodar/ligar os scanners do `clipradar` e trazer 5 temas quentes por nicho.
- [ ] Pra cada tema candidato: passar no filtro de risco do nicho antes de virar roteiro.

## Links

Decisão de nicho → [[04-nicho-decisao]]. Integração das fontes → [[01-melhorias-video]].
Aprovação de referência → [[03-melhorias-telegram]]. Scanners → [[07-clipradar-trends]].
