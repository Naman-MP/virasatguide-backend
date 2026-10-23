# Virasat Guide Backend

## Run

Requires Python 3.13+ and a working MongoDB Atlas connection.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
cp .env.example .env
```

Fill in `.env`:

```dotenv
MONGODB_URI=your-mongodb-connection-string
MONGODB_DB=virasatguide
JWT_SECRET_KEY=your-random-secret-at-least-32-characters
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret
GOOGLE_REDIRECT_URI=http://127.0.0.1:8000/auth/google/callback
FRONTEND_URL=http://127.0.0.1:5173/src/dashboard/index.html
```

Generate a JWT secret with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Allow your IP in Atlas network access. Keep `.env` private.

```sh
python -m uvicorn virasatguide_backend.main:app --reload --host 127.0.0.1 --port 8000
```

API docs: http://127.0.0.1:8000/docs

In a second terminal, open the frontend repo and run `npm run dev`. Open http://127.0.0.1:5173/src/landing-page/index.html. Use `127.0.0.1` for both servers. Restart the backend after editing `.env`.

## Google OAuth and JWT

In Google Cloud Console, configure the consent screen, create an OAuth **Web application** client, and add this authorized redirect URI:

`http://127.0.0.1:8000/auth/google/callback`

Add your Google account as a test user if the app is in testing mode. Put the client ID and secret in the backend `.env`.

Implemented `/auth/google/login` and `/auth/google/callback` with FastAPI and Authlib. The flow validates Google's ID token, state and nonce, and uses PKCE. It creates or finds the MongoDB user by Google account ID, sets a 30-minute JWT in an HttpOnly cookie, and redirects to the frontend dashboard. `/auth/google/status` checks configuration; failed sign-ins show a retry message. Google tokens and passwords are not stored.

Password registration hashes passwords; `/auth/login` returns a bearer JWT. `/auth/me` accepts a valid bearer token or login cookie. JWTs require a valid signature, expiry, user and token version. `/auth/logout` revokes the user's existing tokens and clears the cookie. Cookie logout requires the configured frontend origin; CORS allows that origin. Accounts are not automatically linked by email.

## Test

```sh
python -m pip install pytest httpx
python -m pytest -q
```

Tests simulate Google and MongoDB. The real Google consent step is completed in your browser.
