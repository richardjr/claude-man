# Cargo conventions

- Use `cargo` for everything: `cargo build`/`test`/`run`; dependencies via `cargo add`/`rm`
  (subject to the ask-before-deps rule), never hand-edit version pins without saying so.
- Before declaring work done: `cargo fmt` (only on files you touched — no drive-by reformat of
  the tree) and `cargo clippy` clean for your changes; don't `#[allow(...)]` a lint away without
  flagging it.
- Respect the workspace: in a multi-crate repo run `cargo <cmd> -p <crate>` from the root rather
  than `cd`-ing into crates; keep shared deps in the workspace `[workspace.dependencies]` table.
- Don't commit `Cargo.lock` changes unrelated to your dependency change.
