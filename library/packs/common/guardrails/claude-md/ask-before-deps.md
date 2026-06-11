# Dependencies are a decision, not a convenience

Do not add, remove, or upgrade dependencies without asking first — that includes dev
dependencies, transitive pins, and lockfile-only bumps. Prefer, in order: the language's standard
library, something already in the dependency tree, then a small amount of project code. When a
new dependency genuinely is the right call, propose it with the reason and wait for a yes before
touching the manifest.
