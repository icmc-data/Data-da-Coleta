import os
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import func

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.database import models
from src.database.status import SubmissionStatus


def recalculate_scores_safely():
    """
    Recomputes points_awarded for every confirmed submission (items * 5)
    and then recalculates each team's total score from scratch.
    """
    DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://Data:DataICMC@db:5432/DataDaColeta")
    engine = create_engine(DATABASE_URL)
    db = sessionmaker(autocommit=False, autoflush=False, bind=engine)()

    try:
        submissions = db.query(models.Submission).filter(
            models.Submission.status == SubmissionStatus.CONFIRMED
        ).all()
        for submission in submissions:
            if submission.litter_details:
                submission.points_awarded = sum(submission.litter_details.values()) * 5

        print(f"Updated {len(submissions)} submission scores.")

        teams = db.query(models.Team).all()
        for team in teams:
            new_score = db.query(func.sum(models.Submission.points_awarded)).filter(
                models.Submission.team_id == team.id,
                models.Submission.status == SubmissionStatus.CONFIRMED,
            ).scalar()
            team.score = new_score if new_score is not None else 0
            print(f"  Team '{team.name}': {team.score} points")

        db.commit()
        print("\nScores recalculated successfully.")

    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    recalculate_scores_safely()
