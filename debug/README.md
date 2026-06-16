# debug/

Working investigation notes for open issues that we can't fully diagnose or fix yet —
typically because they need a specific environment to reproduce (e.g. WSL2, macOS) that the
maintainer doesn't currently have to hand. Each file captures the symptom, what we've already
reproduced/ruled out, the leading root-cause hypotheses, the proposed fix direction, and how to
test it — so the work can be picked up later without re-deriving everything.

These are notes, not commitments. One file per issue, named `issue-<n>-<slug>.md`.
