"""Tata Steel Road Infrastructure Dashboard - FastAPI backend.
Serves the static frontend and the live, Postgres-backed API. All dashboard
data comes from the database (/api/db/*); there is no static JSON tier and no
JSON data files in the repo. Rebuild the DB from the source Excel with
db/rebuild.sh (Source/*.xlsx -> db/01_seed.sql -> Postgres).

Run: python3 -m uvicorn dashboard.main:app --reload --port 8000
Env: PORT (Render sets this automatically)
"""
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope


class NoCacheStaticFiles(StaticFiles):
    """Serve static assets with revalidation forced on every request.

    Starlette's StaticFiles already emits ETag / Last-Modified, but without a
    Cache-Control header browsers fall back to heuristic caching and can serve
    a stale dashboard.js / style.css for a long time after a rebuild. Setting
    `no-cache` makes the browser revalidate every time: it still caches, but
    always checks the ETag first, so an unchanged file returns a cheap 304 and
    a changed file is picked up immediately - no hard refresh needed.
    """

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

app = FastAPI(title="Tata Steel Road Works Dashboard")

# Live, Postgres-backed endpoints (weekly entry, computed progress, CPM,
# vendor/LM performance, bottlenecks, milestones). Requires db/rebuild.sh to
# have been run against a reachable Postgres - see dashboard/db.py.
from dashboard.db_routes import router as db_router  # noqa: E402

app.include_router(db_router)


STATIC_DIR = os.path.join(HERE, "static")

# Keep /static for explicit references, and also serve the static dir at root
# (html=True serves index.html at "/") so the page's relative asset paths
# (./style.css, ./dashboard.js) resolve both when served AND when the file is
# opened standalone. API routes above take precedence.
app.mount("/static", NoCacheStaticFiles(directory=STATIC_DIR), name="static")
app.mount("/", NoCacheStaticFiles(directory=STATIC_DIR, html=True), name="root")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
