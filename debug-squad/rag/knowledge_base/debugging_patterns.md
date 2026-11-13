# Debugging Patterns

Notes for the Bug-Finder and Fixer agents on how defects are actually located.

## Read the traceback from the bottom up

The last line names the exception and its message. The frame directly above it
is where the error surfaced. The frames above *that* are how execution got
there. The line that raised is frequently not the line that is wrong - a
`NoneType` attribute error surfaces where the value is used and originates
where it was produced.

## Narrow before you theorise

Find the smallest input that still reproduces the problem. A bug that
reproduces with a 2-element list is far easier to reason about than one that
needs a thousand. Halve the input, halve the code path, repeat.

## Check the boundaries first

When a function is wrong for *some* inputs, the boundary is the first place to
look:
- empty collection
- exactly one element
- all elements equal
- the minimum and maximum representable value
- negative numbers, where the code assumed positive
- zero, where the code is about to divide

## Interrogate the initial value

An accumulator initialised to the wrong value is one of the most common
logical bugs, and it is invisible on happy-path input.

    maximum = 0                     # wrong: assumes a non-negative maximum
    for num in nums:
        if num > maximum:
            maximum = num
    return maximum                  # find_max([-5, -2]) returns 0

Correct patterns: initialise from the first element (`maximum = nums[0]`) and
handle the empty case explicitly, or seed with `float("-inf")`, or just use
the builtin `max()`.

The same mistake appears with `minimum = 0`, with a product accumulator
initialised to `0` instead of `1`, and with a "best so far" initialised to an
arbitrary constant rather than to the first observation.

## Check the comparison and its direction

- `>` where `>=` was meant, and the reverse: this decides what happens on ties.
- A comparison reversed, so the function computes the opposite of its name.
- `and` where `or` was meant. Test both branches, not just the one you expect.
- An inclusive range written exclusively: `range(1, n)` omits `n`.

## Check the loop bounds

- `range(len(items))` is correct; `range(len(items) + 1)` raises `IndexError`.
- `items[i + 1]` needs the loop to stop one element early.
- A slice `items[0:n+1]` keeps `n + 1` elements. If the contract says `n`, use
  `items[:n]`.
- Mutating a list while iterating over it skips elements. Iterate over a copy,
  or build a new list.

## Watch for state that outlives the call

- A mutable default argument (`def f(items=[])`) is created once at definition
  time and shared across every call.
- A module-level accumulator that is never reset makes the second call behave
  differently from the first.
- A function that both returns a value and mutates its argument will surprise
  somebody.

## Verify, do not assume

After a fix, run the exact input that failed before. A fix that was never
executed against the failing case is a hypothesis, not a fix. If the test
still fails, read the new output rather than guessing a second time - the
failure message is evidence, and it is usually telling you something precise.

## Separate "wrong" from "fragile"

Code that produces an incorrect result on plausible input is a bug. Code that
works but would break if an assumption changed is a risk. Report both, but do
not conflate them: labelling a style preference as a critical defect destroys
the reader's trust in the whole report.
