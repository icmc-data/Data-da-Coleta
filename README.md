# Trash Challenge — MVP

FastAPI + Celery + Redis + Postgres + MinIO (S3) + Telegram webhook.

## Quickstart (local)

1) Clone and create `.env` from `.env.example` and fill tokens.
2) Start services:

```bash
docker compose up -d
```

3) Create the S3 bucket:

```bash
bash scripts/create_buckets.sh
```

4) Expose the API (for Telegram webhook), e.g. using ngrok:

```bash
ngrok http 8000
```

5) Set Telegram webhook:

```bash
export $(cat .env | xargs)
bash scripts/set_webhook.sh
```

6) Send a photo to your bot. You should see:
- Upload to MinIO
- Celery task executing inference stub
- Score saved to Postgres
- Telegram message with points
- `GET http://localhost:8000/leaderboard` shows team points

## Notes

- For production, replace MinIO with AWS S3 (clear `S3_ENDPOINT` and set AWS creds in environment).
- Replace `worker/inference_stub.py` with your real model.
- Add before/after logic and geofencing next.
