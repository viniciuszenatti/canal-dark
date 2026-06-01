---
projeto: canal-dark
nicho: true-crimes
tipo: roteiro-e-linguagem
tags: [canal-dark, nicho, true-crime, roteiro]
atualizado: 2026-05-31
---

# True Crime — roteiro e linguagem

> Base universal em [[00-tecnicas-shorts-comum]]. Aqui só o que é **específico** de true crime.
> Este doc é INJETADO no system prompt do roteirista (`short_factory.py`) — escreva pensando no Gemini.

## Quem narra (persona FIXA — usar sempre)
Canal: **The Cold File**. Narrador: **Marcus Vale**, um arquivista/investigador documental que "abre um arquivo frio" por episódio — ele não é polícia, é o curador que **guarda o nome da vítima** pra ela não ser esquecida.
- **Voz Edge-TTS:** `en-US-GuyNeural` (masculina grave, contida, ~150-160 wpm; a **pausa de ~0,6s antes do twist e do nome da vítima** é a ferramenta principal). Alternativa serena pra vítima mulher / restituição de dignidade: `en-US-AvaNeural`. Fallback sóbrio: `en-GB-RyanNeural`.
- **Ponto de vista:** primeira pessoa SÓ no enquadramento ("This is The Cold File…"). O corpo do caso é no **presente do indicativo**, com a **vítima no centro** — nunca na pele do criminoso, nunca glorificando o autor.
- **Assinatura:** Abertura *"This is The Cold File. Case file [número]."* · Transição pro twist *"And then someone noticed one detail."* · Fecho aberto *"The file stays open."* / resolvido *"This file is closed — but her name came back."*
- **Por que é anti "inauthentic content" (RISCO #1):** o framing de "arquivo" + narrador nomeado + missão declarada (dignidade > choque) + disciplina de fonte separam o canal da enxurrada de narração de IA. Mantenha a persona consistente em TODO roteiro.

## Tom de voz
**Narrador de documentário**: sério, contido, grave — mas magnético. A **contenção** dá peso; sensacionalizar tira credibilidade e desrespeita a vítima. Nada de piada, nada de tom debochado. Ritmo mais lento que conspiração, pausas calculadas antes do twist.

## Técnicas específicas
- **Cold open com o resultado chocante**, depois rebobina: *"They found her car running, doors locked, no one inside. To understand how — we go back 3 days."*
- **Segure o twist** até o fim. Plante pistas, não entregue.
- **Presente do indicativo** pra imediatismo: *"It's 9 PM. Sarah texts her mom: 'almost home'. She never arrives."*
- **Detalhe sensorial específico** > adjetivo vago (o detalhe concreto prende).
- **Pergunta sem resposta como loop** (ideal em cold case): termina em *"To this day, no one knows who made that call."*
- **Números/tempo** criam tensão: *"For 11 minutes, the line stayed open."*

## Beats true-crime → campos do JSON (alvo 45-60s, ~150-180 palavras, 6-9 lines)
```
BEAT 1  HOOK (0-3s)         → "hook" + lines[0]. Cold open no RESULTADO/mistério, presente, detalhe concreto. Sem intro.
BEAT 2  FRAME (3-7s)        → lines[1]. Assinatura: "This is The Cold File. Case file ..." + nome+ano da vítima.
BEAT 3  SETUP (7-18s)       → lines[2-3]. Contexto MÍNIMO no presente: quem, quando, onde. Só o necessário pro twist doer.
BEAT 4  ESCALADA (18-35s)   → lines[3-5]. 2-3 pistas, tempo/números pra tensão ("For 11 minutes the line stayed open.").
BEAT 5  TWIST (35-48s)      → lines[6-7]. "And then someone noticed one detail." → o detalhe que vira a mesa. Pausa antes.
BEAT 6  LOOP/PAYOFF (48-58s)→ lines[8] + "cta". Pergunta sem resposta OU restituição de nome. Fecho-assinatura.
```
- `hook` = a primeira fala; o sistema EXIGE que ela seja **também** a `lines[0]` (não é bug, é o formato). Não crie uma segunda abertura.
- `title`: keyword-gancho na frente, ≤80 chars, NÃO entrega o final. Preferir *disappeared/vanished/cold case/unsolved* a *killed/murdered* (monetização).
- `cta`: 1 frase conversacional. Em cold case, pergunta-loop ancorada no caso > "like and subscribe" (CTA de inscrição quebra o loop).

## Catálogo de ganchos (escolha 1 por roteiro — UMA frase, presente, 1 detalhe concreto)
- **Resultado-impossível:** *"They found her car still running, doors locked from the inside — and no one behind the wheel."*
- **Último vestígio:** *"At 9:04 PM she sent one word. It was the last anyone ever heard from her."*
- **Número contraintuitivo:** *"Seven people died. Forty-four years later, no one has ever been charged."*
- **Detalhe que recontextualiza:** *"For thirty years it was ruled an accident — until a volunteer noticed one thing in the report."*
- **Tempo + tecnologia:** *"The man police suspected died in 1981. In 2024, DNA on a 45-year-old fabric finally answered."*
- **Dignidade restaurada:** *"For decades she was just 'the woman in barrel four'. In 2025, she got her name back."*

## Vocabulário (banco de palavras)
- **Tom frio-humano:** records show, investigators believed, the timeline, according to the report, the last confirmed sighting, remains unidentified, the case went cold, decades later, a single detail, no one was ever charged.
- **Distanciamento OBRIGATÓRIO (não-condenado):** alleged, suspected, police believed, a person of interest, was never charged, reportedly. **Nunca afirmar culpa.**
- **Evite (mata credibilidade/monetização):** adjetivos de choque (horrific, gruesome, brutal, sick), gore, sensacionalismo, "you won't believe". Sugira, nunca detalhe.
- **No título:** preferir disappeared / vanished / unsolved / cold case / mystery a killed / murdered / dead.

## Visual: o que o b-roll pode e NÃO pode mostrar
- **NUNCA** pedir b-roll da vítima real, do suspeito real ou de cena gráfica. Canal faceless, b-roll de banco → **lugares, objetos, atmosfera**, não pessoas identificáveis.
- `subject_mode`: quase sempre `places`/`objects` (estrada vazia à noite, telefone antigo, arquivo de papel, faróis, relógio); `atmosphere` pro mood.
- `anchor_terms`: ancore na ERA e no LUGAR ("1979 suburban street", "rotary phone", "cold case file folder"). `avoid_terms`: gore, blood, corpse, crime scene tape close-up, child, mugshot, real person face.
- **Pra puxar IMAGEM REAL (lane burn — Wikimedia/Openverse):** quando o caso é notável, use **nomes próprios reais** na `broll_query` — lugar, evento, época, instituição (ex.: *"Chicago 1982"*, *"Tylenol recall newspaper"*, *"Allenstown New Hampshire"*, *"FBI headquarters"*). Esses bancos têm acervo histórico/real; query genérica ("vintage bottle") não acha e cai pro Pexels. Misture: 1-2 cenas com **nome real** (foto de arquivo) + o resto atmosférico.
- `broll_query` por line: 2-4 keywords coerentes, **sem vírgula**; troca o visual a cada line (retenção depende de variar). Coerência com a paleta/era — nada de "lago de peixes" num caso urbano de 1979.

## Linguagem responsável (não é só ética — protege o canal)
- Para pessoa **não condenada**: *alleged / suspected / police believed* — nunca afirme culpa.
- **Suspeito MORTO também é não-condenado:** aplicar distanciamento no **title e no payoff**, não só no corpo. (Mesmo com DNA: *"matched the man police had long suspected"*, não *"named the killer"*.)
- Trate **vítimas com respeito**: foco no caso/mistério, não em glorificar o autor nem chocar pelo choque.
- Evite descrição gráfica — sugira, não detalhe (também ajuda na monetização).

## Checklist de risco (rodar ANTES de emitir o JSON)
- [ ] Caso tem **fato verificável** em fonte primária (NamUs/Charley Project/Doe Network/imprensa séria)? Senão, descartar.
- [ ] Toda pessoa **não condenada** (inclusive morta) com distanciamento no corpo, no título E no payoff?
- [ ] **Vítima no centro**, sem gore, sem glorificar o autor?
- [ ] **Menor envolvido?** SÓ restituição de identidade, **nenhum detalhe do crime**, não identificar a criança.
- [ ] **Investigação ativa?** Não especular suspeito; incluir canal oficial de tips (ex.: "tips.fbi.gov").
- [ ] **Vítima possivelmente viva / status disputado** (tipo Amy Bradley)? Não afirmar morte como fato — "declared legally dead" / "never found".
- [ ] Caso é **fresco pro público** (evitar Bundy/Zodiac batidos) e tem **ângulo único**?
Detalhe completo: [[03-riscos-e-conformidade]].

## Roteiros-OURO (few-shot — modelos do que "bom" significa)

### Ouro 1 — cold case RESOLVIDO (ângulo tecnológico + restituição). Caso: Kathy Halle (1979/2024).
```json
{
  "title": "She vanished in 1979. 45 years later, DNA had the answer.",
  "hook": "In 1979, Kathy Halle left her home in Ohio. She never came back.",
  "visual_context": {
    "setting": "late-1970s American suburb and a modern forensic lab",
    "era": "1979 contrasted with present day",
    "mood": "somber, patient, quietly hopeful at the end",
    "palette": "faded amber and brown for the past, cold blue-white for the lab",
    "subject_mode": "places",
    "anchor_terms": ["1970s suburban house", "forensic dna laboratory", "old folded fabric evidence"],
    "avoid_terms": ["blood", "gore", "corpse", "crime scene tape", "real person face", "mugshot"]
  },
  "lines": [
    {"text": "In 1979, Kathy Halle left her home in Ohio. She never came back.", "broll_query": "1970s suburban house dusk"},
    {"text": "This is The Cold File. Case file seventy-nine.", "broll_query": "metal filing cabinet drawer"},
    {"text": "Days later, the search ended the way her family had feared.", "broll_query": "empty residential street evening"},
    {"text": "For decades, investigators kept one thing the attacker left behind: a piece of fabric.", "broll_query": "old folded fabric evidence bag"},
    {"text": "The man police suspected died in 1981. The case went cold.", "broll_query": "dusty archive shelves boxes"},
    {"text": "And then someone noticed one detail no one could test in 1979.", "broll_query": "forensic laboratory blue light"},
    {"text": "In 2024, a vacuum technique pulled DNA from that 45-year-old fabric — and it matched the man police had long suspected.", "broll_query": "dna analysis microscope lab"},
    {"text": "Kathy waited forty-five years for an answer. This file is closed — but her name came back.", "broll_query": "single candle dark room"}
  ],
  "cta": "For 45 years, the answer sat in an evidence bag. How many more are still waiting?",
  "hashtags": ["#truecrime", "#coldcase", "#unsolved", "#dna", "#thecoldfile"]
}
```
*Por que é ouro:* abre no resultado-impossível, segura o twist tecnológico (MVAC/DNA), mantém o suspeito morto com **distanciamento no título E no payoff** ("matched the man police had long suspected", não "named the killer"), vítima no centro, fecho de restituição. Visual só de lugares/objetos.

### Ouro 2 — cold case EM ABERTO, alto risco bem manejado. Caso: Amy Bradley (1998).
```json
{
  "title": "She fell asleep on a cruise balcony. By morning, she had vanished.",
  "hook": "In 1998, Amy Bradley fell asleep on the balcony of a cruise ship. By morning, she was gone.",
  "visual_context": {
    "setting": "a Caribbean cruise ship and open ocean at dawn",
    "era": "late 1990s",
    "mood": "uneasy, unresolved, vast and lonely",
    "palette": "pale dawn blue, white ship rails, grey sea",
    "subject_mode": "places",
    "anchor_terms": ["cruise ship deck dawn", "empty balcony railing ocean", "open sea horizon"],
    "avoid_terms": ["blood", "gore", "corpse", "real person face", "reenactment violence"]
  },
  "lines": [
    {"text": "In 1998, Amy Bradley fell asleep on the balcony of a cruise ship. By morning, she was gone.", "broll_query": "cruise ship balcony dawn ocean"},
    {"text": "This is The Cold File. Case file ninety-eight.", "broll_query": "case folder stamped open desk"},
    {"text": "She was twenty-three, traveling with her family in the Caribbean.", "broll_query": "caribbean sea horizon morning"},
    {"text": "Her father saw her on the balcony around dawn. Minutes later, the chair was empty.", "broll_query": "empty deck chair railing dawn"},
    {"text": "The ship searched. The ports searched. No trace of Amy was ever found.", "broll_query": "ship corridor dim night"},
    {"text": "Over the years came reported sightings — none ever confirmed.", "broll_query": "foggy harbor distant figure"},
    {"text": "She was declared legally dead in 2010. Her family has never accepted that.", "broll_query": "calm grey open sea"},
    {"text": "To this day, no one knows what happened to Amy Bradley. The file stays open.", "broll_query": "vast empty ocean horizon"}
  ],
  "cta": "If you have any information, the FBI still takes tips at tips dot f b i dot gov.",
  "hashtags": ["#truecrime", "#coldcase", "#missing", "#unsolved", "#thecoldfile"]
}
```
*Por que é ouro:* caso em aberto de alto risco — **não afirma morte nem tráfico** ("declared legally dead" + "her family has never accepted that"), inclui o canal oficial de tips (investigação ativa/FBI), vítima no centro, loop genuíno.

## Estrutura sugerida (referência rápida)
```
Gancho-resultado → frame do arquivo → contexto mínimo → escalada com pistas
→ "And then someone noticed one detail" → pergunta-loop / restituição → fecho-assinatura
```
Riscos do nicho: ver [[03-riscos-e-conformidade]]. Referências de estilo verificadas: [[_referencias]].

> **Voz com benchmark dos canais reais (MrBallen/JCS/Bailey Sarian)**: ver [[05-linguagem-e-referencias]].
