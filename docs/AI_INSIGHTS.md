# Purchase insights: useful next steps

Insights live inside the expandable **Spending overview** section on Analysis. Generation remains explicit. The ordinary category chart, spending total and healthy budget balance are not repeated as insight cards.

## Supported findings

| Finding | Required evidence | Action |
| --- | --- | --- |
| Repeat purchases | The same confirmed product identity, or identical unmatched wording/category/unit, on at least two positive purchase records in the selected period | Review those records and check what is at home before adding the item again |
| Higher recorded unit price | A confirmed product identity, matching units, distinct dates and unambiguous recorded prices; later price exceeds the previous one | Compare compatible offers before buying again |
| Weekly-list review | Positive clothing lines in the selected receipts | Check whether those clothing items belong in the next weekly grocery list |
| Budget constraint | Recorded spending plus open reservations exceeds the relevant explicitly dated budget | Review the target/purchases or reservations, keeping reservations distinct from recorded spending |
| Missing target | No single budget covers the selected dates | Set a relevant target without inventing an amount |

Only supported findings are eligible; at most three are displayed. A healthy budget does not create another card. One grocery receipt alone does not establish repeat buying or a trend. A single receipt containing clothing can support a planning check, without claiming those purchases were unnecessary. If nothing additional is supported, the interface explains that more history may help and makes no model call.

Unit-price comparisons use the same confirmed product and recorded unit, not fuzzy name similarity. Multiple prices for the same product/unit/date are treated as ambiguous and skipped. Comparisons show dates and absolute change; no percentage is generated, including when the earlier price is zero. Receipt-level discounts may affect checkout totals. The records do not establish why prices differ or promise future savings.

## AI and code responsibilities

Python loads authorized confirmed purchase records and relevant budget context, calculates facts, and builds eligible decision cards. One configured model call selects/prioritizes up to three eligible findings. Strict validation checks each fact/action identifier, rejects duplicates or unverified choices, and requires inclusion of a verified budget constraint when present.

Displayed figures, wording and evidence links are rendered from verified code templates. The model does not supply arbitrary amounts or financial judgments. It receives minimized numeric aggregates, dates, category/unit codes and eligible action identifiers. Product names, merchants, household/account/purchase IDs and receipt images are excluded from this model payload and from traces.

The model must not infer waste, consumption, health, family circumstances, affordability or causes of price changes. A high spending rank is not proof of overspending. An over-target statement requires a relevant explicit budget, and the full budget dates remain visible even for selected subperiods.

## Loading, failure and freshness

- Show the section only for `purchase_count > 0`, including zero-net periods with confirmed purchases.
- Opening Analysis or expanding Spending overview makes no AI call.
- Empty periods and periods without actionable candidates make no model call.
- Provider/validation failures show a bounded set of verified checks and an explicit retry action.
- Date/account/household changes abort waiting and discard late responses. The provider may still finish a request already sent.
- Household query invalidation clears local insight state after app writes.
- Backend rechecks records and budget context after inference. Relevant changes return `409` and require regeneration.
- Results are local component state; there is no persistent/shared cache or automatic repeated generation.

The endpoint remains `POST /households/{household_id}/analysis/insights`, with `start_date` and `end_date`. Responses remain `ready`, `fallback` or `no_data`, with `Cache-Control: no-store`. The prompt version is `spending-insights-v2-actionable-patterns`.

Saved receipts remain a partial spending record. Missing records are not proof of zero real-world spending.

## Validation

On the current combined source: 218 backend tests and the frontend production build passed. Fifteen new backend regressions cover actionable conditions, comparable prices, missing targets, no-pattern/model-call behavior, valid subsets and required budget constraints. Ten component checks cover section placement, compact chat markup, explicit generation, planner actions, five-record history loading and removal of Home's duplicate list/request.

Model responses are simulated in automated tests. DOM checks do not certify desktop/mobile visual layout; live model and visual smoke testing remain separate.
