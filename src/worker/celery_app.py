import os
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

# Get the broker URL from environment variables, with a default for local development
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")

# Initialize the Celery app
celery_app = Celery(
    "tasks",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=["src.worker.tasks"],  # Point to the tasks module
)

# Optional Celery configuration
celery_app.conf.update(
    task_track_started=True,
)
