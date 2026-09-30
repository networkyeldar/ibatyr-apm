from pathlib import Path

from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles


def register_frontend(app):
    web = Path(__file__).resolve().parent / "ai_web"
    app.mount("/ai/assets", StaticFiles(directory=web / "assets"), name="ai-assets")

    @app.get("/ai/", include_in_schema=False)
    async def ai_dashboard():
        return FileResponse(web / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/", include_in_schema=False)
    async def home():
        return RedirectResponse("/ai/")
