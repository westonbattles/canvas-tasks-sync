import unittest
from unittest.mock import Mock
from google_tasks import GoogleTasks
from sync import build_plan, render_notes
from test_sync import TZ, existing, item


class GoogleTests(unittest.TestCase):
    def test_every_task_page_including_completed_is_read(self):
        service = Mock()
        service.tasks.return_value.list.return_value.execute.side_effect = [
            {"items": [{"id": "first"}], "nextPageToken": "second-page"},
            {"items": [{"id": "second"}]},
        ]
        self.assertEqual([t["id"] for t in GoogleTasks(service).tasks("school")], ["first", "second"])
        call = service.tasks.return_value.list.call_args.kwargs
        self.assertTrue(call["showCompleted"])
        self.assertTrue(call["showHidden"])
        self.assertEqual(call["pageToken"], "second-page")

    def test_list_lookup_is_paginated_and_never_creates_a_list(self):
        service = Mock()
        service.tasklists.return_value.list.return_value.execute.side_effect = [
            {"items": [{"id": "other", "title": "Other"}], "nextPageToken": "next"},
            {"items": [{"id": "school", "title": "School"}]},
        ]
        self.assertEqual(GoogleTasks(service).find_list("School")["id"], "school")
        service.tasklists.return_value.insert.assert_not_called()

    def test_duplicate_list_names_fail_instead_of_guessing(self):
        service = Mock()
        service.tasklists.return_value.list.return_value.execute.return_value = {
            "items": [{"id": "a", "title": "School"}, {"id": "b", "title": "School"}]
        }
        with self.assertRaises(ValueError):
            GoogleTasks(service).find_list("School")

    def test_new_tasks_append_without_moving_existing_tasks(self):
        service = Mock()
        service.tasks.return_value.insert.return_value.execute.side_effect = [{"id": "new1"}, {"id": "new2"}]
        previous = [{"id": "first", "status": "needsAction", "position": "001"},
                    {"id": "last", "status": "needsAction", "position": "009"},
                    {"id": "child", "parent": "first", "status": "needsAction", "position": "099"}]
        plan = build_plan([item(), item("3")], previous, TZ)
        GoogleTasks(service).apply("school", plan, previous)
        calls = service.tasks.return_value.insert.call_args_list
        self.assertEqual(calls[0].kwargs["previous"], "last")
        self.assertEqual(calls[1].kwargs["previous"], "new1")
        service.tasks.return_value.move.assert_not_called()
        service.tasks.return_value.delete.assert_not_called()

    def test_lost_insert_response_is_reconciled_without_reposting(self):
        service = Mock()
        service.tasks.return_value.insert.return_value.execute.side_effect = TimeoutError()
        source = item()
        service.tasks.return_value.list.return_value.execute.return_value = {"items": [existing(source)]}
        result = GoogleTasks(service).apply("school", build_plan([source], [], TZ), [])
        self.assertEqual(result["created"], 1)
        self.assertEqual(service.tasks.return_value.insert.call_count, 1)
        self.assertEqual(service.tasks.return_value.insert.return_value.execute.call_args.kwargs["num_retries"], 0)

    def test_unconfirmed_insert_stops_run_and_never_reposts(self):
        service = Mock()
        service.tasks.return_value.insert.return_value.execute.side_effect = TimeoutError()
        service.tasks.return_value.list.return_value.execute.return_value = {"items": []}
        with self.assertRaises(RuntimeError):
            GoogleTasks(service).apply("school", build_plan([item()], [], TZ), [])
        self.assertEqual(service.tasks.return_value.insert.call_count, 1)

    def test_task_existing_on_second_page_is_not_inserted(self):
        service = Mock()
        source = item()
        service.tasks.return_value.list.return_value.execute.side_effect = [
            {"items": [{"id": str(i)} for i in range(100)], "nextPageToken": "next"},
            {"items": [existing(source)]},
        ]
        tasks = GoogleTasks(service).tasks("school")
        self.assertEqual(build_plan([source], tasks, TZ).changes, [])


if __name__ == "__main__":
    unittest.main()
