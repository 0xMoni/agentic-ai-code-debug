# Debug Squad

**Four agents. One smarter code review.**

Paste in some Python and a small team of AI agents reviews it together:

| Agent | What it does |
|---|---|
| **Bug-Finder** | Finds logic errors, crashes waiting to happen and missed edge cases |
| **Style/Lint** | Runs Ruff, then suggests how to make the code easier to read |
| **Fixer** | Writes a corrected version of the code |
| **Test-Writer** | Writes pytest tests and runs them in a sandbox |

If the tests fail, the Fixer reads the real test output and tries again, up
to three times. A fix is only marked as working when the tests actually ran
and passed.

## Getting started

You need Python 3.11 or newer and a free [Groq API key](https://console.groq.com/keys).

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
cd debug-squad
pip install -r requirements.txt

cp .env.example .env               # then add your GROQ_API_KEY to .env
streamlit run app.py
```

The app opens at http://localhost:8501. The first start takes a little
longer while it downloads a small (~80 MB) model for the reference notes.

## What's inside

- **Code workspace** - paste, upload or pick an example, then review it
- **AI agents** - what each agent did and how
- **Results & history** - issues, the fix, downloads, and every past review
- **Test lab** - the generated tests and their real results
- **Prompt inspector** - every prompt sent to the model
- **Compliance** - live checks for the seven assignment criteria
- **Settings** - model, sandbox and reference-note configuration

## Built with

Python, Streamlit, LangChain (LCEL), Groq, ChromaDB, Ruff and pytest. Code is
run in an isolated sandbox: macOS `sandbox-exec`, or Docker where available.

## Running the tests

```bash
cd debug-squad
pytest
```

The tests need no API key and make no network calls.

## More detail

See [debug-squad/docs/GUIDE.md](debug-squad/docs/GUIDE.md) for configuration,
the architecture, how each assignment criterion is met, troubleshooting and
viva notes.
