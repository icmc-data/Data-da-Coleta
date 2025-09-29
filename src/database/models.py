from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, LargeBinary
from sqlalchemy.orm import relationship, declarative_base
import datetime

Base = declarative_base()

class Event(Base):
    __tablename__ = 'events'
    id = Column(Integer, primary_key=True, name="id_evento")
    date = Column(DateTime, nullable=False, name="data")

    teams = relationship("Team", back_populates="event")

class Team(Base):
    __tablename__ = 'teams'
    id = Column(Integer, primary_key=True, name="time_id")
    name = Column(String, nullable=False, unique=True, name="nome")
    score = Column(Integer, default=0, name="pontuacao")

    ## Score provavelmente contará com outras colunas como: numPlasticos, numPapeis, numMetais, etc.
    
    coordinator_id = Column(Integer, ForeignKey('coordinators.id_coordenador'), name="coordenador_id")
    event_id = Column(Integer, ForeignKey('events.id_evento'), name="id_evento")

    coordinator = relationship("Coordinator", back_populates="teams")
    event = relationship("Event", back_populates="teams")
    participants = relationship("Participant", back_populates="team")
    submissions = relationship("Submission", back_populates="team")

class Participant(Base):
    __tablename__ = 'participants'

    # ID should be now the telegram @username or user ID
    id = Column(String, primary_key=True, name="id_participante")
    name = Column(String, nullable=False, name="nome")
    team_id = Column(Integer, ForeignKey('teams.time_id'), name="time_id")
    
    team = relationship("Team", back_populates="participants")

class Submission(Base):
    __tablename__ = 'submissions'
    id = Column(Integer, primary_key=True, name="id_lixo")
    participant_id = Column(Integer, ForeignKey('participants.id_participante'), name="id_participante")
    team_id = Column(Integer, ForeignKey('teams.time_id'), name="time_id")
    
    # todo: change this photo data type if needed, e.g., to store file paths instead of binary data
    photo = Column(LargeBinary, name="foto")

    # Metadata from the photo files
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, name="data_envio")
    latitude = Column(String, name="latitude", nullable=True)
    longitude = Column(String, name="longitude", nullable=True)

    # Result of image recognition (if any)
    status = Column(String, default='confirmed', name="status") # e.g., 'pending_confirmation', 'confirmed'
    litter_type = Column(String, name="tipo_lixo", nullable=True) # e.g., 'plastic_bottle'
    points_awarded = Column(Integer, name="pontos", default=0)

    team = relationship("Team", back_populates="submissions")
    participant = relationship("Participant", backref="submissions")

# Example of engine and session setup for reference
# DATABASE_URL = "postgresql://myuser:mypassword@db/datacoleta"
# engine = create_engine(DATABASE_URL)
# Base.metadata.create_all(bind=engine)