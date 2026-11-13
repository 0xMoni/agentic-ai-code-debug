# Debug Squad

**Four Agents. One Smarter Code Review.**

An AI-powered code review system that works like a small software engineering
team. You submit Python code; four specialised agents collaborate on it:

| Agent | Job |
|---|---|
| **Bug-Finder** | Finds logical errors, runtime risks and missing edge cases |
| **Style/Lint** | Runs Ruff, then judges readability and maintainability |
| **Fixer** | Produces a corrected version, and revises it when tests fail |
| **Test-Writer** | Writes pytest cases and runs them in an isolated sandbox |

If the generated tests fail, the Fixer is shown the **real pytest output** and
revises its correction. That loop runs at most three times, and the result is
reported honestly: a fix is only "Verified" when tests actually executed and
actually passed.

---

## Table of contents

1. [Quick start](#quick-start)
2. [Configuration](#configuration)
3. [How it works](#how-it-works)
4. [The seven acceptance criteria](#the-seven-acceptance-criteria)
5. [Example walkthrough](#example-walkthrough)
6. [The screens](#the-screens)
7. [Secure code execution](#secure-code-execution)
8. [Project layout](#project-layout)
9. [Running the tests](#running-the-tests)
10. [Troubleshooting](#troubleshooting)
11. [Viva preparation notes](#viva-preparation-notes)

---

## Quick start

You need **Python 3.11 or newer** (developed and tested on 3.13).

```bash
# 1. From the repository root, create and activate a virtualenv
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 2. Install the dependencies
cd debug-squad
pip install -r requirements.txt

# 3. Configure your LLM provider (see below) - the app needs an API key
cp .env.example .env
#    then edit .env and paste in your GROQ_API_KEY

# 4. Build the vector index (downloads a ~80 MB embedding model, once)
python -m rag.ingest

# 5. Run it
streamlit run app.py
```

The app opens at <http://localhost:8501>.

> **Without an API key** the app still starts, and the deterministic parts work:
> Ruff linting, the syntax checker, ChromaDB retrieval, the sandbox, and the
> keyword intent router. The LLM-dependent agents will report **Not Configured**
> rather than failing silently. You get a free key in about a minute at
> <https://console.groq.com/keys>.

---

## Configuration

Everything is read from environment variables, loaded from `debug-squad/.env`.
No credential is ever hardcoded, and `.env` is in `.gitignore`.

### Groq (recommended — free)

1. Sign up at <https://console.groq.com> and create a key under **API Keys**.
2. Put it in `debug-squad/.env`:

```bash
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_your_key_here
GROQ_MODEL=openai/gpt-oss-120b
```

The **Settings** screen has a *Show available models* button, which
queries Groq's live model list — useful because provider catalogues change and
a model named in documentation may since have been retired.

Groq's free tier is limited on **tokens per minute**. This project is built
around that: prompts are compact, retrieved context is capped, and the output
parser emits a trimmed JSON shape instead of a full JSON schema.

### Ollama (offline fallback)

If you would rather run a model locally:

```bash
# install Ollama from https://ollama.com, then:
ollama pull qwen2.5-coder:7b    # or gemma3:1b on a low-RAM machine
ollama serve
```

```bash
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5-coder:7b
```

A 1B model will frequently produce malformed JSON. The output parser repairs
what it can, but findings will be noticeably weaker than with a larger model.

### All settings

| Variable | Default | What it does |
|---|---|---|
| `LLM_PROVIDER` | `groq` | `groq` or `ollama` |
| `GROQ_API_KEY` | *(empty)* | Your Groq key. Never displayed in full. |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Validated against the live model list |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Local Ollama server |
| `OLLAMA_MODEL` | `gemma3:1b` | Local model name |
| `LLM_TEMPERATURE` | `0.1` | Low, because these are analysis tasks |
| `LLM_TIMEOUT_SECONDS` | `90` | Per-request timeout |
| `LLM_MAX_RETRIES` | `2` | Transport-level retries |
| `MAX_FIX_ATTEMPTS` | `3` | Hard bound on the reflection loop |
| `SANDBOX_BACKEND` | `auto` | `auto`, `docker`, `seatbelt`, `subprocess`, `disabled` |
| `SANDBOX_TIMEOUT_SECONDS` | `20` | Wall-clock limit per test run |
| `SANDBOX_MEMORY_MB` | `512` | Memory cap |
| `SANDBOX_CPU_SECONDS` | `10` | CPU time cap |
| `RAG_ENABLED` | `true` | Master switch for retrieval |
| `CHROMA_PERSIST_DIR` | `chroma_db` | Where the vector store lives |
| `CHROMA_COLLECTION` | `debug_squad_kb` | Collection name |
| `RAG_TOP_K` | `3` | Passages retrieved per query |

---

## How it works

```
                        user code + request
                                 │
                                 ▼
                      ┌──────────────────────┐
                      │   Intent Router      │  zero-shot LLM classification,
                      │ routing/intent_      │  with deterministic keyword
                      │   router.py          │  routing as the fallback
                      └──────────┬───────────┘
                                 │  selects a workflow
         ┌───────────────────────┼───────────────────────┐
         │                       │                       │
         ▼                       ▼                       ▼
  code_explanation          debugging /            test_generation
         │                 style / full review            │
         ▼                       │                        │
   ┌───────────┐                 ▼                        │
   │ Explainer │        ┌─────────────────┐               │
   └───────────┘        │   Bug-Finder    │ few-shot      │
   (nothing else runs)  │  + ast.parse    │               │
                        └────────┬────────┘               │
                                 ▼                        │
                        ┌─────────────────┐               │
                        │   Style/Lint    │ + real Ruff   │
                        │  + RAG context  │               │
                        └────────┬────────┘               │
                                 ▼                        │
                        ┌─────────────────┐               │
                        │     Fixer       │ ◄─────────┐   │
                        └────────┬────────┘           │   │
                                 ▼                    │   │
                        ┌─────────────────┐           │   │
                        │  Test-Writer    │ ◄─────────┼───┘
                        └────────┬────────┘           │
                                 ▼                    │
                        ┌─────────────────┐           │
                        │ Sandbox: pytest │           │ reflection,
                        │ no network,     │           │ max 3 attempts,
                        │ confined FS     │           │ driven by the
                        └────────┬────────┘           │ REAL pytest output
                                 │                    │
                      tests pass?├── no ──────────────┘
                                 │
                                yes
                                 ▼
                    Consolidated review + HTML report
```

Every agent talks to the next through **Pydantic models** defined in
`models/schemas.py` — never loose dicts or free-form strings. That is what
makes this a pipeline rather than four unrelated chatbot calls.

### The reflection loop, precisely

1. The Test-Writer generates tests **from the original code's intent**, not from
   the Fixer's output. Tests derived from the code under test would only ever
   confirm it.
2. Those tests run against the corrected code in the sandbox.
3. If every executed test passes → **Verified**, stop.
4. If tests fail and attempts remain → the Fixer receives the previous code,
   the test source, and the **actual pytest output**, and revises. The *same*
   tests run again — regenerating them would make a later pass meaningless.
5. At `MAX_FIX_ATTEMPTS` → stop and report **Failed**, with the remaining
   failures listed.
6. If the sandbox could not run at all → **Not Yet Verified**. Not a pass, and
   not a failure either, because nothing was learned.

---

## The seven acceptance criteria

Where each one lives in the code, and how to show it working. The
**Compliance** screen runs a live check for each criterion and reports
`Verified` / `Not Yet Verified` / `Failed` / `Not Configured` - a criterion is
only Verified when its check just succeeded, never because the code exists.
Every "Show it" step below is something you do in the app or a test you can
run with `pytest`.

### 1. LLM Integration
`config/llm_provider.py`, `config/ollama_chat.py`

A configurable provider (Groq API or local Ollama). Before a review runs, the
provider's **live model list** is queried and the configured model is checked
against it. Missing credentials produce an actionable message, not a stack
trace. The active model is shown in the sidebar. No credential is hardcoded,
and the key is masked everywhere it appears.

*Show it:* Settings → **Test the connection** sends a real one-word request and shows the reply.

### 2. Interactive GUI
`app.py`, `ui/pages/`

Seven screens. Paste or upload code, load examples, pick a task with one tap
or in your own words, toggle automatic intent detection and the reference
notes, watch live progress, filter issues by severity, compare original and
corrected code side by side or as a diff, read the real test results and the
reflection timeline, inspect every prompt that was sent, run the live
compliance checks, reopen any past review with the exact code that was run,
and download the fixed code, the diff and the HTML report. Every button has a
real callback, and the button-clicking UI tests prove it.

### 3. Prompt Engineering
`prompts/system_prompts.py`, `prompts/user_prompts.py`, `prompts/examples.py`

- **Zero-shot** — intent classification, style review, first-pass fix, test generation.
- **Few-shot** — the Bug-Finder gets three hand-written examples. The third is
  *correct* code whose expected answer is an empty issue list; without it,
  models reliably invent a defect because the prompt implies one exists.
- **Reflection** — the Fixer revises based on the real pytest output.
- **ReAct-style tool use** — `ast.parse`, `ruff`, the knowledge base and the
  sandbox are tools the agents invoke and read back.

The **Prompt inspector** screen shows, for every call actually made: the
technique, the agent, the system prompt, the user prompt *template*, the
rendered prompt, few-shot examples, retrieved context, the output parser, the
LCEL pipeline, the raw model output and the parsed result. Prompts ask for a
short published justification, never hidden chain-of-thought, and no secret
is ever recorded.

### 4. LangChain and LCEL
`chains/lcel_chains.py`

```python
self.prompt = ChatPromptTemplate.from_messages([
    SystemMessagePromptTemplate.from_template(system_prompt),
    HumanMessagePromptTemplate.from_template(user_template),
])
self.parser = RepairingPydanticParser(pydantic_object=output_model)

self.chain = self.prompt | self.model | self.parser   # ← the LCEL pipeline
```

Every LLM call goes through this. `RepairingPydanticParser` subclasses
LangChain's own `PydanticOutputParser` and overrides **`parse_result`** — not
`parse` — because `parse_result` is the method a Runnable actually calls when a
parser sits in a chain. Overriding it is what puts the JSON-salvage logic
genuinely *inside* the pipeline rather than wrapped around it. The explanation
handler uses `StrOutputParser` for markdown output.

*Show it:* `pytest tests/test_lcel_chains.py -v` builds and invokes real chains
with a stub model, asserting the LangChain types and every repair path.

### 5. Agentic RAG
`rag/retriever.py`

Retrieval is **conditional and observable**. A policy table keyed by
`(intent, agent)` decides whether external knowledge is needed:

| Situation | Retrieves? |
|---|---|
| Style review | Yes - Python style conventions |
| Debugging | Yes - Exception and edge-case notes |
| Test generation | Yes - Testing principles |
| Code explanation | No - the code is the whole input |
| The Fixer | No - it already has the findings |
| RAG toggled off | No |

Skipped retrievals carry a reason, so the negative case is as visible as the
positive one — and the prompt genuinely differs between the two paths.

*Show it:* run example 3 ("Make it readable") and the Style agent's log on the
AI agents screen lists what it looked up; run example 4 ("Explain it") and the
Explainer's log shows it deliberately didn't. The Prompt inspector shows the
retrieved passages inside the actual prompt - and their absence when skipped.

### 6. Intent Detection and Routing
`routing/intent_router.py`

Five intents. Detection is zero-shot through an LCEL chain, with a
deterministic keyword matcher as the fallback when the LLM is unavailable,
rate-limited, or returns something unparseable. Manual override is supported
and recorded as such.

| Request | Intent | Agents that run |
|---|---|---|
| "Find the bug in this code" | Debugging | Bug-Finder → Fixer |
| "Improve readability" | Style Improvement | Style/Lint → Fixer |
| "Explain this code" | Code Explanation | Explainer only |
| "Generate edge-case tests" | Test Generation | Test-Writer |
| "Review and fix everything" | Full Review | all four |

Routing genuinely gates execution: the orchestrator constructs only the
selected agents, and the rest appear as **Skipped** with a reason on the AI
Agents screen.

*Show it:* `pytest tests/test_intent_routing.py -v`, then compare the AI agents
screen after "Find the bug" and after "Explain it".

### 7. ChromaDB Vector Database
`rag/ingest.py`, `rag/knowledge_base/`

Six original knowledge documents (~23 KB) covering Python style conventions,
common exceptions, debugging patterns, testing principles, algorithmic edge
cases and code readability. They are chunked on heading boundaries with
overlap — 51 chunks — embedded with ChromaDB's bundled local **ONNX
all-MiniLM-L6-v2** model (384 dimensions, no API key), and stored in a
`PersistentClient` collection with source, title, section and chunk-index
metadata. Search uses cosine distance and returns real distances.

The notes are built automatically the first time the app starts (or by hand
with `python -m rag.ingest`).

*Show it:* Compliance → criterion 7 → **Run this check** performs a live
similarity search and shows each passage with its distance.
`pytest tests/test_rag.py -v` covers chunking, search and the retrieval policy.

---

## Example walkthrough

Load **Example 1** in the code workspace and press **Review my code**:

```python
def find_max(nums):
    maximum = 0
    for num in nums:
        if num > maximum:
            maximum = num
    return maximum
```

What should happen:

1. **Routing** — "Review and fix everything" → Full Review, all four agents.
2. **Bug-Finder** — `maximum = 0` is wrong for all-negative input;
   `find_max([-5, -2])` returns `0`. The empty-list case is undefined.
3. **Style/Lint** — Ruff's findings, then readability suggestions.
4. **Fixer** — seeds from the first element and handles the empty case,
   producing a unified diff.
5. **Test-Writer** — generates a boundary test on all-negative input, stating
   its oracle ("the definition of a maximum"), and runs it in the sandbox.
6. **Verification** — the test passes against the corrected code. If the first
   fix had missed, the **Test lab** would show the reflection timeline:
   `Try 1: 1 failed → Fixer read the failures and revised → Try 2: all passed`.

Then try **Example 4** with "Explain this code". Only the Explainer runs, the
four review agents are marked "Not needed", and the Explainer's log shows
retrieval being deliberately skipped with its reason. That contrast is the clearest
demonstration of criteria 5 and 6.

---

## The screens

| Screen | What it is for |
|---|---|
| **Code workspace** | Paste, upload or pick an example; one-tap tasks; run the review |
| **AI agents** | The four agents, what each did, the tools and prompting it used |
| **Results & history** | The latest review (issues, the fix, tests, summary and downloads) plus every past review with the exact code that was run |
| **Test lab** | Generated tests, real pass/fail, the oracle for each test, raw pytest output, the reflection timeline |
| **Prompt inspector** | Every prompt, technique, parser and LCEL pipeline, with the model's raw output |
| **Compliance** | The seven criteria, each with a live check that has to pass before it reads Verified |
| **Settings** | AI model, test runner and reference notes; the key is always masked |

The sidebar shows a plain-language system status on every page: whether the
AI model is connected, whether tests can run safely, and whether the notes
are loaded.

---

## Secure code execution

Submitted code is **untrusted** and never runs inside the Streamlit process.

The backend is chosen automatically, strongest first:

**1. Docker** (preferred, portable)
`--network none`, read-only root filesystem, memory and CPU caps, a
`noexec` tmpfs, `--pids-limit`, all capabilities dropped, `no-new-privileges`,
and a non-root user.

**2. macOS seatbelt** (`sandbox-exec`, no install needed)
A profile generated per run: `(deny default)`, `(deny network*)`, reads
allowed so Python can start, and writes confined to one temporary directory.
Enforced by the kernel.

**3. `subprocess`** — resource limits and a timeout only, with **no** network or
filesystem isolation. `auto` will **never** select this; it must be requested
explicitly, and the UI labels every result it produces.

On top of whichever backend is used: `RLIMIT_CPU`, `RLIMIT_FSIZE`,
`RLIMIT_NOFILE` and `RLIMIT_NPROC` are applied in the child, plus a wall-clock
timeout. The working directory is created fresh per run and deleted afterwards.

**If no isolated backend is available, execution is disabled** and tests are
reported as *Not Executed* — never assumed to pass.

### Platform setup

| Platform | What you get |
|---|---|
| **macOS** | Works out of the box — `sandbox-exec` ships with the OS. Install Docker Desktop if you prefer a container. |
| **Linux** | Install Docker (`sudo apt install docker.io`, then add yourself to the `docker` group). There is no seatbelt on Linux, so without Docker the backend resolves to `disabled`. |
| **Windows** | Install Docker Desktop with the WSL 2 backend. Docker is the only isolated option. |

`tests/test_sandbox.py` runs a **live escape probe** inside the sandbox: an
outbound connection, a DNS lookup, a write above the sandbox directory and a
write to `/etc`. All four must be refused, an infinite loop must be killed,
and writing inside the sandbox must still work.

> **A note on seatbelt:** profile paths must be *resolved* paths. On macOS
> `/tmp` is a symlink to `/private/tmp`, and a profile written against `/tmp`
> denies everything. `sandbox/sandbox_config.py` resolves them.

---

## Project layout

```
debug-squad/
├── app.py                      Streamlit entry point and navigation
├── requirements.txt
├── .env.example                every variable, documented
├── ruff.toml                   lint config for this project
├── pytest.ini
├── config/
│   ├── settings.py             env-based configuration
│   ├── llm_provider.py         provider factory + health validation
│   └── ollama_chat.py          a BaseChatModel for Ollama
├── models/
│   └── schemas.py              the Pydantic contracts between agents
├── prompts/
│   ├── system_prompts.py       one per agent role
│   ├── user_prompts.py         templates with {placeholders}
│   └── examples.py             the few-shot examples
├── chains/
│   └── lcel_chains.py          prompt | model | parser
├── agents/
│   ├── base.py                 tracing, ast helpers
│   ├── bug_finder.py           few-shot + ast.parse
│   ├── style_agent.py          real Ruff + LLM judgement
│   ├── fixer_agent.py          correction + reflection
│   ├── test_writer.py          generation + sandbox execution
│   └── explainer.py
├── routing/
│   └── intent_router.py        LLM + deterministic routing
├── rag/
│   ├── ingest.py               chunking, embedding, persistence
│   ├── retriever.py            conditional retrieval policy
│   └── knowledge_base/         six original markdown documents
├── sandbox/
│   ├── sandbox_config.py       backend detection, seatbelt profile
│   └── executor.py             isolated pytest execution
├── services/
│   ├── review_orchestrator.py  workflow + reflection loop
│   ├── report_generator.py     the HTML report
│   ├── history_manager.py      local persistence
│   └── compliance.py           the live criterion checks
├── ui/
│   ├── components.py           shared render helpers
│   ├── state.py                session state + cached probes
│   ├── styles.css              the theme
│   └── pages/                  one module per screen
├── examples/                   the demonstration programs
└── tests/                      the test suite
```

---

## Running the tests

```bash
cd debug-squad
pytest -v
```

**The suite needs no API key and makes no network calls.** A scripted
`FakeChatModel` (`tests/fake_llm.py`) stands in for the LLM, so the prompts,
LCEL chains, output parsing, agents, orchestration and reflection loop are all
exercised deterministically. The sandbox tests run real code in the real
sandbox, and are skipped - never passed - on a machine without one.

Coverage:

| File | What it covers |
|---|---|
| `test_intent_routing.py` | All five documented examples, fallbacks, manual override |
| `test_lcel_chains.py` | Pipeline composition, parsing, all four repair paths |
| `test_agents.py` | Each agent's structured output, real Ruff, diffs, oracles |
| `test_orchestration.py` | Routing gates execution, the reflection bound, verification honesty |
| `test_reporting.py` | All 13 report sections, HTML escaping, history, secret refusal |
| `test_ui_smoke.py` | Every screen renders in every state; the buttons are clicked; your code survives page changes; no page leaks the API key |
| `test_sandbox.py` | A live escape probe: network and filesystem escapes refused, runaway code killed |
| `test_rag.py` | Chunking, meaning-based search, closest-first ordering, and the conditional retrieval policy |

Lint the project with the same tool the Style agent uses:

```bash
python -m ruff check .
```

---

## Troubleshooting

**"GROQ_API_KEY is not set"**
Copy `.env.example` to `.env` and paste in a key from
<https://console.groq.com/keys>. Press *Reload settings from .env* on the Settings
screen, or restart the app.

**"Model 'X' is not available to this account"**
Groq retires models. Press *Show available models* on the Settings
screen and set `GROQ_MODEL` to one of those.

**Rate-limited (HTTP 429)**
The free tier is limited on tokens per minute. Wait a minute, or use a smaller
model. A full review makes 5–7 calls.

**"ChromaDB could not be initialised" / "Reference notes missing"**
Run `python -m rag.ingest`. The first run downloads the ~80 MB embedding model
and needs internet access once; afterwards retrieval is fully local.

**Tests show "Not Executed"**
No isolated sandbox backend was found. On macOS this should not happen. On
Linux or Windows, install Docker. The Settings screen's *Sandbox* tab shows
exactly which backends were detected and why each was or was not selected.

**The sandboxed run was "killed by SIGXCPU"**
The code under test hit the CPU limit — usually an infinite loop. That is the
sandbox working. Raise `SANDBOX_CPU_SECONDS` if the code is legitimately slow.

**A model returned unparseable JSON**
The parser salvages markdown fences and JSON embedded in prose locally, then
falls back to one LLM repair call. If it still fails, the agent reports the
failure rather than inventing a result. Smaller models do this more often.

**Streamlit shows a stale page**
Press `R` in the browser, or restart. `@st.cache_data` probes expire after
60–120 seconds; *Reload settings from .env* on the Settings screen clears them.

---

## Viva preparation notes

Questions a faculty member is likely to ask, and where to point:

**"Show me that the LLM is real, not hardcoded."**
Settings → *Test the connection*. It sends an actual request and prints the
reply and the latency. Then Prompt inspector → any recorded call → *Raw
model output*, which is exactly what came back over the wire.

**"How do I know the agents aren't just four prompts in a row?"**
`models/schemas.py` — they exchange validated Pydantic objects. The Fixer reads
the Bug-Finder's `Issue` list by id and reports which ids it addressed; the
Test-Writer's failures are fed back into the Fixer's reflection prompt as the
literal pytest output.

**"Where exactly is LCEL used?"**
`chains/lcel_chains.py`, the line `self.chain = self.prompt | self.model |
self.parser`. Prompt inspector → *LCEL and parsers* shows it, and
`pytest tests/test_lcel_chains.py -v` builds and runs it.

**"Prove the RAG is conditional."**
Run example 3 ("Make it readable") — the Style agent's log on the AI agents
screen shows the passages it retrieved. Then run example 4 ("Explain it") —
retrieval is skipped with a stated reason, and the Prompt inspector shows the
prompt contains no reference material.

**"Is the routing real, or just a label?"**
AI agents screen after an explanation request: the four review agents read
**Not needed**, with a reason. The orchestrator never constructs them. Test:
`test_explanation_intent_runs_only_the_explainer`.

**"What stops the reflection loop running forever?"**
`services/review_orchestrator.py`, `_run_reflection_loop` — the bound is
checked in the loop condition and every branch returns. Test:
`test_reflection_loop_stops_at_max_attempts`, which uses a Fixer that never
actually fixes anything and asserts it stops at exactly three.

**"Is it safe to run my code?"**
`pytest tests/test_sandbox.py -v`. It runs a live escape probe inside the
sandbox and fails unless the network and filesystem escapes are refused.

**"What does 'Verified' actually mean here?"**
That the generated tests executed in the sandbox and all passed. The app says
so explicitly in its limitations — it is evidence about the inputs those tests
cover, not a proof of correctness. If the sandbox could not run, the verdict is
*Not Yet Verified*, never a pass.

**"What are the limits of this system?"**
- Python only. No other language is claimed or supported.
- Findings are only as good as the model; a small local model is noticeably weaker.
- Generated tests can encode a wrong assumption — which is why every test
  carries an `oracle` field stating where its expectation came from.
- Passing tests are evidence, not proof.
- The knowledge base is six documents written for this project, not an
  exhaustive Python reference.
