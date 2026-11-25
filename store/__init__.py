"""
The store package: the ONE place that knows where CS2-AimAudit's files
live. Four areas, all re-exported here so `store.X` keeps working:

- store.paths      - the data folder, its subfolders and file names
- store.library    - the demo library (copies + linked demos)
- store.caches     - parsed / matchstats caches, storage report and clear
- store.importdata - accounts.json / matches.json (match import)

Nothing here touches the network; plain Python only (no Streamlit, no
demo parsing) so it can be tested on its own. See store/paths.py for
the folder map and the AIMAUDIT_DATA_DIR / read-only-fallback rules.
"""

from __future__ import annotations

from store.paths import *  # noqa: F403
from store.library import *  # noqa: F403
from store.caches import *  # noqa: F403
from store.importdata import *  # noqa: F403
