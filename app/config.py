"""Load only this project's local configuration before environment consumers."""
from pathlib import Path
from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parents[1] / '.env'


def load_config():
    # Deployment/shell settings win. Keep credentials literal, including ${...}.
    return load_dotenv(ENV_FILE, override=False, encoding='utf-8-sig', interpolate=False)
