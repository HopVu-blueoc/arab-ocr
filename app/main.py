from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import batches, images, jobs, lines


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
    def health() -> dict[str, str]:
        return {"status": "ok"}

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
