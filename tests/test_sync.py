import copy
import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from models import WorkItem, identity_url, is_submitted
from sync import SyncConflict, build_plan, render_notes, task_date

TZ = ZoneInfo("America/Los_Angeles")


def item(item_id="2", kind="assignment", due="2026-10-01T06:59:00+00:00", completed=False):
    value = WorkItem("canvas.uoregon.edu", kind, "1", item_id, "Essay",
                     f"https://canvas.uoregon.edu/courses/1/{'assignments' if kind == 'assignment' else 'quizzes'}/{item_id}",
                     datetime.fromisoformat(due) if due else None, completed, "WR101")
    value.add_alias("1", item_id, value.url)
    return value


def existing(value, **overrides):
    task = {"id": "google-id", "status": "needsAction", "position": "00001",
            "title": value.task_title, "notes": render_notes(value, "", TZ)}
    if value.due_at:
        task["due"] = task_date(value.due_at, TZ)
    task.update(overrides)
    return task


class PlanTests(unittest.TestCase):
    def test_second_run_is_noop(self):
        source = item()
        self.assertEqual(build_plan([source], [existing(source)], TZ).changes, [])

    def test_completion_title_and_due_update_together(self):
        source = item(completed=True)
        task = existing(source, title="Old title", due="2020-01-01T00:00:00Z")
        plan = build_plan([source], [task], TZ)
        self.assertEqual(len(plan.changes), 1)
        self.assertEqual(set(plan.changes[0].body), {"title", "due", "status"})
        self.assertEqual(plan.changes[0].body["status"], "completed")
        self.assertNotIn("position", plan.changes[0].body)

    def test_manual_completion_is_preserved(self):
        source = item()
        plan = build_plan([source], [existing(source, status="completed", hidden=True)], TZ)
        self.assertEqual(plan.changes, [])

    def test_completed_source_not_inserted(self):
        self.assertEqual(build_plan([item(completed=True)], [], TZ).already_completed, 1)

    def test_mixed_dated_and_undated_items_sort(self):
        plan = build_plan([item("3", due=None), item()], [], TZ)
        self.assertTrue(plan.changes[0].key.endswith("|2"))
        self.assertNotIn("due", plan.changes[1].body)

    def test_dates_are_local_calendar_days_and_same_date_does_not_patch(self):
        source = item()
        self.assertEqual(task_date(source.due_at, TZ), "2026-09-30T00:00:00.000Z")
        self.assertEqual(build_plan([source], [existing(source, due="2026-09-30T00:00:00Z")], TZ).changes, [])

    def test_due_date_removal_is_explicit_null(self):
        source = item(due=None)
        plan = build_plan([source], [existing(source, due="2026-01-01T00:00:00Z")], TZ)
        self.assertIn("due", plan.changes[0].body)
        self.assertIsNone(plan.changes[0].body["due"])

    def test_legacy_marker_migrates_in_place_and_keeps_user_notes(self):
        source = item()
        task = existing(source, notes=source.url + "\ncanvas-id:1-2\nMy own note")
        change = build_plan([source], [task], TZ).changes[0]
        self.assertEqual(change.action, "update")
        self.assertEqual(change.task_id, "google-id")
        self.assertIn("My own note", change.body["notes"])
        self.assertNotIn("\ncanvas-id:", change.body["notes"])
        updated = {**task, **change.body}
        self.assertEqual(build_plan([source], [updated], TZ).changes, [])

    def test_type_collision_resolved_by_url(self):
        assignment, quiz = item(), item(kind="quiz")
        old = existing(quiz, notes=quiz.url + "\ncanvas-id:1-2")
        changes = build_plan([assignment, quiz], [old], TZ).changes
        self.assertEqual({c.key: c.action for c in changes}, {assignment.key: "create", quiz.key: "update"})

    def test_ambiguous_migration_and_duplicates_fail_before_any_writes(self):
        with self.assertRaises(SyncConflict):
            build_plan([item(), item(kind="quiz")], [existing(item(), notes="canvas-id:1-2")], TZ)
        with self.assertRaises(SyncConflict):
            build_plan([item()], [existing(item()), existing(item(), id="duplicate")], TZ)

    def test_missing_items_and_manual_tasks_are_untouched(self):
        plan = build_plan([], [existing(item()), {"id": "manual", "notes": "Leave me alone"}], TZ)
        self.assertEqual(plan.changes, [])
        self.assertEqual(plan.unmatched_managed, 1)
        self.assertEqual(plan.unmanaged, 1)

    def test_malformed_managed_block_does_not_destroy_notes(self):
        with self.assertRaises(SyncConflict):
            build_plan([item()], [existing(item(), notes="canvas-sync-id:" + item().key + "\n[canvas-sync]\nunfinished")], TZ)

    def test_url_repairs_absolute_and_legacy_double_host(self):
        base = "https://canvas.uoregon.edu"
        self.assertEqual(identity_url(base + "https://canvas.instructure.com/courses/1/assignments/2#submit", base),
                         base + "/courses/1/assignments/2")

    def test_completion_is_specific_to_current_student(self):
        self.assertFalse(is_submitted({"has_submitted_submissions": True}))
        self.assertFalse(is_submitted({"workflow_state": "graded", "missing": True, "score": 0}))
        self.assertTrue(is_submitted({"submitted_at": "2026-01-01T00:00:00Z"}))
        self.assertTrue(is_submitted({"excused": True}))


if __name__ == "__main__":
    unittest.main()
