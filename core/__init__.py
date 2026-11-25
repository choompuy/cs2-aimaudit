"""
CS2 demo analysis logic (no UI). Parsing is done by demoparser2.
All column names / values here are language-neutral; the UI translates them.
"""

from __future__ import annotations

from core.model import *  # noqa: F403
from core.filters import *  # noqa: F403
from core.ids import *  # noqa: F403
from core.unpack import *  # noqa: F403
from core.phases import *  # noqa: F403
from core.loader import *  # noqa: F403
from core.matchlist import *  # noqa: F403
from core.shots import *  # noqa: F403
from core.duels import *  # noqa: F403
from core.openings import *  # noqa: F403
from core.impact import *  # noqa: F403
from core.summary import *  # noqa: F403
from core.replay import *  # noqa: F403
from core.checks import *  # noqa: F403
from core import matchstats  # noqa: F401 - core.matchstats
