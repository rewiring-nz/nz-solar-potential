"""Where the project's keys come from: the environment, else Secret Manager.

Order, first hit wins:
  1. the process environment (LINZ_API_KEY), which is also where a local
     .env lands once python-dotenv has loaded it;
  2. Google Secret Manager, secret `linz-api-key` in project
     solar-potential-map-512020, read with the gcloud CLI as whoever is signed in --
     the build VM's service account, or a team member's Google account.

So nobody needs a copy of the key on their machine: anyone the team's Google
Group lets read the secret gets it on demand, and changing it once changes
it for everyone. The value is never printed or logged.

Named project_secrets, not secrets: scripts run as `python src/x.py` put
src/ first on sys.path, and a src/secrets.py would shadow the standard
library module of that name for every package that imports it.
"""

import os
import subprocess

PROJECT = "solar-potential-map-512020"
SECRETS = {"LINZ_API_KEY": "linz-api-key"}


def get(name):
    """The value of `name`, or None if neither source has it."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    v = os.environ.get(name, "").strip()
    if v:
        return v
    secret = SECRETS.get(name)
    if not secret:
        return None
    try:
        r = subprocess.run(["gcloud", "secrets", "versions", "access", "latest",
                            f"--secret={secret}", f"--project={PROJECT}"],
                           capture_output=True, text=True, timeout=60)
    except Exception:
        return None
    v = r.stdout.strip() if r.returncode == 0 else ""
    if v:
        os.environ[name] = v        # once per process, not once per call
        return v
    return None


def linz_api_key():
    return get("LINZ_API_KEY")
