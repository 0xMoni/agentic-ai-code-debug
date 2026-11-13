# Algorithmic Edge Cases

A checklist of the inputs that break otherwise-plausible implementations.
Used by the Bug-Finder when reasoning about missing cases, and by the
Test-Writer when choosing boundary tests.

## Collections

| Input | What it breaks |
|---|---|
| `[]` | `len()` division, `items[0]`, `max()`/`min()` on the raw call, any accumulator seeded from the first element |
| `[x]` | pairwise logic, `items[i+1]`, "compare with the next one" loops, sorting assumptions |
| `[x, x]` | duplicate handling, "find the second largest", uniqueness assumptions |
| all equal | tie-breaking, strict `>` versus `>=` |
| already sorted | insertion logic, early-exit optimisations |
| reverse sorted | worst case for many sorts; swap counters |
| very long | recursion depth, quadratic loops |
| contains `None` | comparisons raise `TypeError` |
| mixed types | comparison between `str` and `int` raises in Python 3 |

## Numbers

- **Zero.** As a divisor, as a multiplier, as a loop count, as an array index,
  and as an accumulator's starting value.
- **Negative numbers.** The single most common unstated assumption. Any
  accumulator initialised to `0` and compared with `>` is wrong for all-negative
  input. Also check: `abs`, `//` and `%` round towards negative infinity in
  Python (`-7 // 2 == -4`, `-7 % 2 == 1`), square roots, and index arithmetic
  where a negative index silently wraps to the end of the sequence.
- **Floats.** Never compare with `==`. `0.1 + 0.2 != 0.3`. Use
  `math.isclose()` or `pytest.approx`. Accumulated error grows with the number
  of operations.
- **Very large integers.** Python integers are unbounded, so there is no
  overflow - but converting to `float` loses precision above 2^53.
- **`float("nan")`** compares unequal to everything, including itself, so any
  `max`-style scan silently returns the wrong answer.
- **`float("inf")`** is a legitimate accumulator seed for a maximum, and
  propagates through arithmetic.

## Strings

- The empty string is falsy, has length 0, and `""[0]` raises `IndexError`.
- A single character is both the first and the last character.
- Case: `"A" != "a"`, and `.lower()` has locale-dependent surprises.
- Whitespace-only input is often meant to be treated as empty but is not.
- Strings are immutable: building one in a loop with `+=` is quadratic;
  use `"".join(parts)`.
- Iterating a string yields characters, so a function expecting a list of
  words will silently "work" on a string and produce nonsense.

## Dictionaries and sets

- Missing keys raise; use `.get()` or `defaultdict` when absence is normal.
- Mutating a dict while iterating it raises `RuntimeError`.
- Sets drop duplicates and have no order - using one changes behaviour, not
  just performance.
- Dicts preserve insertion order from Python 3.7, but relying on that for
  *sorted* order is a bug.

## Searching and sorting

- Search that returns an index must define what it returns when not found
  (`-1`? `None`? raise?), and callers must honour it. `if index:` is wrong
  when `0` is a valid result.
- "First" versus "last" match: a forward loop with an early return finds the
  first. A function named `last_index` that loops forwards is wrong.
- Binary search needs sorted input; the midpoint and the loop bound are where
  off-by-one errors live.
- Sorting stability matters whenever there are ties and a secondary ordering.

## Loops

- `range(n)` yields `0..n-1`. `range(1, n)` omits `n`.
- Accessing `items[i+1]` requires the loop to stop at `len(items) - 1`.
- A slice is forgiving where an index is not: `items[0:n+1]` keeps `n+1`
  elements without complaint.
- Removing items from a list while iterating it skips elements.
- An accumulator declared outside a nested loop but intended per-iteration is
  a classic silent error.

## Recursion

- Is there a base case, and is it reachable from every input?
- Does each call strictly reduce the problem size?
- What happens at depth 1000 (Python's default limit)?
- Does the empty or single-element case hit the base case, or fall through?
