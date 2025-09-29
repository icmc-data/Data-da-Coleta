from fastapi import FastAPI, HTTPException, Depends
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from pydantic import BaseModel
import os

# Import all models from the database module
from ..database import models

# --- Database Configuration ---
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://myuser:mypassword@db/datacoleta")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create the table in the database (FastAPI can do this on startup)
# This command creates all tables that inherit from models.Base
models.Base.metadata.create_all(bind=engine)

# --- Pydantic Models (for request/response validation) ---
# Schemas for Team
class TeamBase(BaseModel):
    name: str

class TeamCreate(TeamBase):
    # When creating a team, we might not have a coordinator yet
    # or we might pass the coordinator's ID. Let's make it optional for now.
    coordinator_id: int | None = None

class TeamResponse(TeamBase):
    id: int
    score: int
    coordinator_id: int | None = None

    class Config:
        orm_mode = True # Allows Pydantic to read data from ORM models

# Schemas for Coordinator (as an example)
class CoordinatorBase(BaseModel):
    pass

# --- FastAPI Application ---
app = FastAPI(
    title="Data da Coleta API",
    description="Backend service for the Data da Coleta competition.",
    version="1.0.0"
)

# --- Dependency for getting a DB session ---
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- API Endpoints for Teams ---
@app.post("/teams/", response_model=TeamResponse, status_code=201)
def create_team(team: TeamCreate, db: Session = Depends(get_db)):
    """
    Creates a new team in the database.
    - **RF01**: Allows for the creation of teams.
    """
    db_team = db.query(models.Team).filter(models.Team.name == team.name).first()
    if db_team:
        raise HTTPException(status_code=400, detail="Team with this name already exists")
    
    new_team = models.Team(
        name=team.name,
        coordinator_id=team.coordinator_id
    )
    db.add(new_team)
    db.commit()
    db.refresh(new_team)
    return new_team

@app.get("/teams/", response_model=list[TeamResponse])
def get_teams(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """
    Retrieves a list of all teams.
    """
    teams = db.query(models.Team).offset(skip).limit(limit).all()
    return teams

@app.get("/ranking/", response_model=list[TeamResponse])
def get_ranking(db: Session = Depends(get_db)):
    """
    Retrieves a list of all teams, ordered by score in descending order.
    - **RF08**: Exibir ranking atual através de comando.
    """
    teams = db.query(models.Team).order_by(models.Team.score.desc()).all()
    return teams

@app.get("/")
def read_root():
    return {"message": "Welcome to the Data da Coleta API!"}