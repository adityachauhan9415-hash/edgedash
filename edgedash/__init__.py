# EdgeDash package
#
# Load .env exactly once, at package import time.
# This is the single authoritative location for dotenv loading (steering
# rule 13).  Every entry point — run_cycle.py, python -m edgedash.llm,
# python -m edgedash.diagnose — imports this package first, so secrets are
# available before any os.getenv() call anywhere in the codebase.
# override=False means existing env vars (CI, production shell) always win.
from dotenv import load_dotenv as _load_dotenv
_load_dotenv(override=False)
