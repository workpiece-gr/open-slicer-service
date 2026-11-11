"""Shared test configuration.

The service reads its profile root from PROFILE_ROOT at import time (the container
sets /app/profiles). Point it at the repository profiles before any test imports
app.main so profiles_ready() and the printer registry resolve locally.
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("PROFILE_ROOT", str(REPO_ROOT / "profiles"))
