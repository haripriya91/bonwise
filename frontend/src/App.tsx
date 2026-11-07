import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, FormEvent } from "react";
import type { QueryClient } from "@tanstack/react-query";
import { useQuery } from "@tanstack/react-query";
import type { Session } from "@supabase/supabase-js";
import { apiRequest, getErrorText } from "./api/client";
import type { components } from "./api/types";
import { hasSupabaseConfig, supabase } from "./auth/supabase";
import { AnalysisChatProvider, AskBonwise } from "./analysis/AskBonwise";
import { CaptureFlow } from "./purchase/CaptureFlow";
import { ConfirmedPurchaseDetails } from "./purchase/ConfirmedPurchaseDetails";
import { DraftReview } from "./purchase/DraftReview";
import { clearPendingDraftSubmission, getDraftScopeKey, getSourceSnapshotKey } from "./purchase/workflow";
import type { SourceContext } from "./purchase/workflow";

type HouseholdOption = {
  id: string;
  name: string;
  role: string;
};

type Page = "home" | "purchases" | "analysis";
type AppRoute =
  | { kind: "main" }
  | {
    kind: "review";
    draftId: string;
    receiptError?: string;
    requiresReview?: boolean;
    keepReviewedReceipt?: boolean;
  };
type PurchasePage = components["schemas"]["PurchasePage"];
type Purchase = components["schemas"]["PurchaseOut"];
type DraftOut = components["schemas"]["DraftOut"];
type SpendingSummaryData = components["schemas"]["SpendingSummary"];

const categoryLabels: Record<SpendingSummaryData["categories"][number]["category"], string> = {
  groceries: "Groceries",
  clothing: "Clothing & accessories",
  household: "Household",
  personal_care: "Personal care",
  transport: "Transport",
  dining_out: "Dining out",
  other: "Other",
};

const pageSize = 20;
const currency = new Intl.NumberFormat("de-DE", {
  style: "currency",
  currency: "EUR",
});
const dateFormat = new Intl.DateTimeFormat("de-DE", {
  dateStyle: "medium",
});

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseHouseholds(value: unknown): HouseholdOption[] {
  if (!isRecord(value) || !Array.isArray(value.households)) {
    throw new Error("The account response is missing accessible households.");
  }

  return value.households.map((household, index) => {
    if (
      !isRecord(household) ||
      typeof household.id !== "string" ||
      typeof household.name !== "string" ||
      typeof household.role !== "string"
    ) {
      throw new Error(`The account response contains an invalid household at position ${index + 1}.`);
    }
    return { id: household.id, name: household.name, role: household.role };
  });
}

function formatAmount(cents: number): string {
  return currency.format(cents / 100);
}

function formatLocalDateInput(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function currentMonthDateRange() {
  const today = new Date();
  return {
    start_date: formatLocalDateInput(new Date(today.getFullYear(), today.getMonth(), 1)),
    end_date: formatLocalDateInput(today),
  };
}

function formatQuantity(quantity: string, unit: string): string {
  const localized = quantity.replace(".", ",");
  return `${localized} ${unit}`;
}

function currentRoute(): AppRoute {
  const match = /^\/review\/([^/]+)\/?$/.exec(window.location.pathname);
  if (!match) return { kind: "main" };
  try {
    return { kind: "review", draftId: decodeURIComponent(match[1]) };
  } catch {
    return { kind: "review", draftId: match[1] };
  }
}

function SignIn({
  onSignedIn,
  initialError,
}: {
  onSignedIn: () => void;
  initialError: string | null;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(initialError);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!supabase) return;
    setBusy(true);
    setError(null);
    try {
      const { error: signInError } = await supabase.auth.signInWithPassword({ email, password });
      if (signInError) {
        setError("Sign-in failed. Check your email and password, then try again.");
        return;
      }
      onSignedIn();
    } catch {
      setError("Sign-in failed. Check your email and password, then try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-page">
      <div className="auth-brand" aria-label="Bonwise">bonwise<span>.</span></div>
      <section className="auth-panel" aria-labelledby="signin-title">
        <h1 id="signin-title">Welcome back</h1>
        <p className="auth-copy">Sign in to your household account.</p>
        {!hasSupabaseConfig && (
          <p className="notice" role="alert">
            Authentication is not configured. Set the Supabase URL and publishable key in the frontend environment.
          </p>
        )}
        {error && <p className="error-message" role="alert">{error}</p>}
        <form className="sign-in-form" onSubmit={submit}>
          <label htmlFor="email">Email address</label>
          <input
            id="email"
            autoComplete="email"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
            disabled={!hasSupabaseConfig || busy}
          />
          <label htmlFor="password">Password</label>
          <input
            id="password"
            autoComplete="current-password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
            disabled={!hasSupabaseConfig || busy}
          />
          <button className="button button-primary sign-in-button" type="submit" disabled={!hasSupabaseConfig || busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </section>
    </main>
  );
}

function PurchaseRow({
  purchase,
  onViewDetails,
}: {
  purchase: Purchase;
  onViewDetails?: () => void;
}) {
  const merchant = purchase.merchant?.trim() || "Purchase";
  return (
    <li className="purchase-record">
      <div className={`purchase-record-row${onViewDetails ? " has-details-action" : ""}`}>
        <details className="purchase-details">
          <summary className="purchase-row">
            <span className="purchase-description">
              <span className="purchase-merchant">{merchant}</span>
              <span className="purchase-date">{dateFormat.format(new Date(`${purchase.purchased_on}T12:00:00`))}</span>
            </span>
            <span className="purchase-row-meta">
              <span className="purchase-total">{formatAmount(purchase.total_cents)}</span>
              <span className="expand-label">
                <span className="when-closed">View items</span>
                <span className="when-open">Hide items</span>
              </span>
            </span>
          </summary>
          <div className="item-breakdown">
            <div className="breakdown-header" aria-hidden="true">
              <span>Item</span>
              <span>Quantity</span>
              <span>Unit price</span>
              <span>Line total</span>
            </div>
            <ul className="breakdown-list" aria-label={`${merchant} items`}>
              {purchase.items.map((item) => (
                <li className="breakdown-row" key={item.id}>
                  <span className="breakdown-item">{item.original_text}</span>
                  <span data-label="Quantity">{formatQuantity(item.quantity, item.quantity_unit)}</span>
                  <span data-label="Unit price">{formatAmount(item.unit_price_cents)}</span>
                  <span data-label="Line total">{formatAmount(item.line_total_cents)}</span>
                </li>
              ))}
            </ul>
            {purchase.adjustments.length > 0 && (
              <ul className="adjustment-list" aria-label={`${merchant} adjustments`}>
                {purchase.adjustments.map((adjustment, index) => (
                  <li className="adjustment-row" key={`${adjustment.kind}-${adjustment.label}-${index}`}>
                    <span>{adjustment.label}</span>
                    <span>{adjustment.kind === "discount" ? "Discount" : "Deposit"}</span>
                    <span>{formatAmount(adjustment.amount_cents)}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </details>
        {onViewDetails && (
          <button className="text-link purchase-view-details" type="button" onClick={onViewDetails}>
            View details
          </button>
        )}
      </div>
    </li>
  );
}

function usePurchasePage(session: Session, household: HouseholdOption, offset = 0) {
  return useQuery({
    queryKey: ["user", session.user.id, "household", household.id, "purchases", offset, pageSize],
    queryFn: ({ signal }) =>
      apiRequest<PurchasePage>(
        `/households/${encodeURIComponent(household.id)}/purchases?limit=${pageSize}&offset=${offset}`,
        session.access_token,
        { signal },
      ),
  });
}

function useSpendingSummary(
  session: Session,
  household: HouseholdOption,
  startDate: string,
  endDate: string,
) {
  return useQuery({
    queryKey: [
      "user",
      session.user.id,
      "household",
      household.id,
      "spending-summary",
      startDate,
      endDate,
    ],
    queryFn: ({ signal }) =>
      apiRequest<SpendingSummaryData>(
        `/households/${encodeURIComponent(household.id)}/spending-summary?start_date=${encodeURIComponent(startDate)}&end_date=${encodeURIComponent(endDate)}`,
        session.access_token,
        { signal },
      ),
  });
}

function Analysis({
  session,
  household,
}: {
  session: Session;
  household: HouseholdOption;
}) {
  const [initialRange] = useState(currentMonthDateRange);
  const [startDate, setStartDate] = useState(initialRange.start_date);
  const [endDate, setEndDate] = useState(initialRange.end_date);
  const [appliedRange, setAppliedRange] = useState(initialRange);
  const [overviewExpanded, setOverviewExpanded] = useState(true);
  const rangeError = startDate && endDate && endDate < startDate
    ? "To date must be on or after From date."
    : null;
  const { data, error, isPending, isFetching, refetch } = useSpendingSummary(
    session,
    household,
    appliedRange.start_date,
    appliedRange.end_date,
  );

  function applyDateRange(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (rangeError || !startDate || !endDate) return;
    setAppliedRange({ start_date: startDate, end_date: endDate });
  }

  const categories = data?.categories ?? [];
  const highestAmount = Math.max(0, ...categories.map(({ total_cents }) => total_cents));
  const lowestAmount = Math.min(0, ...categories.map(({ total_cents }) => total_cents));
  const amountRange = highestAmount - lowestAmount;
  const verticalZero = amountRange > 0 ? highestAmount / amountRange : 1;
  const horizontalZero = amountRange > 0 ? Math.abs(lowestAmount) / amountRange : 0;

  return (
    <section className="page-section analysis-page" aria-labelledby="spending-summary-title">
      <header className="analysis-page-heading">
        <div className="analysis-page-title">
          <p className="analysis-eyebrow">Spending insights · confirmed purchases</p>
          <h1 id="spending-summary-title">Spending analysis</h1>
        </div>
        <div className="analysis-period-controls">
          <form className="spending-date-form" onSubmit={applyDateRange}>
            <label>
              <span>From</span>
              <input
                type="date"
                value={startDate}
                onChange={(event) => setStartDate(event.target.value)}
                required
              />
            </label>
            <label>
              <span>To</span>
              <input
                type="date"
                value={endDate}
                onChange={(event) => setEndDate(event.target.value)}
                required
                aria-invalid={Boolean(rangeError)}
                aria-describedby={rangeError ? "spending-date-error" : undefined}
              />
            </label>
            <button className="button button-primary spending-apply-button" type="submit" disabled={Boolean(rangeError) || !startDate || !endDate}>
              Apply
            </button>
          </form>
          {rangeError && <p className="spending-date-error" id="spending-date-error" role="alert">{rangeError}</p>}
        </div>
      </header>
      <section className="analysis-overview-panel" aria-labelledby="spending-overview-title">
        <button
          className="analysis-panel-toggle"
          type="button"
          aria-expanded={overviewExpanded}
          aria-controls="spending-overview-content"
          onClick={() => setOverviewExpanded((expanded) => !expanded)}
        >
          <span className="analysis-panel-title" id="spending-overview-title" role="heading" aria-level={2}>Spending overview</span>
          {!overviewExpanded && (
            <span className="analysis-collapsed-summary">
              <span>
                {dateFormat.format(new Date(`${appliedRange.start_date}T12:00:00`))} –{" "}
                {dateFormat.format(new Date(`${appliedRange.end_date}T12:00:00`))}
              </span>
              <strong>{data ? formatAmount(data.total_cents) : isPending ? "Loading total…" : "Total unavailable"}</strong>
            </span>
          )}
          <svg className="analysis-panel-chevron" aria-hidden="true" viewBox="0 0 20 20">
            <path d="m5 7.5 5 5 5-5" />
          </svg>
        </button>
        <div id="spending-overview-content" className="analysis-overview-content" hidden={!overviewExpanded}>
        {isPending && <p className="status-message" role="status">Loading spending analysis…</p>}
        {error && (
          <div className="error-block" role="alert">
            <p>{getErrorText(error)}</p>
            <button className="button button-outline" onClick={() => void refetch()} disabled={isFetching}>
              {isFetching ? "Trying again…" : "Try again"}
            </button>
          </div>
        )}
        {data && (
          <>
            <div className="analysis-total-row">
              <div className="analysis-total">
                <span>Confirmed spending</span>
                <strong>{formatAmount(data.total_cents)}</strong>
              </div>
              <div className="analysis-purchase-count">
                <strong>{data.purchase_count}</strong>
                <span>{data.purchase_count === 1 ? "purchase" : "purchases"}</span>
              </div>
            </div>
            {data.purchase_count === 0 && (
              <p className="analysis-empty" role="status">No confirmed purchases in this period.</p>
            )}
            <section className="analysis-category-surface" aria-labelledby="category-chart-title">
              <div className="analysis-category-heading">
                <div>
                  <h2 id="category-chart-title">Spending by category</h2>
                  <p>Category totals from confirmed purchases</p>
                </div>
                <strong>{formatAmount(categories.reduce((sum, item) => sum + item.total_cents, 0))} total</strong>
              </div>
              <ul
                className="analysis-category-chart"
                aria-label="Category spending chart"
                style={{ "--vertical-zero": `${verticalZero * 100}%`, "--horizontal-zero": `${horizontalZero * 100}%` } as CSSProperties}
              >
                {categories.map((category) => {
                  const amount = category.total_cents;
                  const barSize = amountRange > 0 ? Math.abs(amount) / amountRange * 100 : 0;
                  const verticalTop = amount >= 0 ? (verticalZero - barSize / 100) * 100 : verticalZero * 100;
                  const horizontalLeft = amount >= 0 ? horizontalZero * 100 : (horizontalZero - barSize / 100) * 100;
                  return (
                    <li className="analysis-category-bar" key={category.category}>
                      <strong className="analysis-category-amount">{formatAmount(amount)}</strong>
                      <div className="analysis-bar-plot" aria-hidden="true">
                        <span className="analysis-zero-axis" />
                        <span
                          className={`analysis-bar${amount < 0 ? " negative" : amount === 0 ? " zero" : ""}`}
                          style={{
                            "--vertical-top": `${verticalTop}%`,
                            "--bar-size": `${barSize}%`,
                            "--horizontal-left": `${horizontalLeft}%`,
                          } as CSSProperties}
                        />
                      </div>
                      <span className="analysis-category-label">{categoryLabels[category.category]}</span>
                    </li>
                  );
                })}
              </ul>
              {isFetching && !isPending && <p className="analysis-refreshing" role="status">Updating analysis…</p>}
            </section>
          </>
        )}
        </div>
      </section>
      <AskBonwise
        session={session}
        household={household}
        startDate={appliedRange.start_date}
        endDate={appliedRange.end_date}
      />
    </section>
  );
}

function Purchases({
  session,
  household,
  selectedPurchaseId,
  onSelectPurchase,
  onBackToPurchases,
}: {
  session: Session;
  household: HouseholdOption;
  selectedPurchaseId: string | null;
  onSelectPurchase: (purchaseId: string) => void;
  onBackToPurchases: () => void;
}) {
  const [offset, setOffset] = useState(0);
  const { data, error, isPending, isFetching, refetch } = usePurchasePage(session, household, offset);

  const total = data?.total ?? 0;
  const pageCount = Math.ceil(total / pageSize);

  if (selectedPurchaseId) {
    return (
      <ConfirmedPurchaseDetails
        key={selectedPurchaseId}
        session={session}
        householdId={household.id}
        purchaseId={selectedPurchaseId}
        onBack={onBackToPurchases}
      />
    );
  }

  return (
    <section className="page-section" aria-labelledby="purchases-title">
      <div className="section-heading">
        <h1 id="purchases-title">Purchases</h1>
        {data && <span className="record-count">{total} {total === 1 ? "record" : "records"}</span>}
      </div>
      <div className="records-surface">
        {isPending && <p className="status-message" role="status">Loading purchases…</p>}
        {error && (
          <div className="error-block" role="alert">
            <p>{getErrorText(error)}</p>
            <button className="button button-outline" onClick={() => void refetch()} disabled={isFetching}>
              {isFetching ? "Trying again…" : "Try again"}
            </button>
          </div>
        )}
        {!isPending && !error && data?.purchases.length === 0 && (
          <p className="empty-state">No confirmed purchases yet.</p>
        )}
        {data && data.purchases.length > 0 && (
          <>
            <ul className="purchase-list" aria-label="Purchase history">
              {data.purchases.map((purchase) => (
                <PurchaseRow
                  key={purchase.id}
                  purchase={purchase}
                  onViewDetails={() => onSelectPurchase(purchase.id)}
                />
              ))}
            </ul>
            {pageCount > 1 && (
              <div className="pagination">
                <span className="pagination-count">
                  {offset + 1}–{Math.min(offset + data.purchases.length, total)} of {total}
                </span>
                <div className="pagination-actions">
                  <button
                    className="button button-outline"
                    onClick={() => setOffset(Math.max(0, offset - pageSize))}
                    disabled={offset === 0 || isFetching}
                  >
                    Previous
                  </button>
                  <button
                    className="button button-outline"
                    onClick={() => setOffset(offset + pageSize)}
                    disabled={offset + data.purchases.length >= total || isFetching}
                  >
                    Next
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}

function Home({
  session,
  household,
  onViewPurchases,
  onDraftCreated,
}: {
  session: Session;
  household: HouseholdOption;
  onViewPurchases: () => void;
  onDraftCreated: (draft: DraftOut, source: SourceContext) => void;
}) {
  const { data, error, isPending, isFetching, refetch } = usePurchasePage(session, household);

  return (
    <section className="home-section" aria-labelledby="capture-title">
      <CaptureFlow
        session={session}
        householdId={household.id}
        onDraftCreated={onDraftCreated}
      />
      <section className="recent-section" aria-labelledby="recent-title">
        <div className="recent-heading">
          <h2 id="recent-title">Recent purchases</h2>
          <button className="text-link" onClick={onViewPurchases}>View all purchases</button>
        </div>
        {isPending && <p className="status-message" role="status">Loading recent purchases…</p>}
        {error && (
          <div className="error-block" role="alert">
            <p>{getErrorText(error)}</p>
            <button className="button button-outline" onClick={() => void refetch()} disabled={isFetching}>
              {isFetching ? "Trying again…" : "Try again"}
            </button>
          </div>
        )}
        {!isPending && !error && data?.purchases.length === 0 && (
          <p className="empty-state">No confirmed purchases yet.</p>
        )}
        {data && data.purchases.length > 0 && (
          <ul className="purchase-list recent-list" aria-label="Recent purchases">
            {data.purchases.slice(0, 3).map((purchase) => <PurchaseRow key={purchase.id} purchase={purchase} />)}
          </ul>
        )}
      </section>
    </section>
  );
}

function App({ queryClient }: { queryClient: QueryClient }) {
  const [session, setSession] = useState<Session | null>(null);
  const sessionUserId = useRef<string | null>(null);
  const sourceSnapshots = useRef(new Map<string, SourceContext>());
  const [authLoading, setAuthLoading] = useState(true);
  const [authError, setAuthError] = useState<string | null>(null);
  const [page, setPage] = useState<Page>("home");
  const [route, setRoute] = useState<AppRoute>(currentRoute);
  const [selectedPurchaseId, setSelectedPurchaseId] = useState<string | null>(null);
  const [selectedHouseholdId, setSelectedHouseholdId] = useState<string | null>(null);
  const [signingOut, setSigningOut] = useState(false);
  const [signOutError, setSignOutError] = useState<string | null>(null);

  useEffect(() => {
    if (!supabase) {
      setAuthLoading(false);
      return;
    }

    let active = true;
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      if (!active) return;
      setSession(nextSession);
      const nextUserId = nextSession?.user.id ?? null;
      if (nextUserId !== sessionUserId.current) {
        sessionUserId.current = nextUserId;
        sourceSnapshots.current.clear();
        clearPendingDraftSubmission();
        void queryClient.cancelQueries().finally(() => queryClient.clear());
        setSelectedHouseholdId(null);
      }
    });
    void supabase.auth.getSession()
    .then(({ data, error }) => {
      if (!active) return;
      if (error) setAuthError("Could not restore your sign-in. Please sign in again.");
      sessionUserId.current = data.session?.user.id ?? null;
      setSession(data.session);
      setAuthLoading(false);
    })
    .catch(() => {
      if (!active) return;
      setAuthError("Could not restore your sign-in. Please sign in again.");
      setAuthLoading(false);
    });

    return () => {
      active = false;
      subscription.unsubscribe();
    };
  }, [queryClient]);

  useEffect(() => {
    const handlePopState = () => setRoute(currentRoute());
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  const meQuery = useQuery({
    queryKey: ["user", session?.user.id, "me"],
    enabled: Boolean(session),
    queryFn: async ({ signal }) =>
      parseHouseholds(await apiRequest<unknown>("/me", session!.access_token, { signal })),
  });
  const households = meQuery.data ?? [];
  const selectedHousehold = useMemo(
    () => households.find((household) => household.id === selectedHouseholdId) ?? households[0],
    [households, selectedHouseholdId],
  );

  useEffect(() => {
    if (selectedHousehold && selectedHousehold.id !== selectedHouseholdId) {
      setSelectedHouseholdId(selectedHousehold.id);
    }
  }, [selectedHousehold, selectedHouseholdId]);

  async function changeHousehold(householdId: string) {
    if (householdId === selectedHouseholdId || !session) return;
    setSelectedHouseholdId(householdId);
    setSelectedPurchaseId(null);
    await queryClient.cancelQueries({ queryKey: ["user", session.user.id, "household"] });
    queryClient.removeQueries({ queryKey: ["user", session.user.id, "household"] });
    sourceSnapshots.current.clear();
    clearPendingDraftSubmission();
    if (route.kind === "review") {
      window.history.pushState(null, "", "/");
      setRoute({ kind: "main" });
      setPage("home");
    }
  }

  function navigateToPage(nextPage: Page) {
    setSelectedPurchaseId(null);
    if (route.kind === "review") {
      window.history.pushState(null, "", "/");
      setRoute({ kind: "main" });
    }
    setPage(nextPage);
  }

  function navigateToDraft(
    draft: DraftOut,
    source: SourceContext,
    receiptError?: string,
    requiresReview?: boolean,
    keepReviewedReceipt?: boolean,
  ) {
    if (!selectedHousehold || !session) return;
    clearPendingDraftSubmission(getDraftScopeKey(session.user.id, selectedHousehold.id));
    sourceSnapshots.current.set(
      getSourceSnapshotKey(session.user.id, selectedHousehold.id, draft.id),
      source,
    );
    const nextPath = `/review/${encodeURIComponent(draft.id)}`;
    window.history.pushState(null, "", nextPath);
    setRoute({ kind: "review", draftId: draft.id, receiptError, requiresReview, keepReviewedReceipt });
  }

  function openPurchaseDetails(purchaseId: string) {
    if (route.kind === "review") {
      window.history.pushState(null, "", "/");
      setRoute({ kind: "main" });
    }
    setPage("purchases");
    setSelectedPurchaseId(purchaseId);
  }

  async function signOut() {
    if (!supabase) return;
    setSigningOut(true);
    setSignOutError(null);
    try {
      const { error } = await supabase.auth.signOut();
      if (error) setSignOutError("Could not sign out. Please try again.");
    } catch {
      setSignOutError("Could not sign out. Please try again.");
    } finally {
      setSigningOut(false);
    }
  }

  if (authLoading) {
    return <main className="auth-page"><p className="status-message" role="status">Loading your account…</p></main>;
  }

  if (!session) {
    return <SignIn onSignedIn={() => setAuthError(null)} initialError={authError} />;
  }

  return (
    <AnalysisChatProvider
      key={`${session.user.id}:${selectedHousehold?.id ?? ""}`}
      session={session}
      householdId={selectedHousehold?.id ?? ""}
    >
    <div className="app-shell">
      <aside className="sidebar">
        <a className="wordmark" href="#home" onClick={(event) => { event.preventDefault(); setPage("home"); }}>
          <span className="brand-mark" aria-hidden="true" />
          bonwise
        </a>
        <nav className="side-navigation" aria-label="Main navigation">
          <button className={page === "home" ? "nav-link active" : "nav-link"} onClick={() => navigateToPage("home")}>Home</button>
          <button className={page === "purchases" ? "nav-link active" : "nav-link"} onClick={() => navigateToPage("purchases")}>Purchases</button>
          <button className={page === "analysis" ? "nav-link active" : "nav-link"} onClick={() => navigateToPage("analysis")}>Analysis</button>
        </nav>
        <div className="sidebar-account">
          {meQuery.isPending && <span className="household-loading">Loading household…</span>}
          {selectedHousehold && households.length > 1 && (
            <label className="household-select-label">
              <span className="visually-hidden">Active household</span>
              <select value={selectedHousehold.id} onChange={(event) => void changeHousehold(event.target.value)}>
                {households.map((household) => (
                  <option value={household.id} key={household.id}>{household.name}</option>
                ))}
              </select>
            </label>
          )}
          {selectedHousehold && households.length === 1 && (
            <span className="household-name">{selectedHousehold.name}</span>
          )}
          <button className="sign-out-button" onClick={() => void signOut()} disabled={signingOut}>
            {signingOut ? "Signing out…" : "Sign out"}
          </button>
        </div>
      </aside>
      <div className="workspace">
        <header className="app-header">
          <span className="topbar-household">{selectedHousehold?.name ?? "Your household"}</span>
          <span className="topbar-page">{page === "home" ? "Home" : page === "purchases" ? "Purchases" : "Analysis"}</span>
          <div className="mobile-account-controls">
            {selectedHousehold && households.length > 1 && (
              <label className="household-select-label">
                <span className="visually-hidden">Active household</span>
                <select value={selectedHousehold.id} onChange={(event) => void changeHousehold(event.target.value)}>
                  {households.map((household) => (
                    <option value={household.id} key={household.id}>{household.name}</option>
                  ))}
                </select>
              </label>
            )}
            {selectedHousehold && households.length === 1 && (
              <span className="mobile-household-name">{selectedHousehold.name}</span>
            )}
            <button className="mobile-sign-out" onClick={() => void signOut()} disabled={signingOut}>
              {signingOut ? "Signing out…" : "Sign out"}
            </button>
          </div>
        </header>
        <main className="main-content">
          {authError && <p className="error-message" role="alert">{authError}</p>}
          {signOutError && <p className="error-message" role="alert">{signOutError}</p>}
          {meQuery.error && (
            <div className="error-block" role="alert">
              <p>{getErrorText(meQuery.error)}</p>
              <button className="button button-outline" onClick={() => void meQuery.refetch()} disabled={meQuery.isFetching}>
                {meQuery.isFetching ? "Trying again…" : "Retry"}
              </button>
            </div>
          )}
          {!meQuery.error && !meQuery.isPending && households.length === 0 && (
            <p className="empty-state">No household is linked to this account yet.</p>
          )}
          {selectedHousehold && route.kind === "review" && (
            <DraftReview
              key={`${session.user.id}:${selectedHousehold.id}:${route.draftId}`}
              session={session}
              householdId={selectedHousehold.id}
              draftId={route.draftId}
              source={sourceSnapshots.current.get(
                getSourceSnapshotKey(session.user.id, selectedHousehold.id, route.draftId),
              )}
              initialReceiptError={route.receiptError}
              requiresReviewOnLoad={route.requiresReview}
              initialKeepReceipt={route.keepReviewedReceipt}
              onBackToPurchases={() => navigateToPage("purchases")}
              onReenterSource={() => navigateToPage("home")}
              onViewPurchase={openPurchaseDetails}
            />
          )}
          {selectedHousehold && route.kind === "main" && page === "home" && (
            <Home
              key={`${session.user.id}:${selectedHousehold.id}`}
              session={session}
              household={selectedHousehold}
              onViewPurchases={() => navigateToPage("purchases")}
              onDraftCreated={navigateToDraft}
            />
          )}
          {selectedHousehold && route.kind === "main" && page === "purchases" && (
            <Purchases
              key={`${session.user.id}:${selectedHousehold.id}`}
              session={session}
              household={selectedHousehold}
              selectedPurchaseId={selectedPurchaseId}
              onSelectPurchase={setSelectedPurchaseId}
              onBackToPurchases={() => setSelectedPurchaseId(null)}
            />
          )}
          {selectedHousehold && route.kind === "main" && page === "analysis" && (
            <Analysis
              key={`${session.user.id}:${selectedHousehold.id}`}
              session={session}
              household={selectedHousehold}
            />
          )}
        </main>
        <nav className="mobile-navigation" aria-label="Main navigation">
          <button className={page === "home" ? "mobile-nav-link active" : "mobile-nav-link"} onClick={() => navigateToPage("home")}>Home</button>
          <button className={page === "purchases" ? "mobile-nav-link active" : "mobile-nav-link"} onClick={() => navigateToPage("purchases")}>Purchases</button>
          <button className={page === "analysis" ? "mobile-nav-link active" : "mobile-nav-link"} onClick={() => navigateToPage("analysis")}>Analysis</button>
        </nav>
      </div>
    </div>
    </AnalysisChatProvider>
  );
}

export default App;
