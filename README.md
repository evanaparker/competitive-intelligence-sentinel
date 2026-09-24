# Competitive Intelligence Sentinel

Tracks material changes in your competitor's online presence.

PoC for the PM Agentic AI capstone (Cohort 10). Monitors public competitor sources (pricing, changelog, G2, job boards) and generates materiality-scored insights citing supporting evidence.

## Components

1. Source Ingestion
2. Change Detection
3. Signal Classification
4. Cross-Source Correlation
5. Materiality Scoring & Rationale Generation
6. Human Feedback Loop
7. Delivery & Distribution

## Database

Schema lives in `supabase/migrations/`, applied in order (`0001` through `0006`). Project: `competitive-intelligence-sentinel` (Supabase, `us-east-1`).

Connect with the `service_role` key (never the anon/publishable key — every table has RLS enabled with no policies, so the anon/authenticated roles see zero rows by design). Copy `.env.example` to `.env` and fill in `SUPABASE_URL` (not secret) and `SUPABASE_SERVICE_ROLE_KEY` (from the Supabase dashboard → Project Settings → API; never commit this file).
