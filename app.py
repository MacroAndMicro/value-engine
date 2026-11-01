r"""Launch the web app:  .venv\Scripts\streamlit run app.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))  # works even without `pip install -e .`

from value_engine.ui.main import main  # noqa: E402

main()
