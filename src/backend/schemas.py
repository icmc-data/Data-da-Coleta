from pydantic import BaseModel
from datetime import datetime


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


class ScoreUpdate(BaseModel):
    points: int


class EventBase(BaseModel):
    date: datetime


class EventCreate(EventBase):
    pass


class EventRankingUpdate(BaseModel):
    ranking_thread_id: int


class EventResponse(EventBase):
    id: int
    ranking_thread_id: int | None = None

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
    latitude: float | None = None
    longitude: float | None = None


class SubmissionResponse(SubmissionBase):
    id: int
    participant_id: str
    team_id: int
    litter_details: dict | None = None
    points_awarded: int
    status: str
    photo_path: str

    class Config:
        from_attributes = True
