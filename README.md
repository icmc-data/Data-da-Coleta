# Trash Challenge — MVP

This project is the backend for a "Trash Challenge" application where users can compete in teams to collect and document litter. It uses a Telegram bot for user interaction, a FastAPI backend to handle data, a Celery worker for image processing with a YOLO model, and a Postgres database for data storage.

## About The Project

This application is designed to encourage environmental cleanup through gamification. Participants can join teams, submit photos of litter they've collected, and earn points for their team. A leaderboard tracks the scores of each team, fostering a friendly sense of competition.

### Built With

* [FastAPI](https://fastapi.tiangolo.com/) - For the web framework
* [Celery](https://docs.celeryq.dev/en/stable/) - For asynchronous task processing
* [Redis](https://redis.io/) - As a message broker for Celery
* [PostgreSQL](https://www.postgresql.org/) - For the database
* [Telegram Bot API](https://core.telegram.org/bots/api) - For user interaction
* [YOLO](https://github.com/ultralytics/ultralytics) - For object detection in images

---

## Getting Started

To get a local copy up and running, follow these simple steps.

### Prerequisites

* Docker and Docker Compose
* A Telegram Bot Token. You can get one by talking to the [BotFather](https://t.me/botfather).
* ngrok or a similar tool to expose your local server to the internet for the Telegram webhook.

### Installation

1.  **Clone the repository**
2.  **Create a `.env` file** from the example below and fill in your details:

    ```env
    TELEGRAM_TOKEN=YOUR_TELEGRAM_TOKEN
    CHAT_ID=YOUR_TELEGRAM_CHAT_ID
    BACKEND_URL=http://YOUR_NGROK_URL
    DATABASE_URL=postgresql://Data:DataICMC@db:5432/DataDaColeta
    CELERY_BROKER_URL=redis://redis:6379/0
    CELERY_RESULT_BACKEND=redis://redis:6379/0
    UPLOADS_DIR=./uploads
    ```

3.  **Start the services** using Docker Compose:

    ```bash
    docker compose up -d
    ```

4.  **Expose the API** using ngrok:

    ```bash
    ngrok http 8000
    ```

    *Note: Remember to update the `BACKEND_URL` in your `.env` file with the URL provided by ngrok.*

5.  **Set the Telegram webhook**:
    You will need to make a POST request to the Telegram API to set the webhook. You can do this with a tool like `curl`:

    ```bash
    curl -F "url=http://<your-ngrok-url>/webhook" [https://api.telegram.org/bot](https://api.telegram.org/bot)<YOUR_TELEGRAM_TOKEN>/setWebhook
    ```

6.  **Send a photo to your bot** and you should see the following:
    * A Celery task executing the inference stub.
    * The score saved to Postgres.
    * A Telegram message with the points awarded.
    * The leaderboard at `http://localhost:8000/leaderboard` showing the team's points.

---

## Usage

Once the bot is running, you can interact with it on Telegram:

* **/start**: See the main menu.
* **Create a new group**: Create a new team for the challenge.
* **Join an existing group**: Join a team that has already been created.
* **Submit a photo**: Send a photo of litter to a group topic to score points.
* **/leaderboard**: View the current team rankings.

---