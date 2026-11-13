# Python Style Conventions

Original teaching notes for Debug Squad, summarising widely-accepted Python
style practice (PEP 8 and PEP 257) in the form the Style/Lint agent needs.

## Naming

- Functions and variables use `lower_snake_case`. Classes use `CapWords`.
  Module-level constants use `UPPER_SNAKE_CASE`.
- A name should say what the value *is* or what the function *does*.
  `calc` does not; `calculate_total` does. `x` does not; `total_price` does.
- Avoid single-letter names except for well-understood conventions: `i`, `j`
  for loop indices, `n` for a count, `k`, `v` in a dict comprehension.
- Avoid encoding the type in the name (`price_float`, `items_list`). Type
  hints communicate that better and stay correct when the type changes.
- Never shadow a builtin: `list`, `dict`, `type`, `id`, `sum`, `max`, `input`,
  `str`. Shadowing `max` inside a function that also wants `max()` is a
  classic self-inflicted bug.
- Prefix a name with a single underscore to mark it as internal to a module
  or class. Two leading underscores triggers name mangling and is rarely what
  you want.

## Whitespace and layout

- Four spaces per indentation level. Never tabs, and never mix the two.
- Surround binary operators with a single space: `x = a + b`, not `x=a+b`.
- No space immediately inside brackets, and no space before a comma:
  `f(a, b)`, not `f( a , b )`.
- One space after a comma in an argument list or literal.
- Keyword arguments with no annotation get no spaces around `=`:
  `f(x=1)`. With an annotation they do: `def f(x: int = 1)`.
- Two blank lines between top-level definitions, one between methods.
- Keep lines to a readable width - 88 characters is the common modern limit
  (Black and Ruff's default); 79 is the PEP 8 original.

## Imports

- One import per line, at the top of the file.
- Group in three blocks, separated by a blank line: standard library, then
  third-party, then local application imports.
- Prefer `from package import thing` over `import package.thing as thing`.
- Never use `from module import *`. It hides the origin of every name and
  defeats both linters and readers.
- Remove unused imports. They are dead weight and a sign the file has drifted.

## Docstrings

- Every public module, class and function gets a docstring.
- The first line is a short imperative summary ending in a period:
  "Return the mean of nums." - not "This function will return...".
- For anything non-obvious, document what the parameters mean, what is
  returned, and which exceptions are raised deliberately.
- A docstring states the function's *contract*. That contract is what tests
  should assert against, which is why a missing docstring makes a function
  harder to test correctly, not just harder to read.

## Comparisons and conditionals

- Compare to `None` with `is` / `is not`, never `==`.
- Test truthiness directly: `if items:` rather than `if len(items) > 0:`.
- Do not compare to `True` or `False` explicitly: `if ready:`, not
  `if ready == True:`.
- Prefer early returns over deep nesting. Each level of indentation is a
  branch the reader has to hold in their head.
- Chain comparisons where it reads naturally: `if 0 <= index < len(items):`.

## Type hints

- Annotate public function signatures. They document intent, they are checked
  by tools, and they do not go stale the way a comment does.
- Modern syntax is lighter: `list[int]`, `dict[str, float]`, `int | None`.
- `Optional[X]` and `X | None` mean the same thing; prefer the latter.
- A parameter with a `None` default should be annotated as optional - and
  must never have a mutable default such as `[]` or `{}`, because that object
  is created once and shared by every call.

## Simplicity

- A function should do one thing. If you need "and" to describe it, split it.
- Replace a manual index loop with direct iteration, or with `enumerate` when
  the index is genuinely needed.
- Use a comprehension for a simple map or filter; use an explicit loop when
  the body has branches or side effects. A nested comprehension three levels
  deep is not clever, it is unreadable.
- Delete commented-out code. Version control remembers it; the reader should
  not have to.
