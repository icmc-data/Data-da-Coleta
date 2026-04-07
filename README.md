# Data da Coleta — Trash Challenge

A gamified litter-collection competition platform built for university events. Teams compete to collect the most trash, earn points through AI-powered photo recognition, and track their ranking in real time via a Telegram bot.

---

## How It Works

Participants follow five steps during the event:

1. **Enable location** on their phone camera so GPS coordinates are embedded in photos.
2. **Pick up trash** and photograph it using the native camera app — standing up and framing all items clearly.
3. **Open their team's Telegram topic** and send the photo as a **File** (not as a Photo), to preserve EXIF/GPS metadata.
4. The bot submits the image to the backend, which queues a YOLO inference task.
5. Within seconds, the processed image is sent back to the topic with detected items, points awarded, and the team score is updated.

> **Why File and not Photo?** Telegram compresses photos and strips EXIF data. Sending as a File preserves GPS coordinates used for verification.

---

## Architecture

```
Telegram User
      │  (sends image as File)
      ▼
src/bot/telegramBot.py      ← python-telegram-bot (async polling)
      │  POST /submissions/
      ▼
src/backend/main.py         ← FastAPI REST API
      │  Celery task via Redis
      ▼
src/worker/tasks.py         ← Celery worker (YOLO inference, CPU)
      │
      ├─► PostgreSQL         ← persists submissions, scores, teams
      └─► Telegram Bot API   ← sends annotated image back to topic
```

### Services (Docker Compose)

| Service   | Technology          | Port  | Role                              |
|-----------|---------------------|-------|-----------------------------------|
| `backend` | FastAPI + uvicorn   | 8000  | REST API, image upload, export    |
| `worker`  | Celery + YOLO v8s   | —     | Async inference, score updates    |
| `bot`     | python-telegram-bot | —     | User interaction, ranking display |
| `db`      | PostgreSQL 15       | 5433  | Data persistence                  |
| `redis`   | Redis 7             | 6379  | Celery broker & result backend    |
| `pgadmin` | pgAdmin 4           | 5050  | Database management UI            |

### Scoring

Each trash item detected by the YOLO model awards **5 points** to the team. The score is updated atomically in the database to handle concurrent submissions safely.

---

## Prerequisites

- Docker and Docker Compose
- A Telegram **Bot Token** — create one via [@BotFather](https://t.me/botfather)
- A Telegram **Supergroup** with Forum Topics enabled, where the bot is an admin
- The `CHAT_ID` of that group (negative number, e.g. `-1001234567890`)

---

## Setup

### 1. Create the `.env` file

```env
# Telegram
TELEGRAM_TOKEN=your_bot_token_here
CHAT_ID=-100your_group_chat_id
GROUP_LINK=https://t.me/your_group_username

# Database
DATABASE_URL=postgresql://Data:DataICMC@db:5432/DataDaColeta
POSTGRES_USER=Data
POSTGRES_PASSWORD=DataICMC
POSTGRES_DB=DataDaColeta

# Celery / Redis
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0

# Backend (used by the bot to call the API)
BACKEND_URL=http://backend:8000

# Image storage (local path inside the container)
UPLOADS_DIR=uploads
PATH_IMAGES=./images
```

### 2. Build and start all services

```bash
docker compose up --build -d
```

### 3. Apply database migrations

This must be run once after the first build, and again after any schema change:

```bash
docker compose run --rm backend alembic upgrade head
```

### 4. Create the first event

Before any teams or submissions can be created, an admin must create an event. Use the `/create_event` command in the Telegram group, or call the API directly:

```bash
curl -X POST http://localhost:8000/events/ \
  -H "Content-Type: application/json" \
  -d '{"date": "2025-10-01T10:00:00"}'
```

### 5. Create teams

In a private chat with the bot, use `/create_topic`. The bot will ask for a team name, then:
- Create the team in the database
- Create a Telegram Forum Topic for that team
- Link the two together

---

## Telegram Bot Reference

### User Commands (private chat with bot)

| Command / Action | Description |
|---|---|
| `/start` | Shows the list of teams to join |
| Tap a team button | Registers you in that team and gives you the topic link |
| Send photo as **File** in your team's topic | Submits the image for scoring |

### Admin Commands (inside the group)

| Command | Description |
|---|---|
| `/create_event` | Creates a new competition event (supergroup only) |
| `/create_topic` | (Private chat) Creates a new team + Telegram topic |
| `/remove_points <n>` | Subtracts `n` points from the team of the current topic |
| `/export` | Exports all submission data as CSV or JSON |

### Ranking

A **Ranking** topic is created automatically when the bot starts. It updates every minute and non-admin messages are deleted from it to keep it clean. The ranking topic ID is persisted in the database — if the bot restarts, it picks up where it left off.

---

## API Reference

Base URL: `http://localhost:8000` — Interactive docs at `/docs`.

### Key Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/submissions/` | Upload image + metadata (multipart form) |
| `GET`  | `/ranking/` | Teams ordered by score descending |
| `GET`  | `/teams/` | List all teams |
| `POST` | `/teams/` | Create a team |
| `PATCH`| `/teams/{id}/score` | Subtract points from a team (floor: 0) |
| `POST` | `/events/` | Create a new event |
| `GET`  | `/export/{format}` | Export all submissions (`csv` or `json`) |

#### Submit an image

```bash
curl -X POST http://localhost:8000/submissions/ \
  -F "photo=@/path/to/image.jpg" \
  -F "participant_id=123456789" \
  -F "thread_id=42"
```

#### Get the leaderboard

```bash
curl http://localhost:8000/ranking/
```

---

## Database Migrations

Schema changes are managed via Alembic. The config reads `DATABASE_URL` from the environment.

```bash
# Apply all pending migrations
docker compose run --rm backend alembic upgrade head

# Auto-generate a migration after changing models.py
docker compose run --rm backend alembic revision --autogenerate -m "describe the change"

# Check current revision
docker compose run --rm backend alembic current

# Roll back one migration
docker compose run --rm backend alembic downgrade -1
```

---

## Useful Operations

### Tail logs for a specific service

```bash
docker compose logs -f worker   # YOLO inference output
docker compose logs -f bot      # Telegram handler logs
docker compose logs -f backend  # API request logs
```

### Recalculate all scores from scratch

If scores get out of sync, this script recomputes `points_awarded` for every confirmed submission and rebuilds all team totals:

```bash
docker compose run --rm backend python scripts/recalculate_scores_safely.py
```

### Inspect the Celery task queue

```bash
docker compose exec redis redis-cli
> LLEN celery   # number of pending tasks
> KEYS *        # all keys
```

### Access pgAdmin

Open `http://localhost:5050` — credentials: `admin@example.com` / `admin`.

Register the server with host `db`, port `5432`, user `Data`, password `DataICMC`.

---

## Data Model

```
Event
  └── Team (name unique per event)
        ├── Participant (Telegram user ID → team)
        └── Submission
              ├── photo_path       local file path
              ├── latitude/longitude  decimal degrees from EXIF GPS
              ├── litter_details   {"plastic_bottle": 2, "can": 1, ...}
              ├── points_awarded   len(detected_items) * 5
              └── status           pending_processing → confirmed | failed
```

---

## Project Structure

```
├── src/
│   ├── backend/
│   │   ├── main.py         FastAPI app and all endpoints
│   │   ├── gps.py          EXIF GPS extraction helpers
│   │   └── schemas.py      Pydantic request/response models
│   ├── bot/
│   │   ├── telegramBot.py  Bot handlers and RankingManager
│   │   └── decorators.py   Rate-limit retry decorator
│   ├── database/
│   │   ├── models.py       SQLAlchemy ORM models
│   │   └── status.py       SubmissionStatus constants
│   └── worker/
│       ├── celery_app.py   Celery configuration
│       ├── tasks.py        process_submission task (YOLO)
│       └── weights/        YOLO model weights (not in git)
├── migrations/             Alembic migration files
├── scripts/
│   └── recalculate_scores_safely.py
├── images/                 Bot UI images
├── uploads/                Uploaded and processed images (runtime)
├── alembic.ini
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```
