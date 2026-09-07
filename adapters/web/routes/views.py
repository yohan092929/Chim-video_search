from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

views_router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


@views_router.get("/", response_class=HTMLResponse)
def index_view():
    """Serves the main minimalist UI page."""
    index_file = TEMPLATES_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h1>index.html not found</h1>", status_code=404)
    return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
