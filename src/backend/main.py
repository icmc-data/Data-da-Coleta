from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from pydantic import BaseModel
from datetime import datetime
import os
import uuid
import shutil
from pathlib import Path
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS

from ..database import models
from ..worker.tasks import process_submission

# --- Database Configuration ---
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable not set.")

# --- File Storage Configuration ---
UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", "uploads"))
UPLOADS_DIR.mkdir(exist_ok=True)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

models.Base.metadata.create_all(bind=engine)

# --- GPS Extraction Helpers ---
def get_gps_info(image):
    exif_data = image._getexif()
    if not exif_data:
        return None

    gps_info = {}
    for key, val in exif_data.items():
        tag = TAGS.get(key)
        if tag == "GPSInfo":
            for t in val:
                sub_tag = GPSTAGS.get(t)
                gps_info[sub_tag] = val[t]
            return gps_info
    return None

def dms_to_dd(dms, ref):
    degrees = dms[0]
    minutes = dms[1] / 60.0
    seconds = dms[2] / 3600.0
    dd = degrees + minutes + seconds
    if ref in ['S', 'W']:
        dd *= -1
    return dd

# --- Pydantic Models ---
class TeamBase(BaseModel):
    name: str

class TeamCreate(TeamBase):
    event_id: int

class TeamResponse(TeamBase):
    id: int
    score: int
    event_id: int
    thread_id: int | None = None
    class Config:
        from_attributes = True

class TeamUpdate(BaseModel):
    thread_id: int

class EventBase(BaseModel):
    date: datetime

class EventCreate(EventBase):
    pass

class EventResponse(EventBase):
    id: int
    class Config:
        from_attributes = True

class ParticipantBase(BaseModel):
    id: str
    name: str

class ParticipantCreate(ParticipantBase):
    team_id: int

class ParticipantResponse(ParticipantBase):
    team_id: int
    class Config:
        from_attributes = True

class SubmissionBase(BaseModel):
    timestamp: datetime | None = None
    latitude: str | None = None
    longitude: str | None = None

class SubmissionResponse(SubmissionBase):
    id: int
    participant_id: str
    team_id: int
    litter_type: str | None = None
    points_awarded: int
    status: str
    photo_path: str
    class Config:
        from_attributes = True

# --- FastAPI Application ---
app = FastAPI(
    title="Data da Coleta API",
    description="Backend service for the Data da Coleta competition.",
    version="1.0.0"
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- API Endpoints for Teams ---
@app.post("/teams/", response_model=TeamResponse, status_code=201)
def create_team(team: TeamCreate, db: Session = Depends(get_db)):
    db_team = db.query(models.Team).filter(
        models.Team.name == team.name,
        models.Team.event_id == team.event_id
    ).first()
    if db_team:
        raise HTTPException(status_code=400, detail=f"Team '{team.name}' already exists in this event.")
    
    new_team = models.Team(name=team.name, event_id=team.event_id)
    db.add(new_team)
    db.commit()
    db.refresh(new_team)
    return new_team

@app.get("/teams/", response_model=list[TeamResponse])
def get_teams(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    teams = db.query(models.Team).offset(skip).limit(limit).all()
    return teams

@app.get("/ranking/", response_model=list[TeamResponse])
def get_ranking(db: Session = Depends(get_db)):
    teams = db.query(models.Team).order_by(models.Team.score.desc()).all()
    return teams

@app.patch("/teams/{team_id}", response_model=TeamResponse)
def update_team_thread_id(team_id: int, team_update: TeamUpdate, db: Session = Depends(get_db)):
    db_team = db.query(models.Team).filter(models.Team.id == team_id).first()
    if not db_team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    db_team.thread_id = team_update.thread_id
    db.commit()
    db.refresh(db_team)
    return db_team

# --- API Endpoints for Events ---
@app.post("/events/", response_model=EventResponse, status_code=201)
def create_event(event: EventCreate, db: Session = Depends(get_db)):
    new_event = models.Event(date=event.date)
    db.add(new_event)
    db.commit()
    db.refresh(new_event)
    return new_event

@app.get("/events/", response_model=list[EventResponse])
def get_events(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    events = db.query(models.Event).order_by(models.Event.date.desc()).offset(skip).limit(limit).all()
    return events

@app.get("/events/{event_id}", response_model=EventResponse)
def get_event(event_id: int, db: Session = Depends(get_db)):
    event = db.query(models.Event).filter(models.Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event

# --- API Endpoints for Participants ---
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
    participants = db.query(models.Participant).offset(skip).limit(limit).all()
    return participants

@app.get("/participants/{participant_id}", response_model=ParticipantResponse)
def get_participant(participant_id: str, db: Session = Depends(get_db)):
    participant = db.query(models.Participant).filter(models.Participant.id == participant_id).first()
    if not participant:
        raise HTTPException(status_code=404, detail="Participant not found")
    return participant

# --- API Endpoints for Submissions ---
@app.post("/submissions/", response_model=SubmissionResponse, status_code=201)
def create_submission(
    db: Session = Depends(get_db),
    photo: UploadFile = File(...),
    participant_id: str = Form(...)
):
    """
    Creates a new submission, extracting GPS data from the image EXIF.
    """
    participant = db.query(models.Participant).filter(models.Participant.id == participant_id).first()
    if not participant:
        raise HTTPException(status_code=404, detail=f"Participant with ID '{participant_id}' not found.")
    
    team_id = participant.team_id

    latitude, longitude = None, None
    try:
        image = Image.open(photo.file)
        gps_info = get_gps_info(image)
        if gps_info:
            lat_dms = gps_info.get("GPSLatitude")
            lat_ref = gps_info.get("GPSLatitudeRef")
            lon_dms = gps_info.get("GPSLongitude")
            lon_ref = gps_info.get("GPSLongitudeRef")
            if lat_dms and lat_ref and lon_dms and lon_ref:
                latitude = dms_to_dd(lat_dms, lat_ref)
                longitude = dms_to_dd(lon_dms, lon_ref)
    except Exception as e:
        print(f"Could not extract GPS info: {e}")
    finally:
        photo.file.seek(0)

    file_extension = Path(photo.filename).suffix
    unique_filename = f"{uuid.uuid4().hex}{file_extension}"
    file_path = UPLOADS_DIR / unique_filename

    try:
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(photo.file, buffer)
    except IOError as e:
        logging.error(f"IOError when saving file: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to save the uploaded photo.")
    finally:
        photo.file.close()

    new_submission = models.Submission(
        participant_id=participant_id,
        team_id=team_id,
        photo_path=str(file_path),
        timestamp=datetime.utcnow(),
        latitude=str(latitude) if latitude else None,
        longitude=str(longitude) if longitude else None,
        status='pending_processing'
    )
    db.add(new_submission)
    db.commit()
    db.refresh(new_submission)

    process_submission.delay(new_submission.id)

    return new_submission

@app.get("/")
def read_root():
    return {"message": "Welcome to the Data da Coleta API!"}
