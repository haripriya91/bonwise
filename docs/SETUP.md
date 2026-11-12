# Setup and deployment

[Back to README](../README.md)

## Local setup

### Prerequisites

- Python 3.12 and a Node.js LTS version compatible with the frontend lockfile.
- Supabase PostgreSQL/Auth and a private receipt Storage bucket.
- OpenAI model access and a Pinecone external-vector index matching the configured embedding dimensions.
- LangSmith configuration for tracing.

### 1. Configure the backend

From the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
cp backend/.env.example backend/.env
```

Fill `backend/.env` with your own values:

| Configuration | Environment variables |
| --- | --- |
| Database connections | `DATABASE_URL`, `MIGRATION_DATABASE_URL` |
| Authentication and Storage | `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `SUPABASE_STORAGE_BUCKET` |
| Token validation | `SUPABASE_JWT_ISSUER`, `SUPABASE_JWT_AUDIENCE`, `SUPABASE_JWKS_URL` |
| Models | `OPENAI_API_KEY`, `LLM_MODEL`, `VISION_MODEL`, `TRANSCRIPTION_MODEL` |
| Embeddings and index | `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`, `PINECONE_API_KEY`, `PINECONE_INDEX_NAME`, `PINECONE_INDEX_HOST` |
| Observability | `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, `LANGSMITH_ENDPOINT`, `LANGSMITH_TRACING` |
| Application | `FRONTEND_ORIGIN`, `APP_ENV`, `CORRECTION_RAG_ENABLED` |

Auth URLs derive from the Supabase project URL when omitted. Keep LangSmith input/output hiding enabled. The Pinecone vector dimension must match `EMBEDDING_DIMENSIONS`.

For a new development database, apply the existing migrations from the repository root:

```bash
python -m alembic -c alembic.ini upgrade head
```

Provision Supabase Auth users, household memberships and the private `receipts` bucket. Product catalogues and demo offers have preparation/import scripts under `scripts/`; inspect their configuration before importing. The demo uses preconfigured household accounts rather than public registration.

Start the API:

```bash
cd backend
python -m uvicorn app.main:app --reload --port 8000
```

Health check: `http://localhost:8000/health`  
Interactive API docs: `http://localhost:8000/docs`

### 2. Configure the frontend

In a second terminal, from the repository root:

```bash
cd frontend
npm ci
cp .env.example .env
npm run dev
```

Set these frontend values:

```dotenv
VITE_API_BASE_URL=http://localhost:8000
VITE_SUPABASE_URL=https://YOUR_PROJECT.supabase.co
VITE_SUPABASE_PUBLISHABLE_KEY=YOUR_PUBLIC_BROWSER_KEY
```

Set backend `FRONTEND_ORIGIN` to the actual frontend origin. Server/service-role secrets must never appear in frontend variables. Microphone capture requires browser permission and an appropriate secure context, such as HTTPS or localhost.

### Hosting

Deploy `frontend` and `backend` as their respective Vercel projects. Configure backend secrets in the backend project's environment settings and public `VITE_*` values in the frontend project. Match the API's allowed origin and Supabase authentication redirect settings to the frontend deployment.

The backend includes `backend/vercel.json`. Run migrations separately using the migration connection. Private receipt Storage remains in Supabase. This README does not certify a particular deployment URL.

