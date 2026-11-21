"""Opt-in full API smoke test with Atlas, Ollama, search and Fish Audio.

Run with `uv run python scripts/smoke_backend.py`. It creates a disposable
MongoDB database, seeds public monument records, and removes that database
in a finally block. It never reads real user accounts or chats.
"""

import json
import os
import secrets
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from fastapi.testclient import TestClient
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=True)
SOURCE_DB = os.getenv("MONGODB_DB", "virasatguide")
QA_DB = "virasatguide_qa_" + uuid4().hex[:16]
os.environ["MONGODB_DB"] = QA_DB

from virasatguide_backend.main import app
from virasatguide_backend.data.monuments import slugify


def checked(response, expected=200):
    if response.status_code != expected:
        raise AssertionError(f"{response.request.method} {response.request.url.path}: expected {expected}, got {response.status_code}: {response.text[:250]}")
    return response


def main():
    if not os.getenv("FISH_AUDIO_API_KEY"):
        raise RuntimeError("Set FISH_AUDIO_API_KEY before running the complete smoke test")
    output = Path(os.getenv("QA_OUTPUT_DIR", "/private/tmp/virasat-backend-qa"))
    output.mkdir(parents=True, exist_ok=True)
    checks = []

    def passed(name):
        checks.append(name)
        print("PASS", name, flush=True)

    with TestClient(app, base_url="http://127.0.0.1:8000") as api:
        db = app.state.database

        async def seed():
            documents = await db.client[SOURCE_DB].monuments.find({}, {"_id": 0}).to_list()
            if not documents:
                raise RuntimeError("The source monument catalogue is empty")
            await db.monuments.insert_many(documents)
            return documents

        try:
            documents = api.portal.call(seed)
            checked(api.get("/"))
            assert checked(api.get("/health")).json()["database"] == "connected"
            checked(api.get("/ping-db"))
            passed("health and MongoDB connection")

            page = checked(api.get("/api/v1/monuments", params={"page_size": 2})).json()
            assert page["total"] == len(documents) and len(page["items"]) == 2
            monument = next((m for m in documents if m["city"].casefold() == "agra"), documents[0])
            mid = monument["id"]
            checked(api.get(f"/api/v1/monuments/{mid}"))
            checked(api.get(f"/api/v1/monuments/{slugify(monument['city'])}/{slugify(monument['name'])}"))
            found = checked(api.get("/api/v1/monuments", params={"search": monument["name"], "city": monument["city"]})).json()
            assert mid in [m["id"] for m in found["items"]]
            assert api.get("/api/v1/monuments/9999999").status_code == 404
            assert api.get("/api/v1/monuments?page=0").status_code == 422
            passed("discovery, filters, pagination, numeric and readable detail routes")

            username = "qa-" + uuid4().hex
            password = secrets.token_urlsafe(20)
            credentials = {"username": username, "password": password}
            checked(api.post("/auth/register", json=credentials), 201)
            assert api.post("/auth/register", json=credentials).status_code == 400
            token = checked(api.post("/auth/login", data=credentials)).json()["access_token"]
            headers = {"Authorization": "Bearer " + token}
            checked(api.get("/auth/me", headers=headers))
            assert api.post("/auth/login", data={**credentials, "password": "wrong"}).status_code == 401
            assert api.post(f"/api/v1/monuments/{mid}/story").status_code == 401
            passed("real account registration, duplicate protection, login and authentication")

            print("Generating a real Ollama story...", flush=True)
            story = checked(api.post(f"/api/v1/monuments/{mid}/story", headers=headers)).json()
            assert story["story"] and "\n" not in story["story"] and "\\n" not in story["story"]
            cached = checked(api.post(f"/api/v1/monuments/{mid}/story", headers=headers)).json()
            assert story == cached
            passed("real story generation, text cleaning, persistence and cache reuse")

            audio = checked(api.post(f"/api/v1/monuments/{mid}/audio", headers=headers))
            assert audio.headers["content-type"] == "audio/mpeg" and len(audio.content) > 1000
            (output / "tour.mp3").write_bytes(audio.content)
            passed("real Fish Audio MP3 generation")

            tour = checked(api.post("/api/v1/travel/tour-sessions", json={"monument_id": mid}, headers=headers), 201).json()
            path = "/api/v1/travel/tour-sessions/" + tour["session_id"]
            questions = ["What is this monument known for?", "Who commissioned it?", "What does the tour say about its architecture?", "Why is it significant?"]
            request_id = str(uuid4())
            last = None
            for i, question in enumerate(questions):
                body = {"question": question, "request_id": request_id if i == 3 else str(uuid4())}
                last = checked(api.post(path + "/questions", json=body, headers=headers)).json()
                assert last["answer"]
            view = checked(api.get(path, headers=headers)).json()
            assert [a["question"] for a in view["history"]] == questions[-3:]
            repeated = checked(api.post(path + "/questions", json={"question": questions[-1], "request_id": request_id}, headers=headers)).json()
            assert repeated == last

            async def stored_history():
                return (await db.tour_sessions.find_one({"_id": tour["session_id"]}))["view"]["history"]
            assert len(api.portal.call(stored_history)) == 3
            passed("real follow-ups, session persistence, three-pair DB retention and retry IDs")

            print("Generating a real trip itinerary...", flush=True)
            trip = checked(api.post("/api/v1/travel/plans", json={"destination": monument["city"], "city": monument["city"], "days": 1, "travelers": 2, "budget": 5000}, headers=headers), 201).json()
            plan_path = "/api/v1/travel/plans/" + trip["plan_id"]
            assert checked(api.get(plan_path, headers=headers)).json() == trip
            assert trip["estimated_total"] == sum(d["estimated_total"] for d in trip["days"])
            pdf = checked(api.get(plan_path + "/pdf", headers=headers))
            assert pdf.headers["content-type"] == "application/pdf"
            assert len(PdfReader(BytesIO(pdf.content)).pages) > 0
            (output / "trip.pdf").write_bytes(pdf.content)
            (output / "trip.json").write_text(json.dumps(trip, indent=2))
            passed("real itinerary, budget arithmetic, saved plan and PDF download")

            other = {"username": "qa-" + uuid4().hex, "password": secrets.token_urlsafe(20)}
            checked(api.post("/auth/register", json=other), 201)
            other_token = checked(api.post("/auth/login", data=other)).json()["access_token"]
            other_headers = {"Authorization": "Bearer " + other_token}
            for resource in [path, plan_path, plan_path + "/pdf"]:
                assert api.get(resource, headers=other_headers).status_code == 404
            checked(api.post("/auth/logout", headers=headers))
            assert api.get("/auth/me", headers=headers).status_code == 401
            passed("real resource ownership and logout/token revocation")

            origin = "https://virasat-frontend-nu.vercel.app"
            cors = checked(api.options("/api/v1/travel/plans", headers={"Origin": origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Authorization,Content-Type"}))
            assert cors.headers["access-control-allow-origin"] == origin
            assert cors.headers["access-control-allow-credentials"] == "true"
            passed("production frontend CORS")
            (output / "results.json").write_text(json.dumps({"passed": checks, "temporary_database": QA_DB}, indent=2))
        finally:
            async def cleanup():
                assert db.name == QA_DB and QA_DB.startswith("virasatguide_qa_")
                await db.client.drop_database(QA_DB)
            api.portal.call(cleanup)
            print("Removed disposable QA database", flush=True)
    print(f"All {len(checks)} real-service checks passed. Artifacts: {output}", flush=True)


if __name__ == "__main__":
    main()
