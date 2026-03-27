import os
import requests
import re
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

PST = ZoneInfo("America/Los_Angeles")
from dotenv import load_dotenv

load_dotenv()

AUTH_KEY = os.environ["CANVAS_AUTH_KEY"]
url = "https://canvas.instructure.com"
headers = {'Authorization': f'Bearer {AUTH_KEY}'}



# populate course id dict and return it
def get_course_ids() -> dict:
    endpoint = "/api/v1/courses"

    ids = {}

    params = {"enrollment_state": "active"}
    response = requests.get(url+endpoint, headers=headers, params=params)
    course_json = response.json()
    for course in course_json:
        ids[course['id']] = course['course_code']

    return ids

def get_assignments(course_id, course_code) -> list:
    endpoint = f"/api/v1/courses/{course_id}/assignments"

    assignments = []

    params = {"bucket": "future"}
    response = requests.get(url+endpoint, headers=headers, params=params)
    assignements = response.json()
    for assignment in assignements:
        assignment_dict = {}
        assignment_dict['name'] = assignment['name']
        assignment_dict['course_code'] = course_code
        assignment_dict['id'] = assignment['id']
        assignment_dict['url'] = assignment['html_url'].replace('canvas.instructure.com', 'canvas.uoregon.edu')
        assignment_dict['has_submitted'] = assignment['has_submitted_submissions']

        # parse due date - check if name contains "due M/D" and use that instead
        canvas_due = datetime.fromisoformat(assignment['due_at']).astimezone(PST) if assignment['due_at'] else None
        match = re.search(r'due (\d+)/(\d+)', assignment['name'])
        if match and canvas_due:
            month, day = int(match.group(1)), int(match.group(2))
            try:
                assignment_dict['due_at'] = canvas_due.replace(month=month, day=day)
            except ValueError:
                assignment_dict['due_at'] = canvas_due - timedelta(days=1) # default to one day less i guess
        else:
            assignment_dict['due_at'] = canvas_due

        # add the dict
        assignments.append(assignment_dict)

    return assignments

# TODO - eventually
def get_quizzes(course_id) -> list:
    raise NotImplementedError()
