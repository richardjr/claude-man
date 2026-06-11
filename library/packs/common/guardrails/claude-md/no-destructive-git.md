# No destructive git operations

Never run, without an explicit instruction naming the operation:

- `git push --force` / `--force-with-lease` to any shared branch, or any rewrite of published
  history (`rebase`/`amend` on commits that exist on a remote);
- `git reset --hard`, `git checkout --`/`git restore` over changes you did not author this
  session, or `git clean -fd`;
- branch or tag deletion (`git branch -D`, `git push --delete`);
- history-editing tools (`filter-branch`, `filter-repo`).

If one of these looks like the right fix, stop and say so — present the situation and let the
operator decide. Losing uncommitted or unpushed work is never an acceptable side effect of
"tidying up".
