"""Compatibility CLI re-export.

The full coding-agent CLI now lives in `echoweave_coding_agent.cli`. Runtime keeps
this thin module so older imports and console entry points can continue to work
during the package boundary migration.
"""

from echoweave_coding_agent.cli import *  # noqa: F401,F403
from echoweave_coding_agent.cli import app
