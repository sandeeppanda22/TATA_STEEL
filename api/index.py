"""Vercel serverless entry point for the Tata Steel dashboard.

Vercel's @vercel/python runtime looks for a module-level ASGI/WSGI callable
named `app` in a file under api/. We simply re-export the existing FastAPI
app from dashboard/main.py, so there is exactly one app definition and no
behaviour forked for deployment.

Everything (the /api/db/* routes AND the static front end mounted at /) is
served by this one function; vercel.json rewrites every path to it. The DB
is reached exactly as locally — through dashboard/db.py, which reads
DATABASE_URL (set to the Neon connection string in the Vercel project's
environment variables). See docs/DEPLOY_VERCEL.md.
"""
from dashboard.main import app  # noqa: F401  (re-exported for Vercel)
