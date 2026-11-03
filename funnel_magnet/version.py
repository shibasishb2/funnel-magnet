"""Identify the exact code that is running, so cached results never outlive it.

Streamlit's data cache keys a function by its own source and arguments, not by the
code it calls. Passing this fingerprint as an argument ties a cached upload result
to the importer and engine that produced it.
"""

import hashlib
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent


def code_version(root=PACKAGE_DIR):
    """Return a short hash of every Python file in the package."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]
