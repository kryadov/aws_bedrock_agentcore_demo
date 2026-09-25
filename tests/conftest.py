import importlib.util
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).parent))  # `from conftest import PROJECT_ROOT`

# tests/agent needs strands from the agent's own venv:
#   cd app/HrAssistant && uv run --with pytest pytest ../../tests/agent
collect_ignore = [] if importlib.util.find_spec("strands") else ["agent"]
