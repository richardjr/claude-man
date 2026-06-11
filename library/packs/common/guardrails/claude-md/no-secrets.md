# Secrets never land in files or history

Never write credentials — API keys, tokens, passwords, private keys, connection strings with
passwords — into source files, config, docs, commit messages, or test fixtures, even as
"temporary" values. Use environment variables or the project's secret mechanism and reference
them by name. Never commit `.env`-style files; if one must exist locally, make sure it is
gitignored before creating it. When printing command output or logs, redact any secret values
that appear. If you find an existing committed secret, flag it to the operator (it needs
rotation, not just deletion).
