"""The example programs offered in the Code Workspace."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

EXAMPLES_DIR = Path(__file__).resolve().parent


@dataclass
class Example:
    key: str
    title: str
    filename: str
    summary: str
    expected: str
    """What a correct review should find - stated up front, so a demo can be
    checked against it rather than judged by whatever the model happens to say."""
    suggested_request: str

    @property
    def code(self) -> str:
        path = EXAMPLES_DIR / self.filename
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return f"# Example file not found: {self.filename}\n"


EXAMPLES: list[Example] = [
    Example(
        key="find_max",
        title="1. Logical bug - find_max",
        filename="find_max_logical_bug.py",
        summary="Returns the largest number in a list, or so it claims.",
        expected=(
            "`maximum` is initialised to 0, so any list of only negative numbers "
            "returns 0 instead of the real maximum. The empty-list case is also "
            "undefined. Expect a boundary test on all-negative input to fail "
            "before the fix and pass after it."
        ),
        suggested_request="Review and fix everything",
    ),
    Example(
        key="average",
        title="2. Runtime risk - average",
        filename="average_runtime_risk.py",
        summary="Computes the mean of a list of numbers.",
        expected=(
            "`len(nums)` is zero for an empty list, so the function raises "
            "ZeroDivisionError. The fix must decide a contract - raise ValueError "
            "or return a documented sentinel - and say which it chose."
        ),
        suggested_request="Find the bug in this code",
    ),
    Example(
        key="calc",
        title="3. Code style - calc",
        filename="calc_code_style.py",
        summary="Adds two numbers, in as unhelpful a way as possible.",
        expected=(
            "Ruff should report E231 (no space after the comma), E225/E226 "
            "(no spaces around the operators) and RET504 (pointless assignment "
            "before the return). The LLM should separately object to the names "
            "`calc`, `a`, `b` and `x`, and to the missing docstring and type hints."
        ),
        suggested_request="Improve readability",
    ),
    Example(
        key="explain_me",
        title="4. Explanation only - count_words",
        filename="word_counter_clean.py",
        summary="Correct, documented code. There is nothing to fix.",
        expected=(
            "This code is fine. It demonstrates two things: the Bug-Finder "
            "returning an empty issue list instead of inventing a defect, and - "
            "when asked to explain - retrieval being skipped entirely, with the "
            "reason shown on the AI agents screen."
        ),
        suggested_request="Explain this code",
    ),
    Example(
        key="binary_search",
        title="5. Several defects - binary_search",
        filename="binary_search_edge_cases.py",
        summary="A binary search with a docstring that states its contract.",
        expected=(
            "At least three defects: `high` starts at len(items) rather than "
            "len(items) - 1; `(low + high) / 2` is a float, so indexing raises "
            "TypeError; and neither `low = mid` nor `high = mid` makes progress, "
            "so the loop can spin. The docstring gives the Test-Writer a real "
            "oracle to assert against."
        ),
        suggested_request="Review and fix everything",
    ),
    Example(
        key="inventory",
        title="6. Mixed bag - inventory totals",
        filename="inventory_mixed_issues.py",
        summary="Totals an inventory dict and finds the priciest item.",
        expected=(
            "Lint findings (unused imports, `== None`, single-letter names) sit "
            "alongside real defects: `most_expensive` raises KeyError on an empty "
            "dict, and `process` raises KeyError on any entry missing 'price' or "
            "'qty'. Good for showing all four agents working at once."
        ),
        suggested_request="Review and fix everything",
    ),
]

EXAMPLES_BY_KEY = {example.key: example for example in EXAMPLES}


