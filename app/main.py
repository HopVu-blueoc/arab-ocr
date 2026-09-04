from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import init_db
from app.routers import batches, images


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        init_db()
        yield

    app = FastAPI(title="Arabic OCR Review", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(batches.router)
    app.include_router(images.router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
