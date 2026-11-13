# Bonwise

### Understand your purchases. Plan your next shop.

Bonwise is an AI-assisted household shopping and spending application. Capture a receipt, review its details, understand recorded spending and prepare your next shopping list with budget and offer context.

## What you can do

- **Capture purchases:** upload German or English receipts, describe purchases by voice, or type them. Edit extracted details and confirm before saving.
- **Reuse corrections with RAG:** product matching combines household correction memory, OpenAI embeddings and Pinecone retrieval. Relevant catalogue and correction context supports model interpretation; code checks proposed matches.
- **Ask Bonwise:** ask spending questions by text or voice, follow up with retained date/filter context, and inspect supporting records. LangChain routes requests to bounded read-only calculations.
- **Explore spending:** use category charts and date filters. Generate up to three evidence-backed checks inside Spending overview, such as repeated purchases, higher recorded unit prices or budget pressure. Each offers a relevant action rather than repeating the chart.
- **Manage household finances:** track dated budgets, recorded spending, reservations and available funds separately. Calendar views highlight purchase days.
- **Compare offers:** inspect compatible products and unit prices from validated, dated sample offers.
- **Plan a weekly shop:** a LangGraph workflow uses purchase history, routines, budget and offers to propose a list. Review and edit drafts; approval controls reservations.

Home keeps the calendar and budget summary prominent. The dedicated Purchases screen loads five records initially, with more available on request.

## Live demo

[Open Bonwise](https://bonwise.vercel.app/)

## Technology

| Area | Tools |
| --- | --- |
| Interface | React, TypeScript, Vite, TanStack Query |
| API and validation | Python, FastAPI, Pydantic, SQLAlchemy, Alembic |
| AI workflows | OpenAI vision, speech, language models and embeddings; LangChain; LangGraph |
| Retrieval | Pinecone product and correction index |
| Identity and records | Supabase Auth, PostgreSQL and private Storage |
| Evaluation | LangSmith sanitized traces and evaluation scripts |
| Hosting | Vercel frontend and backend projects |

PostgreSQL holds authoritative household records. RAG supplies product interpretation context; code calculates financial answers. Browser memory retains chat context separately from correction memory. Accepted corrections enrich retrieval rather than retrain model weights.

## Run locally

You need Python 3.14, a compatible Node.js version, and configured Supabase, OpenAI and Pinecone services.

1. Create a Python environment and install `backend/requirements.txt`.
2. Copy `backend/.env.example` to `backend/.env` and configure your services.
3. Apply existing migrations: `python -m alembic -c alembic.ini upgrade head`.
4. From `backend`, run `python -m uvicorn app.main:app --reload --port 8000`.
5. From `frontend`, run `npm ci`, copy `.env.example` to `.env`, configure the public API/Auth values, then run `npm run dev`.

See [setup and Vercel deployment](docs/SETUP.md) for environment variables, account provisioning and Storage configuration. Keep backend secrets out of frontend variables and version control.

## Verification and demo scope

The latest refinement passed **218 backend tests**, the **frontend production build** and **10 component interaction checks**. Model-dependent tests use simulated responses. Live service behavior and hosted permissions need checks against the configured deployment; automated counts are not accuracy or security guarantees.

The demo uses two configured household accounts. Sample offers were used because the deadline did not allow enough time to connect a live price source and verify its permissions, accuracy and update frequency. They are clearly labelled and do not represent live quotes or guaranteed savings.

Saved receipts are a partial spending record. High spending alone does not mean overspending. Private receipt Storage, household authorization, manual privacy covers and text checks reduce exposure; they do not guarantee detection of every personal detail.

## Documentation

| Guide | Contents |
| --- | --- |
| [Setup](docs/SETUP.md) | Local configuration, services and Vercel deployment |
| [Technical overview](docs/TECHNICAL_OVERVIEW.md) | Architecture, security, evaluation, limitations and future work |
| [AI purchase insights](docs/AI_INSIGHTS.md) | Evidence rules, useful actions, privacy and stale-result handling |
| [Privacy runbook](docs/privacy/00_RUNBOOK.md) | Access and data-exposure checks |

API schema: [`docs/openapi.json`](docs/openapi.json). Backend code lives in `backend/app`, interface code in `frontend/src`, and import/indexing/evaluation tools in `scripts`.
