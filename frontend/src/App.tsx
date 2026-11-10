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
import { ShoppingPlanner } from "./planner/ShoppingPlanner";
import { ActivityCalendar } from "./components/ActivityCalendar";
import { NavigationIcon } from "./components/NavigationIcon";
import { PrivacyNotes } from "./components/PrivacyNotes";

type HouseholdOption = {
  id: string;
  name: string;
  role: string;
};

type Page = "home" | "purchases" | "analysis" | "offers" | "household" | "planner";
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
      <PrivacyNotes />
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

function usePurchasePage(session: Session, household: HouseholdOption, offset = 0, limit = pageSize) {
  return useQuery({
    queryKey: ["user", session.user.id, "household", household.id, "purchases", offset, limit],
    queryFn: ({ signal }) =>
      apiRequest<PurchasePage>(
        `/households/${encodeURIComponent(household.id)}/purchases?limit=${limit}&offset=${offset}`,
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

function useOfferOptions(session: Session, household: HouseholdOption) {
  return useQuery({
    queryKey: ["user", session.user.id, "household", household.id, "offer-options"],
    queryFn: ({ signal }) =>
      apiRequest<components["schemas"]["OfferOptions"]>(
        `/households/${encodeURIComponent(household.id)}/offer-options`,
        session.access_token,
        { signal },
      ),
  });
}

function useShoppingOverview(session: Session, household: HouseholdOption) {
  return useQuery({
    queryKey: ["user", session.user.id, "household", household.id, "shopping-overview"],
    queryFn: ({ signal }) =>
      apiRequest<components["schemas"]["ShoppingOverview"]>(
        `/households/${encodeURIComponent(household.id)}/shopping-overview`,
        session.access_token,
        { signal },
      ),
  });
}

function useBudgets(session: Session, household: HouseholdOption) {
  return useQuery({
    queryKey: ["user", session.user.id, "household", household.id, "budgets"],
    queryFn: ({ signal }) =>
      apiRequest<components["schemas"]["BudgetPage"]>(
        `/households/${encodeURIComponent(household.id)}/budgets`,
        session.access_token,
        { signal },
      ),
  });
}

function formatDateShort(dateText: string) {
  const date = new Date(`${dateText}T12:00:00`);
  return new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "short" }).format(date);
}

function formatDateRange(startText: string, endText: string) {
  return `${formatDateShort(startText)}–${formatDateShort(endText)}`;
}

function formatMoney(cents: number | null | undefined) {
  if (typeof cents !== "number") return "—";
  return currency.format(cents / 100);
}

function shortestMoneyLabel(cents: number) {
  return new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR", maximumFractionDigits: 2 }).format(cents / 100);
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
  const [overviewExpanded, setOverviewExpanded] = useState(false);
  const budgetOverview = useShoppingOverview(session, household);
  const budget = budgetOverview.data?.budget;
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
      <section className="analysis-budget-summary" aria-label="Spending and budget at a glance">
        <article className="analysis-stat"><span>Recorded purchases</span><strong>{data ? formatMoney(data.total_cents) : "—"}</strong><small>Confirmed records · selected dates</small></article>
        <article className="analysis-stat"><span>Total budget</span><strong>{budget ? formatMoney(budget.amount_cents) : "—"}</strong><small>{budget ? formatDateRange(budget.start_date, budget.end_date) : "No current budget"}</small></article>
        <article className="analysis-stat"><span>Reserved commitments</span><strong>{budget ? formatMoney(budget.reserved_cents) : "—"}</strong><small>Not recorded as spending</small></article>
        <article className="analysis-stat available"><span>Available to plan</span><strong>{budget ? formatMoney(budget.available_cents) : "—"}</strong><small>Current budget after spending & reservations</small></article>
      </section>
      {budgetOverview.error && <p className="field-error" role="alert">Budget figures could not be loaded. <button className="text-link" onClick={() => void budgetOverview.refetch()}>Try again</button></p>}
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

function OfferCardRow({
  offer,
  productName,
  expanded,
  onToggle,
}: {
  offer: components["schemas"]["OfferCard"];
  productName: string;
  expanded: boolean;
  onToggle: () => void;
}) {
  const validity = `${formatDateShort(offer.valid_from)}–${formatDateShort(offer.valid_until)}`;

  return (
    <div className="offer-row">
      <div className="offer-row-main">
        <div className="offer-meta-block">
          <div className="offer-merchant-badge">{offer.chain}</div>
          <div className="offer-name-wrap">
            <div className="offer-product-line">
              <strong>{productName}</strong>
              {offer.relation === "compatible_alternative" && <span className="offer-tag minor">Compatible alternative</span>}
            </div>
            <span className="offer-subline">{offer.brand ?? "National offer"}</span>
          </div>
        </div>
        <div className="offer-price-block">
          <div className="offer-price-line">
            <strong>{offer.required_packs > 1 ? `${offer.required_packs} packs for ${shortestMoneyLabel(offer.advertised_price_cents)}` : `${shortestMoneyLabel(offer.advertised_price_cents)}`}</strong>
            {offer.is_best_unit_price && <span className="offer-tag success">Lowest public price</span>}
          </div>
          <span className="offer-unit">{offer.required_packs === 1 ? "Per pack" : `${offer.required_packs} packs`}</span>
          <span className="offer-unit">{offer.unit_price_eur} / {offer.comparison_unit}</span>
        </div>
        <div className="offer-conditions-block">
          <div className="offer-validity">{offer.membership_required ? "App/card required" : "National offer"}</div>
          <div className="offer-validity">Valid {validity}</div>
          <button type="button" className="text-link compact-link" onClick={onToggle}>
            {expanded ? "Hide conditions" : "Conditions"}
          </button>
        </div>
      </div>
      {expanded && (
        <div className="offer-condition-panel">
          <div className="offer-condition-grid">
            <div><span>Validity</span><strong>{validity}</strong></div>
            <div><span>Coverage</span><strong>National scope</strong></div>
            <div><span>Bundle</span><strong>{offer.required_packs > 1 ? `Requires ${offer.required_packs} packs` : "Single pack"}</strong></div>
            <div><span>Deposit</span><strong>{offer.deposit_total_cents == null ? "Deposit information not provided" : formatMoney(offer.deposit_total_cents)}</strong></div>
            {offer.minimum_checkout_cents != null && (
              <div><span>Minimum cost</span><strong>{formatMoney(offer.minimum_checkout_cents)} including deposit</strong></div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function OffersPage({
  session,
  household,
}: {
  session: Session;
  household: HouseholdOption;
}) {
  const { data, isPending, error, refetch } = useOfferOptions(session, household);
  const [selectedProductIds, setSelectedProductIds] = useState<string[]>([]);
  const [includeAlternatives, setIncludeAlternatives] = useState(false);
  const [compareError, setCompareError] = useState<string | null>(null);
  const [compareBusy, setCompareBusy] = useState(false);
  const [compareData, setCompareData] = useState<components["schemas"]["CompareOut"] | null>(null);
  const [search, setSearch] = useState("");
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [activeSuggestionIndex, setActiveSuggestionIndex] = useState(-1);
  const [expandedOffers, setExpandedOffers] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (!data) return;
    setSelectedProductIds((current) => {
      if (current.length > 0) return current;
      return data.suggested_product_ids.length > 0 ? [...data.suggested_product_ids] : [];
    });
  }, [data]);

  const products = data?.products ?? [];
  const matchingProducts = useMemo(() => {
    const cleaned = search.trim().toLowerCase();
    const candidates = products.filter((product) => {
      const text = `${product.name} ${product.brand ?? ""} ${product.pack_base_quantity ?? ""}`.toLowerCase();
      return !cleaned || text.includes(cleaned);
    });
    return candidates.slice(0, 8);
  }, [products, search]);

  const suggestionPicks = useMemo(() => {
    const ranked = data?.suggested_product_ids
      ?.map((id) => products.find((product) => product.id === id))
      .filter((product): product is NonNullable<typeof product> => Boolean(product)) ?? [];

    if (ranked.length > 0) return ranked.slice(0, 4);

    const generic = ["Milk", "Yoghurt", "Bread", "Eggs"]
      .map((name) => products.find((product) => product.name.toLowerCase().includes(name.toLowerCase())))
      .filter((product): product is NonNullable<typeof product> => Boolean(product));

    return generic.slice(0, 4);
  }, [data, products]);

  const selectedProducts = selectedProductIds
    .map((id) => products.find((product) => product.id === id))
    .filter((product): product is NonNullable<typeof product> => Boolean(product));

  function toggleProduct(productId: string) {
    setSelectedProductIds((current) => {
      if (current.includes(productId)) {
        return current.filter((id) => id !== productId);
      }
      return [...current, productId];
    });
    setSearch("");
    setShowSuggestions(false);
    setActiveSuggestionIndex(-1);
  }

  function handleSuggestionKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (!matchingProducts.length) return;

    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveSuggestionIndex((current) => (current + 1) % matchingProducts.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveSuggestionIndex((current) => (current <= 0 ? matchingProducts.length - 1 : current - 1));
    } else if (event.key === "Enter") {
      if (activeSuggestionIndex >= 0 && activeSuggestionIndex < matchingProducts.length) {
        event.preventDefault();
        toggleProduct(matchingProducts[activeSuggestionIndex].id);
      }
    } else if (event.key === "Escape") {
      setShowSuggestions(false);
      setActiveSuggestionIndex(-1);
    }
  }

  async function submitCompare() {
    if (!data || selectedProductIds.length === 0) return;
    try {
      setCompareBusy(true);
      setCompareError(null);
      const response = await apiRequest<components["schemas"]["CompareOut"]>(
        `/households/${encodeURIComponent(household.id)}/offers/compare`,
        session.access_token,
        { method: "POST", body: { product_ids: selectedProductIds, include_alternatives: includeAlternatives } },
      );
      setCompareData(response);
    } catch (requestError) {
      setCompareError(getErrorText(requestError));
    } finally {
      setCompareBusy(false);
    }
  }

  return (
    <section className="offers-page" aria-labelledby="offers-page-title">
      <header className="compare-header">
        <div>
          <h1 id="offers-page-title">{data && data.history_linked_product_count > 0 ? "Offers for your usual buys" : "Compare this week’s offers"}</h1>
          <p className="compare-subtitle">
            {data && data.history_linked_product_count > 0
              ? "Based on your confirmed purchases from the last 90 days."
              : "Choose a product to compare offers. Accept product matches in purchase review to get suggestions here."}
          </p>
        </div>
        {data && (
          <div className="offer-range-label">
            <span>{formatDateRange(data.week_start, data.week_end)}</span>
            <strong>This week</strong>
          </div>
        )}
      </header>

      {data && (
        <div className="compare-selector">
          <div className="comparison-toolbar">
            <div className="product-search-wrap">
              <label className="sr-only" htmlFor="product-search">Search products</label>
              <input
                id="product-search"
                type="search"
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  setShowSuggestions(true);
                  setActiveSuggestionIndex(-1);
                }}
                onFocus={() => setShowSuggestions(true)}
                onBlur={() => window.setTimeout(() => setShowSuggestions(false), 120)}
                onKeyDown={handleSuggestionKeyDown}
                placeholder="Search products"
                aria-label="Search products"
                aria-autocomplete="list"
                aria-expanded={showSuggestions && matchingProducts.length > 0}
              />
              {showSuggestions && matchingProducts.length > 0 && (
                <ul className="autocomplete-list" role="listbox" aria-label="Matching products">
                  {matchingProducts.map((product, index) => (
                    <li key={product.id} role="option" aria-selected={activeSuggestionIndex === index}>
                      <button
                        type="button"
                        className={activeSuggestionIndex === index ? "autocomplete-item active" : "autocomplete-item"}
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={() => toggleProduct(product.id)}
                      >
                        <span className="autocomplete-name">{product.name}</span>
                        <span className="autocomplete-meta">
                          {product.brand ?? "Catalogue product"}
                          {product.pack_base_quantity ? ` · ${product.pack_base_quantity} ${product.base_unit ?? "unit"}` : ""}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <label className="simple-check">
              <input type="checkbox" checked={includeAlternatives} onChange={(event) => setIncludeAlternatives(event.target.checked)} />
              <span>Include compatible alternatives</span>
            </label>
            <button className="button button-primary compact-button" type="button" onClick={() => void submitCompare()} disabled={compareBusy || selectedProductIds.length === 0}>
              {compareBusy ? "Comparing…" : "Compare"}
            </button>
          </div>

          {selectedProducts.length > 0 && (
            <div className="selected-product-strip" aria-live="polite">
              {selectedProducts.map((product) => (
                <button key={product.id} type="button" className="selected-product-chip" onClick={() => toggleProduct(product.id)}>
                  {product.name}
                  <span aria-hidden="true">×</span>
                </button>
              ))}
            </div>
          )}

          {suggestionPicks.length > 0 && (
            <div className="suggestion-shortcuts" aria-label="Suggested products">
              <span className="shortcut-label">Suggestions:</span>
              {suggestionPicks.map((product) => (
                <button key={product.id} type="button" className="shortcut-chip" onClick={() => toggleProduct(product.id)}>
                  {product.name}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {isPending && <p className="status-message" role="status">Loading offers…</p>}
      {error && (
        <div className="error-block" role="alert">
          <p>{getErrorText(error)}</p>
          <button className="button button-outline" onClick={() => void refetch()}>
            Retry
          </button>
        </div>
      )}
      {compareError && <p className="error-message" role="alert">{compareError}</p>}

      {compareData && compareData.results.length > 0 && compareData.results.map((result) => (
        <div className="offers-results-area" key={result.product_id}>
          <div className="result-header-row">
            <h2>{result.product_name}</h2>
          </div>
          {result.status === "no_compatible_offer" ? (
            <p className="empty-offer-message">No compatible offer this week in the national listings.</p>
          ) : result.offers.map((offer) => (
            <OfferCardRow
              key={offer.offer_id}
              offer={offer}
              productName={result.product_name}
              expanded={Boolean(expandedOffers[offer.offer_id])}
              onToggle={() => setExpandedOffers((current) => ({ ...current, [offer.offer_id]: !current[offer.offer_id] }))}
            />
          ))}
        </div>
      ))}
    </section>
  );
}

function Home({
  session,
  household,
  onViewPurchases,
  onDraftCreated,
  onGoToBudget,
  onGoToOffers,
  onGoToPlan,
}: {
  session: Session;
  household: HouseholdOption;
  onViewPurchases: () => void;
  onDraftCreated: (draft: DraftOut, source: SourceContext) => void;
  onGoToBudget: () => void;
  onGoToOffers: () => void;
  onGoToPlan: (date?: string, planId?: string) => void;
}) {
  const { data, error, isPending, isFetching, refetch } = usePurchasePage(session, household);
  const overviewQuery = useShoppingOverview(session, household);
  const overview = overviewQuery.data;
  const offerRecommendations = overview?.offers?.recommendations ?? [];

  return (
    <section className="home-section" aria-labelledby="capture-title">
      <div className="home-top-row">
        <CaptureFlow
          session={session}
          householdId={household.id}
          onDraftCreated={onDraftCreated}
        />

        <aside className="budget-context-panel" aria-live="polite">
          {overviewQuery.isPending ? (
            <p className="status-message" role="status">Loading budget overview…</p>
          ) : overview ? (
            <>
              <div className="budget-context-header">
                <div className="budget-context-period">
                  <span>Period</span>
                  <strong>
                    {overview.spending_start && overview.spending_end
                      ? `${formatDateRange(overview.spending_start, overview.spending_end)}`
                      : "This month"}
                  </strong>
                </div>
                <button type="button" className="text-link compact-link" onClick={onGoToBudget}>Manage budget</button>
              </div>

              {overview.budget ? (
                <div className="budget-grid">
                  <div className="budget-stat-box">
                    <span>Budget</span>
                    <strong>{formatMoney(overview.budget.amount_cents)}</strong>
                  </div>
                  <div className="budget-stat-box">
                    <span>Confirmed spending</span>
                    <strong>{formatMoney(overview.budget.spent_cents)}</strong>
                  </div>
                  <div className="budget-stat-box">
                    <span>Reserved</span>
                    <strong>{formatMoney(overview.budget.reserved_cents)}</strong>
                  </div>
                  <div className="budget-stat-box available">
                    <span>Available</span>
                    <strong>{formatMoney(overview.budget.available_cents)}</strong>
                  </div>
                </div>
              ) : (
                <>
                  <div className="budget-grid single-stat">
                    <div className="budget-stat-box">
                      <span>Confirmed spending</span>
                      <strong>{formatMoney(overview.confirmed_spent_cents)}</strong>
                    </div>
                  </div>
                  <button type="button" className="button button-primary button-small" onClick={onGoToBudget}>Set your budget</button>
                </>
              )}
            </>
          ) : null}
        </aside>
      </div>

      {overviewQuery.error && (
        <div className="error-block" role="alert">
          <p>{getErrorText(overviewQuery.error)}</p>
          <button className="button button-outline" onClick={() => void overviewQuery.refetch()} disabled={overviewQuery.isFetching}>
            {overviewQuery.isFetching ? "Trying again…" : "Try again"}
          </button>
        </div>
      )}

      {overview && overview.offers_status === "ready" && (
        <section className="home-offers-panel" aria-labelledby="home-offers-title">
          <div className="recent-heading">
            <h2 id="home-offers-title">Offers for your usual buys</h2>
            <button className="text-link" onClick={onGoToOffers}>Browse offers</button>
          </div>
          {offerRecommendations.length > 0 ? (
            <div className="mini-offers-list">
              {offerRecommendations.slice(0, 3).map(({ product, comparison }) => (
                <div className="mini-offer" key={product.id}>
                  <div className="mini-offer-content">
                    <span className="mini-offer-name">{product.name}</span>
                    <span className="mini-offer-meta">{comparison.offers.length > 0 ? comparison.offers[0].chain : "No current offer"}</span>
                  </div>
                  {comparison.offers.length > 0 ? (
                    <div className="mini-offer-price">
                      <strong>{comparison.offers[0].required_packs > 1 ? `${comparison.offers[0].required_packs} packs` : shortestMoneyLabel(comparison.offers[0].advertised_price_cents)}</strong>
                      <span>{comparison.offers[0].required_packs > 1 ? `for ${shortestMoneyLabel(comparison.offers[0].advertised_price_cents)}` : "public price"}</span>
                    </div>
                  ) : (
                    <span className="mini-offer-price text-muted">No current offer</span>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <p className="empty-offer-message">Accepted product matches in purchase review enable recommendations here.</p>
          )}
        </section>
      )}

      {overview && overview.offers_status === "unavailable" && (
        <section className="home-offers-panel" aria-labelledby="home-offers-title">
          <div className="recent-heading">
            <h2 id="home-offers-title">Offers for your usual buys</h2>
          </div>
          <p className="empty-offer-message">Offers are temporarily unavailable. Please try again in a moment.</p>
          <button className="button button-outline" type="button" onClick={() => void overviewQuery.refetch()} disabled={overviewQuery.isFetching}>
            {overviewQuery.isFetching ? "Trying again…" : "Retry"}
          </button>
        </section>
      )}

      <section className="planner-home-card" aria-labelledby="home-plan-title">
        <div><p className="planner-eyebrow">WHEN YOU ARE READY TO SHOP</p><h2 id="home-plan-title">Start with a suggested weekly list.</h2>
          <p>Check what you need, change the list and review estimated costs before reserving funds.</p></div>
        <button className="button button-outline" onClick={() => onGoToPlan()}>Open shopping plan</button>
      </section>

      <ActivityCalendar session={session} householdId={household.id} onAddPurchase={() => document.querySelector<HTMLElement>(".capture-receipt-action")?.click()} onOpenPlan={(date, planId) => onGoToPlan(date, planId)} onManageCommitments={onGoToBudget} />

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

function HouseholdPage({
  session,
  household,
  onManageBudget,
}: {
  session: Session;
  household: HouseholdOption;
  onManageBudget: () => void;
}) {
  const budgetsQuery = useBudgets(session, household);
  const [selectedBudgetId, setSelectedBudgetId] = useState<string | null>(null);
  const [reserveTitle, setReserveTitle] = useState("Weekly groceries");
  const [reserveDate, setReserveDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [reserveAmountValue, setReserveAmountValue] = useState("100");
  const [budgetForm, setBudgetForm] = useState({ amount: "500", startDate: "", endDate: "" });
  const [budgetError, setBudgetError] = useState<string | null>(null);
  const [budgetBusy, setBudgetBusy] = useState(false);
  const [budgetEditOpen, setBudgetEditOpen] = useState(false);
  const [linkingCommitmentId, setLinkingCommitmentId] = useState<string | null>(null);
  const [linkedPurchaseId, setLinkedPurchaseId] = useState<string | null>(null);
  const [purchasesOffset, setPurchasesOffset] = useState(0);

  const purchasePage = usePurchasePage(session, household, purchasesOffset, 100);
  const selectedBudget = useMemo(() => {
    const budgets = budgetsQuery.data?.budgets ?? [];
    if (!budgets.length) return null;
    if (!selectedBudgetId) {
      const today = new Date();
      const current = budgets.find((budget) => {
        const start = new Date(`${budget.start_date}T12:00:00`);
        const end = new Date(`${budget.end_date}T12:00:00`);
        return today >= start && today <= end;
      }) ?? budgets[0];
      return current;
    }
    return budgets.find((budget) => budget.id === selectedBudgetId) ?? budgets[0];
  }, [budgetsQuery.data, selectedBudgetId]);

  useEffect(() => {
    if (selectedBudget && selectedBudget.id !== selectedBudgetId) {
      setSelectedBudgetId(selectedBudget.id);
    }
  }, [selectedBudget, selectedBudgetId]);

  useEffect(() => {
    if (!selectedBudget) return;
    const start = selectedBudget.start_date;
    const end = selectedBudget.end_date;
    if (!budgetForm.startDate && !budgetForm.endDate) {
      setBudgetForm({ amount: String(selectedBudget.amount_cents / 100), startDate: start, endDate: end });
    }
  }, [selectedBudget, budgetForm.startDate, budgetForm.endDate]);

  const filteredPurchases = (purchasePage.data?.purchases ?? []).filter((purchase) => {
    if (!selectedBudget) return false;
    const date = new Date(`${purchase.purchased_on}T12:00:00`);
    const start = new Date(`${selectedBudget.start_date}T12:00:00`);
    const end = new Date(`${selectedBudget.end_date}T12:00:00`);
    return date >= start && date <= end;
  });

  async function refreshBudgets() {
    await budgetsQuery.refetch();
    if (purchasePage.data && purchasePage.data.total > purchasePage.data.purchases.length) {
      setPurchasesOffset((current) => current + 100);
    }
  }

  async function createBudget(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBudgetBusy(true);
    setBudgetError(null);
    try {
      const requestBody = {
        client_request_id: crypto.randomUUID(),
        start_date: budgetForm.startDate || new Date(new Date().getFullYear(), new Date().getMonth(), 1).toISOString().slice(0, 10),
        end_date: budgetForm.endDate || new Date(new Date().getFullYear(), new Date().getMonth() + 1, 0).toISOString().slice(0, 10),
        amount_cents: Math.round(Number(budgetForm.amount) * 100),
      };
      await apiRequest<components["schemas"]["BudgetOut"]>(
        `/households/${encodeURIComponent(household.id)}/budgets`,
        session.access_token,
        { method: "POST", body: requestBody },
      );
      setBudgetForm({ amount: "500", startDate: "", endDate: "" });
      await refreshBudgets();
    } catch (requestError) {
      setBudgetError(getErrorText(requestError));
    } finally {
      setBudgetBusy(false);
    }
  }

  async function updateCurrentBudget() {
    if (!selectedBudget) return;
    setBudgetBusy(true);
    setBudgetError(null);
    try {
      await apiRequest<components["schemas"]["BudgetOut"]>(
        `/households/${encodeURIComponent(household.id)}/budgets/${selectedBudget.id}`,
        session.access_token,
        { method: "PATCH", body: { expected_version: selectedBudget.version, amount_cents: Math.round(Number(budgetForm.amount) * 100) } },
      );
      setBudgetEditOpen(false);
      await refreshBudgets();
    } catch (requestError) {
      setBudgetError(getErrorText(requestError));
    } finally {
      setBudgetBusy(false);
    }
  }

  async function submitReserveAmount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedBudget) return;
    setBudgetBusy(true);
    setBudgetError(null);
    try {
      await apiRequest<components["schemas"]["BudgetOut"]>(
        `/households/${encodeURIComponent(household.id)}/budgets/${selectedBudget.id}/commitments`,
        session.access_token,
        { method: "POST", body: { client_request_id: crypto.randomUUID(), expected_version: selectedBudget.version, title: reserveTitle, planned_on: reserveDate, amount_cents: Math.round(Number(reserveAmountValue) * 100) } },
      );
      setReserveAmountValue("100");
      setReserveTitle("Weekly groceries");
      await refreshBudgets();
    } catch (requestError) {
      setBudgetError(getErrorText(requestError));
    } finally {
      setBudgetBusy(false);
    }
  }

  async function cancelCommitment(commitmentId: string, expectedVersion: number) {
    if (!selectedBudget) return;
    setBudgetBusy(true);
    setBudgetError(null);
    try {
      await apiRequest<components["schemas"]["BudgetOut"]>(
        `/households/${encodeURIComponent(household.id)}/budgets/${selectedBudget.id}/commitments/${commitmentId}/cancel`,
        session.access_token,
        { method: "POST", body: { expected_version: expectedVersion } },
      );
      await refreshBudgets();
    } catch (requestError) {
      setBudgetError(getErrorText(requestError));
    } finally {
      setBudgetBusy(false);
    }
  }

  async function settleCommitment(commitmentId: string, expectedVersion: number, purchaseId: string) {
    if (!selectedBudget) return;
    setBudgetBusy(true);
    setBudgetError(null);
    try {
      await apiRequest<components["schemas"]["BudgetOut"]>(
        `/households/${encodeURIComponent(household.id)}/budgets/${selectedBudget.id}/commitments/${commitmentId}/settle`,
        session.access_token,
        { method: "POST", body: { expected_version: expectedVersion, purchase_id: purchaseId } },
      );
      setLinkedPurchaseId(null);
      setLinkingCommitmentId(null);
      await refreshBudgets();
    } catch (requestError) {
      setBudgetError(getErrorText(requestError));
    } finally {
      setBudgetBusy(false);
    }
  }

  if (budgetsQuery.isPending) {
    return <p className="status-message" role="status">Loading budget overview…</p>;
  }

  if (!selectedBudget) {
    return (
      <section className="household-page">
        <div className="household-empty">
          <h1>Budget & commitments</h1>
          <p>No budget exists yet. Create a monthly budget to track spending.</p>
          <form className="budget-create-form" onSubmit={createBudget}>
            <div className="budget-form-grid">
              <label>
                <span>Start date</span>
                <input type="date" value={budgetForm.startDate} onChange={(event) => setBudgetForm((current) => ({ ...current, startDate: event.target.value }))} />
              </label>
              <label>
                <span>End date</span>
                <input type="date" value={budgetForm.endDate} onChange={(event) => setBudgetForm((current) => ({ ...current, endDate: event.target.value }))} />
              </label>
              <label>
                <span>Budget amount (€)</span>
                <input type="number" min="0" step="0.01" value={budgetForm.amount} onChange={(event) => setBudgetForm((current) => ({ ...current, amount: event.target.value }))} />
              </label>
            </div>
            <div className="budget-create-actions">
              <button className="button button-primary" type="submit" disabled={budgetBusy}>Create budget</button>
            </div>
          </form>
        </div>
      </section>
    );
  }

  return (
    <section className="household-page" aria-labelledby="household-page-title">
      <header className="compare-header household-header">
        <h1 id="household-page-title">Budget & commitments</h1>
        <button type="button" className="button button-outline" onClick={() => {
          onManageBudget();
          setBudgetEditOpen((value) => !value);
        }}>{budgetEditOpen ? "Close" : "Manage budget"}</button>
      </header>

      {budgetError && <p className="error-message" role="alert">{budgetError}</p>}

      <div className="household-budget-row">
        <div className="period-inline">Period: {formatDateRange(selectedBudget.start_date, selectedBudget.end_date)}</div>
        <select value={selectedBudget.id} onChange={(event) => setSelectedBudgetId(event.target.value)}>
          {(budgetsQuery.data?.budgets ?? []).map((budget) => (
            <option key={budget.id} value={budget.id}>{formatDateRange(budget.start_date, budget.end_date)}</option>
          ))}
        </select>
      </div>

      <div className="budget-summary-grid">
        <div className="budget-summary-box"><span>Budget</span><strong>{formatMoney(selectedBudget.amount_cents)}</strong></div>
        <div className="budget-summary-box"><span>Confirmed spending</span><strong>{formatMoney(selectedBudget.spent_cents)}</strong></div>
        <div className="budget-summary-box"><span>Reserved</span><strong>{formatMoney(selectedBudget.reserved_cents)}</strong></div>
        <div className="budget-summary-box available"><span>Available</span><strong>{formatMoney(selectedBudget.available_cents)}</strong></div>
      </div>

      {budgetEditOpen && (
        <form
          className="budget-edit-form"
          onSubmit={(event) => {
            event.preventDefault();
            void updateCurrentBudget();
          }}
        >
          <div className="budget-edit-header">
            <h2>Manage budget</h2>
          </div>
          <div className="budget-form-grid">
            <label>
              <span>Budget amount (€)</span>
              <input type="number" min="0" step="0.01" value={budgetForm.amount} onChange={(event) => setBudgetForm((current) => ({ ...current, amount: event.target.value }))} />
            </label>
          </div>
          <div className="budget-create-actions">
            <button className="button button-primary" type="submit" disabled={budgetBusy}>Save</button>
            <button className="button button-outline" type="button" onClick={() => setBudgetEditOpen(false)}>Cancel</button>
          </div>
        </form>
      )}

      <div className="budget-card">
        <h2>Planned spending</h2>
        {selectedBudget.commitments.length === 0 ? <p className="empty-state">No planned spending yet.</p> : (
          <div className="commitment-list">
            {selectedBudget.commitments.map((commitment) => {
              const isOpen = commitment.status === "open";
              return (
                <div key={commitment.id} className="commitment-row">
                  <div className="commitment-title-block">
                    <span className="commitment-title">{commitment.title}</span>
                    <span className="commitment-date">{new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "short" }).format(new Date(`${commitment.planned_on}T12:00:00`))}</span>
                  </div>
                  <div className="commitment-amount">{formatMoney(commitment.amount_cents)}</div>
                  <div className="commitment-status">{commitment.status === "open" ? "Open" : commitment.status === "settled" ? "Settled" : "Cancelled"}</div>
                  {isOpen ? (
                    <div className="commitment-actions">
                      <button type="button" className="button button-outline" onClick={() => setLinkingCommitmentId(commitment.id)}>Link purchase</button>
                      <button type="button" className="button button-outline" onClick={() => void cancelCommitment(commitment.id, selectedBudget.version)}>Cancel</button>
                    </div>
                  ) : (
                    <div className="commitment-actions">
                      <span className="commitment-state-label">{commitment.status === "settled" ? "Already linked" : "Cancelled"}</span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      <form className="reserve-form" onSubmit={submitReserveAmount}>
        <h2>Reserve an amount</h2>
        <div className="reserve-grid">
          <label>
            <span>Planned title</span>
            <input value={reserveTitle} onChange={(event) => setReserveTitle(event.target.value)} />
          </label>
          <label>
            <span>Planned date</span>
            <input type="date" value={reserveDate} onChange={(event) => setReserveDate(event.target.value)} />
          </label>
          <label>
            <span>Amount</span>
            <input type="number" min="0" step="0.01" value={reserveAmountValue} onChange={(event) => setReserveAmountValue(event.target.value)} />
          </label>
          <button className="button button-primary" type="submit" disabled={budgetBusy}>Reserve</button>
        </div>
      </form>

      {linkingCommitmentId && (
        <div className="link-purchase-panel">
          <h3>Link purchase</h3>
          <select value={linkedPurchaseId ?? ""} onChange={(event) => setLinkedPurchaseId(event.target.value)}>
            <option value="">Choose a confirmed purchase</option>
            {filteredPurchases.map((purchase) => (
              <option key={purchase.id} value={purchase.id}>{purchase.merchant || "Purchase"} · {new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "short" }).format(new Date(`${purchase.purchased_on}T12:00:00`))}</option>
            ))}
          </select>
          <div className="link-actions">
            <button type="button" className="button button-primary" disabled={!linkedPurchaseId} onClick={() => {
              const commitment = selectedBudget.commitments.find((entry) => entry.id === linkingCommitmentId);
              if (!commitment || !linkedPurchaseId) return;
              void settleCommitment(commitment.id, selectedBudget.version, linkedPurchaseId);
            }}>
              Confirm link
            </button>
            <button type="button" className="button button-outline" onClick={() => { setLinkingCommitmentId(null); setLinkedPurchaseId(null); }}>Cancel</button>
          </div>
        </div>
      )}
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
  const [calendarPlan, setCalendarPlan] = useState<{date: string; id: string} | null>(null);
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
          <button className={page === "home" ? "nav-link active" : "nav-link"} onClick={() => navigateToPage("home")}><NavigationIcon name="home" /><span>Home</span></button>
          <button className={page === "purchases" ? "nav-link active" : "nav-link"} onClick={() => navigateToPage("purchases")}><NavigationIcon name="purchases" /><span>Purchases</span></button>
          <button className={page === "planner" ? "nav-link active" : "nav-link"} onClick={() => navigateToPage("planner")}><NavigationIcon name="planner" /><span>Shopping plan</span></button>
          <button className={page === "offers" ? "nav-link active" : "nav-link"} onClick={() => navigateToPage("offers")}><NavigationIcon name="offers" /><span>Compare offers</span></button>
          <button className={page === "analysis" ? "nav-link active" : "nav-link"} onClick={() => navigateToPage("analysis")}><NavigationIcon name="analysis" /><span>Analysis</span></button>
          <button className={page === "household" ? "nav-link active" : "nav-link"} onClick={() => navigateToPage("household")}><NavigationIcon name="household" /><span>My household</span></button>
        </nav>
        <div className="sidebar-privacy"><PrivacyNotes /></div>
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
          <span className="topbar-page">{page === "home" ? "Home" : page === "purchases" ? "Purchases" : page === "offers" ? "Compare offers" : page === "analysis" ? "Analysis" : page === "planner" ? "Shopping plan" : "My household"}</span>
          <div className="mobile-account-controls">
            <PrivacyNotes />
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
              onGoToBudget={() => navigateToPage("household")}
              onGoToOffers={() => navigateToPage("offers")}
              onGoToPlan={(date, id) => { setCalendarPlan(date && id ? {date, id} : null); navigateToPage("planner"); }}
            />
          )}
          {selectedHousehold && route.kind === "main" && page === "planner" && (
            <ShoppingPlanner
              initialDate={calendarPlan?.date}
              initialPlanId={calendarPlan?.id}
              key={`${session.user.id}:${selectedHousehold.id}`}
              session={session}
              householdId={selectedHousehold.id}
              onManageBudget={() => navigateToPage("household")}
              onViewPurchase={openPurchaseDetails}
            />
          )}
          {selectedHousehold && route.kind === "main" && page === "offers" && (
            <OffersPage
              key={`${session.user.id}:${selectedHousehold.id}`}
              session={session}
              household={selectedHousehold}
            />
          )}
          {selectedHousehold && route.kind === "main" && page === "household" && (
            <HouseholdPage
              key={`${session.user.id}:${selectedHousehold.id}`}
              session={session}
              household={selectedHousehold}
              onManageBudget={() => navigateToPage("household")}
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
          <button className={page === "home" ? "mobile-nav-link active" : "mobile-nav-link"} onClick={() => navigateToPage("home")}><NavigationIcon name="home" /><span>Home</span></button>
          <button className={page === "purchases" ? "mobile-nav-link active" : "mobile-nav-link"} onClick={() => navigateToPage("purchases")}><NavigationIcon name="purchases" /><span>Purchases</span></button>
          <button className={page === "planner" ? "mobile-nav-link active" : "mobile-nav-link"} onClick={() => navigateToPage("planner")}>Plan</button>
          <button className={page === "offers" ? "mobile-nav-link active" : "mobile-nav-link"} onClick={() => navigateToPage("offers")}>Offers</button>
          <button className={page === "analysis" ? "mobile-nav-link active" : "mobile-nav-link"} onClick={() => navigateToPage("analysis")}><NavigationIcon name="analysis" /><span>Analysis</span></button>
          <button className={page === "household" ? "mobile-nav-link active" : "mobile-nav-link"} onClick={() => navigateToPage("household")}>Household</button>
        </nav>
      </div>
    </div>
    </AnalysisChatProvider>
  );
}

export default App;
