from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, LargeBinary, ForeignKeyConstraint
from sqlalchemy.orm import relationship, declarative_base, backref
import datetime

Base = declarative_base()

class Event(Base):
    __tablename__ = 'events'
    id = Column(Integer, primary_key=True, name="id_evento")
    date = Column(DateTime, nullable=False, name="data")

    teams = relationship("Team", back_populates="event")

from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, UniqueConstraint, ForeignKeyConstraint

class Team(Base):
    __tablename__ = 'teams'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, name="nome")
    event_id = Column(Integer, ForeignKey('events.id_evento'), nullable=False, name="id_evento")
    score = Column(Integer, default=0, name="pontuacao")
    thread_id = Column(String, nullable=True, name="thread_id")

    __table_args__ = (UniqueConstraint('nome', 'id_evento', name='_team_name_event_uc'),)

    event = relationship("Event", back_populates="teams")
    participants = relationship("Participant", back_populates="team")
    submissions = relationship("Submission", back_populates="team")

class Participant(Base):
    __tablename__ = 'participants'
    id = Column(String, primary_key=True, name="id_participante")
    name = Column(String, nullable=False, name="nome")
    team_id = Column(Integer, ForeignKey('teams.id'), name="time_id")
    
    team = relationship("Team", back_populates="participants")

class Submission(Base):
    __tablename__ = 'submissions'
    id = Column(Integer, primary_key=True, name="id_lixo")
    participant_id = Column(String, ForeignKey('participants.id_participante'), name="id_participante")
    team_id = Column(Integer, ForeignKey('teams.id'), name="time_id")
    
    # Photo path will contain the local folder path to the saved image
    photo_path = Column(String, name="caminho_foto", nullable=False)

    # Metadata from the photo files
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, name="data_envio")
    latitude = Column(String, name="latitude", nullable=True)
    longitude = Column(String, name="longitude", nullable=True)

    # Result of image recognition (if any)
    litter_type = Column(String, name="tipo_lixo", nullable=True) # e.g., 'plastic_bottle'
    points_awarded = Column(Integer, name="pontos", default=0)
    status = Column(String, default='confirmed', name="status") # e.g., 'pending_confirmation', 'confirmed'

    team = relationship("Team", back_populates="submissions")
    participant = relationship("Participant", back_populates="submissions")

# Add back-reference to Participant for submissions
Participant.submissions = relationship("Submission", order_by=Submission.id, back_populates="participant")