import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import type { QueryClient } from "@tanstack/react-query";
import { useQuery } from "@tanstack/react-query";
import type { Session } from "@supabase/supabase-js";
import { apiRequest, getErrorText } from "./api/client";
import type { components } from "./api/types";
import { hasSupabaseConfig, supabase } from "./auth/supabase";
import { CaptureFlow } from "./purchase/CaptureFlow";
import { DraftReview } from "./purchase/DraftReview";
import { clearPendingDraftSubmission, getDraftScopeKey, getSourceSnapshotKey } from "./purchase/workflow";

type HouseholdOption = {
  id: string;
  name: string;
  role: string;
};

type Page = "home" | "purchases";
type AppRoute = { kind: "main" } | { kind: "review"; draftId: string };
type PurchasePage = components["schemas"]["PurchasePage"];
type Purchase = components["schemas"]["PurchaseOut"];
type DraftOut = components["schemas"]["DraftOut"];

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

function PurchaseRow({ purchase }: { purchase: Purchase }) {
  const merchant = purchase.merchant?.trim() || "Purchase";
  return (
    <li className="purchase-record">
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

function Purchases({
  session,
  household,
}: {
  session: Session;
  household: HouseholdOption;
}) {
  const [offset, setOffset] = useState(0);
  const { data, error, isPending, isFetching, refetch } = usePurchasePage(session, household, offset);

  const total = data?.total ?? 0;
  const pageCount = Math.ceil(total / pageSize);

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
              {data.purchases.map((purchase) => <PurchaseRow key={purchase.id} purchase={purchase} />)}
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
  onDraftCreated: (draft: DraftOut, sourceText: string) => void;
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
  const sourceSnapshots = useRef(new Map<string, string>());
  const [authLoading, setAuthLoading] = useState(true);
  const [authError, setAuthError] = useState<string | null>(null);
  const [page, setPage] = useState<Page>("home");
  const [route, setRoute] = useState<AppRoute>(currentRoute);
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
    await queryClient.cancelQueries({ queryKey: ["user", session.user.id, "household"] });
    queryClient.removeQueries({ queryKey: ["user", session.user.id, "household"] });
    sourceSnapshots.current.clear();
    clearPendingDraftSubmission();
    setSelectedHouseholdId(householdId);
    if (route.kind === "review") {
      window.history.pushState(null, "", "/");
      setRoute({ kind: "main" });
      setPage("home");
    }
  }

  function navigateToPage(nextPage: Page) {
    if (route.kind === "review") {
      window.history.pushState(null, "", "/");
      setRoute({ kind: "main" });
    }
    setPage(nextPage);
  }

  function navigateToDraft(draft: DraftOut, sourceText: string) {
    if (!selectedHousehold || !session) return;
    clearPendingDraftSubmission(getDraftScopeKey(session.user.id, selectedHousehold.id));
    sourceSnapshots.current.set(
      getSourceSnapshotKey(session.user.id, selectedHousehold.id, draft.id),
      sourceText,
    );
    const nextPath = `/review/${encodeURIComponent(draft.id)}`;
    window.history.pushState(null, "", nextPath);
    setRoute({ kind: "review", draftId: draft.id });
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
    <div className="app-shell">
      <aside className="sidebar">
        <a className="wordmark" href="#home" onClick={(event) => { event.preventDefault(); setPage("home"); }}>
          <span className="brand-mark" aria-hidden="true" />
          bonwise
        </a>
        <nav className="side-navigation" aria-label="Main navigation">
          <button className={page === "home" ? "nav-link active" : "nav-link"} onClick={() => navigateToPage("home")}>Home</button>
          <button className={page === "purchases" ? "nav-link active" : "nav-link"} onClick={() => navigateToPage("purchases")}>Purchases</button>
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
          <span className="topbar-page">{page === "home" ? "Home" : "Purchases"}</span>
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
              session={session}
              householdId={selectedHousehold.id}
              draftId={route.draftId}
              sourceText={sourceSnapshots.current.get(
                getSourceSnapshotKey(session.user.id, selectedHousehold.id, route.draftId),
              )}
              onBackToPurchases={() => navigateToPage("purchases")}
              onReenterSource={() => navigateToPage("home")}
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
            <Purchases session={session} household={selectedHousehold} />
          )}
        </main>
        <nav className="mobile-navigation" aria-label="Main navigation">
          <button className={page === "home" ? "mobile-nav-link active" : "mobile-nav-link"} onClick={() => navigateToPage("home")}>Home</button>
          <button className={page === "purchases" ? "mobile-nav-link active" : "mobile-nav-link"} onClick={() => navigateToPage("purchases")}>Purchases</button>
        </nav>
      </div>
    </div>
  );
}

export default App;
