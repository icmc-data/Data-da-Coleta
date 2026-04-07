import csv
import io
import logging
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from PIL import Image
from pillow_heif import register_heif_opener
from sqlalchemy import create_engine, update as sa_update
from sqlalchemy.orm import Session, sessionmaker

register_heif_opener()

from ..database import models
from ..database.status import SubmissionStatus
from ..worker.tasks import process_submission
from .gps import extract_coordinates
from .schemas import (
    EventCreate,
    EventRankingUpdate,
    EventResponse,
    ParticipantCreate,
    ParticipantResponse,
    ScoreUpdate,
    SubmissionResponse,
    TeamCreate,
    TeamResponse,
    TeamUpdate,
)

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable not set.")

UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", "uploads"))
UPLOADS_DIR.mkdir(exist_ok=True)
PROCESSED_DIR = UPLOADS_DIR / "processed_images"
PROCESSED_DIR.mkdir(exist_ok=True)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Data da Coleta API",
    description="Backend service for the Data da Coleta competition.",
    version="1.0.0",
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- Teams ---

@app.post("/teams/", response_model=TeamResponse, status_code=201)
def create_team(team: TeamCreate, db: Session = Depends(get_db)):
    existing = db.query(models.Team).filter(
        models.Team.name == team.name,
        models.Team.event_id == team.event_id,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Team '{team.name}' already exists in this event.")
    new_team = models.Team(name=team.name, event_id=team.event_id)
    db.add(new_team)
    db.commit()
    db.refresh(new_team)
    return new_team


@app.get("/teams/", response_model=list[TeamResponse])
def get_teams(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(models.Team).offset(skip).limit(limit).all()


@app.get("/ranking/", response_model=list[TeamResponse])
def get_ranking(db: Session = Depends(get_db)):
    return db.query(models.Team).order_by(models.Team.score.desc()).all()


@app.patch("/teams/{team_id}", response_model=TeamResponse)
def update_team_thread_id(team_id: int, team_update: TeamUpdate, db: Session = Depends(get_db)):
    db_team = db.query(models.Team).filter(models.Team.id == team_id).first()
    if not db_team:
        raise HTTPException(status_code=404, detail="Team not found")
    db_team.thread_id = team_update.thread_id
    db.commit()
    db.refresh(db_team)
    return db_team


@app.get("/teams/{team_id}", response_model=TeamResponse)
def get_team(team_id: int, db: Session = Depends(get_db)):
    team = db.query(models.Team).filter(models.Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


@app.patch("/teams/{team_id}/score", response_model=TeamResponse)
def update_team_score(team_id: int, score_update: ScoreUpdate, db: Session = Depends(get_db)):
    db_team = db.query(models.Team).filter(models.Team.id == team_id).first()
    if not db_team:
        raise HTTPException(status_code=404, detail="Team not found")
    db_team.score = max(0, db_team.score - score_update.points)
    db.commit()
    db.refresh(db_team)
    return db_team


@app.get("/teams/by_thread/{thread_id}", response_model=TeamResponse)
def get_team_by_thread_id(thread_id: int, db: Session = Depends(get_db)):
    team = db.query(models.Team).filter(models.Team.thread_id == thread_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found for this thread.")
    return team


# --- Events ---

@app.post("/events/", response_model=EventResponse, status_code=201)
def create_event(event: EventCreate, db: Session = Depends(get_db)):
    new_event = models.Event(date=event.date)
    db.add(new_event)
    db.commit()
    db.refresh(new_event)
    return new_event


@app.get("/events/", response_model=list[EventResponse])
def get_events(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(models.Event).order_by(models.Event.date.desc()).offset(skip).limit(limit).all()


@app.get("/events/{event_id}", response_model=EventResponse)
def get_event(event_id: int, db: Session = Depends(get_db)):
    event = db.query(models.Event).filter(models.Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@app.patch("/events/{event_id}/ranking_thread", response_model=EventResponse)
def update_event_ranking_thread(event_id: int, body: EventRankingUpdate, db: Session = Depends(get_db)):
    event = db.query(models.Event).filter(models.Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    event.ranking_thread_id = body.ranking_thread_id
    db.commit()
    db.refresh(event)
    return event


# --- Participants ---

@app.post("/participants/", response_model=ParticipantResponse, status_code=201)
def create_participant(participant: ParticipantCreate, db: Session = Depends(get_db)):
    db_team = db.query(models.Team).filter(models.Team.id == participant.team_id).first()
    if not db_team:
        raise HTTPException(status_code=404, detail=f"Team with ID '{participant.team_id}' not found.")

    db_participant = db.query(models.Participant).filter(models.Participant.id == participant.id).first()
    if db_participant:
        db_participant.team_id = participant.team_id
        db.commit()
        db.refresh(db_participant)
        return db_participant

    new_participant = models.Participant(id=participant.id, name=participant.name, team_id=participant.team_id)
    db.add(new_participant)
    db.commit()
    db.refresh(new_participant)
    return new_participant


@app.get("/participants/", response_model=list[ParticipantResponse])
def get_participants(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(models.Participant).offset(skip).limit(limit).all()


@app.get("/participants/{participant_id}", response_model=ParticipantResponse)
def get_participant(participant_id: str, db: Session = Depends(get_db)):
    participant = db.query(models.Participant).filter(models.Participant.id == participant_id).first()
    if not participant:
        raise HTTPException(status_code=404, detail="Participant not found")
    return participant


# --- Submissions ---

@app.post("/submissions/", response_model=SubmissionResponse, status_code=201)
async def create_submission(
    db: Session = Depends(get_db),
    photo: UploadFile = File(...),
    participant_id: str = Form(...),
    thread_id: int = Form(...),
):
    participant = db.query(models.Participant).filter(models.Participant.id == participant_id).first()
    if not participant:
        raise HTTPException(status_code=404, detail=f"Participant with ID '{participant_id}' not found.")

    team = db.query(models.Team).filter(models.Team.thread_id == thread_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="No team is associated with this chat thread.")

    if participant.team_id != team.id:
        raise HTTPException(status_code=403, detail="You are not registered in this team. Please submit to your own team's chat.")

    file_extension = Path(photo.filename).suffix.lower()
    unique_base = uuid.uuid4().hex
    image_stream = io.BytesIO(await photo.read())
    image = Image.open(image_stream)

    latitude, longitude = extract_coordinates(image)
    if latitude is None or longitude is None:
        logger.info(f"[GPS] Missing coords for '{photo.filename}'. Remind users to send as Document.")

    if file_extension == ".heic":
        output_filename = f"{unique_base}.jpg"
        file_path = UPLOADS_DIR / output_filename
        try:
            exif_bytes = image.info.get("exif")
            if exif_bytes:
                image.save(file_path, "JPEG", exif=exif_bytes)
            else:
                image.save(file_path, "JPEG")
        except Exception as e:
            logger.error(f"Failed to convert HEIC to JPEG: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to convert HEIC image.")
    else:
        output_filename = f"{unique_base}{file_extension}"
        file_path = UPLOADS_DIR / output_filename
        try:
            image_stream.seek(0)
            with file_path.open("wb") as buffer:
                shutil.copyfileobj(image_stream, buffer)
        except IOError as e:
            logger.error(f"IOError saving uploaded file: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to save the uploaded photo.")

    new_submission = models.Submission(
        participant_id=participant_id,
        team_id=team.id,
        photo_path=str(file_path),
        timestamp=datetime.now(timezone.utc),
        latitude=latitude,
        longitude=longitude,
        status=SubmissionStatus.PENDING,
    )
    db.add(new_submission)
    db.commit()
    db.refresh(new_submission)

    process_submission.delay(new_submission.id)
    return new_submission


# --- Export ---

@app.get("/export/{format}")
def export_data(format: str, db: Session = Depends(get_db)):
    if format not in ["csv", "json"]:
        raise HTTPException(status_code=400, detail="Invalid format. Use 'csv' or 'json'.")

    submissions = db.query(models.Submission).all()

    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "submission_id", "participant_id", "team_id", "team_name",
            "timestamp", "latitude", "longitude", "litter_details",
            "points_awarded", "status",
        ])
        for sub in submissions:
            writer.writerow([
                sub.id, sub.participant_id, sub.team_id, sub.team.name,
                sub.timestamp, sub.latitude, sub.longitude, sub.litter_details,
                sub.points_awarded, sub.status,
            ])
        output.seek(0)
        return StreamingResponse(
            output,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=submissions.csv"},
        )

    return [
        {
            "submission_id": sub.id,
            "participant_id": sub.participant_id,
            "team_id": sub.team_id,
            "team_name": sub.team.name,
            "timestamp": sub.timestamp,
            "latitude": sub.latitude,
            "longitude": sub.longitude,
            "litter_details": sub.litter_details,
            "points_awarded": sub.points_awarded,
            "status": sub.status,
        }
        for sub in submissions
    ]


@app.get("/")
def read_root():
    return {"message": "Welcome to the Data da Coleta API!"}
