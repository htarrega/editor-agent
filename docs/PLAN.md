# Plan by milestones

General rule: no corrector milestone is closed without numbers from the evaluation harness
(hence the eval comes first). Every milestone is small and leaves something usable.

Every number in this document was produced by a specific prompt on a specific model with a
specific block numbering, and `reuse.incompatible` compares none of the three — so a reword
would have been reused in silence and the tables would have gone on looking unchanged.
`FrozenRows` in `tests/test_evals/test_systems.py` now pins the model and a hash of the
prompt for the five rows that have them, on both sides of H1's prompt-against-model square.
Moving one is a decision that starts with re-measuring, not a refactor.

## H0 — Evaluation harness (the scientific base) — **done**
- Typed-error corruptor (accents, agreement, dequeísmo, laísmo, dialogue punctuation,
  quotes, capitalization...). → 17 rules in `evals/corruptor.py`, one per taxonomy type.
- A/B corpus: 3–5 clean literary fragments (~2k words each), a corrupted version and an
  untouched version. → 4 fragments, 8254 words.
- Metrics: precision/recall/F0.5 per type, false-positive rate, basic stylometry.
  → `evals/metrics.py`, with cluster matching so a split or merged correction still counts.
- Measured baselines: LanguageTool + a naive prompt to a strong model. → the table below.
- **Done when**: `python -m evals.run` prints a metrics table + the cost of the run.
  → **it does**, and every run writes the full detail to `evals/results/<timestamp>.json`.

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
`naive-claude` comes to **$1.60 per 10,000 words** and the product needs cents.

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
no truncation. The lead over the baseline survives — six later runs on this exact corpus all
clear 0.899 — but the sentence that used to stand here, claiming the run-to-run spread was
0.003 and the margin ten times it, was an artefact of having measured exactly twice. The
spread is 0.043. See «the run-to-run spread is the finding» below.

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

The cost goal — cents per 10,000 words — is therefore met here, at H1, before caching,
batching or routing, which is why no separate cost milestone survives in this plan. What has
not moved is latency: 59.4 s per call, of which 7,690 of 8,694 output tokens are reasoning.
**Nearly 90% of what the workhorse costs is deliberation, not answer.** The calls are
independent, so this is a concurrency problem rather than a research one, and it is settled
in H5.

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

### Finding: the run-to-run spread is the finding, and one run decides nothing

`corrector-v0`, six times over the identical corpus (fingerprint `d5e1ee1f`, `seed 0`,
`rate 0.02`, `repeats 1`), changing nothing but the model's own sampling:

| | | | | | | mean | spread |
|---|---|---|---|---|---|---|---|
| **F0.5** | 0.926 | 0.929 | 0.942 | 0.938 | 0.911 | 0.954 | 0.933 | **0.043** |
| **recall** | 0.836 | 0.848 | 0.879 | 0.842 | 0.788 | 0.903 | 0.849 | **0.115** |

This was first written up from two runs as a spread of 0.003 affecting only small counts.
That was wrong, and wrong in the direction that matters: the *headline* moves by ±0.02 and
recall by ±0.06, which is the size of the effects these milestones are trying to detect.

It was not a harmless error. Chunking (H5) was measured once and declared a win, measured
again and declared dead, and only settled on the third attempt with `--repeats 3` — three
conclusions from the same code, two of them wrong. **A single run of this harness is a draw
from a distribution, not a measurement**, and no milestone below closes on one.

Three consequences:

- **FP/1k is not a usable headline metric.** At 0–3 events per 8254 words it reports the
  draw, not the system. Corpus B has to grow, or `--repeats` be raised, before overcorrection
  can be compared between two systems at this quality level.
- **`--repeats` is the default posture, not an option.** It was affordable only after the
  harness learned to run its calls concurrently (H5): `--repeats 3` is 3× the calls at
  roughly the same wall clock.
- **Two systems are compared on paired outcomes, not on two headline numbers.** Both systems
  see byte-identical corrupted text, so every seeded error is a paired trial and the right
  test is McNemar on the discordant pairs. The H5 result below is the first to use it, and
  it reversed what the headline numbers said on single runs.

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

## H3 — Voice profile

> The verifier this milestone was written against is gone (see «Dropped milestones»), so the
> profile feeds the corrector's own prompt rather than a second pass, and it is measured
> against the corrector rather than against a verifier.

- A single pass that extracts the manuscript's stylistic profile; the corrector takes it as
  policy, so deliberate traits are left alone.
- Strict separation: mechanical edits are applied, style stays as a suggestion.
- **Done when**: the stylometric distance original↔corrected drops against `corrector-blocks`
  and dialogue with deliberate traits survives intact in a test case — over `--repeats 3`,
  never one run.

## H4 — Failure-driven rule pack

> **Still blocked on measurement, but the block is smaller.** This milestone picks its
> targets from per-type failures, and at `repeats 1` those counts were 2–16 items carrying
> ±2 of noise. `--repeats 3` triples them at roughly unchanged wall clock, and the paired
> McNemar test is the tool for deciding whether a rule helped rather than two headline
> numbers. Two targets survived even the noisy measurement and are still there at
> `repeats 3`: `comillas` at 14/22 for `corrector-blocks` and `loismo` at 3/4. Chunking
> already took a slice of `comillas` for free, so re-measure before writing a rule for it.

- Analyse the eval's per-type failures; write RAE/Fundéu rules only for what fails.
- Every new rule ↔ a new error type in the corruptor.
- **Done when**: measurable improvement on the targeted types, with no regression on the rest.

## H5 — Full manuscript

> **Chunking is measured and it is in; splitting it across calls is measured and it is out;
> latency is closed.** Both of the questions this milestone inherited from H1 are answered
> below, and so is the one the answer to them raised. What remains is the manuscript-scale
> machinery: overlap, glossary, global consistency pass, final report.

- Chunking — **done**, without overlap: `corrector/blocks.py` cuts an over-long paragraph
  at its own sentence boundaries, and the blocks abut rather than overlap. Measured below.
- Concurrent calls — **done**, `evals/run.py:correct_all`. Recorded below; it is what makes
  `--repeats` affordable, and `--repeats` is what makes a comparison mean anything.
- Name glossary, global consistency pass, final report (corrected document + list of applied
  corrections and suggestions with a diff) — **pending**. Overlap belongs here too: what is
  done is cutting inside one call, not carrying context across two.
- **Done when**: it processes a 30–50k-word text end to end with no intervention. → **not
  yet**; there is no document-level pass, so nothing above ~2k words runs end to end.

### Result: chunking raises the floor, and the floor is what matters

`corrector/blocks.py` cuts an over-long line into blocks of at most `block_words` words at
its own sentence boundaries. The text is never touched: cutting changes how the same
characters are numbered, nothing else. `corrector-blocks` is `corrector-v0` with
`block_words=50` and everything else held fixed — same model, same `reasoning_effort`, same
prompt, same token cap.

`--repeats 3`: 12 corrupted versions, 495 seeded errors, both systems fed byte-identical
text, no failed calls (`20260819-135324-blocks-repeats3.json`).

| system | P | R | F0.5 | FP/1k | anchors rejected | $ | wall |
|---|---|---|---|---|---|---|---|
| corrector-v0 | 0.957 | 0.820 | 0.926 | 0.24 | 16 | 0.0545 | 260 s |
| **corrector-blocks** | **0.968** | **0.875** | **0.948** | **0.12** | **4** | 0.0538 | 243 s |

Every seeded error is a paired trial, since both systems saw the same text. Pairing them:
378 caught by both, 34 by neither, **28 only by `corrector-v0`, 55 only by
`corrector-blocks`** — exact McNemar two-sided p = 0.004.

**Yet case by case it is 6–5 and a tie.** Blocks wins the aggregate because its wins are
large and its losses small, and the spread across the three samples of each fragment says
why:

| fragment | corrector-v0 | corrector-blocks |
|---|---|---|
| `carta` (245 words/paragraph) | 0.455 – 0.841 | **0.705 – 0.841** |
| `hierro` | 0.784 – 0.961 | **0.882 – 0.941** |
| `diccionario` | 0.886 – 1.000 | 0.857 – 0.943 |
| `sidra` | 0.886 – 0.914 | 0.857 – 0.943 |

Chunking does not lift the ceiling — on the two short-paragraph fragments `corrector-v0` is
level or ahead. It lifts the *floor*, and only where paragraphs are long: on `carta` the
worst draw goes from 0.455 to 0.705. **The cheap model is not made cleverer by being shown
smaller blocks, it is made predictable**, and for a 50k-word manuscript that is the property
that matters, because what ruins a manuscript is the bad draw and not the average.

This also answers the question H1 left open. The mechanism is not a bounded number of edits
per block and not the model giving up partway through: recall by position is flat for both
systems (0.879 first half against 0.871 second). A long block is under-read evenly
throughout, and shorter blocks lift the whole curve.

One gain is mechanical rather than statistical: **4 rejected anchors against 16**. A short
anchor is already unique inside a 50-word block, so proposals stop dying as
`anchor_ambiguous`. Cost and latency are a wash.

Caveat on the p-value: McNemar treats the 495 errors as independent and they are not
entirely — errors inside one call share a single deliberation, so the effective sample is
nearer 12 calls than 495 errors. Read p = 0.004 as optimistic; the floor-raising table does
not rest on that assumption and is the conservative reading.

`corrector-blocks` is therefore the default in `DEFAULT_SYSTEMS`, and the pipeline itself
defaults to `DEFAULT_BLOCK_WORDS = 50`: a `Corrector` built anywhere — H5's manuscript
machinery included — gets the setting that was measured better, rather than the losing one
with the registry as the only place that knew.

The two rows H1's numbers rest on, `corrector-v0` and `corrector-claude`, ask for
`block_words=None` **by name** instead of inheriting it. Their numbers are quoted throughout
this document and cached in past reports, so they have to keep meaning what they say while
the default moves underneath them. `corrector-claude` in particular is one half of the
prompt-against-model square, measured before the blocks existed; a chunked strong model is a
row nobody has paid for and would need a name of its own.

What has *not* been swept is `block_words` itself — 50 is the only value measured, chosen
because it is the order of the paragraphs the model already handles well, not because it won
a search.

### Refuted: a block is a unit of numbering, not a unit of inference

H5 left open whether a block should also be what a *call* covers. It should not.
`blocks_per_call` sends N blocks per request instead of the whole document in one; the
numbering is untouched, so the only thing that varies is how much text a single call sees.

`--limit-words 300 --repeats 1` (`20260819-220556-smoke.json`):

| system | blocks/call | P | R | F0.5 | FP/1k clean | calls | $ | wall |
|---|---|---|---|---|---|---|---|---|
| **corrector-blocks** | all | **0.943** | 0.817 | **0.915** | **0.00** | 8 | 0.0141 | 125 s |
| corrector-batched | 10 | 0.887 | 0.878 | 0.886 | 0.49 | 28 | 0.0212 | 225 s |
| corrector-per-block | 1 | 0.776 | 0.829 | 0.786 | 2.20 | 241 | 0.0647 | 560 s |

Monotonic across all three points, and in the direction the prompt predicts: the rule that
protects the author's voice — a rare word that is *coherente con el resto del texto* is his
and stays — cannot be applied by a call that was never shown the rest of the text. Corpus B
says it without ambiguity, 0 false positives becoming 9. Recall moves the other way and
does not pay for it, because F0.5 weights precision twice and a false positive is what this
product exists to avoid.

The argument *for* splitting was robustness: one bad reply costing a block instead of the
whole fragment. All three rows recorded **zero failed calls**, so the insurance covered
nothing while costing 0.13 F0.5 and 4.6× the money. The truncation it was meant to survive
already has its answer above — raise the cap.

One draw at 300 words settles less than `--repeats 3` would. It is read as decisive anyway
because three monotonic points are not an A/B that can flip, the effect is large, and
corpus B does not depend on the corruptor: every edit there is a false positive by
definition. `blocks_per_call` stays registered as `corrector-batched` and
`corrector-per-block` so the result is reproducible, not because either is a candidate.

### Decision: the harness runs its calls concurrently

`evals/run.py:correct_all` maps `system.correct` over the texts through a thread pool, sized
by `--concurrency` (default 4). Measured 168 s of wall clock against 481 s of summed call
latency. Two properties make it safe rather than merely fast:

- **Results come back in input order.** Scores, false-positive samples and the per-edit
  record are appended in corpus order, so out-of-order results would have two runs of one
  corpus writing two different reports, neither of them wrong.
- **`usage.seconds` is concurrency-invariant**, because it sums each call's own duration. It
  therefore keeps meaning exactly what it meant in H1's table, and for the same reason cannot
  show a speedup — hence `wall_seconds` recorded beside it.

A system may pin its own ceiling: `LanguageToolSystem.concurrency = 1`, because it paces
itself against a 20-requests-per-minute limit and overlapping its chunks would spend the
allowance faster rather than finish sooner.

This is a prerequisite rather than a convenience: `--repeats 3` is what settled chunking, and
it was only affordable once the calls overlapped.

### Finding: the truncation that killed a run was a draw, not a budget

One `corrector-blocks` call on `carta` died with `response truncated by max_tokens` at 32,000
output tokens, which took the fragment out of the run and made the row look like a loss.
Raising the cap to 64,000 and re-running produced no truncation at all and spent ~10,000
output tokens per call — a third of the *old* cap. The runaway is stochastic, so a bigger cap
is insurance and not a fix.

A run that moves it is measuring something else, so `MAX_OUTPUT_TOKENS` reads
`EVAL_MAX_OUTPUT_TOKENS` and every report records the value it used, next to `max_retries`.
Both SDKs already retried twice on 429s and 5xx; that is now pinned explicitly at 3 rather
than inherited from a default that can move under an upgrade, because a failed call is not a
bad score — it is a fragment dropped from the false-positive rate.

## Interfaces

**HTTP — partly done.** `api/main.py` exposes `POST /correct-file`: a path in, the corrected
text and the proposed/applied/rejected counts out. It never writes the file. A pass whose
every call failed answers 502, because returning the original text with a 200 reads as "this
text is clean" — the confusion `parse_edits` already refuses to make. A pass that lost only
some of its calls returns what the rest produced.

**Blocked on one decision before it can leave `127.0.0.1`**: `file_path` is not restricted to
any root, so every file the process can read is readable through the endpoint. The remedy is
a contract choice — an allow-list, the content in the request body instead of a path, or
auth — and not a patch.

It carries no document-level pass of its own, so it inherits H5's ceiling: nothing above
~2k words runs end to end.

## H6 — Google Drive
- Read a document from Drive, write the corrected version + the report (Docs with
  suggestions or a copy).
- **Done when**: a full cycle from a real Doc of the user's.

## Dropped milestones

Kept as two lines each because the reason they died is a measurement, and that is worth more
than the milestone was.

**H2 — Verifier (overcorrection control).** A second pass accepting or rejecting each edit,
with a strong arbiter on disagreement. Dropped: H1 removed its premise. A verifier exists to
delete false positives and there are almost none left — 0.12 per 1,000 clean words for
`corrector-blocks`. Its entire ceiling was a handful of edits, bought with a second paid pass
and paid for in recall, which is the weak side of the ledger. Overcorrection control folds
into H4.

**H7 — Cost.** Prompt caching, batch API, routing. Dropped: the target was met at H1 —
$0.0291 per 10k words against `naive-claude`'s $1.6030 — before any of those techniques were
applied. The latency half moved to H5, where concurrency settled it.

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
