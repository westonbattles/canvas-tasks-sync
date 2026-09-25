"""Canvas records and stable identities, independent of either API client."""
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin, urlsplit, urlunsplit


def parse_date(value):
    if not value:
        return None
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Canvas returned a timestamp without a timezone")
    return result


def identity_url(value, base_url):
    """Compare resource URLs without query/fragment; repair the old URL concatenation."""
    if not value:
        return ""
    if value.startswith(base_url + "http"):
        value = value[len(base_url):]
    parts = urlsplit(urljoin(base_url + "/", value))
    host = parts.netloc.lower()
    if host == "canvas.instructure.com":
        host = urlsplit(base_url).netloc.lower()
    return urlunsplit(("https", host, parts.path.rstrip("/"), "", ""))


def is_submitted(submission):
    if not isinstance(submission, dict):
        return False
    return bool(
        submission.get("excused")
        or submission.get("submitted_at")
        or submission.get("submitted") is True
        or submission.get("workflow_state") in ("submitted", "pending_review")
    )


@dataclass
class WorkItem:
    host: str
    kind: str
    course_id: str
    item_id: str
    title: str
    url: str
    due_at: datetime | None
    completed: bool = False
    course_code: str = ""
    # Legacy IDs alone are not unique across Planner resource types.
    legacy_ids: set[str] = field(default_factory=set)
    urls: set[str] = field(default_factory=set)

    @property
    def key(self):
        return "|".join((self.host, self.kind, self.course_id or "-", self.item_id))

    @property
    def task_title(self):
        return f"({self.course_code}) {self.title}" if self.course_code else self.title

    def add_alias(self, course_id, item_id, url):
        self.legacy_ids.add(f"{course_id or ''}-{item_id}")
        self.urls.add(identity_url(url, f"https://{self.host}"))

    def merge_planner(self, planner):
        # Assignments own the title/deadline; Planner contributes completion and aliases.
        self.completed = self.completed or planner.completed
        self.legacy_ids.update(planner.legacy_ids)
        self.urls.update(planner.urls)
