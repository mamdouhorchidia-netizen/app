"""Local Vorath (fleet, sign-in on) over the golden fixtures, with a ready owner. Loopback only; for developing the phone app."""
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
home = Path(os.environ["VORATH_HOME"]) / "config"
from fleet.accounts import Accounts, totp
from fleet.server import serve, Gate
from spine.store import Store
PW = "correct horse battery"
acc = Accounts(home)
if acc.ensure_owner({"VORATH_OWNER_USER": "owner", "VORATH_OWNER_PASSWORD": PW}):
    P = acc._people()
    if P["owner"].get("must_change") or not P["owner"].get("totp_on"):
        P["owner"].pop("must_change", None); acc._save_people(P)
        acc.totp_start("owner")
        acc.totp_confirm("owner", totp(acc._people()["owner"]["totp_pending"], time.time()))
secret = acc._people()["owner"]["totp"]
(home / "dev-login.json").write_text(json.dumps({"user": "owner", "password": PW, "totp_secret": secret}))
Gate.secure_cookie = False
serve(host="127.0.0.1", port=int(os.environ.get("PORT", "8790")), store=Store(), accounts=acc, schedule=False)
