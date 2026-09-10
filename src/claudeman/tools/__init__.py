"""The approved-tool registry (docs/TOOLS.md): a per-project SELECTION of pinned, floor-verified
tools baked as an additive image layer on top of the project's overlay. ``library`` discovers +
parses ``library/tools/<name>/tool.toml``; ``render`` turns a selection into a Dockerfile + a
content-addressed image name. Both are PURE (stdlib only) so the CLI/tests import them without
docker or textual."""
