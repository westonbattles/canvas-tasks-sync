from datetime import datetime
import canvas
import google_tasks
from googleapiclient.errors import HttpError
from pprint import pprint

# Name for task list to be added into
TASKS_LIST_NAME = "School"


def main():

    try:
        service = google_tasks.authenticate()
        tasklist = google_tasks.get_tasklist_or_create(service, TASKS_LIST_NAME)

        course_ids = canvas.get_course_ids()

        # collect all assignments across all courses
        all_assignments = []
        #for course_id, course_code in course_ids.items():
            #all_assignments.extend(canvas.get_assignments(course_id, course_code))
        all_assignments.extend(canvas.get_planner_items(course_ids))

        # sort by due date (None dates go to the end)
        all_assignments.sort(key=lambda a: a['due_at'] or datetime.max)

        google_tasks.sync_assignments(service, all_assignments, tasklist_id=tasklist['id'])

            
    except HttpError as err:
        print(err)

if __name__ == "__main__":
    main()

