from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, LargeBinary, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship, declarative_base, backref
import datetime

Base = declarative_base()

class Event(Base):
    __tablename__ = 'events'
    id = Column(Integer, primary_key=True, name="id")
    date = Column(DateTime, nullable=False, name="date")

    teams = relationship("Team", back_populates="event")

class Team(Base):
    __tablename__ = 'teams'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, name="name")
    event_id = Column(Integer, ForeignKey('events.id'), name="event_id")
    score = Column(Integer, default=0, name="score")
    thread_id = Column(Integer, nullable=True, name="thread_id")

    event = relationship("Event", back_populates="teams")
    participants = relationship("Participant", back_populates="team")
    submissions = relationship("Submission", back_populates="team")

    __table_args__ = (UniqueConstraint('name', 'event_id', name='_team_event_uc'),)

class Participant(Base):
    __tablename__ = 'participants'
    id = Column(String, primary_key=True, name="id")
    name = Column(String, nullable=False, name="name")
    team_id = Column(Integer, ForeignKey('teams.id'), name="team_id")
    
    team = relationship("Team", back_populates="participants")

class Submission(Base):
    __tablename__ = 'submissions'
    id = Column(Integer, primary_key=True, name="id")
    participant_id = Column(String, ForeignKey('participants.id'), name="participant_id")
    team_id = Column(Integer, ForeignKey('teams.id'), name="team_id")
    
    # Photo path will contain the local folder path to the saved image
    photo_path = Column(String, name="photo_path", nullable=False)

    # Metadata from the photo files
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, name="timestamp")
    latitude = Column(String, name="latitude", nullable=True)
    longitude = Column(String, name="longitude", nullable=True)

    # Result of image recognition (if any)
    litter_details = Column(JSONB, nullable=True) # e.g., {'plastic_bottle': 2, 'Can': 1}
    points_awarded = Column(Integer, name="points_awarded", default=0)
    status = Column(String, default='confirmed', name="status") # e.g., 'pending_confirmation', 'confirmed'

    team = relationship("Team", back_populates="submissions")
    participant = relationship("Participant", back_populates="submissions")

# Add back-reference to Participant for submissions
Participant.submissions = relationship("Submission", order_by=Submission.id, back_populates="participant")
