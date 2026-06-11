# Keep the project's notes in step with the code

If the project keeps planning/decision notes (a docs/ folder, a vault, ADRs, an issue list),
update them in the same pass as the code change — don't wait to be asked:

- promote a placeholder/TBC entry to a full write-up once the change lands;
- record cross-repo touchpoints, trade-offs, and follow-ups that aren't obvious from the diff;
- bump any status/updated fields the notes carry (proposed → accepted, open → fixed);
- append a dated log entry for operator-visible changes (migrations, behaviour changes, new
  surfaces).
