# api/

Vercel serverless entry point only; no application logic lives here. Overview and deploy notes: [`docs/PROJECT_DOCUMENTATION.md`](../docs/PROJECT_DOCUMENTATION.md), [`docs/DEPLOY_VERCEL.md`](../docs/DEPLOY_VERCEL.md).

| File | Role |
|---|---|
| `index.py` | Re-exports `dashboard.main.app` so Vercel's Python runtime serves the same FastAPI app as local/Render |

Wiring: `../vercel.json` rewrites every path to this function. The database is reached through `DATABASE_URL` (Neon); Vercel never runs `rebuild.sh`, so the schema and seed must be applied to Neon by hand. Not used by local runs or by the Render deploy (`uvicorn dashboard.main:app`).
