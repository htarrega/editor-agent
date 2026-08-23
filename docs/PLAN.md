# Plan by milestones

Two rules govern everything below, and they outrank any instruction to move fast.

1. **Nothing becomes a default without numbers from the harness.**
2. **One run is a draw, not a measurement.** The run-to-run spread of this harness is
   **0.043 F0.5** (six runs of one system, same corpus, nothing changed but sampling). A
   difference smaller than that is not a result. Use `--repeats 3`.

---

## Where we are

Every system, measured on the same corpus (4 fragments, 8,254 words, `--repeats 3`,
495 seeded errors) unless the row says otherwise. Rows marked ° are 2026-08-17/18;
everything else is 2026-08-24 and is not mixed with the older rows — see "The deadline
was a bet" below for why that matters, and "Two bugs" for why the `$/10k` column itself
changed shape.

| system | F0.5 | P | R | FP/1k clean | $/10k words | s/document | draws |
|---|---|---|---|---|---|---|---|
| **`corrector-blocks`** — reference row, and what the API ships | **0.963** | 0.974 | 0.921 | 0.24 | 0.0415 | ~74 | 2 |
| `corrector-swept` — promising cost row, not shipped | 0.952 | 0.955 | 0.941 | 0.85 | **0.0354** | ~60 | **1** |
| `corrector-lean` — tried as a cost row, refuted | 0.929 | 0.942 | 0.883 | 0.36 | 0.0378 | ~68 | 1 |
| `corrector-raced` — no longer shipped | 0.860 | 0.933 | 0.655 | 0.00 | 0.0824 | **5.6** | 1 |
| `corrector-gemini` — paid key, refuted | 0.934 | 0.934 | 0.937 | — | 0.1066 | 31 | 1 |
| `corrector-fast`° | 0.867 | 0.904 | 0.745 | 0.24 | 0.056¹ | 2.4 | — |
| `rules-only`° | 0.789 | 0.970 | 0.453 | 0.12 | **0** | **0.00** | — |
| `naive-claude`° — the baseline² | 0.899 | 0.894 | 0.921 | 2.18 | 1.603¹ | ~97 | — |

¹ Pre-2026-08-16 DeepSeek rate; not directly comparable to the rows above it.
² Measured on a **different corpus** — see the warning in H0.

**The deadline was a bet, and 2026-08-24 is the day it did not pay.** `raced`'s whole case
was under-five-seconds without giving up the deliberation, bought with redundancy: three
deliberated tickets racing a `hurried` no-reasoning floor, first answer wins. That case was
built on one day's reading of how fast the provider answers. Re-measured `--repeats 3`,
`raced` alone and uncontended: **F0.5 0.860, not 0.919** — recall down to 0.655 from 0.857,
a gap four times the harness's own 0.043 spread, so this is not sampling. The deadline itself
never moved; more of the three deliberated tickets per block simply stopped beating it, so
more blocks fell back to the cheap `hurried` answer, which finds less by design.

`corrector-blocks` was re-measured the same day, same corpus, no hard deadline to miss:
F0.5 0.963. The comparison *between* `blocks` and `raced` on one day holds regardless of what
either number would have read on a different day: `blocks` is both better and roughly half
`raced`'s cost ($0.0415 against $0.0824 per 10k words), which is not a trade, and
`EDITOR_AGENT_SYSTEM` now defaults to it. `raced` stays registered, out of `DEFAULT_SYSTEMS`,
for whoever still wants the clock and can accept that its quality is a bet on the hour rather
than a number.

**Two bugs, found chasing this, neither about which system is faster.**

1. **DeepSeek moved `deepseek-v4-flash` off its flat $0.14/$0.28 rate to peak/off-peak billing
   on 2026-08-16** — UTC 01:00–04:00 and 06:00–10:00 at $0.44/$1.32, every other hour (most of
   the day, and every measurement in this section) at $0.22/$0.66. `corrector/llm.py`'s
   `PRICING` table still had the old flat rate; every run before this fix understated what it
   actually billed. Fixed in `_deepseek_v4_flash_rate` (`corrector/llm.py`), covered by
   `tests/test_corrector/test_llm.py` with the hour injectable so the tests do not depend on
   when they run.
2. **The harness's `coste$` column (`evals/run.py:summary_row`) prints `usage.cost_usd`
   directly — the raw total for the whole run, not `$/10k words`.** This document's older rows
   (`corrector-blocks` 0.019, `corrector-raced` 0.171) read as if they were already
   per-10k-words; reproducing `blocks`'s the same way this session confirms it was — 0.0612 raw
   over 33,016 words (8,254 × 1 corpus B + 8,254 × 3 corpus A at `--repeats 3`) normalizes to
   $0.0185, matching the old row almost exactly. Reproducing `raced`'s the same way does not:
   0.1706 raw normalizes to $0.0517, not $0.171 — the older `raced` row appears to have quoted
   the raw total as if it were the per-10k figure, a mismatch this document cannot fully
   reconstruct now and is flagging rather than guessing at. Every `$/10k` figure from
   2026-08-24 onward in this file is computed by hand from each report's own `usage`
   (input/output tokens × the rate that applied ÷ words/10000), not read off the column.

Both bugs point the same direction: **every cost figure this document quoted before
2026-08-24 understated the real number**, by roughly 2.2× from the pricing change alone and,
for `raced` specifically, by a further ~3.3× from the units mismatch. Neither bug is about
`blocks` against `raced`; both rows moved together, so the comparison between them survives.
What does not survive is any absolute cost claim made before today.

**Tried the same day, on the theory that less to search for is less to deliberate about:**
`corrector-lean` narrows `blocks`' brief to `juicio` — the types no rule decides — asking
nothing about the four orthotypographic and three dictionary types the rule pack already
owns. 9% cheaper than `blocks` ($0.0378 against $0.0415), but real recall lost (0.883 against
0.921, F0.5 0.929 against 0.963) for reasoning-token counts that barely moved (9,318 against
9,966 per call). A narrower brief bought a less careful read, not a shorter one. Consistent
with narrowing already being logged as a wash on precision below — here it was spent on cost
and lost. Registered as `corrector-lean` / `corrector/presets.py:lean`, not shipped.

**Tried the same day, on a different theory — less to look at, not less to be asked about:**
`corrector-swept` runs the rule pack *before* the call instead of after (`Corrector`'s new
`precorrect=True`, replacing `mechanical=True`'s post-hoc override for this shape): the model
never sees a straight quote, a missing accent on a dictionary word, or any of the other three
rule-decidable types, because they are already fixed in the text it reads. Its edits resolve
against that swept text and are translated back to the caller's original offsets by
`_remap_to_original` (`corrector/correct.py`), which refuses — rather than guesses at — a span
that lands inside a rule's own replacement. One draw, `--repeats 3`: F0.5 0.952 against
`blocks`' 0.963 (inside the spread), recall *up* (0.941 against 0.921), at 15% less cost
($0.0354) and 12% fewer reasoning tokens per call (8,742 against 9,966). An earlier
single-fragment smoke draw had suggested a far larger cut (reasoning down ~44–62%); at
full-corpus scale that shrank to this — a reminder that a one-fragment draw is not a
measurement either. **Not shipped**: this document's own rule is `--repeats 3` twice before a
result is trusted, and the DeepSeek key ran out of balance
(`{'error': {'message': 'Insufficient Balance', ...}}`, confirmed not transient) mid-way
through the follow-up run. Registered as `corrector-swept` / `corrector/presets.py:swept`,
waiting on a second draw.

**Put a paid key behind Gemini — done, and it settles the question rather than opening it
further.** The free-tier draws that used to sit in this table (F0.5 0.994, one fragment, one
call) suggested Gemini might carry both quality and cost at once. Measured `--repeats 3` with a
paid key: F0.5 0.934 — *below* `blocks`, not above it — at $0.1066 per 10k words, more than
double `blocks`' cost. Gemini's per-token rate is roughly 8× DeepSeek's on output, and while it
needed noticeably fewer reasoning tokens per call, not enough fewer to close that gap. One
call also came back unparseable (`sidra#2: unparseable reply: Extra data...`), the only error
of the run. Registered as `corrector-gemini`, not a candidate.

**What the harness measures did not move with the reversal.** `corrector-blocks` was already
the row in `DEFAULT_SYSTEMS` and the best F0.5 measured; it is now also what ships, so the
two words that used to name two things — the reference row and the shipped one — name one
again. `corrector/presets.py` is where the shipped configuration lives, pinned against the
harness's row by `tests/test_corrector/test_presets.py` so the two cannot drift.

**On the order-of-magnitude question this round of work was chasing:** the honest, measured
number is `raced` → `blocks`, roughly 2×, with quality *up* — real, and the most defensible
number in this section, but not ten times. Every other measured lever either cost real quality
for a small saving (`lean`), cost more for worse quality (`gemini`), or is a genuine further win
that simply is not confirmed yet (`swept`, which would bring the combined figure to roughly
2.3× if its one draw holds). Two more cells of the matrix were identified but not reached, for
the same external reason (the DeepSeek key ran out of balance, exactly -$0.10, confirmed via
`/user/balance` rather than inferred from a failed call) — not because either was tried and
found wanting: `corrector-bare` and `corrector-swift`, the only two rows that remove rather than
shave the reasoning-token majority of the bill. `bare` was the first attempt and is probably the
wrong shape for it — whole-document at `reasoning_effort=none`, which "Settled" already logs as
worse than a window with context. `swift` is the corrected version: `fast`'s own shape, with
`precorrect` in place of `mechanical`, one line different from a row already measured at F0.5
0.867. Everything actually tried this session supports 2-2.3×, not ten times, without either
sacrificing recall or waiting on a draw this session could not pay for; `swift` is the one open
question grounded enough to change that answer rather than merely hope to.

**The cost side of that question is not actually in doubt — only the quality side is, and it is
worth being precise about which.** Reasoning tokens are ~86-90% of every deliberating row's
output; strip them to zero and what is left is only what a call needs to *state* the edits it
found. Computed from `swept`'s own already-measured non-reasoning output (939.9 tokens/call,
9.7% of its output — the honest floor for `swift`, which starts from the same rule-cleaned text)
at today's off-peak rate: **$0.0074/10k words, 11.1× `raced`'s $0.0824.** The arithmetic clears
the target with room to spare. (The same floor computed from `blocks`' own non-reasoning share
instead — 13.9%, before the rules removed anything — is $0.0096/10k, 8.6×; which floor is the
right one to quote depends on which text `swift` is really reading, and it reads the cleaned
one.) So the honest statement is not "10× is out of reach" — it is **"10× is arithmetically
available and has never been quality-tested,"** which is a narrower and more answerable
question than this section spent most of a day treating it as. Every prior test of
`reasoning_effort=none` (`fast`, and the four failures logged under "Settled") asked it of a
model reading text the rules had *not* yet cleared — a different, harder question than the one
`swift` asks, and the reason `fast`'s 0.867 is a prior for `swift`, not a verdict on it.

### What to do next

0. **Measure `corrector-swift` first, the moment the DeepSeek key has balance again** —
   `corrector-fast` (windowed, `reasoning_effort=none`, `context_blocks=12`) with the rule pack
   moved from post-hoc (`mechanical`) to pre-applied (`precorrect`). One line different from a
   row already measured at F0.5 0.867, which is as close to a grounded prior as an unmeasured
   row gets here. `corrector-bare` was the first attempt at this question — deliberation off, on
   text the rules already cleared — and used the wrong shape for it: whole-document, which
   "Settled" (below) already logs as *worse* at `reasoning_effort=none` than a window with
   context (P 0.756 against 0.935). `swift` asks the same question through the shape that
   finding actually supports. `--repeats 3` before trusting a number either way; see
   `corrector/presets.py:swift` and `:bare`.
1. **Confirm `corrector-swept` with a second `--repeats 3` draw**, same key, same balance. If it
   holds, it is the next reversal this file records; if it does not, it joins `lean` as a
   registered refutation. Lower priority than `swift` only because `swift` is the row that could
   change the magnitude of the answer, not just its confidence.
2. **Re-measure `naive-claude` on the current corpus** (`--repeats 3`, ~$1.32 at today's
   Claude rate — not re-verified this round either). The baseline this document quotes
   throughout has never run on it.
3. **Fix `carta.txt`.** It contains `adverti` for `advertí`. H0 requires corpus B to be free
   of the author's own typos, because a system that correctly spots one is scored as having
   overcorrected.
4. **Then H5's manuscript machinery**: name glossary, global consistency pass, overlap at
   the seams, final report.
5. **Reconcile the `raced` units mismatch properly**, if the earlier report or its raw
   input/output token counts turn up — right now this document can say the old $0.171 does not
   reproduce from first principles, not why.

### Blocked on the author, not on work

- Anthropic is ruled out and the balance is spent. Before that, windowed `claude-sonnet-5`
  measured F0.5 0.994 at 11 s on one fragment.
- Nothing authenticates or rate-limits `POST /jobs`, and every call spends money. The
  endpoint stays on `127.0.0.1` until a shared secret, a proxy or accounts is chosen.
- H5 asks for a 30–50k-word text and the corpus holds 8,254. Either a real manuscript goes
  into `evals/corpus/`, or the bar changes to something the harness can score.

---

## Settled — do not spend money reopening these

**About the model**

- Deliberation is what buys recall. Four ways of avoiding it were measured and all four
  failed: `reasoning_effort=none` (10× faster, finds less *and* overcorrects more), a
  non-reasoning model (`corrector-haiku`, 5.6× faster, loses a quarter of the recall,
  McNemar p = 2.4e-24), an off-taxonomy label as a filter (61 of 145 proposals off-schema,
  only 5 of them false), and the model's own `confidence` (8 of 23 wrong edits came with
  1.0). **The model does not know when it is wrong.**
- `deepseek-v4-pro` is the same trade one notch along: 0.802 at 3.5 s, 0.950 at 53 s.
- DeepSeek's context cache is automatic and input-side only, so it cannot touch a bill that
  is ~87% reasoning *output*.

**About splitting the work**

- Chunking is in (H5). Splitting the blocks across calls **without context** is out: it
  costs 0.039 F0.5 and 10× the false positives.
- Splitting them **with** context is in — that is `corrector-raced`.
- At `reasoning_effort=none`, more context is *worse*: the whole document beside a window
  scores P 0.756 against 0.935 for ±600 words.
- Gemini loses recall when windowed (0.971 → 0.657). Sonnet gained. The split is
  model-specific and travels with nobody.

**About buying quality back cheaply — everything here is a wash**

Unioning draws, majority voting, a verifier as a second wave, a scratchpad in the answer,
narrowing the model's brief to what no rule decides. Each buys precision immediately and
pays more recall for it; F0.5 never moves outside the spread. **Only knowledge from outside
the model has ever moved the curve** — the rule pack (+0.15) and the dictionary (+0.04).

**Corrected here, having been wrong in this document before**

- ~~The provider caps parallelism near 16 and the wall clock floors at ~25 s.~~ Both were
  artefacts of building an SDK client inside every call. Shared, sixteen calls go from 8.4 s
  to 2.4 s, and DeepSeek sustains **67 concurrent**.
- ~~No window size fits under five seconds.~~ True at two blocks per call, false at one.
- ~~The run-to-run spread is 0.003.~~ It is 0.043, and that error caused three different
  conclusions to be drawn from the same chunking code.

**Other**

- The output cap binds at ~12,000 words per call at 32k tokens, ~24,000 at 64k. Output
  demand runs ~2,700 tokens per 1,000 words and is the side that binds.
- Repeated text cannot measure scale: the model recognises the repetition and coasts.

---

## H0 — Evaluation harness — **done**

`python -m evals.run` prints a metrics table and the cost of the run, and writes full detail
to `evals/results/<timestamp>.json`. 17 corruptor rules (one per taxonomy type), 4 clean
fragments, precision/recall/F0.5 per type with cluster matching, false-positive rate,
stylometry.

### Decisions

**The corpus lives outside the repository.** `evals/corpus/` and `evals/results/` are
gitignored: they are the author's own texts and the reports quote them. A clean clone cannot
run the harness, and that is the price.

**A new fragment goes in clean or it does not go in.** On corpus B every edit counts as a
false positive, so a pre-existing typo contaminates the headline metric.

**The baseline prompt may state the output contract, never the correction policy.** «Devuelve
únicamente el texto corregido» is allowed; «haz ediciones mínimas» or «respeta la voz» is
not — that is the product thesis, and handing it to the baseline would measure the pipeline
against itself. Every report stores each system's `prompt` and `model`.

### The bar

| system | P | R | F0.5 | FP/1k | cost |
|---|---|---|---|---|---|
| null | 0.000 | 0.000 | 0.000 | 0.00 | $0 |
| languagetool | 0.424 | 0.630 | 0.454 | 14.90 | $0 |
| naive-claude | 0.894 | 0.921 | **0.899** | 2.18 | $1.32 |

**F0.5 > 0.899, FP/1k < 2.18, voice < 0.002** — and the real gap is cost: $1.60 per 10,000
words where the product needs cents.

### Finding: without an output contract, a strong model does not return a document

With the bare prompt `"Corrige este texto:"`: DeepSeek blew through 32,000 output tokens in
6 of 8 calls, rewriting the text and then justifying every change. 45% of Claude's false
positives were markdown and the preamble «Aquí tienes el texto revisado…». On one fragment
Claude returned literary criticism instead of the text, and the diff compared prose against
a review.

This is the argument for ARCHITECTURE §4 — the model never rewrites, it emits anchored edits.

### Warning: `naive-claude` has never been measured on the corpus in use

Its row (0.899, FP/1k 2.18, $1.6030) comes from fingerprint `d5e1ee1f` at **`repeats 1`**.
Everything since chunking uses `656d9e4c` at `repeats 3`. `repeats` enters the fingerprint,
so these are different corpora and `reuse` will not mix them.

What can honestly be said:

- **On overcorrection, cost and latency the comparison is orders of magnitude and safe.**
  2.18 false positives per 1,000 clean words against 0.12; $1.603 against $0.019; ~97 s a
  call against 4.35 s a document.
- **On F0.5 it rests on H1's pairing** — `corrector-v0` 0.929 against 0.899 on the same
  corpus — which alone is inside the spread. What carries it is the six-run study: 0.926,
  0.929, 0.942, 0.938, 0.911, 0.954, all six above 0.899. Six draws of ours against one of
  theirs.
- **`corrector-raced` cannot claim it**: 0.012 inside a 0.043 spread, on a different corpus.

Sixteen calls would settle it.

---

## H1 — Corrector v0 — **done**

Minimal-edit prompt, JSON output of typed edits, deterministic application by anchors, every
discard logged. **F0.5 0.929 against the bar's 0.899.**

### Decisions

**The anchor carries a line number.** Without it an anchor must be unique across the whole
fragment — fine for `corrio`, useless for `"` or ` ,`. The line is a hint, not a claim: an
anchor unique in the whole text resolves whatever line the model thought it was on.

**The marker sits on its own line** (`[7]` above the text, not `7| texto`). Half of what this
pass corrects is orthotypography, and a dialogue dash judged inside `7| —Vamos` is a dash
shown in a context the author never wrote.

**The corrector's prompt may say what the baseline's may not** — «edición mínima», «ante la
duda, no corriges», the invented words are the author's. That policy is the product.

**`--fresh` exists because `--reuse` is a trap for the system under development.** After its
first run it has a cache too, and a routine `--reuse` publishes last run's numbers as this
run's, silently.

### Finding: the runaway is the reasoning, and JSON output does not stop it

On a *clean* 250-word fragment the model spent all 32,000 output tokens deliberating over
dialogue punctuation and emitted nothing. What stops it is `reasoning_effort`:

| effort | corpus A edits | corpus B false positives | reasoning tok | s |
|---|---|---|---|---|
| `none` | 25 | 6 | 0 | 10 |
| `minimal` | 33 | **0** | 13,437 | 99 |
| `low` | did not return in ~20 min | — | — | — |

`minimal` is the default. Chunking bounds reasoning per call, but the effort cap is what
makes a single call terminate at all — the two are not substitutes.

### Result

| system | P | R | F0.5 | FP/1k | $/10k | s/call |
|---|---|---|---|---|---|---|
| languagetool | 0.424 | 0.630 | 0.454 | 14.90 | 0.0000 | 4.9 |
| naive-deepseek | 0.713 | 0.873 | 0.740 | 2.54 | 0.0204 | 37.6 |
| naive-claude | 0.894 | 0.921 | 0.899 | 2.18 | 1.6030 | 97.2 |
| **corrector-v0** | **0.952** | 0.848 | **0.929** | 0.00 | **0.0291** | 59.4 |
| corrector-claude | 0.987 | 0.909 | **0.970** | 0.12 | 1.0144 | 54.6 |

### Finding: the pipeline is worth more than the model, and most to the cheap one

F0.5, precision in brackets:

| | naive prompt | our prompt | **the prompt is worth** |
|---|---|---|---|
| **Claude** | 0.899 (0.894) | 0.970 (0.987) | **+0.071** |
| **DeepSeek** | 0.740 (0.713) | 0.929 (0.952) | **+0.189** |
| **the model is worth** | +0.159 | +0.041 | |

On the same model our prompt takes precision from 0.894 to 0.987 and cuts false positives
eighteenfold at unchanged recall — and costs *less*, because emitting edits is fewer tokens
than rewriting. **The pipeline contributes more than the model does, and most where the model
is weakest.**

`corrector-v0` at 0.929 beats `naive-claude` at 0.899 for 1/55th of the cost. The cost goal —
cents per 10,000 words — is met here, before caching or batching, which is why no separate
cost milestone survives.

### Finding: the run-to-run spread is 0.043, and one run decides nothing

`corrector-v0`, six times over the identical corpus, changing nothing but sampling:

| | | | | | | mean | spread |
|---|---|---|---|---|---|---|---|
| **F0.5** | 0.926 | 0.929 | 0.942 | 0.938 | 0.911 | 0.954 | 0.933 | **0.043** |
| **recall** | 0.836 | 0.848 | 0.879 | 0.842 | 0.788 | 0.903 | 0.849 | **0.115** |

This was first written up from two runs as a spread of 0.003. That error made chunking be
declared a win, then dead, then settled — three conclusions from the same code, two wrong.

Consequences: **FP/1k is not a usable headline metric** at 0–3 events per 8,254 words;
**`--repeats 3` is the posture, not an option**; and **two systems are compared on paired
outcomes** (McNemar on the discordant pairs), not on two headline numbers.

---

## H4 — Failure-driven rule pack — **half done**

`corrector/rules.py` decides 9 of the 17 error types with no model call, in ~5 ms:
**224 of 495 seeded errors at P 0.970**, and one edit on 8,254 words of clean prose — the
real typo in `carta.txt`, not a false positive. Opt-in via `Corrector(mechanical=True)`.

| decided by | types | note |
|---|---|---|
| the norm | `comillas`, `espaciado`, `mayuscula`, `raya_dialogo`, `signo_apertura`, `verbo_2sg` | `comillas` 22/22, against the 14/22 that motivated this milestone |
| a dictionary | `tilde`, `ortografia_h`, `ortografia_bv` | `corrio` is not a word and `corrió` is |

**What remains needs the sentence read.** `tilde_diacritica` (`esta`/`está`) and `homofono`
(`tuvo`/`tubo`) are pairs of real words; `loismo` is grammar. No rule decides any of them.

### Decision: a rule is kept only if being incomplete is its worst failure

The corpus is four fragments by one author. A rule tuned until that sample is clean has
learned the sample. So: **an incomplete rule costs recall, a wrong rule costs precision, and
only the first is acceptable.** Every rule kept is silent where it is ignorant — a straight
quote is wrong in every Spanish text there is; the dictionary rules need a repair *into* the
dictionary, so an unknown word is left alone. `signo_apertura` (P 0.833) is the one that can
be wrong rather than silent, because where the `¿` goes is a judgement.

`tests/test_corrector/test_rules.py:GeneralisesBeyondTheCorpus` enforces this offline against
prose deliberately unlike the corpus, to the same bar as corpus B: nothing to correct.

### Refuted: gender agreement is decidable and was still not worth having

It is genuinely decidable — a noun's gender is lexical and the determiner agrees with it — and
it reached R 0.725 at **P 1.000** with no false positives on corpus B. Dropped anyway:

- **It bought nothing.** With it, F0.5 0.862; without, 0.867 / 0.867 / 0.874. The model was
  already finding what it found.
- **Its precision rested on five hand-written exception lists.** Every gap in them is a
  *wrong* edit, not a missed one, and the corpus cannot tell you what is missing from a list
  of exceptions. `el karma`, `la disco` and `el cisma` were not on it.

Worth keeping from the attempt: a noun lemmatises to its own singular while a verb lemmatises
to an infinitive, which is a reusable part-of-speech signal.

### Finding: the dictionary refuses to guess, and that is why it works

Three types where a misspelling turns a real word into a **non-word**. The rule fires only
when the word is not Spanish *and* exactly one minimal repair makes it Spanish. The second
half is what keeps it off `vasu`, `pumarada`, `fíos`, `gomitar` and `merequetengue` — they
are not in the dictionary either, and nothing puts them there. **Being unknown is never on
its own a reason to touch a word.**

Two guards, both bugs first:

- **Accents are only ever added.** The dictionary lacks `ojalá` and `jamás` but holds `ojala`
  and `jamas` as forms of `ojalar` and `jamar`, so the removal direction proposed stripping
  accents off two correct adverbs.
- **A verb with pronouns attached is not a misspelling.** `irme` is not in the dictionary and
  `hirme` is. Caught by taking the pronouns off and asking again.

`simplemma` is the word list — bundled data, no network, no system package. `pyspellchecker`
was tried and rejected: its Spanish list is 86,158 words and lacks `hubo` and `corrió`.

### Finding: the rule pack is the only part that cannot fail

Whether it still earns its place next to a *deliberating* model was measured with
`EVAL_RACE_RULES=0`. Back to back on one fragment: **F0.5 0.920 with the rules, 0.773
without** — 0.147, larger than the whole gap to the default. One draw a side, so the
direction is solid and the third decimal is not.

The better half of the answer arrived by accident. The full control run returned **F0.5
0.000 — all 1,096 calls missed the deadline**, because DeepSeek was slower that hour and with
`mechanical=False` there is no floor: every window times out and the document comes back
untouched. With the rules on it still returns its 224 of 495, in five milliseconds, whatever
the provider is doing. **A deadline needs a floor, and this is the floor.**

---

## H5 — Full manuscript

Chunking and concurrency are done. What remains is the manuscript-scale machinery: name
glossary, global consistency pass, overlap at the seams, final report.

**Done when** it processes a 30–50k-word text end to end with no intervention. Not yet —
there is no document-level pass.

### Result: chunking raises the floor, and the floor is what matters

`corrector-blocks` is `corrector-v0` with `block_words=50`, everything else held fixed.
`--repeats 3`, both systems fed byte-identical text:

| system | P | R | F0.5 | FP/1k | anchors rejected |
|---|---|---|---|---|---|
| corrector-v0 | 0.957 | 0.820 | 0.926 | 0.24 | 16 |
| **corrector-blocks** | **0.968** | **0.875** | **0.948** | **0.12** | **4** |

Paired: 28 caught only by `corrector-v0`, 55 only by `corrector-blocks`, McNemar p = 0.004.
Read that as optimistic — errors inside one call share a deliberation, so the effective
sample is nearer 12 calls than 495 errors.

**Yet case by case it is 6–5 and a tie.** Chunking does not lift the ceiling; it lifts the
*floor*, and only where paragraphs are long. On `carta` (245 words a paragraph) the worst
draw goes from 0.455 to 0.705. **The cheap model is not made cleverer by smaller blocks, it
is made predictable** — and for a 50k-word manuscript that is what matters, because what
ruins a manuscript is the bad draw, not the average.

Recall by position is flat (0.879 first half against 0.871 second), so a long block is
under-read evenly throughout rather than abandoned partway.

`block_words = 50` is the only value ever measured, chosen because it is the order of the
paragraphs the model already handles well.

### Refuted: a block is a unit of numbering, not a unit of inference

`blocks_per_call` sends N blocks per request. Overlapped and re-measured at `--repeats 3`:

| system | shape | P | R | F0.5 | FP/1k clean | wall |
|---|---|---|---|---|---|---|
| **corrector-blocks** | all blocks, 1 call | **0.960** | **0.899** | **0.947** | **0.12** | 650 s |
| corrector-batched | 10 blocks, ×8 | 0.918 | 0.873 | 0.908 | 1.21 | **322 s** |

Batching halves the wall clock and loses 0.039 F0.5, taking false positives on clean text
from 0.12 to 1.21. The recall difference is not established (McNemar p = 0.10); the precision
difference is where the cost sits, and precision is what the product exists to protect.

The reason is in the prompt, not the arithmetic: the rule that a strange word *coherente con
el resto del texto* belongs to the author cannot be applied by a call that never saw the rest
of the text. **That is what `corrector-raced` fixes — it splits the calls without splitting
the context.**

### Result: the deadline is a promise, and redundancy is how it is kept

One block per call at `reasoning_effort=minimal`, with ±600 words of context, scores **F0.5
0.948 on its own** — the default's number — and takes 19 s. All 19 are the tail:

| min | median | p90 | max |
|---|---|---|---|
| 1.8 s | **4.3 s** | 16.1 s | **19.0 s** |

The median call already fits in five seconds. The wall clock is the *slowest of sixty-four*.
Deliberation is a lottery, and a pass that waits for every ticket waits for the worst one.

Three things make the fix work:

- **Redundancy beats patience.** Each call is issued three times at once, first answer wins.
  The provider sustains 67 concurrent calls, so the copies cost nothing in wall clock.
- **The deadline is a promise, not a timeout.** The pass returns what it has at 4.3 s. A
  system that *usually* takes four seconds is bounded by nothing.
- **Submission order is the reverse of preference order.** A fast `reasoning_effort=none`
  ticket for *every* block is queued first, so the floor is bought before any redundancy is.
  Ordered the other way, a long document spends its whole budget on its first third: on the
  2,563-word fragment that took recall from 0.914 to **0.569**.

Measured twice, `--repeats 3`, per-document clock at `--concurrency 1`:

| system | P | R | F0.5 | FP/1k clean | $/10k | s/doc | worst |
|---|---|---|---|---|---|---|---|
| **corrector-blocks** | 0.960 | 0.899 | **0.947** | **0.12** | **0.019** | ~88 | ~90 |
| **corrector-raced** | 0.936 | 0.857 | 0.919 | 0.36 | 0.171 | **4.35** | **4.78** |
| corrector-raced, again | 0.916 | 0.857 | 0.903 | 0.12 | 0.175 | 4.35 | 4.78 |

**Every one of the 32 documents finished under five seconds**, with zero failed calls in
either run. The guarantee is a ceiling the code enforces, not a mean that falls out of it.

**The quality difference is 0.036 against a spread of 0.043** — by the rule this document set
for itself, a difference it cannot resolve. It is bought at 9× the money ($0.171 per 10,000
words against $0.019), which is where the 1,780 calls a run go. Still cents, still 9× cheaper
than `naive-claude`.

### Warning: the five-second ceiling is deadline plus collection

The 4.78 s worst case was taken on a good hour. On a slow one the same fragment took **5.0 s**.
The bound is `deadline + 0.5–0.7 s` of collecting what came back, and the overhead grows with
how much is still in flight when the clock fires. `EVAL_RACE_DEADLINE=4.0` is the setting for
anyone who needs the bound to hold on a bad day; nobody has measured what it costs.

### Finding: Gemini is faster *and* better than the default, and the key cannot run it

One call, the whole document, deliberation left to the model, rule pack alongside — on
`sidra`, one draw:

| | F0.5 | P | R | s |
|---|---|---|---|---|
| `corrector-blocks` | 0.947 | 0.960 | 0.899 | ~88 |
| **`gemini-2.5-flash`, one call** | **0.994** | **1.000** | **0.971** | **31** |

The key is free tier: **20 requests a day**, ~5 concurrent. The `--repeats 3` run that would
have pinned this lost 15 of its 16 calls. `corrector-gemini` stays registered for whoever has
the quota.

### Decision: the harness runs its calls concurrently

`evals/run.py:correct_all` maps `system.correct` over the texts through a thread pool.
**Results come back in input order** — scores and samples are appended in corpus order, so
out-of-order results would have two runs of one corpus writing two different reports.
`usage.seconds` sums each call's own duration and is concurrency-invariant, which is why
`wall_seconds` and `document_seconds` are recorded beside it. A system may pin its own
ceiling: `LanguageToolSystem.concurrency = 1`, because it paces itself against a
20-requests-per-minute limit.

### Finding: what bounds one call is output demand

At `EVAL_MAX_OUTPUT_TOKENS=64000`, on distinct prose:

| words | input tok | output tok | s | output per 1k words |
|---|---|---|---|---|
| 8,254 | 14,777 | 23,551 | 144 | 2,853 |
| 16,508 | 28,650 | 44,562 | 247 | 2,699 |
| 24,762 | 42,564 | 51,782 | 268 | 2,091 |

Output demand scales at ~2,700 tokens per 1,000 words and is the side that binds. The cap
sets the chunk: ~12,000 words before 32,000 truncates, ~24,000 before 64,000 does. A 50k-word
manuscript is 3–6 calls, few enough that the seams stay countable.

**Repeated text cannot measure this.** At 24,762 words of tripled corpus the model returned
120 output tokens in 3 seconds — it recognises the repetition and coasts. That reads like a
ceiling and is not one; it would have set the chunk size an order of magnitude too small.

### Finding: truncation was a draw, not a budget

One call died at 32,000 output tokens and took a fragment out of a run. Raising the cap to
64,000 produced no truncation and spent ~10,000 output tokens per call — a third of the *old*
cap. The runaway is stochastic, so a bigger cap is insurance, not a fix. `MAX_OUTPUT_TOKENS`
reads `EVAL_MAX_OUTPUT_TOKENS` and every report records the value it used, next to
`max_retries` (pinned at 3), because a failed call is not a bad score — it is a fragment
dropped from the false-positive rate.

---

## H3 — Voice profile

The verifier this was written against is gone, so the profile feeds the corrector's own
prompt rather than a second pass.

- A single pass extracts the manuscript's stylistic profile; the corrector takes it as policy,
  so deliberate traits are left alone.
- Mechanical edits are applied; style stays a suggestion.
- **Done when**: stylometric distance original↔corrected drops against `corrector-blocks` and
  dialogue with deliberate traits survives intact — over `--repeats 3`.

## H6 — Google Drive

Read a document from Drive, write back the corrected version and the report.
**Done when**: a full cycle from a real Doc of the author's.

---

## Interfaces

**HTTP — submitted and polled, not awaited.** `api/service.py` exposes `submit_job` and
`get_job`, the one place either surface validates a submission or looks one up; `api/main.py`
answers them as JSON at `/api` (`POST /api/jobs`, `202` and a job id out; `GET /api/jobs/{id}`;
`GET /api/health`), and `api/web.py` answers the same two operations as HTML at the bare paths
for the browser. A default pass runs 60–90 s and a blocking POST that long trips proxy
timeouts and, from a browser, looks like a dead server.

A job whose every call failed ends `failed` with the reason in `detail`, because completing
with the original text reads as "this text is clean". A job that lost only some of its calls
completes with what the rest produced and the failures in `errors`.

**The content travels in the body.** `POST /correct-file` is gone rather than patched — it
read any path the process could read. `tests/test_api/test_main.py` pins its absence.

Jobs live in the API process's memory: one container, and a restart loses what was in flight.
Anything more wants a queue, and a queue wants an operational story this does not have. The
newest 256 are kept and finished ones are dropped past that, because a process meant to stay
up for days otherwise accumulates every document it has ever corrected. A running job is
never evicted: that would lose a paid call and leave a poller on an id that never answers.

**A browser front, server-rendered.** `templates/` (Jinja2) and `static/` (vendored HTMX,
vanilla CSS/JS) — HTMX makes the requests, the templates render what `Job` already carries,
and the same process answers both `/api` and the browser's own paths. No separate build, no
second origin, no CORS. Replaced a React-on-Vite front that shared no code with the API: once
HTMX covered the same submit/poll/render lifecycle, the case for a second toolchain went with it.

`EDITOR_AGENT_MAX_WORDS` (2,000) is refused at submit with a `413`. It is a measured ceiling,
not a policy: above it the pipeline runs where nobody has scored it.

---

## Dropped milestones

**H2 — Verifier.** A second pass accepting or rejecting each edit. Dropped three times, of a
different cause each time: first because the default leaves almost nothing to verify (0.12
false positives per 1,000 clean words); then again when `corrector-fast` restored the premise
and the verifier removed false positives exactly as designed — while taking true positives
with them at a worse ratio (F0.5 0.866 against 0.872, and +2.5 s). `corrector-verified` stays
registered so the result reproduces.

**H7 — Cost.** Prompt caching, batch API, routing. Dropped: the target was met at H1 —
$0.0291 per 10k words against `naive-claude`'s $1.6030 — before any of them were applied.

---

## Future work — Narrative coherence

Developmental editing rather than orthotypographic: continuity errors in long manuscripts —
character attributes that change between chapters, impossible chronology, characters who know
things before they could. A knowledge graph in Neo4j, grown from H5's entity glossary, with a
detection pass querying for contradictions. This is where a pipeline with structured memory
gains most over a generalist chat.

## Out of scope (for now)

Other languages, our own fine-tuning, a UI. Only if the eval data justifies it.
