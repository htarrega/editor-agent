# Plan by milestones

General rule: no corrector milestone is closed without numbers from the evaluation harness
(hence the eval comes first). Every milestone is small and leaves something usable.

## H0 — Evaluation harness (the scientific base)
- Typed-error corruptor (accents, agreement, dequeísmo, laísmo, dialogue punctuation,
  quotes, capitalization...).
- A/B corpus: 3–5 clean literary fragments (~2k words each), a corrupted version and an
  untouched version.
- Metrics: precision/recall/F0.5 per type, false-positive rate, basic stylometry.
- Measured baselines: LanguageTool + a naive prompt to a strong model.
- **Done when**: `python -m evals.run` prints a metrics table + the cost of the run.

### Decisions taken in H0

**The corpus lives outside the repository.** `evals/corpus/` and `evals/results/` are in
gitignore: they are the author's own texts, and the reports quote fragments of them in the
false-positive samples. We lose exact reproducibility from a clean clone; we gain not
putting our own work into the history. No titles and no pen names: they are paratext, not
prose, and they distort stylometry and word counts.

**A new fragment goes in clean or it does not go in.** Corpus B assumes text free of typos:
a pre-existing typo is counted as a false positive when a system correctly spots it, which
contaminates the headline metric. When adding a fragment, its unambiguous typos must be
fixed and a record kept of which ones.

**How far the baseline prompt may go.** It may state the output contract; never the
correction policy.

| allowed (contract) | forbidden (policy) |
|---|---|
| «devuelve únicamente el texto corregido» | «haz ediciones mínimas» |
| «sin comentarios ni explicaciones» | «no cambies el estilo» / «respeta la voz» |
| «con los mismos saltos de línea» | «no toques las palabras inventadas» |
| | «corrige solo ortografía y gramática» |

The right-hand column is the product thesis (ARCHITECTURE §1, overcorrection control).
Handing it to the baseline turns the comparison into "our pipeline against our pipeline
without the code". The left-hand column is what anyone writes after reading one answer.
Every report stores the `prompt` and the `model` of each system, because changing them
changes what its numbers mean.

### Finding: without an output contract, a strong model does not return a document

Measured with the bare prompt `"Corrige este texto:"`, before the contract was added. Three
faces of the same failure:

- DeepSeek blew through the 32,000 output tokens in **6 of 8 calls**: it rewrites the text
  and then writes an essay justifying every change.
- **45%** of Claude's false positives were markdown (`**`, `*`) and the preamble «Aquí
  tienes el texto revisado…», not language.
- On one fragment, Claude **did not return the text**: it returned a piece of literary
  criticism («el texto está redactado con mucho cuidado… he conservado…»), and the diff
  ended up comparing prose against a review.

This is the direct argument for ARCHITECTURE §4 —the model never rewrites, it only emits
anchored edits— and for the product not being "a better model" but a pipeline that returns
applicable edits. It is recorded here and in
`evals/results/20260817-211125-claude-4frag.json`; we do not pay for it again on every run.

### The bar H1 has to beat

Corpus of 4 fragments, 8254 words, 165 seeded errors (`seed 0`, `rate 0.02`).

| system | P | R | F0.5 | FP/1k | voice | cost |
|---|---|---|---|---|---|---|
| null | 0.000 | 0.000 | 0.000 | 0.00 | 0.000 | $0 |
| languagetool | 0.424 | 0.630 | 0.454 | 14.90 | 0.003 | $0 |
| naive-claude | 0.894 | 0.921 | **0.899** | 2.18 | 0.002 | $1.32 |

**F0.5 > 0.899, with FP/1k < 2.18 and voice < 0.002.** And the real gap is in the cost:
`naive-claude` comes to **$1.60 per 10,000 words** and H7 asks for cents.

A strong model with a one-line prompt already scores 100% on 9 of 17 types and barely
overcorrects. ARCHITECTURE §1 already warned that it cannot be beaten on raw intelligence
over a single fragment; now it is measured. The advantage has to come from points 2, 3 and
4: manuscript-scale consistency, traceability and cost.

**Shared blind spots, targets for H4**: `comillas` is missed by all three (LanguageTool 0/6,
Claude 0/6 — it leaves straight quotes unconverted), and `raya_dialogo` is missed entirely
by LanguageTool (0/10) and partly by Claude (8/10). Both are orthotypography, which is
exactly where a rule pack pays off.

### Warning for the model choice: `deepseek-v4-flash` reasons

`deepseek-v4-flash` is a reasoning model, and its `reasoning_tokens` count **inside**
`completion_tokens`, which is what `max_tokens` limits. Correcting four words it spends 66
reasoning tokens against 8 of answer.

As a baseline it failed to complete 6 of 8 calls even with 32,000 output tokens. The telling
part is *which* ones it failed: `sidra` and `diccionario`, the **shortest** fragments. It is
not a matter of length, but of it getting tangled deliberating over whether `vasu`,
`gomitar` or `merequetengue` are typos. **It spends unbounded reasoning precisely on the
text where overcorrection is the risk.**

It stays out of the default baseline set (the plan asks for LanguageTool + a strong model,
and the strong one is Claude). But it is ARCHITECTURE §6's choice as the corrector's
workhorse, so H1 has to measure its tokens and its latency early instead of taking them on
faith. Two things work in its favour: emitting edits as JSON instead of rewritten text cuts
the output down enormously, and H5's chunking bounds the reasoning per call.

## H1 — Corrector v0 (one pass, minimal-edit)
- Minimal-edit prompt with JSON output of typed edits (Pydantic).
- Deterministic application by anchors; an edit that does not match is discarded and logged.
- Measure tokens and latency of `deepseek-v4-flash` from the first run (see the warning above).
- **Done when**: it beats the baselines on F0.5 on corpus A, with the numbers in the report.

## H2 — Verifier (overcorrection control)
- A second pass that accepts/rejects each edit; disagreement → strong arbiter or "doubtful".
- **Done when**: the false-positive rate on corpus B drops measurably without sinking recall
  on corpus A.

## H3 — Voice profile
- A single pass that extracts the manuscript's stylistic profile; the verifier uses it as
  policy (deliberate traits are left alone).
- Strict separation: mechanical edits are applied, style stays as a suggestion.
- **Done when**: the stylometric distance original↔corrected drops against H2 and dialogue
  with deliberate traits survives intact in a test case.

## H4 — Failure-driven rule pack
- Analyse the eval's per-type failures; write RAE/Fundéu rules only for what fails.
- Every new rule ↔ a new error type in the corruptor.
- **Done when**: measurable improvement on the targeted types, with no regression on the rest.

## H5 — Full manuscript
- Chunking with overlap, name glossary, global consistency pass, final report (corrected
  document + list of applied corrections and suggestions with a diff).
- **Done when**: it processes a 30–50k-word text end to end with no intervention.

## H6 — Google Drive
- Read a document from Drive, write the corrected version + the report (Docs with
  suggestions or a copy).
- **Done when**: a full cycle from a real Doc of the user's.

## H7 — Cost
- Prompt caching, batch API if the provider offers one, routing (arbiter only when needed).
- **Done when**: the cost per 10k words is published in the eval report and is stable.

## Future work — Narrative coherence (continuity checking)
Developmental editing, not orthotypographic: detecting continuity errors in long manuscripts
(character attributes that change between chapters, impossible chronology, characters who
know things before they could know them). This is where a pipeline with structured memory
gains the most over a generalist chat.

- Knowledge graph in **Neo4j**: growing H5's entity glossary into a graph (characters, places,
  objects as nodes; relations and attributes with chapter/scene as properties). The pipeline
  populates it while reading the manuscript and a detection pass queries for contradictions
  (the same attribute with different values, impossible chronology, information known before
  it is revealed).
- Why Neo4j fits here: relational queries (genealogies, character networks, transitive
  who-knows-what) and reuse of the graph across volumes of the same saga.

## Out of scope (for now)
- Other languages, our own fine-tuning, a UI. Only if the eval data justifies it.
