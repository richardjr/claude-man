"""Live container log pane (Phase 1).

A RichLog fed by an `@work(thread=True, exclusive, group='logs')` worker running
`docker logs -f --tail 200 --timestamps <container>`, pushing lines via
`call_from_thread`. The follower is reaped (`proc.terminate()`) on container switch and
app shutdown so no `docker logs -f` leaks.
"""

from __future__ import annotations

# Implemented in Phase 1 — see ROADMAP.md.
