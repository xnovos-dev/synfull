from pathlib import Path

from contextlib import asynccontextmanager
import threading

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router

from .integrations.discord_alerts import monitor
from .services.management import monitor_loop


@asynccontextmanager
async def lifespan(_app: FastAPI):
    monitor.start()
    stop_event = threading.Event()
    management_thread = threading.Thread(target=monitor_loop, args=(stop_event,), name="synfull-monitoring", daemon=True)
    management_thread.start()
    try:
        yield
    finally:
        stop_event.set()
        management_thread.join(timeout=3)
        monitor.stop()


app = FastAPI(title="Synfull", version="0.1.0", lifespan=lifespan)
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}


_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if (_FRONTEND_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str):
    if path.startswith("api/") or path in {"docs", "redoc", "openapi.json", "health"}:
        raise HTTPException(status_code=404)
    index = _FRONTEND_DIST / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail="Frontend assets are not built")
    from .services.management import auth_status
    return FileResponse(index, headers={"Cache-Control": "no-store"} if auth_status()["setup_required"] else None)
