"""Review-gated three-way merge of in-container Claude config changes back to the host.

Pipeline (see docs/ARCHITECTURE.md): seed baseline -> detect changes -> diff (secret-masked)
-> TUI accept/reject gate -> backup + field-merge -> git-commit audit. The denylist is
enforced BEFORE any read and AGAIN at git-staging time.
"""
