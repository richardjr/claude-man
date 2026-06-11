# Test discipline

- Run the project's test suite before declaring work done, and report the result honestly — if
  tests fail, say so with the output; never describe failing work as finished.
- New behaviour ships with tests; a bug fix ships with the test that would have caught it.
- Never weaken, skip, or delete a failing test just to make the suite pass. If a test is wrong,
  say why and fix it as its own visible change.
- Don't mock away the thing under test; prefer the lightest test that would actually catch the
  regression.
