"""Point every module at a throwaway SQLite file before anything imports it."""
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

os.environ["S2C_DB_PATH"] = str(Path(tempfile.mkdtemp(prefix="s2c-tests-")) / "jobs.db")
os.environ.setdefault("GOOGLE_API_KEY", "test-key")
os.environ.setdefault("GEMINI_API_KEY", "test-key")
