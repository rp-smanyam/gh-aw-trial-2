"""sys.path shim so backfill script modules are importable as flat names.

`scripts/task_activity_backfill/` is a script directory, not a package —
the scripts insert their own parent on sys.path at module top. Tests
need the same shim before any `import replay_trace_activity_events`.
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKFILL_DIR = Path(__file__).resolve().parents[4] / "scripts" / "task_activity_backfill"
if str(_BACKFILL_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKFILL_DIR))
