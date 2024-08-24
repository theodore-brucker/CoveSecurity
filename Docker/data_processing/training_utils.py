# training_utils.py

training_status = {
    "status": "idle",
    "progress": 0,
    "message": ""
}

def update_training_status(status, progress, message):
    global training_status
    training_status["status"] = status
    training_status["progress"] = progress
    training_status["message"] = message

def get_training_status():
    global training_status
    return training_status