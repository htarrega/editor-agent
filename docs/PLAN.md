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

## H1 — Corrector v0 (one pass, minimal-edit) — **done**
- Minimal-edit prompt with JSON output of typed edits (Pydantic). → `corrector/correct.py`
- Deterministic application by anchors; an edit that does not match is discarded and logged.
  → 5 discarded of the 150 the model emitted, counted by reason in every report.
- Measure tokens and latency of `deepseek-v4-flash` from the first run (see the warning
  above). → 59.4 s/call, 4,064 in / 8,694 out, 7,690 of it reasoning.
- **Done when**: it beats the baselines on F0.5 on corpus A, with the numbers in the report.
  → **F0.5 0.929 against 0.899 and 0.454.**

### Decisions taken in H1

**The anchor carries a line number.** The text goes to the model with its lines numbered and
each edit names the line it belongs to; the anchor is resolved inside that line. Without it an
anchor has to be unique across the whole fragment, which is fine for `corrio` and useless for
`"`, ` ,` or `-`: the model would have to quote a whole clause to point at a quotation mark.
That is a token cost on exactly the category —orthotypography— where H0 says the baselines
already fail. The line is a hint and not a claim: an anchor that is unique in the whole text
resolves whatever line the model thought it was on, so a wrong number costs nothing and a
right one buys short anchors.

The marker goes on its own line (`[7]` above the text) rather than inline (`7| texto`). Half
of what this pass corrects is orthotypography, and a dialogue dash judged inside `7| —Vamos`
is a dash shown in a context the author never wrote.

**The corrector's prompt may say what the baseline's may not.** H0 fixed the line for the
baseline: output contract yes, correction policy no. This prompt is the other side of that
line — «edición mínima», «no corriges el estilo», «ante la duda, no corriges», the invented
words are the author's. That policy is the product (ARCHITECTURE §1); handing it to the
baseline would have been measuring the pipeline against itself, and withholding it from the
pipeline would be measuring nothing at all.

**`--fresh` exists because `--reuse` is a trap for the system under development.** H0 built
reuse so a baseline is not paid for twice. But after its first run the system being worked on
has a cache too, and a routine `--reuse` then publishes last run's numbers as this run's —
silently, since the row looks identical. `--fresh corrector-v0` is the flag that says which
system is not a baseline today.

### Finding: the runaway is the reasoning, and JSON output does not stop it

H0 hoped that "emitting edits as JSON instead of rewritten text cuts the output down
enormously" would tame `deepseek-v4-flash`. It does not, because the tokens were never going
into the answer. Measured on a **clean** 250-word fragment, with the edits protocol already
in place, the model spent all 32,000 output tokens deliberating over dialogue punctuation and
emitted no answer at all — `completion_tokens=32000`, `content=""`. The H0 diagnosis was
right and its remedy was aimed at the wrong half.

What does stop it is `reasoning_effort`, which the DeepSeek API accepts. On the full `sidra`
fragment (1737 words, 35 seeded errors):

| `reasoning_effort` | corpus A edits | corpus B false positives | reasoning tok | s |
|---|---|---|---|---|
| `none` | 25 | 6 | 0 | 10 |
| `minimal` | 33 | **0** | 13,437 | 99 |
| `low` | did not return in ~20 min; stopped by hand | — | — | — |

`minimal` is the default. `none` is ten times faster and is the wrong trade twice over: it
finds less *and* overcorrects more, which is the combination the product exists to avoid.
`low` and above reproduce the runaway. This also settles a question H5 was going to inherit:
chunking bounds the reasoning per call, but the effort cap is what makes a single call
terminate at all, so the two are not substitutes.

### Result: H1 clears the bar — **closed**

Same corpus as H0 — 4 fragments, 8254 words, 165 seeded errors, `seed 0`, `rate 0.02`.
Baselines reused; everything else measured live. `evals/results/20260818-133524-square.json`.

| system | P | R | F0.5 | FP/1k | voice | $/10k words | s/call |
|---|---|---|---|---|---|---|---|
| null | 0.000 | 0.000 | 0.000 | 0.00 | 0.000 | 0.0000 | — |
| languagetool | 0.424 | 0.630 | 0.454 | 14.90 | 0.003 | 0.0000 | 4.9 |
| naive-deepseek | 0.713 | 0.873 | 0.740 | 2.54 | 0.000 | 0.0204 | 37.6 |
| naive-claude | 0.894 | 0.921 | 0.899 | 2.18 | 0.002 | 1.6030 | 97.2 |
| **corrector-v0** | **0.952** | 0.848 | **0.929** | 0.00 | 0.000 | **0.0291** | 59.4 |
| corrector-claude | 0.987 | 0.909 | **0.970** | 0.12 | 0.000 | 1.0144 | 54.6 |

The bar was F0.5 > 0.899 with FP/1k < 2.18 and voice < 0.002. Met, with no failed calls and
no truncation. The margin is real and not a lucky draw: two independent runs of
`corrector-v0` on the identical corpus scored 0.926 and 0.929, so the ~0.028 lead over the
best baseline is about ten times the run-to-run spread.

### Finding: the pipeline is worth more than the model, and most to the cheap one

The result above cannot on its own tell whether the win came from our prompt or from
DeepSeek simply being more conservative than Claude — `corrector-v0` changed both at once.
Holding each in turn fixed answers it. F0.5, with precision in brackets:

| | naive prompt | our prompt | **the prompt is worth** |
|---|---|---|---|
| **Claude** | 0.899 (0.894) | 0.970 (0.987) | **+0.071** |
| **DeepSeek** | 0.740 (0.713) | 0.929 (0.952) | **+0.189** |
| **the model is worth** | +0.159 | +0.041 | |

On the same model, our prompt and edit protocol take precision from 0.894 to 0.987 and cut
false positives on clean text eighteenfold, at unchanged recall — and cost *less*, because
emitting edits is fewer output tokens than rewriting a text. With a naive prompt, buying the
strong model buys +0.159; with our prompt it buys +0.041. **The pipeline contributes more
than the model does, and it contributes most where the model is weakest**, which is the
economic case for ARCHITECTURE §1 rather than a restatement of it.

The number the product is: **`corrector-v0` at 0.929 beats `naive-claude` at 0.899 for 1/55th
of the cost** ($0.029 against $1.6030 per 10k words). A cheap model inside the pipeline beats
a strong model pasted into a chat. That was the thesis; it is now measured.

H7's "cents per 10k words" is therefore met at H1, before caching, batching or routing. What
has not moved is latency — 59.4 s per call, of which 7,690 of 8,694 output tokens are
reasoning. **Nearly 90% of what the workhorse costs is deliberation, not answer.** The calls
are independent, so this is a concurrency problem rather than a research one, and it belongs
with H5 rather than H7.

### Finding: the cheap model's recall gap is one fragment, and it is the one with 220-word paragraphs

Recall looks like the price we pay for precision (0.848 against `naive-claude`'s 0.921).
Almost all of it is a single fragment:

| | all four | excluding `carta` |
|---|---|---|
| corrector-v0 | 0.848 | **0.926** |
| corrector-claude | 0.909 | 0.917 |

`carta` averages 220 words per line; the other three run 39–47. On normal paragraphs the
cheap model matches the strong one. On `carta` it falls to 0.636 while Claude holds 0.886.

The obvious explanation — that the model spends a bounded number of edits per numbered block
— does not survive the per-block counts: block 9, at 374 words the longest in the corpus,
scored 3/3, while block 8 at 260 words scored 0/3. Something about paragraph size hurts the
bounded-reasoning model and the mechanism is not yet pinned down. **This is H5's chunking
re-cast as an accuracy question rather than a scale one**, and the cheap experiment that
settles it is to re-cut `carta` into smaller numbered blocks and re-measure.

### Finding: below ~5 events per run the harness cannot tell signal from noise

Two runs of `corrector-v0` over the identical corpus, differing only in sampling: clean-text
false positives went **3 → 0**; `raya_dialogo` 6/10 → 8/10; `homofono` 13/14 → 11/14;
`concordancia_genero` 11/14 → 13/14. Overall F0.5 moved 0.003, so the headline is stable, but
everything built on small counts is not.

Two consequences, both of which change later milestones:

- **FP/1k has stopped being a usable headline metric.** At 0–3 events per 8254 words it
  reports the draw, not the system. Corpus B needs to be much larger, or `--repeats` raised,
  before overcorrection can be compared between two systems at this quality level.
- **H4 cannot be run as written.** A failure-driven rule pack chooses its targets from
  per-type counts, and those counts are 2–16 items carrying ±2 of noise. Writing rules
  against that is writing rules against sampling error, and the eval would then "confirm"
  them at about the rate of a coin flip. More corpus and more repeats come first.

### Refuted: an off-taxonomy label does not predict a bad edit

Worth recording because it was cheap to test and would have been a filter costing no model
call. The corrector sometimes labels an edit with something the schema never offered (a
category name like `ortotipografía`, or an invented type). The guess was that those edits are
the wrong ones. They are not: **61 of 145 proposals were off-schema and only 5 of them were
false.** Dropping them would discard 56 correct edits to remove 5 wrong ones. The per-edit
record added in H1 (`metrics.outcomes`) is what made the question answerable offline.

A side observation from the same data: `corrector-claude` emitted **zero** off-schema labels
where `corrector-v0` emitted 42%. Schema adherence is a model property, and the taxonomy is
therefore worth validating in code rather than trusting to the prompt.

## H2 — Verifier (overcorrection control)

> **H1 undercut this milestone's premise.** A verifier exists to remove false positives, and
> after H1 there are almost none left to remove: `corrector-v0` makes 0–3 per 8254 clean
> words and `corrector-claude` makes 1. Its whole ceiling is a handful of edits, bought with
> a second paid pass and paid for in recall — which is now the weak side of the ledger, not
> the strong one. Either re-scope it to a second pass that only *proposes* and never rejects,
> pointing the same architecture at recall, or drop it and fold overcorrection control into
> H4. Not to be started before the noise finding above is addressed, since at 0–3 events the
> eval cannot show whether a verifier helped.

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

> **Blocked on measurement, not on ideas.** This milestone picks its targets from per-type
> failures, and H1 showed those counts swing by ±2 between identical runs on 2–16 item
> samples. Enlarging the corpus and raising `--repeats` until a per-type number means
> something is a prerequisite, not a refinement. Two targets do survive the noise, having
> held across both runs and both models: `comillas` sticks at 4/6 even for
> `corrector-claude`, and `loismo` at 1/2 — those two are real, and the per-edit record now
> says which instances were missed.

- Analyse the eval's per-type failures; write RAE/Fundéu rules only for what fails.
- Every new rule ↔ a new error type in the corruptor.
- **Done when**: measurable improvement on the targeted types, with no regression on the rest.

## H5 — Full manuscript

> **Promoted: chunking is an accuracy lever, not only a scale one.** H1 found the cheap
> model's whole recall deficit in the one fragment with 220-word paragraphs (0.636 against
> 0.926 on the rest). Cutting text into smaller units is therefore the largest recall
> improvement currently on the table, and no rule pack can match it because it lifts every
> type at once. The experiment that settles it costs about $0.01: re-cut `carta` into
> smaller numbered blocks and re-measure. Latency belongs here too — the calls are
> independent, so 59.4 s each is a concurrency problem, and a 50k-word manuscript is ~25
> calls.

- Chunking with overlap, name glossary, global consistency pass, final report (corrected
  document + list of applied corrections and suggestions with a diff).
- **Done when**: it processes a 30–50k-word text end to end with no intervention.

## H6 — Google Drive
- Read a document from Drive, write the corrected version + the report (Docs with
  suggestions or a copy).
- **Done when**: a full cycle from a real Doc of the user's.

## H7 — Cost

> **The cost target was met at H1** — $0.0291 per 10k words against `naive-claude`'s
> $1.6030, before any of the techniques listed below. What is left of this milestone is
> latency, and that moved to H5 because it is concurrency rather than pricing.

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
