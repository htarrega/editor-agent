# web — Amanuense

The browser front for the corrector. A single screen: paste or upload a manuscript, get it
back corrected, copy or download it. No marks, no annotations, no diff — the corrected text
is the product.

Built with React 19 + Vite on the [Astryx](https://astryx.design) component kit
(`chocolate` theme). Component-level guidance for agents working in here is in
[`AGENTS.md`](AGENTS.md).

## Run

```bash
cd web
npm install
npm run dev          # http://localhost:5173
```

The dev server proxies `/api` to `http://127.0.0.1:8000`, so the API has to be up in the
other terminal:

```bash
uvicorn api.main:app        # from the repository root
```

```bash
npm run build        # tsc -b && vite build  →  web/dist/
npm run lint         # oxlint
npm run preview      # serve the build
```

## How it talks to the API

**Same origin, always `/api`.** In development the Vite proxy rewrites it
(`vite.config.ts`); in production the same container that serves the API serves the build.
Neither side ever needs CORS, and the two situations cannot diverge. `VITE_API_URL`
overrides the base when the API genuinely lives somewhere else.

**Submitted and polled, not awaited.** A pass runs 60–90 s on a 2k-word fragment. A
blocking request that long trips proxy timeouts and, from a browser, is indistinguishable
from a dead server. So `src/lib/proofread.ts` posts the text, gets a job id, and polls —
starting at 500 ms and backing off to 3 s, because short texts finish fast and long ones
should not cost sixty requests.

The whole surface the front depends on is those two endpoints:

| | |
|---|---|
| `POST /jobs` | `{"text": ...}` in; job id and `status` out |
| `GET /jobs/{id}` | `status`, and on completion `text`, `applied`, `proposed`, `skipped`, `errors`, `detail` |

`detail` is shown to the user verbatim: the API explains its refusals in Spanish (empty
text, over the word ceiling), and a generic message would throw that away. A job that ends
`failed` surfaces as an error rather than as the original text, which would read as "this
text is clean".

A submission over `EDITOR_AGENT_MAX_WORDS` (2,000) is refused at submit with a `413`, and an
empty one with a `400`, both before anything is spent at the provider. Jobs live in the API
process's memory and the newest 256 are kept, so a poller that comes back much later gets
the same `404` it would get after a restart.

## Layout

| | |
|---|---|
| `src/App.tsx` | the whole state machine: compose → result, and the handlers |
| `src/views/` | `ComposeView` (the manuscript) and `ResultView` (the correction) |
| `src/lib/proofread.ts` | the API client — submit, poll, surface the failure |
| `src/lib/text.ts` | word/paragraph counting, download, file naming |
| `src/theme/` | the compiled `chocolate` theme; tokens come from CSS, not runtime |
| `src/types.ts` | `Source` and `Stage` |

Fonts are the app's job, not the theme's: Astryx names Fraunces and Albert Sans, and
`index.html` is what loads them.
