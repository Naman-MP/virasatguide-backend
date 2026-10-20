# Virasat Guide Backend

FastAPI backend with an asynchronous PyMongo connection to the MongoDB Atlas `virasat-guide` cluster. Requires Python 3.10 or later.

## Run locally

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

On Windows, activate the environment with `.venv\Scripts\activate` and copy the environment template using your file manager or shell.

Edit your private `.env` and replace `<db_password>` with the Atlas database user's password. URL-encode special characters in the password. Allow your current machine's IP in Atlas → Database & Network Access.

```sh
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

- API documentation: http://127.0.0.1:8000/docs
- Database health: http://127.0.0.1:8000/health
- OpenAPI schema: http://127.0.0.1:8000/openapi.json

A successful health check returns `{"status":"ok","database":"connected"}`. If the database becomes unavailable, the endpoint returns HTTP 503. Startup verifies Atlas access, and shutdown closes the connection.

Credentials belong only in your private `.env` or your hosting provider's secret environment variables. Never commit them. The existing local private `.env` can be reused; its old `PORT` value is ignored because Uvicorn uses `--port`.

GitHub stores the source; running this backend creates the database connection. No online hosting or deployment is configured.

MongoDB driver reference: https://www.mongodb.com/docs/languages/python/pymongo-driver/current/connect/
