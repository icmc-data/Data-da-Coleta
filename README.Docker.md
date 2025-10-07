# Docker and Deployment

This file provides a more detailed explanation of the Docker setup and guidance for deploying the application.

## Docker Compose Services

The `docker-compose.yml` file defines the following services:

* **`redis`**: A Redis instance that acts as the message broker for Celery.
* **`db`**: A PostgreSQL database to store application data. The data is persisted in a Docker volume named `postgres_data`.
* **`pgadmin`**: A pgAdmin service for managing the PostgreSQL database. It's accessible at `http://localhost:5050`.
* **`backend`**: The FastAPI application that serves the API. It depends on the `db` and `redis` services.
* **`worker`**: The Celery worker that processes image submissions. It depends on the `redis` service.
* **`bot`**: The Telegram bot that interacts with users. It depends on the `backend` service.

---

### Building and Running the Application

When you're ready, start your application by running:

```bash
docker compose up --build