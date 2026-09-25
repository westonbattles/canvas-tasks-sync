"""Google Tasks adapter. Authentication is non-interactive except --authorize."""
import json
import os
from pathlib import Path

from sync import managed_identity

SCOPES = ["https://www.googleapis.com/auth/tasks"]


def authenticate(token_path="token.json", authorize=False, credentials_path="credentials.json"):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    import google_auth_httplib2
    import httplib2

    if authorize:
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
        credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")
        # This is only executed on an explicit local authorization command.
        descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.chmod(token_path, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(credentials.to_json())
    else:
        raw = os.environ.get("GOOGLE_TOKEN")
        if raw:
            info = json.loads(raw)
        elif Path(token_path).is_file():
            info = json.loads(Path(token_path).read_text())
        else:
            raise ValueError("Missing GOOGLE_TOKEN or token.json; run python main.py --authorize locally first")
        credentials = Credentials.from_authorized_user_info(info, SCOPES)
        if not credentials.valid:
            if not credentials.refresh_token:
                raise ValueError("Google refresh token is missing; authorize again locally")
            credentials.refresh(Request())
        # Access-token refresh does not need to rewrite the GitHub secret every run.
    transport = google_auth_httplib2.AuthorizedHttp(credentials, http=httplib2.Http(timeout=45))
    return build("tasks", "v1", http=transport, cache_discovery=False)


class GoogleTasks:
    def __init__(self, service):
        self.service = service

    @staticmethod
    def _pages(make_request):
        result, seen, token = [], set(), None
        while True:
            page = make_request(token).execute(num_retries=3)
            result.extend(page.get("items") or [])
            token = page.get("nextPageToken")
            if not token:
                return result
            if token in seen:
                raise ValueError("Google repeated a pagination token")
            seen.add(token)

    def find_list(self, list_name, list_id=None):
        if list_id:
            return self.service.tasklists().get(tasklist=list_id).execute(num_retries=3)
        lists = self._pages(lambda token: self.service.tasklists().list(maxResults=1000, pageToken=token))
        matches = [tasklist for tasklist in lists if tasklist["title"] == list_name]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one Google list named {list_name!r}; found {len(matches)}. Set GOOGLE_TASKLIST_ID explicitly.")
        return matches[0]

    def tasks(self, list_id):
        return self._pages(lambda token: self.service.tasks().list(
            tasklist=list_id, maxResults=100, pageToken=token,
            showCompleted=True, showHidden=True, showDeleted=False,
        ))

    def apply(self, list_id, plan, original_tasks):
        # Append after the last open root task, preserving the user's priority order.
        roots = [t for t in original_tasks if not t.get("parent") and not t.get("hidden")
                 and not t.get("deleted") and t.get("status") != "completed"]
        previous = max(roots, key=lambda t: t.get("position", ""))["id"] if roots else None
        completed_ids = {c.task_id for c in plan.changes if c.body.get("status") == "completed"}
        # Insert before completing the chosen previous sibling, which may leave the visible list.
        changes = sorted(plan.changes, key=lambda c: c.action != "create")
        counts = {"created": 0, "updated": 0, "completed": 0}
        for change in changes:
            if change.action == "update":
                self.service.tasks().patch(tasklist=list_id, task=change.task_id, body=change.body).execute(num_retries=3)
                counts["updated"] += 1
                counts["completed"] += int(change.task_id in completed_ids)
                continue
            args = {"tasklist": list_id, "body": change.body}
            if previous:
                args["previous"] = previous
            try:
                # POST is not idempotent. Never blindly retry an ambiguous insert.
                inserted = self.service.tasks().insert(**args).execute(num_retries=0)
            except Exception as error:
                try:
                    matches = [task for task in self.tasks(list_id)
                               if managed_identity(task)[0] == change.key]
                except Exception:
                    raise RuntimeError("Google insert outcome is unknown. Run a fresh dry-run before retrying.") from error
                if len(matches) != 1:
                    raise RuntimeError("Google insert failed or its outcome is unknown. Run a fresh dry-run before retrying.") from error
                inserted = matches[0]
            previous = inserted["id"]
            counts["created"] += 1
        return counts
