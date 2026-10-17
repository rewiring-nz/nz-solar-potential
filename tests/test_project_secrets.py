"""Keys come from the environment first, then Secret Manager, never printed."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import project_secrets as ps


def test_environment_wins():
    os.environ["LINZ_API_KEY"] = "from-env"
    try:
        assert ps.linz_api_key() == "from-env"
    finally:
        del os.environ["LINZ_API_KEY"]


def test_no_gcloud_means_none_not_a_crash():
    saved = {k: os.environ.get(k) for k in ("LINZ_API_KEY", "PATH")}
    os.environ.pop("LINZ_API_KEY", None)
    os.environ["PATH"] = "/nonexistent"
    try:
        import dotenv
        orig = dotenv.load_dotenv
        dotenv.load_dotenv = lambda *a, **k: False      # ignore any local .env
        assert ps.get("LINZ_API_KEY") is None
    finally:
        dotenv.load_dotenv = orig
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_unknown_names_are_none():
    assert ps.get("NOT_A_PROJECT_SECRET") is None


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("  pass ", name)
            except AssertionError as e:
                fails += 1
                print("  FAIL ", name, e)
    sys.exit(1 if fails else 0)
