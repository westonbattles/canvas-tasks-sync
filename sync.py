"""Pure reconciliation: plan first, then let the Google adapter apply it."""
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from models import identity_url

START = "[canvas-sync]"
END = "[/canvas-sync]"


class SyncConflict(ValueError):
    pass


def managed_identity(task):
    notes = task.get("notes") or ""
    current = re.findall(r"^canvas-sync-id:(.+)$", notes, re.MULTILINE)
    legacy = re.findall(r"^canvas-id:(.*)$", notes, re.MULTILINE)
    if len(current) > 1 or len(legacy) > 1:
        raise SyncConflict(f"Task {task['id']} has multiple Canvas identity markers")
    return (current[0].strip() if current else None, legacy[0].strip() if legacy else None)


def task_date(due_at, tz):
    return due_at.astimezone(tz).strftime("%Y-%m-%dT00:00:00.000Z") if due_at else None


def render_notes(item, old_notes, tz):
    user_notes = old_notes or ""
    if START in user_notes or END in user_notes:
        if user_notes.count(START) != 1 or user_notes.count(END) != 1 or user_notes.index(START) > user_notes.index(END):
            raise SyncConflict(f"Malformed managed notes for {item.key}; repair the marker block first")
        user_notes = re.sub(re.escape(START) + r".*?" + re.escape(END), "", user_notes, flags=re.DOTALL)
    else:
        # Migrate the old two-line format without deleting notes the user added.
        lines = []
        for line in user_notes.splitlines():
            if line.startswith(("canvas-id:", "canvas-sync-id:")):
                continue
            if line.startswith(("https://", "http://")) and identity_url(line.strip(), f"https://{item.host}") in item.urls:
                continue
            lines.append(line)
        user_notes = "\n".join(lines)
    block = [START, item.url]
    if item.due_at:
        block.append("Canvas deadline: " + item.due_at.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z"))
    block.extend(["canvas-sync-id:" + item.key, END])
    notes = "\n".join(block)
    if user_notes.strip():
        notes = user_notes.strip() + "\n\n" + notes
    if len(notes) > 8192:
        raise SyncConflict(f"Notes exceed Google's limit for {item.key}")
    return notes


@dataclass
class Change:
    action: str
    key: str
    body: dict
    task_id: str | None = None


@dataclass
class Plan:
    changes: list[Change] = field(default_factory=list)
    unchanged: int = 0
    already_completed: int = 0
    unmanaged: int = 0
    unmatched_managed: int = 0

    def summary(self):
        counts = Counter(change.action for change in self.changes)
        return {
            "create": counts["create"], "update": counts["update"],
            "complete": sum(c.body.get("status") == "completed" for c in self.changes),
            "unchanged": self.unchanged, "already_completed": self.already_completed,
            "unmanaged": self.unmanaged, "unmatched_managed": self.unmatched_managed,
        }


def build_plan(items, tasks, tz):
    sources = {item.key: item for item in items}
    if len(sources) != len(items):
        raise SyncConflict("Duplicate canonical Canvas identities in source data")
    legacy_index = defaultdict(set)
    for item in items:
        for alias in item.legacy_ids:
            legacy_index[alias].add(item.key)

    mapped = {}
    plan = Plan()
    for task in tasks:
        if task.get("deleted"):
            continue
        key, legacy = managed_identity(task)
        if not key and not legacy:
            plan.unmanaged += 1
            continue
        if not key:
            candidates = legacy_index.get(legacy, set())
            if not candidates:
                plan.unmatched_managed += 1
                continue
            # Resolve collisions between quiz/discussion/assignment IDs using source URLs.
            found_urls = re.findall(r"https?://\S+", task.get("notes") or "")
            matches = {candidate for candidate in candidates if any(
                identity_url(url, f"https://{sources[candidate].host}") in sources[candidate].urls
                for url in found_urls
            )}
            if found_urls:
                if len(matches) != 1:
                    raise SyncConflict(f"Cannot safely migrate legacy task {task['id']} ({legacy}); source URL is ambiguous or mismatched")
                key = next(iter(matches))
            elif len(candidates) == 1:
                key = next(iter(candidates))
            else:
                raise SyncConflict(f"Legacy task {task['id']} ({legacy}) matches multiple Canvas item types")
        if key not in sources:
            plan.unmatched_managed += 1
            continue
        if key in mapped:
            raise SyncConflict(f"Multiple Google tasks map to {key}: {mapped[key]['id']} and {task['id']}. Resolve the duplicate before applying.")
        mapped[key] = task

    # Sorting affects only new insertions; existing task positions are never patched.
    maximum = datetime.max.replace(tzinfo=timezone.utc)
    for item in sorted(items, key=lambda i: (i.due_at is None, i.due_at or maximum, i.key)):
        existing = mapped.get(item.key)
        if existing is None and item.completed:
            plan.already_completed += 1
            continue
        if len(item.task_title) > 1024:
            raise SyncConflict(f"Title exceeds Google's limit for {item.key}")
        desired = {
            "title": item.task_title,
            "notes": render_notes(item, existing.get("notes") if existing else "", tz),
            "due": task_date(item.due_at, tz),
        }
        if existing is None:
            plan.changes.append(Change("create", item.key, {k: v for k, v in desired.items() if v is not None}))
            continue
        body = {key: value for key, value in desired.items()
                if (existing.get(key) or None) != value}
        # Google may serialize the same calendar date with a different UTC suffix.
        if (existing.get("due") or "")[:10] == (desired["due"] or "")[:10]:
            body.pop("due", None)
        if item.completed and existing.get("status") != "completed":
            body["status"] = "completed"
        # Never send needsAction: preserve completion made manually in Google.
        if body:
            plan.changes.append(Change("update", item.key, body, existing["id"]))
        else:
            plan.unchanged += 1
    return plan
