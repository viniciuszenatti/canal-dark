---
projeto: canal-dark
tipo: missao-reutilizavel
tags: [canal-dark, pesquisa, nichos, prompts, roteiro]
atualizado: 2026-05-31
---

# MISSÃO — Pesquisa profunda de nichos & prompts

> Cole este texto no chat (na pasta `C:\Users\aless\canal-dark`) para rodar o ciclo de
> melhoria de CONTEÚDO/CONHECIMENTO. Faça ESTA antes da de imagem/legenda
> ([[_MISSAO-ciclo-imagem-legenda]]) — melhora o roteiro primeiro, o visual depois.

MISSÃO: Melhorar os NICHOS e seus PROMPTS do Canal Dark via PESQUISA PROFUNDA — (1) técnicas
de engajamento (script, cortes, ganchos, retenção) e (2) aprofundamento de cada tema, criando
uma BASE DE CONHECIMENTO e um banco de REFERÊNCIAS por nicho. Você é o cd-gerente (PM):
planeje e DELEGUE — não faça na mão.

CONTEXTO:
- 3 canais separados, cada um com persona/voz/b-roll próprios: true-crimes,
  conspiracy-theories, one-piece.
- Cada nicho já tem base em nichos/<nicho>/ com 5 docs:
  01-conteudo-e-pesquisa · 02-roteiro-e-linguagem · 03-riscos-e-conformidade ·
  04-roteiro-de-pesquisa · 05-linguagem-e-referencias.
  Técnicas comuns de Shorts ficam em nichos/00-tecnicas-shorts-comum.md.
- O playbook do nicho (sobretudo 02-roteiro-e-linguagem + 01-conteudo) é INJETADO no
  SCRIPT_SYSTEM_PROMPT do short_factory.py em runtime. Melhorar esses docs = melhorar o
  roteiro gerado. Isto é TUNING de prompt/conhecimento, NÃO treino de modelo.
- COPYRIGHT: afrouxado em geral. EXCEÇÃO DURA do ONE PIECE: nada de frame de anime/mangá
  (Content ID Toei/Shueisha) — vale só pra ele.

EQUIPE (delegue ao agente certo):
- cd-pesquisa  → toda a pesquisa (engajamento + aprofundamento de tema + referências)
- cd-melhorias → transformar a pesquisa em prompt/playbook melhor (02-roteiro, persona, few-shot)
- cd-testes    → gerar roteiros de amostra pra validar antes × depois
Ferramenta sugerida: skill /deep-research e WebSearch/WebFetch (grátis).

FASE 1 — PESQUISA DE ENGAJAMENTO (transversal)  (delega: cd-pesquisa)
Objetivo: levantar técnicas atuais que aumentam retenção em Shorts narrados faceless.
Cobrir: estrutura de script (arco, beats, duração ideal por trecho); GANCHOS (tipos de hook
que param o scroll nos primeiros 3s, com exemplos); CORTES e ritmo (frequência de troca de
b-roll, pattern interrupt, j-cuts); CTA e loop de retenção; padrões de legenda que prendem.
Entregável: um doc atualizado nichos/00-tecnicas-shorts-comum.md, com cada técnica + POR QUE
funciona + fonte (link + data). Separe FATO verificado de hipótese.
⏸️ CHECKPOINT: me mostre o resumo das técnicas antes de seguir.
▶ Próximo passo: com meu OK, ir pra Fase 2.

FASE 2 — APROFUNDAMENTO POR NICHO (1 rodada por tema)  (delega: cd-pesquisa)
Objetivo: virar especialista em cada um dos 3 temas e abastecer a base de conhecimento.
Para CADA nicho (true-crimes, conspiracy-theories, one-piece), pesquise a fundo:
  (a) sub-tópicos/casos com bom potencial de Short (lista de ideias rastreável);
  (b) vocabulário, tom e "regras do gênero" (o que o público espera; o que irrita);
  (c) criadores/canais de referência que acertam o formato (e o que copiar de ESTILO,
      não de conteúdo);
  (d) fontes primárias confiáveis pra pesquisa de roteiro (bancos de caso, wikis, arquivos);
  (e) armadilhas de risco/política específicas do tema.
Entregável por nicho: enriquecer os 5 docs (sobretudo 01-conteudo, 02-roteiro, 05-referencias)
E criar/atualizar um banco estruturado em nichos/<nicho>/_referencias.md no formato:
  | Tipo | Fonte/Criador | Link | O que extrair | Data |
⚠️ HONESTIDADE (crítico): NUNCA invente fonte, link, canal ou estatística. Toda referência
tem que ser real e verificável; se não confirmou, marque [NÃO VERIFICADO]. Reporte lacunas.
⚠️ ONE PIECE: referência de estilo é ok; b-roll continua sem frame de anime/mangá.
⏸️ CHECKPOINT: me mostre a base de cada nicho (eu valido as referências).
▶ Próximo passo: com meu OK, ir pra Fase 3.

FASE 3 — APRIMORAR OS PROMPTS DOS NICHOS  (delega: cd-melhorias)
Objetivo: converter a pesquisa em prompt melhor — onde mora a qualidade do roteiro.
Para cada nicho, com base nas Fases 1–2:
  (1) reforce nichos/<nicho>/02-roteiro-e-linguagem.md (estrutura, ganchos e tom do gênero);
  (2) defina/firme a PERSONA nomeada do canal (nome, voz, ponto de vista) — anti "inauthentic";
  (3) adicione 1–2 exemplos de roteiro-OURO (few-shot) no estilo das referências da Fase 2.
Mostre antes/depois do trecho de prompt. Re-sincronize as 2 cópias (código que roda + OneDrive)
e mantenha o prompt do roteirista nicho-agnóstico (quem muda é o playbook injetado).
⏸️ CHECKPOINT: eu aprovo as mudanças de prompt por nicho.
▶ Próximo passo: com meu OK, ir pra Fase 4.

FASE 4 — VALIDAR (antes × depois)  (executa: cd-testes; conteúdo: cd-melhorias)
Objetivo: provar que o prompt novo gera roteiro melhor — sem achismo.
Para cada nicho, gere 1 roteiro de amostra com o playbook ANTIGO e 1 com o NOVO (mesmo tema).
Compare: hook mais forte? menos genérico? tom do gênero? Use o checklist /cd-revisar-roteiro.
Entregável: tabela comparativa por nicho (v0 × v1) com veredito.
⏸️ CHECKPOINT: eu decido se adoto o prompt novo.
▶ Próximo passo: documentar.

FASE 5 — DOCUMENTAR  (consolida: cd-gerente)
Registre no [[Canal Dark — MOC]] e em [[Decisões Travadas]]: o que mudou em cada nicho, a
persona firmada, e o índice de referências criado. Atualize o "Estado atual" do canal 02
(prompt) e 04 (nicho).
▶ Próximo passo: nichos prontos pra alimentar o ciclo de produção.

REGRAS (todas as fases):
- GRÁTIS PRIMEIRO (deep-research/WebSearch). Pago só com custo real e ganho claro.
- HONESTIDADE acima de tudo: referência inventada é o pior erro possível aqui. Cite fonte
  (link + data); o que não verificou, marque como tal. Se um nicho rende pouco, DIGA.
- RISCO #1: roteiros ÚNICOS por canal, persona COERENTE, NADA de molde repetido.
- ESCOPO: TUNING de prompt/conhecimento — NÃO é treino/fine-tune de modelo.
- Termine CADA fase com "▶ Próximo passo".
