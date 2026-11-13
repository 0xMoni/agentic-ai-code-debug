"""Few-shot examples for the Bug-Finder agent."""

from __future__ import annotations

BUG_FINDER_FEW_SHOT: list[tuple[str, str]] = [
    (
        """def get_discount(price, is_member):
    if is_member:
        discount = price * 0.1
    return price - discount""",
        """{
  "issues": [
    {
      "issue_id": "BUG-01",
      "title": "`discount` is undefined when `is_member` is False",
      "severity": "critical",
      "line": 4,
      "explanation": "`discount` is only assigned inside the `if is_member` block. When `is_member` is False the name is never bound, so line 4 raises UnboundLocalError instead of returning the undiscounted price.",
      "reproduction": "get_discount(100, False) raises UnboundLocalError: cannot access local variable 'discount'",
      "suggested_fix": "Initialise `discount = 0` before the `if`, so a non-member simply gets no discount.",
      "confidence": "high",
      "source": "llm"
    }
  ],
  "summary": "One critical defect: a conditionally-assigned local is read on a path where it was never assigned.",
  "syntax_valid": true,
  "syntax_error": null
}""",
    ),
    (
        """def last_index(items, target):
    for i in range(len(items)):
        if items[i] == target:
            return i
    return -1

def trim_list(items, n):
    return items[0:n+1]""",
        """{
  "issues": [
    {
      "issue_id": "BUG-01",
      "title": "`last_index` returns the first match, not the last",
      "severity": "high",
      "line": 2,
      "explanation": "The loop walks forwards and returns on the first equal element, so the function's name and its behaviour disagree. For the last index the list must be scanned in reverse.",
      "reproduction": "last_index([1, 2, 1], 1) returns 0, but the last index of 1 is 2",
      "suggested_fix": "Iterate with `reversed(range(len(items)))`, or return `len(items) - 1 - items[::-1].index(target)` when present.",
      "confidence": "high",
      "source": "llm"
    },
    {
      "issue_id": "BUG-02",
      "title": "`trim_list` keeps n+1 elements, an off-by-one error",
      "severity": "medium",
      "line": 8,
      "explanation": "The slice `items[0:n+1]` includes index n, yielding n+1 elements. A function asked to trim a list to n items should return exactly n.",
      "reproduction": "trim_list([1, 2, 3, 4], 2) returns [1, 2, 3] - three elements, not two",
      "suggested_fix": "Use `items[:n]`.",
      "confidence": "medium",
      "source": "llm"
    }
  ],
  "summary": "Two logic defects: a reversed-scan error and an off-by-one slice boundary.",
  "syntax_valid": true,
  "syntax_error": null
}""",
    ),
    (
        """def count_vowels(text):
    \"\"\"Return the number of vowels in text, case-insensitively.\"\"\"
    vowels = set("aeiou")
    return sum(1 for ch in text.lower() if ch in vowels)""",
        """{
  "issues": [],
  "summary": "No defects found. The function lowercases its input before comparing, handles the empty string correctly (returning 0), and the generator expression has no boundary conditions to get wrong.",
  "syntax_valid": true,
  "syntax_error": null
}""",
    ),
]


def format_few_shot_block(examples: list[tuple[str, str]] | None = None) -> str:
    """Render the few-shot examples as a single prompt block."""
    examples = examples if examples is not None else BUG_FINDER_FEW_SHOT
    parts: list[str] = []
    for index, (code, output) in enumerate(examples, start=1):
        parts.append(
            f"### Example {index}\n"
            f"Code:\n```python\n{code}\n```\n"
            f"Correct output:\n```json\n{output}\n```"
        )
    return "\n\n".join(parts)


def example_summaries(examples: list[tuple[str, str]] | None = None) -> list[str]:
    """Short labels for the Prompt Inspector's few-shot list."""
    examples = examples if examples is not None else BUG_FINDER_FEW_SHOT
    labels = [
        "Conditionally-assigned local read on another path (UnboundLocalError)",
        "Reversed-scan error plus an off-by-one slice boundary",
        "Correct code - demonstrates returning an empty issue list",
    ]
    out: list[str] = []
    for index, (code, _) in enumerate(examples):
        label = labels[index] if index < len(labels) else f"Example {index + 1}"
        first_line = code.strip().splitlines()[0]
        out.append(f"{label}  --  `{first_line}`")
    return out
