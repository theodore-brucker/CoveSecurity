import uuid
from threading import Lock

# Global dictionaries to store job statuses
training_status = {
    "status": "idle",
    "progress": 0,
    "message": ""
}

job_statuses = {}

# Locks for thread-safe operations
training_status_lock = Lock()
job_statuses_lock = Lock()

def update_training_status(status, progress, message):
    global training_status
    with training_status_lock:
        training_status["status"] = status
        training_status["progress"] = progress
        training_status["message"] = message

def get_training_status():
    global training_status
    with training_status_lock:
        return training_status.copy()

def create_job():
    job_id = str(uuid.uuid4())
    with job_statuses_lock:
        job_statuses[job_id] = {
            "status": "created",
            "progress": 0,
            "message": "Job created"
        }
    return job_id

def update_job_status(job_id, status, progress, message):
    with job_statuses_lock:
        if job_id in job_statuses:
            job_statuses[job_id] = {
                "status": status,
                "progress": progress,
                "message": message
            }

def get_job_status(job_id):
    with job_statuses_lock:
        return job_statuses.get(job_id, {
            "status": "not_found",
            "progress": 0,
            "message": "Job not found"
        }).copy()

def remove_job_status(job_id):
    with job_statuses_lock:
        job_statuses.pop(job_id, None)