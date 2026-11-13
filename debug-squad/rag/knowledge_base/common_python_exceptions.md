# Common Python Exceptions and What They Really Mean

Reference notes for the Bug-Finder agent: how each exception arises, and the
code shape that causes it.

## ZeroDivisionError

Raised by `/`, `//` or `%` when the divisor is zero.

The classic source is an average or a percentage computed over a collection
that can be empty:

    return sum(nums) / len(nums)        # len(nums) == 0 -> ZeroDivisionError

Decide the contract before fixing it. Either raise a clear `ValueError` for an
empty input, or return a documented sentinel. Silently returning `0` is
usually wrong, because "the average of nothing is zero" is a false statement
that will mislead a caller downstream.

## IndexError

A sequence index is outside `range(len(seq))`.

Typical causes:
- `items[len(items)]` - a loop bound written with `<=` instead of `<`.
- `items[i + 1]` inside a loop that runs to the final element.
- `items[0]` on a collection that may be empty.
- An index computed by arithmetic that was never bounds-checked.

Note that slicing does *not* raise: `items[5:10]` on a 2-element list quietly
returns `[]`. That silence hides off-by-one errors rather than reporting them.

## KeyError

A dictionary key is absent. `d["missing"]` raises; `d.get("missing")` returns
`None`; `d.get("missing", default)` returns the default. Use
`collections.defaultdict` when every lookup should auto-create an entry.

A `KeyError` on data parsed from JSON or user input is a validation failure,
not a programming error - it should be handled, not just allowed to escape.

## TypeError

An operation was applied to an object of the wrong type.

- `"3" + 5` - string and int concatenation.
- `len(5)` - no length on an int.
- `None + 1` - very often the real bug is further upstream, where a function
  returned `None` by falling off its end without a `return`.
- Calling a function with the wrong number of arguments.

A function with a branch that returns a value and another branch that returns
nothing is a `TypeError` waiting to happen at the call site.

## AttributeError

The attribute does not exist on that object. `'NoneType' object has no
attribute 'x'` is the single most common Python error in real code, and it
almost always means an earlier call returned `None` and nobody checked.

Trace backwards to find where the `None` was produced, not where it was used.

## ValueError

The type is right but the value is unacceptable: `int("abc")`,
`math.sqrt(-1)`, unpacking a 3-tuple into 2 names.

`ValueError` is the right exception to *raise* yourself when an argument is
out of contract - for instance an empty list passed to a mean function.

## UnboundLocalError

A local name is read before it is assigned. Almost always a variable assigned
only inside an `if` or `try`, and read after it:

    if is_member:
        discount = price * 0.1
    return price - discount          # is_member False -> UnboundLocalError

Fix by initialising before the branch.

## StopIteration

`next()` on an exhausted iterator. Use `next(it, default)` to supply a
fallback. Inside a generator, raising `StopIteration` is an error - return
instead.

## RecursionError

Recursion depth exceeded, which means the base case is missing or never
reached. Check that each recursive call moves strictly towards the base case.

## Exception handling practice

- Catch the narrowest exception that can actually occur.
- Never write a bare `except:` - it swallows `KeyboardInterrupt` and
  `SystemExit` along with real errors. `except Exception:` is the broadest
  defensible form.
- Never silence an exception with `pass` unless you can explain in a comment
  why nothing needs to happen.
- Use `raise ... from exc` to preserve the original cause.
- `finally` runs regardless; prefer a `with` block for resource cleanup.
