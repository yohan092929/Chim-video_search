from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from adapters.web.routes.api import api_router
from adapters.web.routes.views import views_router

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


def create_app() -> FastAPI:
    """Creates and configures the FastAPI web application."""
    app = FastAPI(title="Video Keyword Highlight Studio", version="1.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )


    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    app.include_router(api_router)
    app.include_router(views_router)

    return app


app = create_app()
