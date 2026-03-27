import os
import requests
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


PST = ZoneInfo("America/Los_Angeles")
from dotenv import load_dotenv

load_dotenv()

AUTH_KEY = os.environ["CANVAS_AUTH_KEY"]
url = "https://canvas.uoregon.edu"
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
        assignment_dict['assignment_id'] = assignment['id']
        assignment_dict['course_id'] = course_id
        assignment_dict['course_code'] = course_code
        assignment_dict['url'] = assignment['html_url'].replace('canvas.instructure.com', 'canvas.uoregon.edu')
        assignment_dict['has_submitted'] = assignment['has_submitted_submissions']

        assignment_dict['due_at'] = get_canvas_due_date(assignment['due_at'], assignment['name'])

        # add the dict
        assignments.append(assignment_dict)

    return assignments

def get_planner_items(course_ids) -> list:
    endpoint = "/api/v1/planner/items"

    planner_items = []

    params = {
        "start_date": datetime.now(PST).strftime("%Y-%m-%d"),
        "per_page": 100,
    }

    response = requests.get(url + endpoint, headers=headers, params=params)
    items = response.json()

    for planner_item in items:

        # don't know what would cause this lowk
        if planner_item.get('plannable') is None: continue
        
        planner_item_dict = {}
        planner_item_dict['name'] = planner_item['plannable']['title']
        planner_item_dict['assignment_id'] = planner_item['plannable']['id']
        planner_item_dict['course_id'] = planner_item.get('course_id', '')
        course_code = course_ids.get(planner_item_dict['course_id'], "")
        planner_item_dict['course_code'] = course_code
        planner_item_dict['type'] = planner_item.get('plannable_type', '')
        planner_item_dict['url'] = url + planner_item['html_url']

        submissions = planner_item.get('submissions') or {}
        planner_item_dict['has_submitted'] = submissions.get('submitted', None)
        
        due_at_str = planner_item['plannable'].get('due_at') or planner_item.get('plannable_date')
        planner_item_dict['due_at'] = get_canvas_due_date(due_at_str, planner_item_dict['name'])

        # add the dict
        planner_items.append(planner_item_dict)
    return planner_items

# TODO - eventually
def get_quizzes(course_id) -> list:
    raise NotImplementedError()

# HELPERS

# check if name contains "due M/D" and use that instead of canvas due date
def get_canvas_due_date(due_at_str, name) -> datetime | None:
    canvas_due = datetime.fromisoformat(due_at_str).astimezone(PST) if due_at_str else None
    match = re.search(r'due (\d+)/(\d+)', name)
    if match and canvas_due:
        month, day = int(match.group(1)), int(match.group(2))
        try:
            return canvas_due.replace(month=month, day=day)
        except ValueError:
            return canvas_due - timedelta(days=1) # default to one day less i guess
    return canvas_due
