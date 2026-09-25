"""Canvas -> Google Tasks. Local default: dry-run; --apply performs writes."""
import argparse
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

LOG = logging.getLogger("canvas-sync")
ROOT = Path(__file__).resolve().parent


def run(args):
    from dotenv import load_dotenv
    from canvas import CanvasClient
    from google_tasks import GoogleTasks, authenticate
    from sync import build_plan

    load_dotenv(ROOT / ".env")
    if args.authorize:
        authenticate(str(ROOT / "token.json"), authorize=True, credentials_path=str(ROOT / "credentials.json"))
        LOG.info("Authorization saved locally. Copy token.json to the GOOGLE_TOKEN Actions secret.")
        return
    token = os.environ.get("CANVAS_AUTH_KEY")
    if not token:
        raise ValueError("CANVAS_AUTH_KEY is required")
    tz = ZoneInfo(os.environ.get("SYNC_TIMEZONE", "America/Los_Angeles"))
    selected = [value.strip() for value in os.environ.get("CANVAS_COURSE_IDS", "").split(",") if value.strip()]
    if any(not value.isdigit() for value in selected):
        raise ValueError("CANVAS_COURSE_IDS must be comma-separated numeric IDs")
    google = GoogleTasks(authenticate(str(ROOT / "token.json")))
    tasklist = google.find_list(os.environ.get("GOOGLE_TASKLIST_NAME", "School"),
                                os.environ.get("GOOGLE_TASKLIST_ID") or None)
    tasks = google.tasks(tasklist["id"])
    canvas = CanvasClient(os.environ.get("CANVAS_BASE_URL", "https://canvas.uoregon.edu"), token)
    lookback = int(os.environ.get("PLANNER_LOOKBACK_DAYS", "120"))
    ahead = int(os.environ.get("PLANNER_AHEAD_DAYS", "365"))
    if lookback < 1 or ahead < 1:
        raise ValueError("Planner lookback and ahead days must be positive")
    items = canvas.collect(tasks, selected, lookback, ahead)
    overrides = json.loads(os.environ.get("CANVAS_DUE_OVERRIDES", "{}"))
    if not isinstance(overrides, dict):
        raise ValueError("CANVAS_DUE_OVERRIDES must be a JSON object of source IDs to timestamps or null")
    from models import parse_date
    for item in items:
        if item.key in overrides:
            item.due_at = parse_date(overrides[item.key])
    plan = build_plan(items, tasks, tz)
    report = {
        "mode": "apply" if args.apply else "dry-run",
        "at": datetime.now(timezone.utc).isoformat(),
        "canvas": dict(canvas.counts), "plan": plan.summary(),
        # IDs and changed field names only; no student names, task titles, or tokens in CI logs.
        "changes": [{"action": c.action, "source": c.key, "fields": sorted(c.body)} for c in plan.changes],
    }
    print(json.dumps(report, indent=2))
    if args.apply:
        result = google.apply(tasklist["id"], plan, tasks)
        print(json.dumps({"sync_success_at": datetime.now(timezone.utc).isoformat(), **result}))
    else:
        LOG.info("Dry-run complete. No Google tasks or lists were changed. Use --apply to apply this plan.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Apply the changes to Google Tasks")
    mode.add_argument("--dry-run", action="store_true", help="Read and show changes only (default)")
    mode.add_argument("--authorize", action="store_true", help="Authorize Google locally and save token.json; does not sync")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        run(args)
    except Exception as error:
        # HttpError and auth errors may contain payloads: don't dump them into public logs.
        if isinstance(error, (ValueError, RuntimeError)) and error.__class__.__module__ not in ("requests.exceptions",):
            LOG.error("%s: %s", type(error).__name__, error)
        else:
            LOG.error("%s: sync failed; check API access, credentials, and the last successful run", type(error).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
