"""Test package setup.

`rank` starts a background Explorer so a finished search has a working link.
Unit tests must never spawn a server, so the opt-out is set here for the whole
suite rather than left to whoever runs it. `tests/test_explorer.py` covers the
launcher itself, enabling it explicitly where that is the thing under test.
"""

import os

os.environ.setdefault("MUSE_SHROOM_NO_EXPLORER", "1")
