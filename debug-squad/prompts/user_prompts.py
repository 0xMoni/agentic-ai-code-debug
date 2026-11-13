"""User prompt templates."""

from __future__ import annotations

INTENT_ROUTER_USER = """Classify this request.

User's instruction: {user_request}

First 40 lines of their Python code:
```python
{code_preview}
```

{format_instructions}"""


BUG_FINDER_USER = """Here are examples of correctly-formatted bug reports.

{few_shot_examples}

### Now analyse this code
{syntax_note}
```python
{code}
```
{retrieved_context}
{format_instructions}"""


STYLE_USER = """Review this code for readability and maintainability.

```python
{code}
```

Ruff (the deterministic linter) has already been run. Its findings:
{ruff_findings}

Do not repeat those. Add the judgement a linter cannot make.
{retrieved_context}
{format_instructions}"""


FIXER_USER = """Correct this code based on the findings below.

Original code:
```python
{code}
```

Bug-Finder findings:
{bug_findings}

Style/Lint findings:
{style_findings}

Return the complete corrected file. Change only what these findings justify.

{format_instructions}"""


FIXER_REFLECTION_USER = """Your previous fix failed its tests. This is attempt {attempt} of {max_attempts}.

The code you produced last time:
```python
{previous_code}
```

The tests that were run against it:
```python
{test_source}
```

Actual pytest output from the sandbox:
```
{test_output}
```

Summary: {passed} passed, {failed} failed.

Failing tests and their messages:
{failure_details}

Work out from the output above what is actually wrong, then return the complete
revised file. If a test asserts behaviour that nothing in the code's purpose
requires, say so in `ambiguities` instead of changing correct code.

{format_instructions}"""


TEST_WRITER_USER = """Write pytest tests for this code.

The module under test will be importable as `submitted_code`:
```python
{code}
```

Callable names available to import: {callables}

Write {target_count} tests covering normal, boundary and edge cases.
Remember: assert what the code SHOULD do, and state your oracle for each test.
{retrieved_context}
{format_instructions}"""


EXPLAINER_USER = """Explain this Python code.

```python
{code}
```

{user_request}"""


NO_CONTEXT = ""


def format_retrieved_context(passages: list, max_chars: int = 1200) -> str:
    """Render retrieved passages into a promptable block."""
    if not passages:
        return NO_CONTEXT

    lines = ["\nReference material from the knowledge base:"]
    used = 0
    for index, passage in enumerate(passages, start=1):
        text = passage.text.strip()
        remaining = max_chars - used
        if remaining <= 100:
            break
        if len(text) > remaining:
            text = text[:remaining].rsplit(" ", 1)[0] + " ..."
        used += len(text)
        lines.append(f"\n[{index}] Source: {passage.source}\n{text}")
    lines.append(
        "\nGround your answer in this material where it applies, and name the "
        "convention you are relying on.\n"
    )
    return "\n".join(lines)


def format_ruff_findings(issues: list, limit: int = 12) -> str:
    """Render Ruff's deterministic findings for the Style agent's prompt."""
    if not issues:
        return "  (none - Ruff reported no violations)"
    lines = []
    for issue in issues[:limit]:
        location = f"line {issue.line}" if issue.line else "file"
        lines.append(f"  - [{issue.rule_code or 'RUFF'}] {location}: {issue.title}")
    if len(issues) > limit:
        lines.append(f"  ... and {len(issues) - limit} more")
    return "\n".join(lines)


def format_issue_list(issues: list, limit: int = 15) -> str:
    """Render issues for the Fixer's prompt, keeping ids so it can cite them."""
    if not issues:
        return "  (none reported)"
    lines = []
    for issue in issues[:limit]:
        location = f" (line {issue.line})" if issue.line else ""
        lines.append(
            f"  - {issue.issue_id} [{issue.severity.value}/{issue.confidence.value}]"
            f"{location}: {issue.title}\n    {issue.explanation}"
        )
    if len(issues) > limit:
        lines.append(f"  ... and {len(issues) - limit} more")
    return "\n".join(lines)


def format_style_suggestions(suggestions: list, limit: int = 8) -> str:
    if not suggestions:
        return "  (none reported)"
    lines = []
    for suggestion in suggestions[:limit]:
        location = f" (line {suggestion.line})" if suggestion.line else ""
        lines.append(f"  - {suggestion.suggestion_id}{location}: {suggestion.title}")
    return "\n".join(lines)


def code_preview(code: str, max_lines: int = 40) -> str:
    """Trim code for the router's prompt - it only needs the gist."""
    lines = code.splitlines()
    if len(lines) <= max_lines:
        return code
    return "\n".join(lines[:max_lines]) + f"\n# ... ({len(lines) - max_lines} more lines)"
