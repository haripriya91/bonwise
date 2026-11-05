# Bonwise project instructions

Bonwise is a household purchase tracker and shopping assistant. React + Vite +
TypeScript frontend, FastAPI backend, Supabase authentication. Models propose;
code verifies; the user reviews and confirms purchases. Apply UI rules below to
frontend work only. Read existing repository instructions before changes.

## Scope and contracts
- Read docs/openapi.json and the requested task before coding. If missing or
  inconsistent with the backend, identify the issue; never invent an endpoint.
- Generate frontend/src/api/types.ts from OpenAPI. Use actual schema names.
  UI-only form/view state is allowed when explicitly mapped to API fields.
- Build only working features requested in the task. No fake budgets, offers,
  charts, success statuses, confidence scores or duplicate-check results.
- Today's navigation: Home and Purchases. Roadmap pages stay out of production UI.
- Preserve existing backend behavior, dependency locks and frontend configuration.
  Explain important decisions before editing. Never print secrets or open .env files
  to extract values; use documented variable names and .env.example instead.

## Visual language
- Match the original Bonwise prototype visual identity. Avoid a generic admin dashboard.
  Reference its layout, fonts, colors and density; do not copy simulated behavior.
- React, Tailwind custom theme, restyled shadcn/ui primitives. Use existing package
  versions. Do not paste setup/configuration for a different Tailwind version.
- CSS color tokens only: paper #F8F7F3; surface #FFFFFF; line #E6E3DF;
  ink #302535; muted #706573; accent #543663; lime #E3F09B;
  lavender #EEE9F3; sidebar #EEECE7; warn-bg #FBEFD5; warn-ink #7A4B07;
  danger #A3362B. Define values in one theme file. The muted color is deliberately
  darker than the old prototype for readable small text. Purple with white text is
  the primary action; lime is a restrained highlight with dark plum text.
- Manrope 600/700 for page titles and prominent totals; DM Sans for body/forms.
  Provide sensible local fallbacks. Use tabular numerals for aligned amounts.
- 4px spacing scale; typical section gap 24px and row gap 12px. Radius 8px for
  controls and 12px for main surfaces, matching the prototype. Hairlines and focus rings are exceptions.
- At most one container/card level. Use rows and dividers for item/history lists.
  No gradients, glass effects, decorative chart cards, avatars, emoji or slogans.
- One primary action for the active step. Secondary actions are restrained links
  or outline buttons. Icons only for navigation, input modes and useful controls
  or status indications; no decorative row/heading icons.
- One visual focus per screen. Review: current total. Home: purchase capture.
  Do not let a mock hero override balanced budget information when budgets exist.
- Keep text concise and functional. Do not add a subtitle beneath every label.

## Responsive and accessible
- Start at 360–430px; md 768px, lg 1024px. No accidental horizontal scrolling.
- Below 768px use a compact bottom navigation. Use a compact header navigation on
  tablets; from 1024px use the prototype's approximately 218px left rail. Show supported destinations only.
- Max content width about 1100px. Desktop must use available width purposefully.
- Touch targets >=44px; form font >=16px; real labels; keyboard operation; visible
  focus; error/status announcements; reduced-motion support; safe-area-aware
  footers and 100dvh. No content hidden behind fixed navigation or the keyboard.
- Small text contrast >=4.5:1. Do not rely on color alone. Use ink for category
  text. Decorative line token must not be the only input boundary/focus cue;
  use a high-contrast token such as muted for essential control outlines.

## Data, privacy and trust
- Category enum: groceries, household, personal_care, transport, dining_out, other.
  Labels: Groceries, Household, Personal care, Transport, Dining out, Other.
- Display amounts using Intl.NumberFormat('de-DE', {style:'currency',currency:'EUR'}).
  Keep integer cents at API boundaries and decimal-string quantities, e.g. 0,578 kg.
- Parse edited money without floating-point errors; blank means unknown, never 0.
  The backend's calculated_total_cents is authoritative. Any local preview uses
  exact decimal/integer arithmetic and is marked provisional after edits.
- Show deposits/discounts as adjustments, not also duplicated as purchase items.
- Only confirmed purchases count as recorded spending. Never treat history.total
  (a record count) as an amount or sum one page as all-time spending.
- Use Supabase's auth SDK; pass the access token to FastAPI. Household options come
  from /me. Never hardcode demo user/household IDs in application code.
- Keep backend secrets, model keys and privileged Storage access out of the browser.
  Do not persist receipt text or purchase forms in localStorage/sessionStorage or
  analytics. Do not manually store credentials/tokens outside the auth SDK.
- Privacy preview is not proof of anonymisation. Changing the reviewed text resets
  its acknowledgement. Require explicit review before sending to extraction.
- Keep the exact text submitted to extraction as a read-only source snapshot through
  purchase review, distinct from edited fields. Hold it only in scoped memory and
  clear on logout/household switch. Do not save it to browser storage or invent a
  source field in DraftOut. The API does not currently persist this source: on
  refresh/direct entry show an honest unavailable state and a way to supply/review
  the source again. Do not silently reconstruct it from extracted fields.
- Desktop review shows source left and editable fields right; mobile provides an
  accessible source viewer with edits preserved. Real images/PDFs use redacted
  copies and authenticated retrieval when document APIs are implemented.
- Keep request IDs stable when retrying draft creation. Use the current revision for
  PATCH/confirmation. On 409, reload and require review; never silently overwrite.
- Do not auto-confirm after recalculating an edited draft. Show the updated server
  total first, then request a separate explicit confirmation.
- Clear household/user-specific state and query caches on switch/logout; cancel or
  ignore stale in-flight responses. Use user/household-scoped cache keys.
- Production uses real APIs. Fixtures belong to isolated visual/test stories only,
  visibly labelled synthetic; never replace failed requests with fake success.

## Delivery
- Build one workflow at a time. Prefer focused components, not an arbitrary line cap.
- Include relevant loading, empty, error, long-running, missing-field, mismatch,
  stale-revision and success states. Do not invent unsupported states/fields.
- Run type/build checks and meaningful interaction checks. Inspect 390px and 1280px
  layouts. Report changed files, validation and any genuine blocker concisely.