"""Shared job environment and default test-mode exclusions."""

import os
from pathlib import Path
from threading import Lock

DEFAULT_TEST_MODE_EXCLUDED = frozenset({"aed", "email", "mms"})

# The AED SDK context and request credentials are process-global.
AED_ENVIRONMENT_LOCK = Lock()
DATABASE_ENVIRONMENT_LOCK = Lock()


def read_environment(project_dir):
    """Load project defaults without changing process-global environment values."""
    from dotenv import dotenv_values

    with AED_ENVIRONMENT_LOCK:
        environ = dict(os.environ)
    values = dotenv_values(
        Path(project_dir) / ".env", encoding="utf-8-sig", interpolate=False
    )
    return {**{name: value or "" for name, value in values.items()}, **environ}
