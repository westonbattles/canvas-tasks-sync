import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

from canvas import CanvasClient, CanvasError
from models import WorkItem
from sync import render_notes
from test_sync import TZ, existing, item


class Response:
    def __init__(self, body, next_url=None, status=200):
        self.body = body
        self.links = {"next": {"url": next_url}} if next_url else {}
        self.status_code = status
    def json(self):
        return self.body


class CanvasTests(unittest.TestCase):
    def client(self):
        return CanvasClient("https://canvas.uoregon.edu", "test-token", session=Mock())

    def test_pagination_and_auth_headers(self):
        client = self.client()
        client.session.get.side_effect = [Response([1], "https://canvas.uoregon.edu/api/v1/courses?page=2"), Response([2])]
        self.assertEqual(client.pages("/api/v1/courses", {"per_page": 100}), [1, 2])
        self.assertEqual(client.session.get.call_count, 2)
        self.assertIsNone(client.session.get.call_args.kwargs["params"])
        self.assertFalse(client.session.get.call_args.kwargs["allow_redirects"])

    def test_foreign_pagination_never_receives_canvas_token(self):
        client = self.client()
        client.session.get.return_value = Response([1], "https://attacker.invalid/api/v1/courses?page=2")
        with self.assertRaises(ValueError):
            client.pages("/api/v1/courses")
        self.assertEqual(client.session.get.call_count, 1)

    def test_http_error_is_not_an_empty_collection(self):
        client = self.client()
        client.session.get.return_value = Response({}, status=401)
        with self.assertRaises(CanvasError):
            client.pages("/api/v1/courses")

    def test_assignments_merge_with_planner_quizzes_and_overrides(self):
        client = self.client()
        client.courses = Mock(return_value={"1": {"id": 1, "course_code": "TEST"}})
        client.pages = Mock(side_effect=[
            [{"id": 10, "name": "Final quiz", "quiz_id": 22, "due_at": None, "submission": {}}],
            [{"course_id": 1, "plannable_type": "quiz", "plannable_id": 22,
              "plannable": {"id": 22, "title": "Final quiz", "due_at": None},
              "plannable_date": "2026-10-01T00:00:00Z", "html_url": "/courses/1/quizzes/22",
              "planner_override": {"marked_complete": True}}]
        ])
        values = client.collect([])
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0].kind, "assignment")
        self.assertIsNone(values[0].due_at)  # Planner display date is not a made-up deadline.
        self.assertTrue(values[0].completed)
        self.assertEqual(values[0].legacy_ids, {"1-10", "1-22"})
        self.assertEqual(client.pages.call_args_list[0].args[1]["include[]"], "submission")
        self.assertNotIn("filter", client.pages.call_args_list[1].args[1])

    def test_calendar_events_skipped_and_personal_planner_note_kept(self):
        client = self.client()
        client.courses = Mock(return_value={})
        client.pages = Mock(return_value=[
            {"plannable_type": "calendar_event", "plannable": {"id": 1}},
            {"plannable_type": "planner_note", "plannable_id": 2, "plannable": {"id": 2, "title": "Read"},
             "plannable_date": "2026-10-01T00:00:00Z", "html_url": "/api/v1/planner_notes/2"}
        ])
        values = client.collect([])
        self.assertEqual([value.kind for value in values], ["planner_note"])
        self.assertEqual(client.counts["skipped_calendar_event"], 1)

    def test_old_unfinished_assignment_receives_targeted_check(self):
        client = self.client()
        client.courses = Mock(return_value={})
        client.get = Mock(side_effect=[
            {"id": 2, "name": "Essay", "due_at": None, "submission": {"workflow_state": "submitted"}},
            {"id": 1, "course_code": "OLD"}
        ])
        client.pages = Mock(return_value=[])
        values = client.collect([existing(item())])
        self.assertEqual(len(values), 1)
        self.assertTrue(values[0].completed)
        self.assertEqual(client.counts["backlog_assignments"], 1)

    def test_inaccessible_old_assignment_is_left_alone(self):
        client = self.client()
        client.courses = Mock(return_value={})
        client.get = Mock(side_effect=CanvasError(404, "/api/v1/courses/1/assignments/2"))
        client.pages = Mock(return_value=[])
        self.assertEqual(client.collect([existing(item())]), [])

    def test_course_filter_and_explicit_older_course_selection(self):
        client = self.client()
        old = {"id": 1, "term": {"end_at": "2020-01-01T00:00:00Z"}}
        current = {"id": 2, "course_code": "CURRENT"}
        client.pages = Mock(return_value=[old, current])
        self.assertEqual(set(client.courses()), {"2"})
        client.get = Mock(return_value=old)
        self.assertEqual(set(client.courses(["1"])), {"1"})

    def test_old_due_dates_expand_planner_window(self):
        client = self.client()
        client.courses = Mock(return_value={})
        client.pages = Mock(return_value=[])
        note = item(kind="planner_note", due="2025-01-01T00:00:00+00:00")
        client.collect([existing(note)], now=datetime(2026, 9, 25, tzinfo=timezone.utc))
        self.assertLess(client.pages.call_args.args[1]["start_date"], "2025-01-01")


if __name__ == "__main__":
    unittest.main()
