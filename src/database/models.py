from sqlalchemy import Column, Integer, String, DateTime, Float, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship, declarative_base
import datetime

Base = declarative_base()


class Event(Base):
    __tablename__ = 'events'
    id = Column(Integer, primary_key=True)
    date = Column(DateTime, nullable=False)
    ranking_thread_id = Column(Integer, nullable=True)

    teams = relationship("Team", back_populates="event")


class Team(Base):
    __tablename__ = 'teams'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    event_id = Column(Integer, ForeignKey('events.id'))
    score = Column(Integer, default=0)
    thread_id = Column(Integer, nullable=True)

    event = relationship("Event", back_populates="teams")
    participants = relationship("Participant", back_populates="team")
    submissions = relationship("Submission", back_populates="team")

    __table_args__ = (UniqueConstraint('name', 'event_id', name='_team_event_uc'),)


class Participant(Base):
    __tablename__ = 'participants'
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    team_id = Column(Integer, ForeignKey('teams.id'))

    team = relationship("Team", back_populates="participants")


class Submission(Base):
    __tablename__ = 'submissions'
    id = Column(Integer, primary_key=True)
    participant_id = Column(String, ForeignKey('participants.id'))
    team_id = Column(Integer, ForeignKey('teams.id'))
    photo_path = Column(String, nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    litter_details = Column(JSONB, nullable=True)
    points_awarded = Column(Integer, default=0)
    status = Column(String, default='confirmed')

    team = relationship("Team", back_populates="submissions")
    participant = relationship("Participant", back_populates="submissions")


# Back-reference added after Submission is defined to avoid forward-reference issues
Participant.submissions = relationship("Submission", order_by=Submission.id, back_populates="participant")
