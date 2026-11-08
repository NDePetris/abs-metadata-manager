"""Local HTTP shell for the bulk tool; all remote writes start at POST /api/apply."""

import os
import secrets
import threading
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .adapter import CliAdapter
from .bulk import Bulk, keys, strict_json
from .config import ROOT, Settings, detect_cli


def create_app(service=None, assets=None):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    token = secrets.token_urlsafe(32)
    assets = Path(assets or ROOT / "frontend" / "dist")
    state = {"service": service, "preview": None, "job": {"running": False}}
    gate = threading.Lock()

    @app.middleware("http")
    async def local_only(request, call_next):
        host = request.url.hostname
        origin = request.headers.get("origin")
        if host not in ("127.0.0.1", "localhost", "testserver") or (
            origin and origin != f"{request.url.scheme}://{request.url.netloc}"
        ):
            return JSONResponse({"detail": "Local same-origin requests only"}, status_code=403)
        if request.url.path.startswith("/api/") and not secrets.compare_digest(
            request.headers.get("x-session-token", ""), token
        ):
            return JSONResponse({"detail": "Reload the app to establish a session"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(TypeError)
    @app.exception_handler(KeyError)
    @app.exception_handler(ValueError)
    async def bad_value(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(FileNotFoundError)
    async def missing_snapshot(request, exc):
        return JSONResponse(
            {"detail": "Original snapshot/build missing; export again or run start.ps1 -Install"},
            status_code=400,
        )

    def bulk():
        if not state["service"]:
            raise ValueError("Connect to ABS first")
        return state["service"]

    async def body(request):
        data = await request.body()
        if len(data) > 25 * 1024 * 1024:
            raise ValueError("JSON exceeds 25 MB")
        return strict_json(data.decode("utf-8-sig"))

    def start(work):
        if not gate.acquire(blocking=False):
            raise ValueError("An operation is running")
        state["job"] = {"running": True, "done": 0, "total": 0, "message": "Starting"}

        def progress(done, total, message):
            state["job"] = {
                **state["job"],
                "done": done,
                "total": total,
                **({"results": message} if isinstance(message, list) else {"message": message}),
            }

        def run():
            try:
                result = work(progress)
                state["job"] = {**state["job"], "result": result}
            except Exception as exc:  # noqa: BLE001 -- background operation boundary reports failures to UI
                state["job"] = {**state["job"], "error": str(exc)}
            finally:
                state["job"] = {**state["job"], "running": False}
                gate.release()

        threading.Thread(target=run, daemon=True).start()
        return {"started": True}

    @app.post("/api/connect")
    async def connect(request: Request):
        data = await body(request)
        keys(data, ("server", "token"))

        def work(progress):
            adapter = CliAdapter(
                Settings(server=data["server"], token=data["token"], executable=detect_cli())
            )
            info = adapter.probe()
            legacy = Path(os.getenv("ABS_MM_DB", str(ROOT / "data" / "metadata.sqlite3")))
            state["service"] = Bulk(adapter, legacy.parent / "bulk", legacy)
            state["preview"] = None
            return {**info, "server": adapter.server}

        return start(work)

    @app.post("/api/export")
    async def export(request: Request):
        data = await body(request)
        keys(data, ("library_id",))
        service = bulk()
        return start(lambda progress: service.export(data["library_id"], progress))

    @app.get("/api/job")
    def job():
        return state["job"]

    @app.post("/api/review")
    async def review(request: Request):
        document = await body(request)
        rows = bulk().review(document)
        library = bulk().snapshot(document["snapshot_id"])["library_id"]
        if request.query_params.get("library_id") != library:
            raise ValueError("Corrections belong to a different library; select their original library")
        return rows

    @app.post("/api/preview")
    async def preview(request: Request):
        if gate.locked():
            raise ValueError("An operation is running")
        plan = bulk().preview(await body(request))
        state["preview"] = plan
        snapshot = bulk().snapshot(plan["document"]["snapshot_id"])
        return {
            "digest": plan["digest"],
            "rows": plan["rows"],
            "phrase": "APPLY " + plan["digest"][:12],
            "server": snapshot["server"],
            "library_id": snapshot["library_id"],
        }

    @app.post("/api/apply")
    async def apply(request: Request):
        data = await body(request)
        keys(data, ("digest", "confirmation"))
        plan = state["preview"]
        if not plan or data["digest"] != plan["digest"]:
            raise ValueError("Preview expired; review again")
        if data["confirmation"] != "APPLY " + plan["digest"][:12]:
            raise ValueError("Enter the exact confirmation phrase")
        service = bulk()
        result = start(lambda progress: service.apply(plan, data["confirmation"], progress))
        state["preview"] = None
        return result

    @app.post("/api/check")
    def check():
        service = bulk()
        return start(lambda progress: service.check_results())

    @app.post("/api/ebook-report")
    async def report(request: Request):
        return bulk().ebook_report(await body(request))

    @app.get("/")
    def index():
        return HTMLResponse(
            (assets / "index.html").read_text(encoding="utf-8").replace("__SESSION_TOKEN__", token)
        )

    if (assets / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=assets / "assets"), name="assets")

    app.state.session_token = token  # Test access only; never an API endpoint.
    return app


app = create_app()
