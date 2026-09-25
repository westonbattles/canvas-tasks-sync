"""Read-only Canvas access. Every collection is paginated before reconciliation."""
import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

from models import WorkItem, identity_url, is_submitted, parse_date

LOG = logging.getLogger(__name__)
PLANNER_TYPES = {
    "assignment", "quiz", "discussion_topic", "wiki_page", "planner_note",
    "peer_review", "sub_assignment", "peer_review_sub_assignment",
}


class CanvasError(RuntimeError):
    def __init__(self, status, path):
        self.status = status
        hint = " — check CANVAS_AUTH_KEY and CANVAS_BASE_URL" if status == 401 else ""
        super().__init__(f"Canvas request failed ({status}): {path}{hint}")


class CanvasClient:
    def __init__(self, base_url, token, session=None):
        self.base_url = base_url.rstrip("/")
        origin = urlsplit(self.base_url)
        if origin.scheme != "https" or not origin.netloc or origin.username or origin.password:
            raise ValueError("CANVAS_BASE_URL must be an HTTPS origin")
        if origin.path or origin.query or origin.fragment:
            raise ValueError("CANVAS_BASE_URL must not include a path, query, or fragment")
        self.host = origin.netloc.lower()
        self.token = token
        if session is None:
            import requests
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry
            session = requests.Session()
            retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
                          allowed_methods=["GET"], respect_retry_after_header=True)
            session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session = session
        self.counts = Counter()

    def request(self, path, params=None):
        target = urljoin(self.base_url + "/", path)
        parsed = urlsplit(target)
        if (parsed.scheme != "https" or parsed.netloc.lower() != self.host
                or not parsed.path.startswith("/api/v1/")):
            raise ValueError("Refusing a Canvas pagination URL outside this Canvas API")
        try:
            response = self.session.get(
                target, params=params, headers={"Authorization": f"Bearer {self.token}"},
                timeout=(10, 45), allow_redirects=False,
            )
        except Exception as error:
            # Do not include request objects, headers, or token-bearing errors in logs.
            raise CanvasError("network", parsed.path) from error
        if not 200 <= response.status_code < 300:
            raise CanvasError(response.status_code, parsed.path)
        return response

    def get(self, path, params=None):
        data = self.request(path, params).json()
        if not isinstance(data, dict):
            raise ValueError("Expected a Canvas object")
        return data

    def pages(self, path, params=None):
        result, seen = [], set()
        while path:
            response = self.request(path, params)
            data = response.json()
            if not isinstance(data, list):
                raise ValueError("Expected a Canvas collection")
            result.extend(data)
            path = response.links.get("next", {}).get("url")
            if path in seen:
                raise ValueError("Canvas repeated a pagination link")
            if path:
                seen.add(path)
            params = None  # The next Link URL already contains all pagination parameters.
        return result

    def courses(self, selected_ids=()):
        if selected_ids:
            records = [self.get(f"/api/v1/courses/{course_id}") for course_id in selected_ids]
        else:
            records = self.pages("/api/v1/courses", {
                "enrollment_state": "active", "enrollment_type": "student",
                "include[]": "term", "per_page": 100,
            })
        courses = {}
        now = datetime.now(timezone.utc)
        for course in records:
            if not selected_ids:
                if course.get("workflow_state") in ("completed", "deleted"):
                    continue
                # Explicit course selection can include an older course if desired.
                end = course.get("end_at") if course.get("restrict_enrollments_to_course_dates") else (course.get("term") or {}).get("end_at")
                if end and parse_date(end) < now:
                    continue
            courses[str(course["id"])] = course
        self.counts["courses"] = len(courses)
        return courses

    def assignment(self, record, course):
        course_id = str(course["id"])
        item_id = str(record["id"])
        item = WorkItem(
            self.host, "assignment", course_id, item_id,
            record["name"], urljoin(self.base_url, record.get("html_url") or f"/courses/{course_id}/assignments/{item_id}"),
            parse_date(record.get("due_at")), is_submitted(record.get("submission")),
            course.get("course_code") or course.get("name") or course_id,
        )
        item.add_alias(course_id, item_id, item.url)
        if record.get("quiz_id"):
            item.add_alias(course_id, str(record["quiz_id"]), f"/courses/{course_id}/quizzes/{record['quiz_id']}")
        discussion_id = record.get("discussion_topic_id") or (record.get("discussion_topic") or {}).get("id")
        if discussion_id:
            item.add_alias(course_id, str(discussion_id), f"/courses/{course_id}/discussion_topics/{discussion_id}")
        return item

    def planner(self, record, courses):
        kind = record.get("plannable_type")
        if kind not in PLANNER_TYPES:
            self.counts[f"skipped_{kind or 'unknown'}"] += 1
            return None
        obj = record.get("plannable")
        if not isinstance(obj, dict):
            raise ValueError("Planner returned a supported item without its object")
        course_id = str(record.get("course_id") or obj.get("course_id") or "")
        if course_id and course_id not in courses:
            self.counts["skipped_other_course"] += 1
            return None
        item_id = str(record.get("plannable_id") or obj["id"])
        course = courses.get(course_id, {})
        due = obj.get("due_at")
        if kind not in ("assignment", "sub_assignment", "peer_review_sub_assignment"):
            due = due or obj.get("todo_date") or record.get("plannable_date")
        item = WorkItem(
            self.host, kind, course_id, item_id,
            obj.get("title") or obj.get("name") or "Untitled Canvas item",
            urljoin(self.base_url, record.get("html_url") or ""),
            parse_date(due),
            is_submitted(record.get("submissions")) or bool((record.get("planner_override") or {}).get("marked_complete")),
            course.get("course_code") or course.get("name") or "",
        )
        item.add_alias(course_id, item_id, item.url)
        return item

    def collect(self, tasks, selected_ids=(), lookback_days=120, ahead_days=365, now=None):
        now = now or datetime.now(timezone.utc)
        courses = self.courses(selected_ids)
        records, alias_map = {}, {}

        def add_assignment(raw, course):
            item = self.assignment(raw, course)
            records[item.key] = item
            for alias in item.urls:
                alias_map[alias] = item.key
            return item

        for course_id, course in courses.items():
            rows = self.pages(f"/api/v1/courses/{course_id}/assignments",
                              {"include[]": "submission", "per_page": 100})
            self.counts["assignments"] += len(rows)
            for row in rows:
                add_assignment(row, course)

        # Revisit known unfinished assignments even when their course has ended.
        # Merely disappearing/inaccessibility never completes or deletes a Google task.
        from sync import managed_identity
        tracked = set()
        since = now - timedelta(days=lookback_days)
        for task in tasks:
            key, legacy = managed_identity(task)
            if not (key or legacy) or task.get("status") == "completed":
                continue
            if task.get("due"):
                since = min(since, parse_date(task["due"]) - timedelta(days=1))
            reference = None
            if key:
                parts = key.split("|")
                if len(parts) == 4 and parts[0] == self.host and parts[1] == "assignment":
                    reference = (parts[2], parts[3])
            elif legacy:
                for candidate in re.findall(r"https?://\S+", task.get("notes", "")):
                    url = urlsplit(identity_url(candidate, self.base_url))
                    match = re.fullmatch(r"/courses/(\d+)/assignments/(\d+)", url.path)
                    if url.netloc == self.host and match:
                        reference = match.groups()
            if reference:
                course_id, item_id = reference
                source_key = "|".join((self.host, "assignment", course_id, item_id))
                if source_key in records or reference in tracked:
                    continue
                tracked.add(reference)
                try:
                    raw = self.get(f"/api/v1/courses/{course_id}/assignments/{item_id}", {"include[]": "submission"})
                except CanvasError as error:
                    if error.status not in (403, 404):
                        raise
                    LOG.warning("Previously imported assignment %s/%s is inaccessible; left unchanged", course_id, item_id)
                    continue
                course = courses.get(course_id)
                if course is None:
                    course = self.get(f"/api/v1/courses/{course_id}")
                add_assignment(raw, course)
                self.counts["backlog_assignments"] += 1

        rows = self.pages("/api/v1/planner/items", {
            "start_date": since.isoformat(), "end_date": (now + timedelta(days=ahead_days)).isoformat(),
            "per_page": 100,
            # Fetch completed items too so we can propagate completion.
        })
        self.counts["planner_items"] = len(rows)
        for row in rows:
            item = self.planner(row, courses)
            if item is None:
                continue
            obj = row["plannable"]
            canonical_key = item.key if item.key in records else alias_map.get(identity_url(item.url, self.base_url))
            associated_id = obj.get("assignment_id") or (row.get("planner_override") or {}).get("assignment_id")
            if associated_id and item.kind in ("quiz", "discussion_topic"):
                candidate = "|".join((self.host, "assignment", item.course_id, str(associated_id)))
                if candidate not in records:
                    raw = self.get(f"/api/v1/courses/{item.course_id}/assignments/{associated_id}", {"include[]": "submission"})
                    add_assignment(raw, courses[item.course_id])
                canonical_key = candidate
            if canonical_key:
                records[canonical_key].merge_planner(item)
            else:
                records[item.key] = item
        self.counts["unique_items"] = len(records)
        return list(records.values())
