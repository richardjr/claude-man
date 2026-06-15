"""Pure helpers for patching claude-man-owned *managed blocks* into a CLAUDE.md body.

A managed block is a fenced region delimited by an HTML-comment begin/end marker that claude-man
owns: the content inside is regenerated on every start, while operator content OUTSIDE the markers
is preserved verbatim (the same field-patch philosophy as the settings.json merge). Two features
use it — the pack ``@``-import list (``packs/materialize.py``) and the scratch-dir note
(``scratch.py``) — each with its OWN distinct marker prefix so multiple blocks coexist in one file
without colliding.

``patch_block`` replaces an existing block IN PLACE (only appending at end when the block is
absent), so a file carrying two managed blocks is stable across repeated patches — neither block
migrates to the bottom, so the asset/bind sync never sees spurious churn. Pure stdlib, no imports,
fully unit-testable.
"""

from __future__ import annotations


def patch_block(
    text: str,
    lines: list[str],
    *,
    begin: str,
    end: str,
    begin_prefix: str | None = None,
    end_prefix: str | None = None,
) -> str:
    """Replace / append / remove the managed block delimited by ``begin``/``end`` in ``text``.

    ``lines`` are the block's interior rows. An empty ``lines`` REMOVES the block. Matching is by
    prefix (``begin_prefix``/``end_prefix``, defaulting to the full markers) so an in-container edit
    to the marker comment's trailing text can't orphan the block — the canonical markers are always
    rewritten. A start marker with no end marker (a corrupted edit) is healed by treating everything
    from the start marker to EOF as the old block.

    Position-preserving: an existing block is rewritten where it sits; a NEW block is appended after
    the (blank-trimmed) body with one blank-line separator. Idempotent — re-patching identical
    content returns a byte-identical string.
    """
    bpre = begin_prefix or begin
    epre = end_prefix or end
    rows = text.splitlines()

    start_idx: int | None = None
    for k, row in enumerate(rows):
        if row.lstrip().startswith(bpre):
            start_idx = k
            break

    if start_idx is None:
        if not lines:
            return text  # no block, nothing to add — byte-identical round-trip
        out = list(rows)
        while out and not out[-1].strip():
            out.pop()
        if out:
            out.append("")
        out.extend([begin, *lines, end])
        return "\n".join(out) + "\n"

    # Locate the end marker (or EOF when the block is unterminated — the heal case).
    end_idx = start_idx + 1
    while end_idx < len(rows) and not rows[end_idx].lstrip().startswith(epre):
        end_idx += 1
    before = rows[:start_idx]
    after = rows[end_idx + 1:] if end_idx < len(rows) else []

    if lines:
        new_rows = before + [begin, *lines, end] + after
    else:
        # Removal: drop the block and collapse the blank-line seam it leaves behind.
        b = list(before)
        while b and not b[-1].strip():
            b.pop()
        a = list(after)
        while a and not a[0].strip():
            a.pop(0)
        new_rows = (b + [""] + a) if (b and a) else (b + a)

    body = "\n".join(new_rows)
    return body + "\n" if body else ""
