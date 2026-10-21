# Virasat Guide Backend

FastAPI backend with an asynchronous PyMongo connection to the MongoDB Atlas `virasat-guide` cluster. Requires Python 3.13 or later.

## Run locally

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

On Windows, activate the environment with `.venv\Scripts\activate` and copy the environment template using your file manager or shell.

Edit your private `.env` and replace `<db_password>` with the Atlas database user's password. URL-encode special characters in the password. Allow your current machine's IP in Atlas → Database & Network Access.

```sh
uvicorn virasatguide_backend.main:app --reload --host 127.0.0.1 --port 8000
```

- API documentation: http://127.0.0.1:8000/docs
- Database health: http://127.0.0.1:8000/health
- OpenAPI schema: http://127.0.0.1:8000/openapi.json

A successful health check returns `{"status":"ok","database":"connected"}`. If the database becomes unavailable, the endpoint returns HTTP 503. Startup verifies Atlas access, and shutdown closes the connection.

Credentials belong only in your private `.env` or your hosting provider's secret environment variables. Never commit them. The existing local private `.env` can be reused; its `PORT` value is ignored because Uvicorn uses `--port`.

GitHub stores the source; running this backend creates the database connection. No online hosting or deployment is configured.

MongoDB driver reference: https://www.mongodb.com/docs/languages/python/pymongo-driver/current/connect/

## Database choices

FastAPI serves HTTP requests; it is not an ORM. This starter uses the official async PyMongo driver directly. Keep it for now: there are no domain models or CRUD routes that would benefit from an additional ODM layer. Use Pydantic request/response models when adding those routes. Beanie can be reconsidered if document relationships and repeated model persistence logic become substantial.

MongoDB Shell (`mongosh`) is a database client, not a server. Atlas hosts the server, so a local MongoDB Community Server installation is optional. For a local server, set `MONGODB_URI=mongodb://127.0.0.1:27017`.

The application checks connectivity before accepting requests, bounds database waits, and closes its client on shutdown. `/ping-db` remains available for existing callers. Error responses do not expose connection details.

If a database password has appeared in source code, rotate that password in Atlas and update the private `.env`. Removing it from the current file does not invalidate copies in Git history.

## Code layout

- `main.py` creates the API and registers routes.
- `data/database.py` owns the shared async PyMongo connection and database dependency.
- `routes/health.py` serves the status endpoints.

Add database-backed routes using `Depends(get_database)` to reuse the application connection pool. The backend reads the project-root `.env`; it does not create a second local database connection.
