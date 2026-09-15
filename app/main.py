from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import batches, images, lines


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
    app.include_router(lines.router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
