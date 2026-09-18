from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlmodel import Session

from app.config import get_settings
from app.db import get_engine
from app.health import build_health_report
from app.routers import batches, images, jobs, lines
from app.storage import get_storage


def create_app() -> FastAPI:
    # No schema creation on startup. Migrations own the schema and run once,
    # from the `migrate` compose service, before api and worker start - they
    # share one SQLite file and would otherwise race to build it.
    app = FastAPI(title="Arabic OCR Review")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(batches.router)
    app.include_router(images.router)
    app.include_router(jobs.router)
    app.include_router(lines.router)

    @app.get("/api/health")
    def health() -> JSONResponse:
        """Database and storage reachability, gating Docker's own healthcheck
        (`depends_on: condition: service_healthy`); broker reachability and
        the current OCR backlog are reported alongside for an operator to see,
        not treated as failing conditions - see app/health.py.
        """
        with Session(get_engine()) as session:
            status_code, body = build_health_report(session, get_storage(), get_settings())
        return JSONResponse(status_code=status_code, content=body)

    @app.get("/api/limits")
    def limits() -> dict[str, int]:
        """Upload caps, so the client packs requests the proxy will accept.

        Served rather than duplicated in the frontend: a hardcoded copy drifts
        the moment either limit is tuned, and the symptom is a 413 the user
        cannot do anything about.
        """
        settings = get_settings()
        return {
            "max_file_bytes": settings.max_upload_bytes,
            "max_request_bytes": settings.max_request_bytes,
        }

    return app


app = create_app()
