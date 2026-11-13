# Code Readability

Notes for the Style/Lint agent on judgements a linter cannot make. A linter
checks whether code conforms. Readability is about whether the next person can
understand it, which is a different question.

## Names carry the explanation

The reader meets a name far more often than its definition, so the name does
most of the explaining.

- `calc(a, b)` tells the reader nothing. `calculate_subtotal(price, quantity)`
  removes the need to read the body at all.
- `data`, `info`, `temp`, `result`, `value`, `obj`, `thing` are placeholders,
  not names. `thing2` is a confession.
- Length should track scope. `i` inside a three-line loop is fine; `i` as a
  module-level variable is not.
- Booleans read best as assertions: `is_valid`, `has_permission`,
  `should_retry`. A boolean called `flag` or `check` forces the reader to
  work out which way round it is.
- Say the unit when there is one: `timeout_seconds`, `size_mb`, `price_usd`.
  This prevents an entire class of bug, not just confusion.
- Be consistent within a codebase. `fetch`, `get` and `retrieve` used
  interchangeably makes the reader hunt for a distinction that is not there.

## Functions

- A function should fit on one screen. Longer than that and the reader is
  scrolling to remember what a variable meant.
- One level of abstraction per function. Mixing high-level orchestration with
  low-level byte manipulation forces a mental gear change mid-read.
- Many parameters signals a missing structure. Beyond four or five, group them
  into a dataclass.
- A boolean parameter that selects between two behaviours is usually two
  functions wearing one name.
- Return early to handle the trivial cases, then let the main path read
  straight down the page without nesting.

## Nesting

Each level of indentation is a condition the reader must keep in mind. Three
levels is noticeable; five is unreadable. Reduce it by:

- inverting a condition and returning early (a guard clause)
- extracting the inner block into a named function
- using `any()` / `all()` / a comprehension instead of a loop-with-a-flag
- handling the error case first and getting it out of the way

## Comments

- Comments explain **why**, never **what**. `# add one to i` is noise;
  `# the API is 1-indexed` is essential.
- A comment that restates the code will drift out of date and then actively
  mislead.
- A comment explaining a confusing block is a hint that the block should be
  extracted into a function with a descriptive name instead.
- Commented-out code should be deleted. Version control already remembers it.
- `TODO` is acceptable if it says who, what and why. A bare `# TODO: fix` is
  a note to nobody.

## Structure and consistency

- Group related code together. A function used by exactly one other function
  belongs next to it.
- Keep a consistent order within a file: constants, then classes, then
  functions, then the entry point.
- Avoid clever one-liners that need decoding. A nested ternary inside a
  comprehension is a puzzle, and the reader did not ask to solve one.
- Prefer the explicit form when the implicit one needs a footnote.

## Magic values

A bare `0.1`, `86400` or `"ACTIVE"` in the middle of an expression means the
reader has to infer its significance. Name it: `MEMBER_DISCOUNT_RATE`,
`SECONDS_PER_DAY`, `Status.ACTIVE`. The name is documentation that cannot go
stale, and the constant can be changed in one place.

## Signs that readability needs attention

- You had to read the function twice to know what it returns.
- You cannot name the function without using "and".
- The same four lines appear in three places with one value changed.
- A variable is reassigned to mean something different halfway through.
- There is a comment apologising for the code.
- The indentation reaches the right-hand margin.

## A caution

Readability work must not change behaviour. Renaming a variable, extracting a
function and adding a docstring are safe. Reordering operations, changing a
default, or "simplifying" a condition can alter results. Keep the two kinds of
change separate, and never present a behavioural change as a tidy-up.
