# Architecture: autonomous corrector for literary texts in Spanish

## 0. Problem, user and competition

**Problem.** Correcting a literary manuscript (spelling, grammar, orthotypography and light
style) is expensive and slow with a human copy editor, and unreliable with automated tools:
classic correctors (Word, LanguageTool) do not understand literary context, and chat LLMs
overcorrect, flatten the author's voice and lose consistency over long texts.

**Who it is for.** Fiction writers in Spanish —from the hobbyist who self-publishes to the
professional who wants to hand a clean manuscript to their editor— who need
professional-quality correction, cheap, that respects their voice without having to
supervise the process sentence by sentence.

**The real competition: pasting the text straight into an LLM** (dropping chapters into
Opus/ChatGPT). It is free or nearly so, and sentence by sentence it corrects well. Its
weaknesses —which are exactly our value proposition— are: it rewrites instead of correcting
(the author has to compare two versions by eye), it overcorrects with no control and no
measurement, it loses its criteria on long manuscripts (manual chunking, no consistency
across chapters), and it offers no traceability of what changed and why. Secondary
competitors: LanguageTool/Stilus (no literary understanding) and professional human
correction (our quality ceiling, at 100x our cost).

## 1. Product thesis

A generalist LLM (Opus on claude.ai) already corrects a standalone paragraph well. We
cannot beat it on raw intelligence; we can beat it —and this is where the product lives— on:

1. **Overcorrection control.** It is the documented failure #1 of LLMs in correction (they
   flatten the voice: less lexical diversity, normalized sentences, punctuation "fixed").
   A chat does not measure it; we do, and we filter it out with a verifier pass.
2. **Manuscript-scale consistency.** A chat degrades on long texts and guarantees no uniform
   criteria (is it «Méjico» or «México» in ch. 3 and ch. 17?). A pipeline does.
3. **Traceability.** We never rewrite: we emit typed, pinpoint edits with a diff and a
   justification. The writer audits, instead of comparing two versions by eye.
4. **Cost.** A cheap model as the workhorse + a strong model only as arbiter. Target: cents
   per 10k words, against pasting chapters into an Opus chat.

The edge comes from the pipeline + measurement, not from the model. That is attainable;
what is not realistic is beating Opus at the stylistic judgement of an isolated sentence.

## 2. What the state of the art says (applied summary)

- **Minimal-edit GEC > fluency-edit** for our case: the goal is to correct errors, not to
  "improve" the text. LLMs default to fluency-edit; minimal-edit has to be forced through
  the prompt and the output format (a list of edits, not rewritten text).
- **Overcorrection is systematic and directional**: every model pushes style in the same
  direction (normalization). "Preserve the voice" prompts reduce the magnitude but not the
  direction → a downstream filter is needed, not just a better prompt.
- **Classic metrics (ERRANT/M², GLEU) underrate**: ~74% of the corrections that differ from
  the gold are just as valid or better. Modern evaluation is hybrid: reference metrics + a
  double LLM judge with a human only on disagreement (cuts human work ~64% at κ≈0.7-0.9
  against experts).
- **Seeded errors give exact references for free**: corrupting clean text with typed errors
  yields precision/recall per error type without manual annotation. The clean, uncorrupted
  text measures the false-positive rate (= overcorrection).
- Model ensembles add little (LLMs fail alike, ρ≈0.95 across models); a single model + a
  verifier beats three models voting.

## 3. Decision: Neo4j with RAE/Fundéu norms? No.

The norms have no graph shape: they are a flat list of rules with examples. On top of that,
current LLMs already know the core RAE/Fundéu norms; their failures are of *application*
(not seeing the error in context, or correcting what they should not), not of *lookup*. A
graph adds infrastructure without attacking the real problem.

**Replacement: a versioned rule pack in the repo.** Markdown/YAML files with the rules where
the eval proves the model fails (dialogue dash, laísmo/leísmo, dequeísmo, Spanish angle
quotes, capitalization...), with minimal examples. They are injected into the prompt by
category. Only if the pack outgrows the prompt budget do we add simple retrieval
(BM25/embeddings over SQLite). Zero infrastructure until the data asks for it.

## 4. Pipeline

```
manuscript
   │
   ▼
[1] Ingest and segmentation ──► chunks with overlap + proper-name glossary
   │
   ▼
[2] Voice profile (1 pass, cached) ──► register, characteristic punctuation,
   │                                   deliberate traits (e.g. a character's laísmo)
   ▼
[3] Corrector pass (cheap model, minimal-edit)
   │     JSON output: {original, correction, type, rule, confidence}
   │     types: spelling | grammar | orthotypography | style
   ▼
[4] Verifier pass (second prompt/model)
   │     a real error? does it respect the voice profile? → filters overcorrection
   │     disagreement → arbiter (strong model) or flagged "doubtful"
   ▼
[5] Global consistency ──► aggregated edits: double spellings, uniform criteria
   │
   ▼
[6] Deterministic application + report
         · mechanical (spelling/typography, high confidence): applied on their own
         · style/doubtful: left as suggestions with a diff in the report
```

Key points:

- **The model never rewrites text.** It emits anchored edits (like a `search/replace`); the
  application is deterministic code. If an anchor does not match, the edit is discarded and
  logged. This removes the risk of silent rewrites.
- **The anchor is scoped to a line.** The text goes to the model with its lines numbered, and
  an edit names the line it belongs to. Without it the anchor has to be unique across the
  whole manuscript, which forces the model to quote a clause where a word would do —
  precisely on orthotypography (a dash, a quote mark, a comma), which is the one category
  where the baselines already fail. The line is a hint, not a claim: an anchor that is unique
  in the whole text resolves whatever line the model thought it was on.
- **Autonomous but auditable**: the agent iterates on its own (correct → verify → apply),
  and the only output towards the writer is the corrected document + the corrections report.
  It asks no questions unless genuinely blocked (e.g. an ambiguous proper name).
- **The voice profile is the editing policy**, not a plea in the prompt: the verifier rejects
  edits that contradict traits declared deliberate.

## 5. Evaluation (scientific and cheap)

In `evals/`, runnable with one command, with the cost per run recorded.

| Corpus | What it measures | Metric |
|---|---|---|
| A: clean text + errors seeded by the corruptor | detection/correction | precision, recall, F0.5 **per error type** |
| B: the same clean text, untouched | overcorrection | false-positive rate (headline metric) |
| C: real text with natural errors (COWS-L2H and/or the user's own manuscripts) | external validity | double LLM judge (consensus; human only on disagreement) |

- **Voice**: stylometric distance original↔corrected (mean sentence length, lexical richness,
  punctuation density) — cheap, objective, catches the directional flattening.
- **Fixed baselines**: (a) LanguageTool, (b) a naive prompt to a strong model ("correct
  this"). Every prompt/pipeline change is compared against the previous run and the baselines.
- The corruptor is the central scientific piece: typed errors = exact references for free,
  reproducible, and they steer the rule pack (a rule is added ↔ an error type is added to the
  corruptor ↔ we measure whether it improves).

## 6. Stack

| Piece | Choice | Why |
|---|---|---|
| Language | Python 3.12 (the current one) | the skeleton already exists |
| LLM client | `openai` lib (OpenAI-compatible API) | provider-agnostic, already in use |
| Corrector model | DeepSeek V4-Flash (~$0.14/$0.28 per M tok) | cheap workhorse |
| Verifier model | the same cheap one with a judge prompt; strong arbiter (Sonnet/GPT) only on disagreement | the arbiter's cost is paid only on ~hard cases |
| Edit schema | Pydantic | validation of the output JSON |
| Eval state / runs | SQLite + JSON in the repo | zero infra |
| Norms | markdown/YAML rule pack in the repo | see §3 |
| Drive | `google-api-python-client` (final phase) | standard integration |
| Agent frameworks | **none** (no LangChain or similar) | the pipeline is not agentic: a fixed sequence of calls with structured output, with the model never picking a tool. There is nothing to orchestrate |

The concrete models are swappable via config; the evaluation decides with data whether a
model change pays off.

## 7. Accepted risks

- The LLM judge shares biases with the corrector → mitigation: a double judge from different
  families + periodic human sampling.
- Clean contemporary corpora are scarce (public domain = 19th-century Spanish) → mix classics
  with the user's own text and verified contemporary fragments.
- COWS-L2H is L2 learner text, not literary → useful for grammar, not for voice; voice is
  measured on corpora A/B.

## Sources

- [Adapting LLMs for Minimal-edit GEC (BEA 2025)](https://arxiv.org/abs/2506.13148)
- [Multi-Dimensional Evaluation of LLMs for GEC (AIED 2026)](https://arxiv.org/html/2605.07635)
- [Voice Under Revision: LLMs and the Normalization of Personal Narrative](https://arxiv.org/pdf/2604.22142)
- [GEC: A Survey of the State of the Art](https://arxiv.org/pdf/2211.05166)
- [MultiGEC-2025 shared task](https://spraakbanken.github.io/multigec-2025/)
- [COWS-L2H (Spanish corpus)](https://github.com/ucdaviscl/cowsl2h)
