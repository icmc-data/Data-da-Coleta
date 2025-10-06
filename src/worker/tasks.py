import time
from collections import Counter
from pathlib import Path
from ..database import models
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os
import httpx
from .celery_app import celery_app
from ultralytics import YOLO

# --- YOLO Model Configuration ---
MODEL_PATH = Path(__file__).parent / "weights" / "best.pt"

if not MODEL_PATH.exists():
    print(f"CRITICAL: YOLO model not found at {MODEL_PATH}")
    model = None
else:
    print(f"Loading YOLO model from {MODEL_PATH}...")
    model = YOLO(MODEL_PATH)
    print("YOLO model loaded successfully.")

# --- Database and Telegram Configuration ---
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable not set for worker.")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@celery_app.task
def process_submission(submission_id: int):
    """
    A Celery task to process a submission, update the database, and send results back to Telegram.
    """
    if model is None:
        print("YOLO model is not available. Cannot process submission.")
        return

    db = SessionLocal()
    try:
        # 1. Fetch the submission from the database
        submission = db.query(models.Submission).filter(models.Submission.id == submission_id).first()
        if not submission:
            print(f"Submission with ID {submission_id} not found.")
            return

        if submission.status != 'pending_processing':
            print(f"Submission {submission_id} is not pending, status is '{submission.status}'. Skipping.")
            return

        print(f"Processing submission {submission_id} for image: {submission.photo_path}")

        # 2. Perform AI model inference
        results = model(submission.photo_path)
        
        # 3. Process the results
        detected_classes = []
        if results:
            # Save the image with bounding boxes
            # The result object has a save() method. It saves the image in a `runs/detect/predict` folder.
            # To save to a specific location, we can use the filename argument.
            processed_image_path = str(Path(submission.photo_path).with_suffix('')) + "_processed.jpg"
            results[0].save(filename=processed_image_path)

            for result in results:
                if result.boxes:
                    for box in result.boxes:
                        class_id = int(box.cls)
                        class_name = model.names[class_id]
                        detected_classes.append(class_name)

        if not detected_classes:
            litter_type = "none"
            points = 0
            caption = "Nenhum lixo detectado na imagem."
            print("No objects detected.")
        else:
            class_counts = Counter(detected_classes)
            litter_type = class_counts.most_common(1)[0][0]
            points = len(detected_classes) * 10
            
            caption = f"Foram detectados {len(detected_classes)} objetos:\n"
            for class_name, count in class_counts.items():
                caption += f"- {class_name}: {count}\n"
            caption += f"\nTotal de pontos: {points}"
            print(f"Detected {len(detected_classes)} objects. Most common: '{litter_type}'. Awarded {points} points.")

        # 4. Update the submission record
        submission.litter_type = litter_type
        submission.points_awarded = points
        submission.status = 'confirmed'

        # 5. Update the team's score
        team = db.query(models.Team).filter(models.Team.id == submission.team_id).first()
        if team:
            team.score += points
            print(f"Updated team {team.id} score. New score: {team.score}")
        
        db.commit()
        print(f"Successfully processed and saved submission {submission_id}.")

        # 6. Send the processed image back to Telegram
        if team and team.thread_id and TELEGRAM_TOKEN and CHAT_ID:
            try:
                with open(processed_image_path, "rb") as image_file:
                    files = {"photo": image_file}
                    data = {"chat_id": CHAT_ID, "message_thread_id": team.thread_id, "caption": caption}
                    
                    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
                    response = httpx.post(url, data=data, files=files)
                    
                    if response.status_code != 200:
                        print(f"Error sending photo to Telegram: {response.text}")
            except Exception as e:
                print(f"Failed to send photo to Telegram: {e}")

    except Exception as e:
        print(f"An error occurred while processing submission {submission_id}: {e}")
        db.rollback()
    finally:
        db.close()
