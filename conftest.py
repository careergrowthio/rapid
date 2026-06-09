import os, sys

# Put the repo root on sys.path so tests can `import rapid_agent` regardless of
# pytest's import mode (tests/ is intentionally not a package).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
