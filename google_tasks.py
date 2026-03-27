import os.path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


SCOPES = ["https://www.googleapis.com/auth/tasks"]

def authenticate():
    creds = None

    # The file token.json stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first time.
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES
            )
            creds = flow.run_local_server(port=0)

        # Save the credentials for the next run
        with open("token.json", "w") as token:
            token.write(creds.to_json())

    return build("tasks", "v1", credentials=creds)


"""
returns TaskList:
{
  "kind": "tasks#taskList",
  "id": "QzR5QXNqcFJYTVRkM05hXw",
  "etag": "\"9fhkEeFhWQM\"",
  "title": "Groceries",
  "updated": "2026-03-26T11:30:41.256Z",
  "selfLink": "https://www.googleapis.com/tasks/v1/users/@me/lists/QzR5QXNqcFJYTVRkM05hXw"
}
"""
def get_tasklist_or_create(service, tasklist_name):
    results = service.tasklists().list().execute()
    items = results.get("items", [])

    for item in items:
        if (tasklist_name == item['title']):
            return item

    # if no tasklist was foundwith the given name, let's make one
    return service.tasklists().insert(body={"title":tasklist_name}).execute()

def to_tasks_date(dt):
    """Convert a datetime to Google Tasks date format (date only, no time)."""
    return dt.strftime("%Y-%m-%dT00:00:00.000Z") if dt else None
    #return dt.isoformat() if dt else None # time gets ignored because of course it does google :DDDDDD

def sync_assignments(service, assignments, tasklist_id):

    tasks = {}

    # Build tasks dictionary (from tasks that already exists in google tasks)
    results = service.tasks().list(tasklist=tasklist_id, maxResults=100, showCompleted=True, showHidden=True).execute()
    task_items = results.get("items", [])
    for task in task_items:
        # snipe the canvas id from the description of our task (all automatically
        # created tasks should have this)
        description = task.get('notes', '')
        parts = description.rpartition('\ncanvas-id:')

        # canvas id was found
        if parts[1]:
            canvas_id = parts[-1]
            tasks[canvas_id] = task

        # canvas id not found... skip the task so we dont mess with it
        else:
            continue
  
    # Loop through assignments
    # if assignment id in tasks:
    #   If Uncompleted
    #       maybe update due date?
    #   If Completed:
    #       mark as completed
    #
    # if assignemtn id not in tasks
    #   if uncompleted:
    #       create task
    #
    # reversed(assignments) because tasks lists are a stack and we want
    # the earliest due dates to appear first


    for assignment in reversed(assignments):

        # skip quizzes for now
        if assignment.get('type','') == 'quiz': continue

        completed = assignment['has_submitted']

        canvas_id_dict_key = f"{assignment['course_id']}-{assignment['assignment_id']}"

        # if the assignment id is a key in tasks
        if canvas_id_dict_key in tasks:
            # patches
            body = {"id": tasks[canvas_id_dict_key]['id']}
            # if canvas assignment is completed and the task doesn't already reflect that, update it
            if completed and tasks[canvas_id_dict_key]['status'] != "completed":
                body['status'] = "completed"
            # if the due dates are misaligned, update the task due date
            elif tasks[canvas_id_dict_key].get('due') != to_tasks_date(assignment['due_at']):
                body['due'] = to_tasks_date(assignment['due_at'])
            else: continue # no update needs to happen so go to the next assignment

            service.tasks().patch(tasklist=tasklist_id, task=tasks[canvas_id_dict_key]['id'], body=body).execute()


        # assignment not already a task, create one if it's not already completed
        elif not completed:
            task = {
                "title": f"({assignment['course_code']}) {assignment['name']}",
                "notes": f"{assignment['url']}\ncanvas-id:{canvas_id_dict_key}"
            }

            if assignment['due_at']:
                task["due"] = to_tasks_date(assignment['due_at'])


            service.tasks().insert(tasklist=tasklist_id, body=task).execute()#
        
