import logging
import os
import time
from collections import Counter
from pathlib import Path

import httpx
import pillow_heif
from sqlalchemy import create_engine, update as sa_update
from sqlalchemy.orm import sessionmaker
from ultralytics import YOLO

from ..database import models
from ..database.status import SubmissionStatus
from .celery_app import celery_app

pillow_heif.register_heif_opener()

logger = logging.getLogger(__name__)

# --- File paths ---
UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", "uploads"))
PROCESSED_DIR = UPLOADS_DIR / "processed_images"
PROCESSED_DIR.mkdir(exist_ok=True)

# --- YOLO model ---
MODEL_PATH = Path(__file__).parent / "weights" / "YOLOv8_s" / "best.pt"

if not MODEL_PATH.exists():
    logger.critical(f"YOLO model not found at {MODEL_PATH}. Worker will mark tasks as failed.")
    model = None
else:
    logger.info(f"Loading YOLO model from {MODEL_PATH}...")
    model = YOLO(MODEL_PATH)
    logger.info("YOLO model loaded successfully.")

# --- Database ---
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable not set for worker.")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# --- Telegram ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
MAX_TELEGRAM_RETRIES = 5


def _send_telegram_photo(url: str, data: dict, files: dict) -> httpx.Response | None:
    """POST a photo to the Telegram Bot API with rate-limit handling and a retry cap."""
    for attempt in range(1, MAX_TELEGRAM_RETRIES + 1):
        try:
            with httpx.Client() as client:
                response = client.post(url, data=data, files=files)

            if response.status_code == 200:
                return response

            if response.status_code == 429:
                retry_after = response.json().get("parameters", {}).get("retry_after", 2)
                logger.warning(
                    f"Telegram rate limit hit. Retrying in {retry_after}s "
                    f"(attempt {attempt}/{MAX_TELEGRAM_RETRIES})."
                )
                time.sleep(retry_after)
            else:
                logger.error(f"Telegram returned {response.status_code}: {response.text}")
                return response

        except httpx.RequestError as e:
            logger.error(f"Network error sending photo (attempt {attempt}/{MAX_TELEGRAM_RETRIES}): {e}")
            time.sleep(5)

    logger.error(f"Giving up sending photo after {MAX_TELEGRAM_RETRIES} attempts.")
    return None


@celery_app.task
def process_submission(submission_id: int):
    """Celery task: run YOLO inference on a submission and update scores."""
    db = SessionLocal()
    try:
        submission = db.query(models.Submission).filter(models.Submission.id == submission_id).first()
        if not submission:
            logger.error(f"Submission {submission_id} not found in database.")
            return

        if submission.status != SubmissionStatus.PENDING:
            logger.warning(
                f"Submission {submission_id} has status '{submission.status}' (expected "
                f"'{SubmissionStatus.PENDING}'). Skipping."
            )
            return

        if model is None:
            logger.critical(f"YOLO model unavailable. Marking submission {submission_id} as failed.")
            submission.status = SubmissionStatus.FAILED
            db.commit()
            return

        logger.info(f"Processing submission {submission_id}: {submission.photo_path}")
        image_path = Path(submission.photo_path)
        results = model(image_path)

        detected_classes = []
        processed_image_path = PROCESSED_DIR / (image_path.stem + "_processed.jpg")

        if results:
            results[0].save(filename=processed_image_path)
            for result in results:
                if result.boxes:
                    for box in result.boxes:
                        detected_classes.append(model.names[int(box.cls)])

        if not detected_classes:
            litter_details = {}
            points = 0
            caption = "Nenhum lixo detectado na imagem."
            logger.info(f"Submission {submission_id}: no objects detected.")
        else:
            class_counts = Counter(detected_classes)
            litter_details = dict(class_counts)
            points = len(detected_classes) * 5
            caption = f"Foram detectados {len(detected_classes)} objetos:\n"
            for class_name, count in class_counts.items():
                caption += f"- {class_name}: {count}\n"
            caption += f"\nTotal de pontos: {points}"
            logger.info(f"Submission {submission_id}: {len(detected_classes)} objects detected, {points} points awarded.")

        submission.litter_details = litter_details
        submission.points_awarded = points
        submission.status = SubmissionStatus.CONFIRMED

        # Atomic increment avoids race conditions with concurrent submissions
        db.execute(
            sa_update(models.Team)
            .where(models.Team.id == submission.team_id)
            .values(score=models.Team.score + points)
        )
        db.commit()
        logger.info(f"Submission {submission_id} committed successfully.")

        # Send annotated image back to the team's Telegram topic
        team = db.query(models.Team).filter(models.Team.id == submission.team_id).first()
        if team and team.thread_id and TELEGRAM_TOKEN and CHAT_ID:
            try:
                with open(processed_image_path, "rb") as image_file:
                    _send_telegram_photo(
                        url=f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                        data={"chat_id": CHAT_ID, "message_thread_id": team.thread_id, "caption": caption},
                        files={"photo": image_file},
                    )
            except Exception as e:
                logger.error(f"Failed to send Telegram photo for submission {submission_id}: {e}")

    except Exception as e:
        logger.error(f"Unexpected error processing submission {submission_id}: {e}", exc_info=True)
        db.rollback()
        # Best-effort: mark as failed so it doesn't stay stuck in pending
        try:
            submission = db.query(models.Submission).filter(models.Submission.id == submission_id).first()
            if submission and submission.status == SubmissionStatus.PENDING:
                submission.status = SubmissionStatus.FAILED
                db.commit()
        except Exception:
            logger.error(f"Failed to mark submission {submission_id} as failed after error.", exc_info=True)
    finally:
        db.close()
