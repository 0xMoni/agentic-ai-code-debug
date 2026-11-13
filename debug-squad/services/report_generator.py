"""Consolidated review report."""

from __future__ import annotations

import html
import json
from datetime import UTC, datetime

from jinja2 import Environment, select_autoescape
from markupsafe import Markup

from models.schemas import (
    ReviewSession,
    TestOutcome,
    VerificationStatus,
)
from routing.intent_router import INTENT_LABELS, WORKFLOW_LABELS

_STATUS_COLOURS = {
    VerificationStatus.VERIFIED: ("#1A1D24", "#34D399"),
    VerificationStatus.FAILED: ("#1A1D24", "#F87171"),
    VerificationStatus.NOT_VERIFIED: ("#1A1D24", "#FB923C"),
    VerificationStatus.NOT_CONFIGURED: ("#1A1D24", "#A3ACBD"),
}

_SEVERITY_COLOURS = {
    "critical": "#F87171",
    "high": "#FB923C",
    "medium": "#93C5FD",
    "low": "#A3ACBD",
    "info": "#A3ACBD",
}

_OUTCOME_COLOURS = {
    "passed": "#34D399",
    "failed": "#F87171",
    "error": "#F87171",
    "skipped": "#A3ACBD",
    "not_executed": "#A3ACBD",
}

TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Debug Squad review - {{ session.filename }}</title>
<style>
  :root {
    --bg: #111318; --surface: #1A1D24; --elevated: #2E3340;
    --accent: #5B9DFF; --text: #F1F4F9; --muted: #A3ACBD;
    --success: #34D399; --warning: #FB923C; --error: #F87171;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 32px 20px; background: var(--bg); color: var(--text);
    font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    line-height: 1.6; font-size: 17px;
  }
  .wrap { max-width: 920px; margin: 0 auto; }
  header { border-bottom: 1px solid var(--elevated); padding-bottom: 20px; margin-bottom: 28px; }
  h1 { font-size: 26px; margin: 0 0 4px; letter-spacing: -0.02em; }
  .tagline { color: var(--accent); font-size: 15px; font-weight: 500; margin: 0 0 14px; }
  h2 {
    font-size: 19px; margin: 34px 0 12px; padding-bottom: 7px;
    border-bottom: 1px solid var(--elevated);
  }
  h3 { font-size: 16px; margin: 20px 0 8px; color: var(--muted);
       text-transform: uppercase; letter-spacing: 0.06em; }
  .meta { display: flex; flex-wrap: wrap; gap: 8px 24px; font-size: 15px; color: var(--muted); }
  .meta b { color: var(--text); font-weight: 500; }
  .verdict {
    border-radius: 10px; padding: 16px 18px; margin: 22px 0;
    background: {{ verdict_bg }}; border-left: 5px solid {{ verdict_fg }};
  }
  .verdict .label { color: {{ verdict_fg }}; font-weight: 600; font-size: 15px;
                    text-transform: uppercase; letter-spacing: 0.08em; }
  .verdict p { margin: 6px 0 0; }
  .cards { display: flex; flex-wrap: wrap; gap: 10px; margin: 14px 0; }
  .card {
    background: var(--surface); border: 1px solid var(--elevated);
    border-radius: 9px; padding: 12px 16px; min-width: 112px;
  }
  .card .n { font-size: 22px; font-weight: 600; font-variant-numeric: tabular-nums; }
  .card .k { font-size: 13px; color: var(--muted); text-transform: uppercase;
             letter-spacing: 0.06em; }
  pre, code { font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace; }
  pre {
    background: var(--surface); border: 1px solid var(--elevated); border-radius: 9px;
    padding: 14px 16px; overflow-x: auto; font-size: 14.5px; line-height: 1.55;
  }
  .issue {
    background: var(--surface); border: 1px solid var(--elevated);
    border-left: 3px solid var(--muted); border-radius: 7px;
    padding: 12px 15px; margin-bottom: 10px;
  }
  .issue .head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
  .issue .id { font-family: "JetBrains Mono", monospace; font-size: 13.5px; color: var(--muted); }
  .issue .title { font-weight: 600; }
  .badge {
    display: inline-block; font-size: 12.5px; font-weight: 600; padding: 2px 7px;
    border-radius: 4px; text-transform: uppercase; letter-spacing: 0.05em;
  }
  .issue p { margin: 7px 0 0; font-size: 15.5px; }
  .issue .repro { color: var(--muted); font-size: 14.5px; }
  table { width: 100%; border-collapse: collapse; font-size: 15px; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--elevated); }
  th { color: var(--muted); font-size: 13px; text-transform: uppercase;
       letter-spacing: 0.06em; font-weight: 500; }
  td.mono { font-family: "JetBrains Mono", monospace; font-size: 14px; }
  ul { margin: 8px 0; padding-left: 20px; }
  li { margin-bottom: 5px; }
  .diff .add { color: var(--success); }
  .diff .del { color: var(--error); }
  .diff .hunk { color: var(--accent); }
  .muted { color: var(--muted); }
  .warnbox {
    background: var(--surface); border-left: 4px solid var(--warning); border-radius: 9px;
    padding: 13px 16px; margin: 12px 0;
  }
  footer {
    margin-top: 40px; padding-top: 16px; border-top: 1px solid var(--elevated);
    color: var(--muted); font-size: 14px;
  }
</style>
</head>
<body><div class="wrap">

<header>
  <h1>Debug Squad &mdash; Code Review Report</h1>
  <p class="tagline">Four Agents. One Smarter Code Review.</p>
  <div class="meta">
    <span><b>File</b> {{ session.filename }}</span>
    <span><b>Session</b> {{ session.session_id }}</span>
    <span><b>Generated</b> {{ generated_at }}</span>
    <span><b>Model</b> {{ session.model }} ({{ session.provider }})</span>
  </div>
</header>

<div class="verdict">
  <div class="label">Final verification status &mdash; {{ session.verification.value }}</div>
  <p>{{ session.overall_assessment }}</p>
</div>

<h2>1. Submitted code</h2>
<div class="meta">
  <span><b>Lines</b> {{ line_count }}</span>
  <span><b>Characters</b> {{ session.original_code|length }}</span>
  <span><b>Review duration</b> {{ session.duration_seconds }}s</span>
</div>
<pre>{{ session.original_code }}</pre>

<h2>2. Detected intent and routing</h2>
{% if session.intent %}
<div class="meta">
  <span><b>Intent</b> {{ intent_label }}</span>
  <span><b>Detection</b> {{ session.intent.method }}</span>
  <span><b>Confidence</b> {{ session.intent.confidence.value }}</span>
</div>
<p>{{ session.intent.explanation }}</p>
<p class="muted">Selected workflow: {{ workflow_label }}</p>
{% if session.user_request %}<p class="muted">User instruction: &ldquo;{{ session.user_request }}&rdquo;</p>{% endif %}
{% else %}<p class="muted">No intent was recorded.</p>{% endif %}

<h2>3. Agents invoked</h2>
<table>
  <tr><th>Agent</th><th>Status</th><th>Duration</th><th>Findings</th><th>Techniques</th><th>Tools</th></tr>
  {% for trace in session.traces %}
  <tr>
    <td>{{ trace.agent.value }}</td>
    <td>{{ trace.status.value }}{% if trace.skip_reason %} <span class="muted">&mdash; {{ trace.skip_reason }}</span>{% endif %}</td>
    <td class="mono">{{ "%.2f"|format(trace.duration_seconds) }}s</td>
    <td class="mono">{{ trace.findings_count }}</td>
    <td class="muted">{{ trace.techniques|map(attribute='value')|join(', ') or '-' }}</td>
    <td class="muted">{{ trace.tools_used|join(', ') or '-' }}</td>
  </tr>
  {% endfor %}
</table>

<h2>4. Bug findings</h2>
{% if session.bug_report %}
  {% if not session.bug_report.syntax_valid %}
  <div class="warnbox">The file does not parse: {{ session.bug_report.syntax_error }}</div>
  {% endif %}
  <p>{{ session.bug_report.summary }}</p>
  {% for issue in session.bug_report.issues %}{{ render_issue(issue) }}{% endfor %}
  {% if not session.bug_report.issues %}<p class="muted">No defects were reported.</p>{% endif %}
{% else %}<p class="muted">The Bug-Finder did not run for this request.</p>{% endif %}

<h2>5. Style findings</h2>
{% if session.style_report %}
  <h3>Ruff &mdash; deterministic lint findings</h3>
  {% if not session.style_report.ruff_available %}
  <div class="warnbox">Ruff could not be run: {{ session.style_report.ruff_error }}</div>
  {% elif session.style_report.ruff_issues %}
    {% for issue in session.style_report.ruff_issues %}{{ render_issue(issue) }}{% endfor %}
  {% else %}<p class="muted">Ruff reported no violations.</p>{% endif %}

  <h3>LLM readability suggestions</h3>
  {% if session.style_report.suggestions %}
  <ul>
  {% for suggestion in session.style_report.suggestions %}
    <li><b>{{ suggestion.title }}</b>{% if suggestion.line %} <span class="muted">(line {{ suggestion.line }})</span>{% endif %}<br>
    {{ suggestion.explanation }}
    {% if suggestion.convention %}<br><span class="muted">Convention: {{ suggestion.convention }}</span>{% endif %}</li>
  {% endfor %}
  </ul>
  {% else %}<p class="muted">No readability suggestions were made.</p>{% endif %}
{% else %}<p class="muted">The Style/Lint agent did not run for this request.</p>{% endif %}

{% if session.explanation %}
<h2>Code explanation</h2>
<pre>{{ session.explanation }}</pre>
{% endif %}

<h2>6. Corrected code</h2>
{% if session.final_fix and session.final_fix.corrected_code %}
  {% if not session.final_fix.syntax_valid %}
  <div class="warnbox">The proposed correction did not parse and was rejected. The original code is shown.</div>
  {% endif %}
<pre>{{ session.final_fix.corrected_code }}</pre>
{% else %}<p class="muted">No correction was produced.</p>{% endif %}

<h2>7. Code changes</h2>
{% if session.final_fix and session.final_fix.changes %}
<ul>{% for change in session.final_fix.changes %}<li>{{ change }}</li>{% endfor %}</ul>
{% else %}<p class="muted">No changes were described.</p>{% endif %}
{% if session.final_fix and session.final_fix.unified_diff %}
<h3>Unified diff</h3>
<pre class="diff">{{ diff_html }}</pre>
{% endif %}

<h2>8. Generated test cases</h2>
{% if session.final_test_run and session.final_test_run.tests %}
<div class="cards">
  <div class="card"><div class="n">{{ run.generated }}</div><div class="k">Generated</div></div>
  <div class="card"><div class="n">{{ run.executed }}</div><div class="k">Executed</div></div>
  <div class="card"><div class="n" style="color:var(--success)">{{ run.passed }}</div><div class="k">Passed</div></div>
  <div class="card"><div class="n" style="color:var(--error)">{{ run.failed }}</div><div class="k">Failed</div></div>
  <div class="card"><div class="n" style="color:var(--warning)">{{ run.skipped }}</div><div class="k">Skipped</div></div>
</div>
<table>
  <tr><th>Test</th><th>Category</th><th>Outcome</th><th>Oracle</th></tr>
  {% for test in run.tests %}
  <tr>
    <td class="mono">{{ test.name }}</td>
    <td class="muted">{{ test.category }}</td>
    <td><span class="badge" style="background:{{ outcome_colour(test.outcome.value) }}22;color:{{ outcome_colour(test.outcome.value) }}">{{ test.outcome.value }}</span></td>
    <td class="muted">{{ test.oracle }}</td>
  </tr>
  {% endfor %}
</table>
<h3>Test module that was executed</h3>
<pre>{{ run.test_file_source }}</pre>
{% else %}<p class="muted">No tests were generated for this request.</p>{% endif %}

<h2>9. Actual execution results</h2>
{% if session.final_test_run %}
<div class="meta">
  <span><b>Sandbox backend</b> {{ run.sandbox.backend }}</span>
  <span><b>Executed</b> {{ "yes" if run.sandbox.executed else "no" }}</span>
  <span><b>Exit code</b> {{ run.sandbox.exit_code if run.sandbox.exit_code is not none else "n/a" }}</span>
  <span><b>Duration</b> {{ "%.2f"|format(run.sandbox.duration_seconds) }}s</span>
</div>
{% if not run.sandbox.executed %}
<div class="warnbox">
  The tests were <b>not executed</b>: {{ run.sandbox.unavailable_reason }}.
  No claim is made about whether they would have passed.
</div>
{% elif run.sandbox.timed_out %}
<div class="warnbox">{{ run.sandbox.stderr }}</div>
{% endif %}
{% if run.sandbox.stdout %}
<h3>Raw pytest output</h3>
<pre>{{ run.sandbox.stdout }}</pre>
{% endif %}
{% if failing %}
<h3>Failure details</h3>
{% for test in failing %}
<div class="issue" style="border-left-color:var(--error)">
  <div class="head"><span class="id">{{ test.name }}</span><span class="title">{{ test.description }}</span></div>
  <pre>{{ test.failure_message }}</pre>
</div>
{% endfor %}
{% endif %}
{% else %}<p class="muted">Nothing was executed.</p>{% endif %}

<h2>10. Reflection attempts</h2>
{% if session.attempts %}
<p>{{ session.attempt_count }} attempt(s) were made, with a configured maximum of {{ max_attempts }}.</p>
<table>
  <tr><th>Attempt</th><th>Changes</th><th>Executed</th><th>Passed</th><th>Failed</th><th>Verified</th></tr>
  {% for item in session.attempts %}
  <tr>
    <td class="mono">{{ item.attempt }}</td>
    <td class="muted">{{ item.fix.changes|length }}</td>
    <td class="mono">{{ item.test_run.executed }}</td>
    <td class="mono" style="color:var(--success)">{{ item.test_run.passed }}</td>
    <td class="mono" style="color:var(--error)">{{ item.test_run.failed }}</td>
    <td>{{ "yes" if item.verified else "no" }}</td>
  </tr>
  {% endfor %}
</table>
{% for item in session.attempts if item.reflection_notes %}
<p class="muted">Attempt {{ item.attempt }}: {{ item.reflection_notes }}</p>
{% endfor %}
{% else %}<p class="muted">No correction attempts were made.</p>{% endif %}

<h2>11. Retrieved documentation</h2>
{% if session.retrievals %}
<table>
  <tr><th>Agent</th><th>Retrieved</th><th>Query</th><th>Passages</th></tr>
  {% for decision in session.retrievals %}
  <tr>
    <td>{{ decision.agent.value }}</td>
    <td>{{ "yes" if decision.should_retrieve else "no" }}</td>
    <td class="mono muted">{{ decision.query or '-' }}</td>
    <td class="mono">{{ decision.passages|length }}</td>
  </tr>
  {% endfor %}
</table>
{% for decision in session.retrievals if decision.passages %}
<h3>Sources used by the {{ decision.agent.value }}</h3>
<ul>
{% for passage in decision.passages %}
  <li><b>{{ passage.source }}</b>{% if passage.score is not none %} <span class="muted">(similarity {{ passage.score }})</span>{% endif %}</li>
{% endfor %}
</ul>
{% endfor %}
{% else %}<p class="muted">No retrieval decisions were recorded.</p>{% endif %}

<h2>12. Final verification status</h2>
<div class="verdict">
  <div class="label">{{ session.verification.value }}</div>
  <p>{{ verification_meaning }}</p>
</div>

<h2>13. Remaining issues and limitations</h2>
{% if session.limitations or session.errors %}
<ul>
  {% for item in session.limitations %}<li>{{ item }}</li>{% endfor %}
  {% for item in session.errors %}<li><b>Error:</b> {{ item }}</li>{% endfor %}
</ul>
{% else %}
<p class="muted">No limitations were recorded for this review.</p>
{% endif %}
<div class="warnbox">
  This report describes what the agents observed and what the sandbox actually
  executed. Passing tests are evidence about the specific inputs they cover,
  not a proof of correctness.
</div>

<footer>
  Debug Squad &mdash; generated {{ generated_at }} &middot; session {{ session.session_id }}
  &middot; model {{ session.model }} via {{ session.provider }}
</footer>

</div></body></html>
"""

VERIFICATION_MEANING = {
    VerificationStatus.VERIFIED: (
        "The generated tests were executed in the sandbox and all of them passed "
        "against the corrected code. This is evidence for the cases those tests "
        "cover, not a proof that the code is correct in general."
    ),
    VerificationStatus.FAILED: (
        "The generated tests were executed and at least one of them still fails. "
        "The correction should not be treated as working."
    ),
    VerificationStatus.NOT_VERIFIED: (
        "Nothing was verified by execution. Either no tests were run, or the "
        "sandbox was unavailable. The correction is neither confirmed nor refuted."
    ),
    VerificationStatus.NOT_CONFIGURED: (
        "The system was not configured well enough to attempt verification."
    ),
}


def _colourise_diff(diff: str) -> str:
    """Escape and colour a unified diff for the HTML report."""
    lines = []
    for line in diff.splitlines():
        escaped = html.escape(line)
        if line.startswith("+++") or line.startswith("---"):
            lines.append(f'<span class="muted">{escaped}</span>')
        elif line.startswith("@@"):
            lines.append(f'<span class="hunk">{escaped}</span>')
        elif line.startswith("+"):
            lines.append(f'<span class="add">{escaped}</span>')
        elif line.startswith("-"):
            lines.append(f'<span class="del">{escaped}</span>')
        else:
            lines.append(escaped)
    return Markup("\n".join(lines))


def _render_issue(issue) -> str:
    colour = _SEVERITY_COLOURS.get(issue.severity.value, "#A3ACBD")
    location = f"line {issue.line}" if issue.line else "no line"
    source = issue.rule_code or issue.source.value
    parts = [
        f'<div class="issue" style="border-left-color:{colour}">',
        '<div class="head">',
        f'<span class="id">{html.escape(issue.issue_id)}</span>',
        f'<span class="title">{html.escape(issue.title)}</span>',
        f'<span class="badge" style="background:{colour}22;color:{colour}">'
        f"{issue.severity.value}</span>",
        f'<span class="id">{html.escape(location)} &middot; {html.escape(source)} '
        f"&middot; {issue.confidence.value} confidence</span>",
        "</div>",
        f"<p>{html.escape(issue.explanation)}</p>",
    ]
    if issue.reproduction:
        parts.append(
            f'<p class="repro"><b>Reproduction:</b> '
            f"{html.escape(issue.reproduction)}</p>"
        )
    if issue.suggested_fix:
        parts.append(
            f'<p class="repro"><b>Suggested fix:</b> '
            f"{html.escape(issue.suggested_fix)}</p>"
        )
    parts.append("</div>")
    return Markup("".join(parts))


def generate_html_report(session: ReviewSession, max_attempts: int = 3) -> str:
    """Render the full HTML report for one session."""
    environment = Environment(autoescape=select_autoescape(["html"]))
    template = environment.from_string(TEMPLATE)

    run = session.final_test_run
    failing = (
        [
            test
            for test in run.tests
            if test.outcome in (TestOutcome.FAILED, TestOutcome.ERROR)
            and test.failure_message
        ]
        if run
        else []
    )

    verdict_bg, verdict_fg = _STATUS_COLOURS.get(
        session.verification, ("#1A1D24", "#A3ACBD")
    )

    return template.render(
        session=session,
        run=run,
        failing=failing,
        generated_at=datetime.now(UTC).strftime("%d %b %Y, %H:%M UTC"),
        line_count=len(session.original_code.splitlines()),
        intent_label=INTENT_LABELS.get(session.intent.intent, "unknown")
        if session.intent
        else "unknown",
        workflow_label=WORKFLOW_LABELS.get(session.intent.intent, "unknown")
        if session.intent
        else "unknown",
        verdict_bg=verdict_bg,
        verdict_fg=verdict_fg,
        verification_meaning=VERIFICATION_MEANING.get(session.verification, ""),
        diff_html=_colourise_diff(session.final_fix.unified_diff)
        if session.final_fix
        else "",
        render_issue=_render_issue,
        outcome_colour=lambda value: _OUTCOME_COLOURS.get(value, "#A3ACBD"),
        max_attempts=max_attempts,
    )


def generate_json_report(session: ReviewSession) -> str:
    """Machine-readable form of the same data."""
    return json.dumps(session.model_dump(mode="json"), indent=2, default=str)


def report_filename(session: ReviewSession, extension: str = "html") -> str:
    stamp = session.created_at.strftime("%Y%m%d-%H%M%S")
    return f"debug-squad-review-{stamp}-{session.session_id}.{extension}"
