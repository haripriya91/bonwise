# Performance

## What is known

The saved authenticated baseline is `evaluation-results/authenticated-before.json`, measured on the user's machine. Five requests per scenario succeeded:

| Request | Baseline median |
| --- | ---: |
| `/me` | 1,213.55 ms |
| Home combined overview | 2,855.39 ms |
| First five purchases | 2,123.26 ms |
| Spending summary | 1,467.55 ms |
| Offer picker options | 2,096.74 ms |

These are HTTP timings, not browser rendering metrics. Five samples do not establish stable p95 latency. The later connectivity check passed with 815.56 ms for connection setup plus `SELECT 1`; that is not 815 ms of SQL execution.

A supplied Chrome observation showed LCP 0.35 s, CLS 0.03 and INP 128 ms. It does not measure how long later offer data takes to appear. No representative browser before/after or authenticated after-patch result is available yet.

## Changes supported by regression fixtures

| Operation | Previous SELECTs | Revised SELECTs | Change |
| --- | ---: | ---: | --- |
| Five-purchase page | 8 | 4 | Fetch item rows once for the page |
| Offer picker | 6 | 4 | Combine membership/timezone lookup; count offers in SQL; no store lookup |
| Home offers with linked history | 6 | 4 | Fetch only linked catalogue entries and offers for the three displayed products |
| Home offers without linked history | 6 | 2 | Stop after authorization/timezone and history |
| Offer comparison | 6 | 3 | No purchase-history/store lookup; fetch only relevant offer rows |

Counts are verified on isolated SQL fixtures. They do not establish a measured percentage improvement on Vercel. Household checks and expiry, membership, bundle and product-compatibility rules remain enforced. Recommendations and comparisons use SQL/code, not an AI call. No shared financial cache was introduced.

Home budget and offers load independently. All page loaders reuse the existing labelled three-dot component; reduced-motion users still see the loading text. Non-retriable access/validation failures and temporary 503 responses do not automatically make a second request.

`backend/vercel.json` specifies Stockholm (`arn1`) because the supplied Supabase pooler error identifies `eu-north-1`. Verify that this is still the database region before deploying; update the code setting if the project moved. The active deployment's previous region was not inspected. Configuration changes take effect after redeployment.

Official guidance: [function regions](https://vercel.com/docs/functions/configuring-functions/region), [region codes](https://vercel.com/docs/regions).

## Measure the deployment

1. Run the offline suite and frontend build, then deploy the backend and frontend.
2. From the project root, with the normal Python environment active:

   ```bash
   python scripts/benchmark_readonly.py --authenticated --output evaluation-results/authenticated-after.json
   ```

   Missing access token, household UUID and dates are prompted locally. Run directly in a terminal, not a stdin heredoc. Use the same household, dates, data, machine and network as the baseline.

3. Compare equivalent scenarios:

   ```bash
   python scripts/compare_performance.py evaluation-results/authenticated-before.json evaluation-results/authenticated-after.json --output evaluation-results/performance-comparison.json
   ```

4. To separately measure Home's budget-only path, repeat with `--ledger-only --output evaluation-results/ledger-after.json`. Comparing that endpoint with the old combined endpoint is different work, not an equivalent-endpoint speedup. The benchmark also measures the standalone Home offers endpoint; it has no earlier standalone baseline.
5. In Chrome DevTools → Performance, record five foreground reloads under the same cache/throttling conditions, then navigate Home → Purchases → Offers in the same order. Record LCP/CLS/INP and the time until each data section visibly appears. Keep initial and repeat loads separate. Do not publish an authenticated HAR with tokens or response bodies.

The comparator refuses a speedup calculation when a scenario has failed requests or important recorded conditions differ. It cannot verify household/date/data equivalence because identifiers are intentionally excluded. Record those conditions privately.

## Find the remaining bottleneck

Read `Server-Timing` in DevTools → Network. It includes fixed names and durations only:

- `app`: request entry to response headers; excludes runtime startup before the app runs.
- `auth`: token validation, including signing-key lookup when needed.
- `db_connect`: physical connection creation, including failed attempts; excludes pool waiting.
- `db_query`: SQL cursor execution; excludes row fetching.
- `ai_stage`: model/transcription stages, including local parsing/validation.
- `retrieval`: existing retrieval spans.

These timings can overlap; do not add them as disjoint components. The browser's full duration also includes network transfer and client processing. Optimize the dominant measured stage before adding more infrastructure. Keep `DATABASE_POOL_SIZE=0` until a bounded-pool trial is justified by measurements and capacity.

If connectivity fails again:

```bash
python scripts/diagnose_database.py
```

It performs DNS/TCP checks and `SELECT 1`, without writing data or printing credentials. Check the current Supabase connection string and project/network status privately. Changing loaders or SQL query counts cannot fix an unreachable database.

## Files to keep

`evaluation-results/` keeps the sanitized baseline and, once measured, reviewed after/comparison/live-AI reports. Build logs, UI-style checks, dry-run output and machine-environment dumps are local artifacts and are ignored. They are not portfolio AI evaluation results.
