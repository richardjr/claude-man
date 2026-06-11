# Code quality

- Write code that reads like the surrounding code: match its naming, idiom, error handling, and
  comment density. Before writing a new helper, search for an existing one that already does it.
- No drive-by churn: don't reformat, reorder imports, or "improve" lines the task doesn't touch —
  it buries the real change in the diff.
- Comments state constraints the code can't show (the *why*, an invariant, a gotcha) — never
  narrate what the next line does, and never address the reviewer ("now we fix…").
- Keep functions focused; if a change makes one sprawl, extract — but only within the task's
  scope.
- Leave the linter clean for the files you touched; don't suppress warnings to get there without
  flagging it.
