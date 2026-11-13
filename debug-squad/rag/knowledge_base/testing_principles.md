# Testing Principles

Notes for the Test-Writer agent on designing tests that are worth running.

## The oracle problem

A test needs two things: an input, and the correct output for that input. The
input is easy. The correct output - the *oracle* - is the hard part, and
getting it from the wrong place is the most common way an entire test suite
becomes worthless.

Legitimate sources for an expected value:

1. A written specification or the function's docstring.
2. A mathematical or definitional truth. The mean of `[1, 2, 3]` is `2`
   because that is what a mean is, regardless of any implementation.
3. A hand-computed result a human can check by inspection.
4. An independent, simpler implementation (`sum(xs) / len(xs)` to check an
   optimised streaming mean).
5. A stated assumption, written down as an assumption.

**Illegitimate source:** running the code and recording what it returned.
That produces a test which passes forever and asserts nothing, because it
asserts the bug. A test over `find_max` that expects `0` for `[-5, -2]` has
canonised the defect: the implementation can never be fixed without the test
turning red, and the suite now actively defends the bug.

When no specification exists and the behaviour is genuinely a judgement call -
should `average([])` raise, or return `None`? - write the assumption down
explicitly rather than quietly picking one and presenting it as fact.

## What to cover

**Normal cases.** Typical input, the thing the function exists to do. At
least one, and keep it obvious enough that a failure is immediately readable.

**Boundary cases.** Where behaviour changes:
- empty collection
- exactly one element
- exactly two elements, for anything involving comparison or pairing
- zero, as a value and as a divisor
- the first and last position of a sequence
- the smallest and largest values the contract allows

**Negative and edge cases.** Input the function should reject or handle
deliberately:
- wrong type
- `None` where a value was expected
- negative numbers where the code assumed non-negative
- duplicate values, where uniqueness may have been assumed
- already-sorted and reverse-sorted input, for anything that sorts

## Writing a good test

- One behaviour per test. A test asserting six unrelated things reports only
  the first failure and tells you little.
- The name is the specification: `test_find_max_handles_all_negative_values`
  beats `test_2`.
- Arrange, act, assert - in that order, visibly.
- No dependency on execution order. Each test sets up everything it needs.
- No network, no clock, no randomness, no filesystem. A test that depends on
  any of those is a test that fails for reasons unrelated to the code.
- Assert the specific value, not merely that something was returned.
  `assert result == 3` is a test; `assert result is not None` barely is.

## Testing for exceptions

Assert that the *right* exception type is raised, and ideally that it carries
a useful message:

    with pytest.raises(ValueError, match="empty"):
        average([])

`pytest.raises(Exception)` is almost worthless - it passes when the code fails
for an entirely different reason than intended.

## Parametrisation

When the same logic holds over many inputs, `@pytest.mark.parametrize` keeps
each case independently reported while writing the assertion once. Keep the
cases visible in the decorator rather than generating them from a loop, so a
failure names the exact input.

## What a passing test does and does not prove

A passing test proves that one input produced one expected output. It does not
prove the function is correct, and a suite of passing tests is evidence rather
than proof. Conversely, a failing test is a fact: something concrete about
that specific input is wrong - either in the code or in the expectation. Read
the failure output before deciding which.

Tests that were never executed prove nothing at all, and should never be
reported as if they had passed.
