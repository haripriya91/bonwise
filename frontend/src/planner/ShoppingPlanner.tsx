import { useEffect, useMemo, useRef, useState } from "react";
import { InformationTip } from "../components/InformationTip";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { Session } from "@supabase/supabase-js";
import { apiRequest, getErrorText } from "../api/client";
import type { components } from "../api/types";
import { dayInput, defaultShoppingDate, editable, euros, exportList, money, weekStart } from "./types";
import type { Item, Offer, Plan, ProductOption, TextPreview, Unit } from "./types";
import "./ShoppingPlanner.css";
import { UpcomingNeeds } from "./UpcomingNeeds";
import { useHouseholdSettings } from "../household/HouseholdSettings";

type PurchasePage = components["schemas"]["PurchasePage"];
const dateLabel = (value: string) => new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long" }).format(new Date(`${value}T12:00:00`));
const newItem = (): Item => ({ id: crypto.randomUUID(), product_id: null, name: "", quantity: null, unit: null,
  excluded: false, done: false, manual_unit_price_cents: null, offer_id: null, offer_batch_id: null });


function ProductNameInput({ session, householdId, item, disabled, invalid, onChange, onChoose }: {
  session: Session; householdId: string; item: Item; disabled: boolean; invalid?: string;
  onChange: (value: string) => void; onChoose: (product: ProductOption) => void;
}) {
  const [results, setResults] = useState<ProductOption[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [searching, setSearching] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [resultQuery, setResultQuery] = useState("");
  const query = item.name.trim().slice(0,120);
  useEffect(() => {
    setResults([]); setActive(-1); setFailure(null); setSearching(false);
    if (!open || item.product_id || query.length < 2 || disabled) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setSearching(true);
      void apiRequest<{ products: ProductOption[] }>(`/households/${householdId}/weekly-plans/products?q=${encodeURIComponent(query)}`, session.access_token, { signal: controller.signal })
        .then(data => { if (!controller.signal.aborted) {setResults(data.products);setResultQuery(query);} })
        .catch(error => { if (!controller.signal.aborted) setFailure(getErrorText(error)); })
        .finally(() => { if (!controller.signal.aborted) setSearching(false); });
    }, 350);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [query, item.product_id, open, disabled, session.access_token, householdId]);
  const visible = open && resultQuery === query ? results : [];
  function choose(product: ProductOption) { onChoose(product); setOpen(false); setResults([]); }
  return <div className="planner-name-field">
    <label htmlFor={`planner-name-${item.id}`}>Product</label>
    <input id={`planner-name-${item.id}`} value={item.name} maxLength={160} disabled={disabled}
      autoComplete="off" role="combobox" aria-autocomplete="list" aria-expanded={open && visible.length > 0}
      aria-controls={`planner-products-${item.id}`} aria-activedescendant={active >= 0 && visible[active] ? `planner-option-${item.id}-${active}` : undefined}
      aria-invalid={!!invalid} aria-describedby={invalid ? `planner-name-${item.id}-error` : `planner-search-note-${item.id}`}
      onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}
      onChange={event => {onChange(event.target.value);setOpen(true);}}
      onKeyDown={event => {
        if (event.key === "Escape") {setOpen(false);setActive(-1);}
        if (event.key === "ArrowDown" && visible.length) {event.preventDefault();setActive(value => (value+1)%visible.length);}
        if (event.key === "ArrowUp" && visible.length) {event.preventDefault();setActive(value => value<0?visible.length-1:(value-1+visible.length)%visible.length);}
        if (event.key === "Enter" && visible[active]) {event.preventDefault();choose(visible[active]);}
      }} />
    {invalid && <span className="field-error" id={`planner-name-${item.id}-error`}>{invalid}</span>}
    {open && !item.product_id && <div className="planner-product-picker">
      {searching && <p className="planner-hint" role="status">Looking for products…</p>}
      {failure && <p className="planner-hint">Suggestions unavailable. You can keep your own item name.</p>}
      <ul id={`planner-products-${item.id}`} role="listbox" aria-label="Matching products">{visible.map((product,index) => <li key={product.id} role="presentation"><button
        type="button" role="option" id={`planner-option-${item.id}-${index}`} aria-selected={active===index}
        onMouseDown={event => event.preventDefault()} onClick={() => choose(product)}>
        {product.name}{product.pack_base_quantity && product.base_unit ? ` · ${Number(product.pack_base_quantity)} ${product.base_unit}` : ""}
      </button></li>)}</ul>
      {!searching && !failure && query.length>=2 && resultQuery===query && !visible.length && <p className="planner-hint">No matching products. Keep your own wording.</p>}
    </div>}
    <p className="planner-hint" id={`planner-search-note-${item.id}`}>{item.product_id ? "Product selected." : "Select a suggestion or keep your own name. AI search: avoid personal details."}</p>
  </div>;
}

export function ShoppingPlanner({ session, householdId, onManageBudget, onViewPurchase, initialDate, initialPlanId }: {
  initialDate?: string; initialPlanId?: string;
  session: Session; householdId: string; onManageBudget: () => void; onViewPurchase: (id: string) => void;
}) {
  const queryClient = useQueryClient();
  const preferences = useHouseholdSettings(session, householdId);
  const [preferencesReady, setPreferencesReady] = useState(false);
  const base = `/households/${encodeURIComponent(householdId)}`;
  const [browseDate, setBrowseDate] = useState(() => initialDate ?? defaultShoppingDate());
  useEffect(() => {
    if (preferencesReady || preferences.isPending) return;
    if (!initialDate && preferences.data?.shopping_day != null) {
      const today = new Date(), weekday = (today.getDay()+6)%7;
      today.setDate(today.getDate()+(preferences.data.shopping_day-weekday+7)%7);
      setBrowseDate(dayInput(today));
    }
    setPreferencesReady(true);
  }, [preferences.isPending, preferences.data, preferencesReady, initialDate]);
  const week = weekStart(browseDate);
  const key = ["user", session.user.id, "household", householdId, "weekly-plans", week];
  const query = useQuery({ queryKey: key, enabled: preferencesReady,
    queryFn: ({ signal }) => apiRequest<{ plans: Plan[] }>(`${base}/weekly-plans?week_start=${week}`, session.access_token, { signal, cache: "no-store" }),
    refetchOnMount: "always",
  });
  const [plan, setPlan] = useState<Plan | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [plannedOn, setPlannedOn] = useState(browseDate);
  const [target, setTarget] = useState("");
  const [buffer, setBuffer] = useState("0");
  const [showCosts, setShowCosts] = useState(false);
  const [filter, setFilter] = useState<"open" | "done" | "all">("open");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorPlace, setErrorPlace] = useState<"page"|"save"|"text"|"review"|"budget">("page");
  const [fieldErrors, setFieldErrors] = useState<Record<string,string>>({});
  const [focusTarget, setFocusTarget] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [privacyReviewed, setPrivacyReviewed] = useState(false);
  const [preview, setPreview] = useState<TextPreview | null>(null);
  const [reserveValue, setReserveValue] = useState("");
  const [reviewed, setReviewed] = useState(false);
  const [unknownAcknowledged, setUnknownAcknowledged] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [commitmentOpen, setCommitmentOpen] = useState(false);
  const [commitTitle, setCommitTitle] = useState("");
  const [commitAmount, setCommitAmount] = useState("");
  const [commitDate, setCommitDate] = useState(browseDate);
  const [offers, setOffers] = useState<{ itemId: string; offers: Offer[]; message: string } | null>(null);
  const [linkOpen, setLinkOpen] = useState(false);
  const [purchaseId, setPurchaseId] = useState("");
  const [purchaseOffset, setPurchaseOffset] = useState(0);
  const [copyFallback, setCopyFallback] = useState<string | null>(null);
  const [needsOpen, setNeedsOpen] = useState(false);
  useEffect(() => { setNeedsOpen(false); }, [plan?.id, plan?.version]);
  const requests = useRef(new Set<AbortController>());
  const autoStarted = useRef(new Set<string>());
  const createIds = useRef(new Map<string, string>());
  const approvalRequest = useRef<{ signature: string; body: Record<string, unknown> } | null>(null);
  const commitmentRequest = useRef<{ signature: string; body: Record<string, unknown> } | null>(null);
  const reviewPanel = useRef<HTMLElement | null>(null);
  const errorPanel = useRef<HTMLParagraphElement | null>(null);
  const activePlan = useRef<Plan | null>(null);
  activePlan.current = plan;

  const localBody = useMemo(() => ({ planned_on: plannedOn, items: items.map(editable),
    target_cents: target.trim() ? euros(target) : null, buffer_cents: euros(buffer) }), [plannedOn, items, target, buffer]);
  const dirty = Boolean(plan && JSON.stringify(localBody) !== JSON.stringify({ planned_on: plan.planned_on,
    items: plan.content.items.map(editable), target_cents: plan.content.target_cents, buffer_cents: plan.content.buffer_cents }));
  const editablePlan = plan?.status === "draft" || plan?.status === "approved";
  const figures = plan?.content.summary;
  const purchases = useQuery({
    queryKey: ["user", session.user.id, "household", householdId, "planner-link-purchases", purchaseOffset], enabled: linkOpen,
    queryFn: ({ signal }) => apiRequest<PurchasePage>(`${base}/purchases?limit=100&offset=${purchaseOffset}`, session.access_token, { signal }),
  });

  useEffect(() => () => { for (const controller of requests.current) controller.abort(); }, []);

  useEffect(() => {
    if (!focusTarget) return;
    const frame = requestAnimationFrame(() => {
      const element = document.getElementById(focusTarget);
      element?.scrollIntoView({ behavior: "smooth", block: "center" });
      element?.focus({ preventScroll: true }); setFocusTarget(null);
    });
    return () => cancelAnimationFrame(frame);
  }, [focusTarget, expanded, busy]);

  useEffect(()=>{
    if(!error || errorPlace!=="page" || busy)return;
    errorPanel.current?.scrollIntoView({behavior:"smooth",block:"center"});
    errorPanel.current?.focus({preventScroll:true});
  },[error,errorPlace,busy]);

  function addItem() {
    // Reuse an unfinished blank row instead of creating invisible validation traps.
    const item = items.find(row => !row.name.trim()) ?? newItem();
    if (!items.some(row => row.id === item.id)) setItems(current => [...current,item]);
    setFilter("all"); setExpanded(item.id); setFocusTarget(`planner-name-${item.id}`);
  }

  function receive(saved: Plan) {
    if (saved.household_id !== householdId) throw new Error("The shopping list does not belong to this household.");
    setFieldErrors({});
    setPlan(saved); setItems(structuredClone(saved.content.items)); setPlannedOn(saved.planned_on);
    setTarget(saved.content.target_cents == null ? "" : String(saved.content.target_cents/100));
    setBuffer(String((saved.content.buffer_cents ?? 0)/100));
    const amount = saved.reservation_cents || saved.content.summary?.minimum_reservation_cents || 0;
    setReserveValue(amount > 0 ? String(amount/100) : "");
    setReviewed(false); setUnknownAcknowledged(false); setPreview(null); setConfirmCancel(false);
    queryClient.setQueryData<{ plans: Plan[] }>(key, old => ({ plans: [saved, ...(old?.plans ?? []).filter(row => row.id !== saved.id)] }));
  }

  useEffect(() => {
    if (!query.data || busy) return;
    const saved = query.data.plans.find(row => row.id === initialPlanId) ?? query.data.plans.find(row => row.status === "draft" || row.status === "approved") ?? query.data.plans[0];
    if (saved && (plan === null || weekStart(plan.planned_on) !== week)) receive(saved);
    // Server refreshes do not overwrite unsaved form edits. Explicit Reload does.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.data, busy, week]);

  async function perform(label: string, action: (signal: AbortSignal) => Promise<void>) {
    if (busy) return;
    const controller = new AbortController(); requests.current.add(controller);
    setBusy(label); setError(null); setMessage(null);
    setErrorPlace(label==="Saving list changes" ? "save" : /changes|suggestions/.test(label) ? "text" : /approving|Linking|Cancelling/.test(label) ? "review" : label==="Reserving planned spending" ? "budget" : "page");
    try { await action(controller.signal); }
    catch (failure) { if (!controller.signal.aborted) { setError(getErrorText(failure)); } }
    finally { requests.current.delete(controller); if (!controller.signal.aborted) setBusy(null); }
  }

  async function prepare(signal: AbortSignal, forceNew = false) {
    const scope = `${browseDate}:${forceNew ? "new" : "initial"}`;
    let id = createIds.current.get(scope);
    if (!id) { id = crypto.randomUUID(); createIds.current.set(scope, id); }
    const saved = await apiRequest<Plan>(`${base}/weekly-plans`, session.access_token, {
      method: "POST", signal, body: { client_request_id: id, planned_on: browseDate, force_new: forceNew },
    });
    if (!signal.aborted) {
      receive(saved);
      if (forceNew && saved.status !== "cancelled" && saved.status !== "settled") createIds.current.delete(scope);
    }
  }

  useEffect(() => {
    if (!preferencesReady || !query.data || query.data.plans.length || busy || autoStarted.current.has(week) || browseDate < dayInput(new Date())) return;
    autoStarted.current.add(week);
    void perform("Preparing your suggested list", signal => prepare(signal));
    // Initial preparation is idempotent on the server and is not repeated on errors.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.data, week, busy, preferencesReady]);

  async function reload(signal: AbortSignal) {
    if (!plan) { await query.refetch(); return; }
    const saved = await apiRequest<Plan>(`${base}/weekly-plans/${plan.id}`, session.access_token, { signal, cache: "no-store" });
    if (!signal.aborted) receive(saved);
  }

  function updateItem(id: string, patch: Partial<Item>) {
    setItems(current => current.map(item => item.id === id ? { ...item, ...patch } : item));
    setFieldErrors(current => Object.fromEntries(Object.entries(current).filter(([key]) => !key.includes(id))));
    setPreview(null); setReviewed(false); setMessage(null);
  }

  async function save(signal: AbortSignal) {
    if (!plan) return;
    const issues: Record<string,string> = {};
    if (localBody.buffer_cents === null) issues["planner-buffer"] = "Enter zero or a positive euro amount.";
    if (target.trim() && (localBody.target_cents === null || localBody.target_cents === 0)) issues["planner-target"] = "Enter a limit above zero, or leave it empty.";
    for (const item of items) {
      if (!item.name.trim()) issues[`planner-name-${item.id}`] = "Enter an item name or remove this row.";
      const quantity = item.quantity?.replace(",", ".");
      if (quantity !== undefined && (!/^\d+(?:\.\d{1,3})?$/.test(quantity) || Number(quantity)<=0 || Number(quantity)>1000))
        issues[`planner-quantity-${item.id}`] = "Use a quantity above zero, up to 1000, with at most three decimal places.";
      if (item.quantity !== null && !item.unit) issues[`planner-quantity-${item.id}`] = "Choose a unit for this quantity.";
    }
    setFieldErrors(issues);
    if (Object.keys(issues).length) {
      const first = Object.keys(issues)[0];
      const invalidItem = items.find(item => first.endsWith(item.id));
      if (invalidItem) {setExpanded(invalidItem.id);setFilter("all");}
      else document.getElementById(first)?.closest("details")?.setAttribute("open", "");
      setFocusTarget(first);
      return;
    }
    const saved = await apiRequest<Plan>(`${base}/weekly-plans/${plan.id}`, session.access_token, {
      method: "PATCH", signal, body: { expected_version: plan.version, ...localBody },
    });
    if (!signal.aborted) { receive(saved); setMessage("List changes saved. Review estimates before approval."); }
  }

  async function mutate(signal: AbortSignal, route: string, body: unknown) {
    if (!plan) return;
    const saved = await apiRequest<Plan>(`${base}/weekly-plans/${plan.id}/${route}`, session.access_token, { method: "POST", body, signal });
    if (!signal.aborted) {
      receive(saved);
      void queryClient.invalidateQueries({ queryKey: ["user", session.user.id, "household", householdId] });
    }
  }

  async function approve(signal: AbortSignal) {
    if (!plan?.budget) throw new Error("Create a budget covering this shopping date first.");
    const amount = euros(reserveValue);
    if (!amount || !reviewed) throw new Error("Check quantities and enter the amount to reserve.");
    const payload = { expected_version: plan.version, expected_budget_version: plan.budget.version,
      reservation_cents: amount, quantities_reviewed: true, acknowledge_unknown_prices: unknownAcknowledged };
    const signature = JSON.stringify(payload);
    if (approvalRequest.current?.signature !== signature) approvalRequest.current = { signature, body: { ...payload, client_request_id: crypto.randomUUID() } };
    await mutate(signal, "approve", approvalRequest.current.body);
  }

  function takeList(download: boolean) {
    if (!plan) return;
    const output = exportList(plan, showCosts);
    if (!download) {
      void navigator.clipboard?.writeText(output).then(() => setMessage("Shopping list copied."), () => setCopyFallback(output));
      if (!navigator.clipboard) setCopyFallback(output);
      return;
    }
    const url = URL.createObjectURL(new Blob([output], { type: "text/plain;charset=utf-8" }));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = `bonwise-shopping-${plan.planned_on}.txt`; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function shareList() {
    if(!plan || dirty)return;
    const output=exportList(plan,showCosts);
    if(!navigator.share){setCopyFallback(output);setMessage("Sharing is unavailable in this browser. Copy the list below instead.");return;}
    try {await navigator.share({title:"Bonwise shopping list",text:output});}
    catch(error){if(!(error instanceof DOMException && error.name==="AbortError")){setCopyFallback(output);setMessage("The list was not shared. You can copy it instead.");}}
  }

  const visibleItems = items.filter(item => plan?.status === "draft" || filter === "all" || filter === "done" && item.done || filter === "open" && !item.done);
  const openAmount = plan?.budget ? plan.budget.available_cents + (plan.is_revision ? plan.reservation_cents : 0) : null;

  return <section className="shopping-planner" aria-labelledby="planner-title">
    <header className="planner-heading"><div><p className="planner-eyebrow">YOUR NEXT SHOPPING ROUND</p><h1 id="planner-title">Your weekly shopping list</h1>
      <p>Suggestions from your confirmed purchases. Choose what you need and check what is already at home.</p></div>
      <label>Shopping date<input type="date" value={plan ? plannedOn : browseDate} disabled={busy !== null} onChange={event => {
        const value = event.target.value;
        if (!value) return;
        if (plan && editablePlan && weekStart(value) === week) {setPlannedOn(value);setReviewed(false);return;}
        if (dirty) {setErrorPlace("page");setError("Save or cancel your edits before choosing another week.");return;}
        setPlan(null);setItems([]);setBrowseDate(value);setPlannedOn(value);setError(null);setPreview(null);
      }} /></label>
    </header>
    {query.isPending && <p role="status">Loading your shopping lists…</p>}
    {query.error && <div className="planner-notice" role="alert"><p>{getErrorText(query.error)}</p><button className="button button-outline" onClick={() => void query.refetch()}>Retry lists</button></div>}
    {busy && <p className="planner-notice" role="status">{busy}…</p>}
    {error && errorPlace==="page" && <p ref={errorPanel} className="field-error planner-inline-error" role="alert" tabIndex={-1}>{error}</p>}
    {message && <p className="planner-notice" role="status">{message}</p>}
    {!plan && !query.isPending && !busy && <button type="button" className="button button-primary" onClick={() => void perform("Preparing your suggested list", signal => prepare(signal))}>Prepare suggested list</button>}
    {plan && <>
      <div className="planner-status-row"><span className={`planner-status planner-status-${plan.status}`}>{plan.is_revision ? "Changes awaiting approval" : plan.phase === "retry_required" ? "Suggestions unavailable" : plan.phase === "preparing" ? "Preparing suggestions" : plan.status === "draft" ? "Suggested · awaiting approval" : plan.status === "settled" ? "Purchase linked" : plan.status}</span>
        <span>{dateLabel(plan.week_start)} — {dateLabel(dayInput(new Date(new Date(`${plan.week_start}T12:00:00`).getTime()+6*86400000)))}</span>
        <button className="text-link" disabled={busy !== null || dirty} onClick={() => void perform("Refreshing list", reload)}>Refresh</button>
      </div>
      {plan.content.notice && <p className="planner-hint" role="status">{plan.content.notice}</p>}

      {(plan.phase === "preparing" || plan.phase === "retry_required") && <div className="planner-notice"><p>{plan.phase === "preparing" ? "The suggestion may still be preparing. Refresh shortly, or retry after the previous run finishes." : "You can retry the assistant or add items yourself."}</p>
        <button className="button button-outline" disabled={busy !== null} onClick={() => void perform("Preparing suggestions", signal => mutate(signal, "retry", {}))}>Retry suggestion</button></div>}
      <div className={`planner-layout${needsOpen ? " planner-layout-needs" : ""}`}>
        <div className="planner-main">
          {editablePlan && <UpcomingNeeds key={plan.id} session={session} householdId={householdId} plan={plan} disabled={busy!==null || dirty} open={needsOpen} onOpenChange={setNeedsOpen}
            onAdd={proposed => {
              const names = new Set(items.map(i=>i.name.trim().toLocaleLowerCase()));
              const ids = new Set(items.map(i=>i.product_id).filter(Boolean));
              const fresh = proposed.filter(i=>!names.has(i.name.trim().toLocaleLowerCase()) && (!i.product_id || !ids.has(i.product_id)));
              if(items.length+fresh.length>40){setError("Keep at most 40 draft items. Remove an item before adding more.");setErrorPlace("page");return false;}
              setItems(current=>[...current,...fresh.map(i=>({...i,id:crypto.randomUUID(),done:false,excluded:false}))]);
              setReviewed(false);setPreview(null);setFilter("all");setMessage(fresh.length?"Selected suggestions added to your shopping list. Review quantities, then save changes. No additional funds reserved.":"These items are already on your list.");
              setFocusTarget("planner-list-title");
              return true;
            }} />}



          <div className="planner-list-view" hidden={needsOpen}>
          <section className="planner-card" aria-labelledby="planner-list-title">
            <div className="planner-section-heading"><div className="planner-list-title"><h2 id="planner-list-title" tabIndex={-1}>Your shopping list</h2>
              <InformationTip label="How suggestions work" text="Suggested from your confirmed purchases, using frequency and recency. Past purchases do not mean you have run out. You choose what to keep; funds are reserved only after approval." /></div>
              {editablePlan && <button className="button button-outline" disabled={busy !== null || dirty || items.length >= 40} onClick={addItem}>Add item</button>}
            </div>
            <div className="planner-export" aria-label="Shopping list actions">
              <button type="button" className="button button-outline" disabled={dirty || !items.length} onClick={()=>takeList(true)}>↓ Download list (.txt)</button>
              <button type="button" className="button button-outline" disabled={dirty || !items.length} onClick={()=>takeList(false)}>Copy list</button>
              <button type="button" className="button button-outline" disabled={dirty || !items.length} onClick={()=>void shareList()}>Share…</button>
            </div>
            <div className="planner-list-toolbar"><button className="text-link" onClick={() => setShowCosts(value => !value)}>{showCosts ? "Hide estimated costs" : "Show estimated costs"}</button>
              {plan.status !== "draft" && <div className="planner-filters" aria-label="Shopping checklist filter">{(["open","done","all"] as const).map(value => <button type="button" key={value} aria-pressed={filter === value} onClick={() => setFilter(value)}>{value[0].toUpperCase()+value.slice(1)}</button>)}</div>}
            </div>
            {!items.length && <p className="planner-empty">No items yet. Add an item, or describe what you need below.</p>}
            {!!items.length && !visibleItems.length && <p className="planner-empty">No items in this view. Choose All to see the list.</p>}
            <ul className="planner-items">{visibleItems.map(item => <li key={item.id} className={`planner-item${item.excluded ? " planner-item-excluded" : ""}${item.done ? " planner-item-done" : ""}`}>
              <div className="planner-item-top">
                {(plan.status === "approved" || plan.status === "settled") && !item.excluded && <input className="planner-done-checkbox" type="checkbox" aria-label={`Mark ${item.name} ${item.done ? "open" : "done"}`} checked={item.done} disabled={busy !== null || dirty} onChange={event => void perform("Updating checklist", signal => mutate(signal,"checklist",{expected_version:plan.version,item_id:item.id,done:event.target.checked}))} />}
                <div><div className="planner-item-name"><h3>{item.name || "New item"}</h3><InformationTip label={`Why ${item.name || "this item"} is on your list`} text={item.reason ?? "Added by you."} /></div><p className="planner-item-quantity">{item.quantity ? `${item.quantity} ${item.unit}` : "Quantity to confirm"}{item.excluded ? " · Already have" : ""}</p></div>
                {editablePlan && <button className="text-link" disabled={busy !== null} onClick={() => setExpanded(expanded === item.id ? null : item.id)}>{expanded === item.id ? "Close edit" : "Edit"}</button>}
              </div>
              
              {showCosts && <div className="planner-item-estimate">{dirty ? <p>Save changes to refresh estimates.</p> : <><strong>{item.estimate?.line_total_cents != null ? `About ${money(item.estimate.line_total_cents)}` : "Price unknown"}</strong>
                <p>{item.estimate?.label ?? "No previous price available"}{item.estimate?.checkout_packs ? ` · Requires ${item.estimate.checkout_packs} packs` : ""}</p>
                {item.estimate?.reference && <button className="text-link" onClick={() => onViewPurchase(item.estimate!.reference!)}>View supporting purchase</button>}</>}
              </div>}
              {expanded === item.id && editablePlan && <div className="planner-item-edit"><div className="planner-edit-grid">
                <ProductNameInput session={session} householdId={householdId} item={item} disabled={busy!==null} invalid={fieldErrors[`planner-name-${item.id}`]}
                  onChange={name => updateItem(item.id,{name,product_id:null,offer_id:null,offer_batch_id:null,manual_unit_price_cents:null})}
                  onChoose={product => updateItem(item.id,{product_id:product.id,name:product.name,offer_id:null,offer_batch_id:null,manual_unit_price_cents:null})} />
                <label htmlFor={`planner-quantity-${item.id}`}>Quantity<input id={`planner-quantity-${item.id}`} aria-invalid={!!fieldErrors[`planner-quantity-${item.id}`]} aria-describedby={fieldErrors[`planner-quantity-${item.id}`] ? `planner-quantity-${item.id}-error` : undefined} value={item.quantity ?? ""} inputMode="decimal" placeholder="To confirm" disabled={busy !== null} onChange={event => updateItem(item.id,{quantity:event.target.value || null,unit:event.target.value ? item.unit ?? "pack" : null})} />{fieldErrors[`planner-quantity-${item.id}`] && <span className="field-error" id={`planner-quantity-${item.id}-error`}>{fieldErrors[`planner-quantity-${item.id}`]}</span>}</label>
                <label>Unit<select value={item.unit ?? "pack"} disabled={busy !== null || item.quantity === null} onChange={event => updateItem(item.id,{unit:event.target.value as Unit})}><option value="pack">pack</option><option value="each">each</option><option value="kg">kg</option><option value="l">litre</option></select></label>
              </div>
                {showCosts && <label>Your estimated price per {item.unit ?? "unit"} (€)<input id={`planner-price-${item.id}`} aria-invalid={!!fieldErrors[`planner-price-${item.id}`]} key={`${item.id}:${item.manual_unit_price_cents}`} inputMode="decimal" placeholder="Optional" defaultValue={item.manual_unit_price_cents == null ? "" : String(item.manual_unit_price_cents/100)} disabled={busy !== null}
                  onBlur={event => { const amount=event.target.value.trim() ? euros(event.target.value) : null; if (event.target.value.trim() && amount === null) {event.target.value=item.manual_unit_price_cents == null ? "" : String(item.manual_unit_price_cents/100);setFieldErrors(current=>({...current,[`planner-price-${item.id}`]:"Enter a valid estimated price, such as 1.25."}));return;} if(amount!==item.manual_unit_price_cents)updateItem(item.id,{manual_unit_price_cents:amount,offer_id:null,offer_batch_id:null}); }} />{fieldErrors[`planner-price-${item.id}`] && <span className="field-error">{fieldErrors[`planner-price-${item.id}`]}</span>}</label>}
              </div>}
              {editablePlan && <div className="planner-item-actions"><button className="text-link" disabled={busy !== null} onClick={() => updateItem(item.id,{excluded:!item.excluded})}>{item.excluded ? "Need this after all" : "Already have"}</button>
                <button className="text-link" disabled={busy !== null} onClick={() => { setItems(current => current.filter(row => row.id !== item.id)); setFieldErrors(current=>Object.fromEntries(Object.entries(current).filter(([key])=>!key.includes(item.id)))); setPreview(null); }}>Remove</button>
                {item.product_id && <button className="text-link" disabled={busy !== null || dirty} onClick={() => void perform("Checking available alternatives",async signal => {
                  const result=await apiRequest<{offers:Offer[];message:string}>(`${base}/weekly-plans/${plan.id}/offers?item_id=${item.id}`,session.access_token,{signal}); if(!signal.aborted)setOffers({itemId:item.id,...result});
                })}>Compare alternatives</button>}
              </div>}
              {offers?.itemId === item.id && <div className="planner-offer-options"><p>{offers.message}</p>{offers.offers.map(offer => <div key={`${offer.batch_id}:${offer.offer_id}`}><strong>{offer.product_name}</strong><p>{offer.chain} · {offer.pack_base_quantity} {offer.comparison_unit} per pack · {money(offer.advertised_price_cents)}{offer.required_packs > 1 ? ` for ${offer.required_packs} packs` : ""}</p><p className="planner-hint">Demonstration data · {offer.valid_from}–{offer.valid_until}. Minimum checkout {money(offer.minimum_checkout_cents)}.</p>
                <button className="button button-outline" disabled={busy !== null} onClick={() => { updateItem(item.id,{product_id:offer.product_id,name:offer.product_name,offer_id:offer.offer_id,offer_batch_id:offer.batch_id,manual_unit_price_cents:null});setOffers(null);setShowCosts(true); }}>Use this option</button></div>)}<button className="text-link" onClick={() => setOffers(null)}>Close options</button></div>}
            </li>)}</ul>

            {dirty && <div className="planner-save-bar"><span>{showCosts ? "Save changes to update estimates" : "Unsaved changes"}</span>{error && errorPlace==="save" && <p className="field-error planner-inline-error" role="alert">{error}</p>}<button className="button button-primary" disabled={busy !== null} onClick={() => void perform("Saving list changes",save)}>Save changes</button><button className="text-link" disabled={busy !== null} onClick={() => receive(plan)}>Cancel edits</button></div>}
            {editablePlan && <button type="button" className="button button-outline planner-add-bottom" disabled={busy!==null || dirty || items.length>=40} onClick={addItem}>Add item</button>}
            <p className="planner-hint">“Already have” excludes an item from this shop. Checking an item Done does not record spending.</p>
          </section>

          {copyFallback && <label className="planner-copy-fallback">Copy your list<textarea readOnly rows={8} value={copyFallback} onFocus={event => event.target.select()} /><button className="text-link" onClick={() => setCopyFallback(null)}>Close</button></label>}
          </div>
        </div>

        <aside hidden={needsOpen} className="planner-sidebar" aria-label="Shopping tools and budget">
          {editablePlan && <section className="planner-card planner-text-entry"><h2>List tools</h2><p className="planner-hint">Make changes in your own words.</p>
            <label htmlFor="planner-text">What do you need this week?</label>
            <textarea id="planner-text" value={text} maxLength={1000} rows={2} disabled={busy !== null} placeholder="Add 2 packs of milk, remove rice, and change bread to 1 pack." onChange={event => { setText(event.target.value); setPreview(null); setPrivacyReviewed(false); }} />
            <label className="planner-checkbox"><input type="checkbox" checked={privacyReviewed} onChange={event => setPrivacyReviewed(event.target.checked)} disabled={busy !== null} /><span>I checked that this contains shopping details only, without personal or sensitive information.</span></label>
            <div className="planner-actions"><button className="button button-outline" disabled={busy !== null || dirty || !privacyReviewed || text.trim().length < 2} onClick={() => void perform("Understanding your changes", async signal => {
              const result = await apiRequest<TextPreview>(`${base}/weekly-plans/${plan.id}/text-preview`, session.access_token, { method: "POST", signal, body: { text, expected_version: plan.version, privacy_reviewed: true } });
              if (!signal.aborted) setPreview(result);
            })}>Preview changes</button><span className="planner-hint">Your request is sent to OpenAI. Changes need your acceptance.</span></div>
            {error && errorPlace==="text" && <p className="field-error planner-inline-error" role="alert">{error}</p>}
            {dirty && <p className="planner-hint">Save or cancel your direct edits before sending a text change.</p>}
            {preview && <div className="planner-change-preview" aria-live="polite"><h3>{preview.clarification ? "A little more detail needed" : "Suggested changes"}</h3>
              {preview.clarification ? <p>{preview.clarification}</p> : <><ul>{preview.changes.map((change,index) => <li key={index}>{change}</li>)}</ul>
                <button className="button button-primary" disabled={busy !== null || dirty} onClick={() => void perform("Applying accepted changes", async signal => {
                  await mutate(signal, "accept-text", { token: preview.token, expected_version: plan.version }); setText(""); setPrivacyReviewed(false);
                })}>Apply these changes</button></>}
              <button className="text-link" onClick={() => setPreview(null)}>Dismiss</button>
            </div>}
          </section>}

          <section className="planner-card"><h2>Your budget, in context</h2>{plan.budget ? <><dl className="planner-money"><div><dt>Budget</dt><dd>{money(plan.budget.amount_cents)}</dd></div><div><dt>Confirmed spending</dt><dd>{money(plan.budget.spent_cents)}</dd></div><div><dt>Reserved</dt><dd>{money(plan.budget.reserved_cents)}</dd></div><div className="planner-available"><dt>Available now</dt><dd>{money(plan.budget.available_cents)}</dd></div></dl>
            {plan.reservation_cents > 0 && <p className="planner-hint">This list already has {money(plan.reservation_cents)} reserved.{plan.is_revision ? ` You can reuse that reservation; up to ${money(openAmount!)} is available for this list.` : ""}</p>}
            <button className="text-link" disabled={dirty} onClick={() => setCommitmentOpen(value => !value)}>{commitmentOpen ? "Close planned spending" : "Add planned spending"}</button>
          </> : <p>No budget covers this shopping date. You can prepare a draft, then create a budget before approval.</p>}
            <button className="text-link" disabled={dirty} onClick={onManageBudget}>Manage budget & commitments</button>
            {error && errorPlace==="budget" && <p className="field-error planner-inline-error" role="alert">{error}</p>}
            {commitmentOpen && plan.budget && <form className="planner-commitment-form" onSubmit={event => {event.preventDefault();void perform("Reserving planned spending",async signal => {
              const amount=euros(commitAmount);if(!amount)throw new Error("Enter an amount above zero.");
              const payload={expected_version:plan.budget!.version,title:commitTitle,planned_on:commitDate,amount_cents:amount};
              const signature=JSON.stringify(payload);
              if(commitmentRequest.current?.signature!==signature)commitmentRequest.current={signature,body:{...payload,client_request_id:crypto.randomUUID()}};
              await apiRequest(`${base}/budgets/${plan.budget!.id}/commitments`,session.access_token,{method:"POST",signal,body:commitmentRequest.current.body});
              await reload(signal);setCommitmentOpen(false);setCommitTitle("");setCommitAmount("");
            });}}><label>Title<input value={commitTitle} maxLength={120} required onChange={event=>setCommitTitle(event.target.value)} /></label><label>Planned date<input type="date" value={commitDate} required onChange={event=>setCommitDate(event.target.value)} /></label><label>Amount (€)<input inputMode="decimal" value={commitAmount} required onChange={event=>setCommitAmount(event.target.value)} /></label><button className="button button-outline" disabled={busy!==null || dirty || !commitTitle.trim()}>Reserve amount</button><p className="planner-hint">Use a short non-sensitive title. This uses your existing budget.</p></form>}
          </section>
          {editablePlan && <section className="planner-card"><h2>Shopping settings</h2><p className="planner-hint">Optional limits for this list.</p><details><summary>Spending options</summary><label>Limit for this shopping list (€)<input id="planner-target" aria-invalid={!!fieldErrors["planner-target"]} value={target} inputMode="decimal" placeholder="Optional" disabled={busy!==null} onChange={event=>{setTarget(event.target.value);setReviewed(false);}} />{fieldErrors["planner-target"] && <span className="field-error">{fieldErrors["planner-target"]}</span>}</label><label>Extra for price changes (€)<input id="planner-buffer" aria-invalid={!!fieldErrors["planner-buffer"]} value={buffer} inputMode="decimal" disabled={busy!==null} onChange={event=>{setBuffer(event.target.value);setReviewed(false);}} />{fieldErrors["planner-buffer"] && <span className="field-error">{fieldErrors["planner-buffer"]}</span>}</label><p className="planner-hint">Optional: cap this list at €50, or add €5 extra for price changes. Both use your existing available budget.</p></details>
          </section>}
          <section className="planner-card planner-review" ref={reviewPanel} aria-labelledby="planner-review-title"><h2 id="planner-review-title">{plan.status==="draft" ? "Estimated total & approval" : "Plan summary"}</h2>
            {!showCosts ? <><p>Check the estimated costs and price coverage before approving.</p><button className="button button-outline" onClick={()=>setShowCosts(true)}>Show estimated costs</button></> : <>
              {figures && !dirty && <><dl className="planner-money"><div><dt>Priced subtotal · estimate</dt><dd>{money(figures.subtotal_cents)}</dd></div><div><dt>Items priced</dt><dd>{figures.priced_count} / {figures.item_count}</dd></div><div><dt>Your buffer</dt><dd>{money(figures.buffer_cents)}</dd></div></dl>
                {figures.unknown_count>0 && <p className="planner-notice">{figures.unknown_count} item(s) have no price estimate. They are excluded from the subtotal.</p>}
                {figures.missing_quantities>0 && <p className="planner-notice">Confirm quantities and units for {figures.missing_quantities} item(s).</p>}
                {figures.over_target && <p className="planner-notice">The estimates exceed your target. Adjust the list or target.</p>}
              </>}
              {dirty && <p className="planner-hint">Estimates need updating. Save your changes to calculate the new total.</p>}
              {plan.status==="draft" && <><label>Amount to reserve (€)<input value={reserveValue} inputMode="decimal" disabled={busy!==null || dirty} onChange={event=>setReserveValue(event.target.value)} /></label>
                <label className="planner-checkbox"><input type="checkbox" checked={reviewed} disabled={busy!==null || dirty} onChange={event=>setReviewed(event.target.checked)} /><span>I checked the items, quantities and price sources.</span></label>
                {!dirty && !!figures?.unknown_count && <label className="planner-checkbox"><input type="checkbox" checked={unknownAcknowledged} onChange={event=>setUnknownAcknowledged(event.target.checked)} /><span>My reservation includes an allowance for unpriced items. Their final cost is unknown.</span></label>}
              </>}
              <p className="planner-hint">Estimates are not guaranteed checkout prices. Previous purchases show what you paid then.</p>
            </>}
            {error && errorPlace==="review" && <p className="field-error planner-inline-error" role="alert">{error}</p>}
            {plan.status==="draft" && <button className="button button-primary planner-approve" disabled={busy!==null || dirty || showCosts && (!reviewed || !plan.budget || !figures?.item_count || !!figures?.missing_quantities || !!figures?.over_target)} onClick={()=>{
              if(!showCosts){setShowCosts(true);reviewPanel.current?.scrollIntoView({behavior:"smooth",block:"center"});return;}
              void perform("Checking funds and approving",approve);
            }}>{showCosts ? "Approve and reserve" : "Review & reserve"}</button>}
            {plan.status==="approved" && <p className="planner-notice">Approved · {money(plan.reservation_cents)} reserved. Record your purchase, then link it to release this reservation.</p>}
            {(plan.status==="cancelled" || plan.status==="settled") && <button className="button button-outline" disabled={busy!==null || browseDate<dayInput(new Date())} onClick={()=>void perform("Preparing a new list",signal=>prepare(signal,true))}>Create another list</button>}
          </section>
          {plan.status==="approved" && !dirty && <section className="planner-card"><h2>After shopping</h2><p className="planner-hint">Save your receipt or purchase first, then choose the purchase that fulfilled this list. One saved purchase can settle this reservation.</p><button className="button button-outline" onClick={()=>setLinkOpen(value=>!value)}>Link saved purchase</button>
            {linkOpen && <div className="planner-link-purchase">{purchases.isPending && <p>Loading purchases…</p>}{purchases.error && <p role="alert">{getErrorText(purchases.error)}</p>}
              <label>Confirmed purchase<select value={purchaseId} onChange={event=>setPurchaseId(event.target.value)}><option value="">Choose a purchase</option>{purchases.data?.purchases.filter(purchase=>weekStart(purchase.purchased_on)===plan.week_start).map(purchase=><option key={purchase.id} value={purchase.id}>{purchase.merchant||"Purchase"} · {purchase.purchased_on} · {money(purchase.total_cents)}</option>)}</select></label>
              <div className="planner-actions"><button className="text-link" disabled={purchaseOffset===0} onClick={()=>setPurchaseOffset(value=>Math.max(0,value-100))}>Previous purchases</button><button className="text-link" disabled={!purchases.data || purchaseOffset+100>=purchases.data.total} onClick={()=>setPurchaseOffset(value=>value+100)}>More purchases</button></div>
              <button className="button button-primary" disabled={busy!==null || !purchaseId} onClick={()=>void perform("Linking purchase",signal=>mutate(signal,"settle",{expected_version:plan.version,purchase_id:purchaseId}))}>Link and release reservation</button>
            </div>}
          </section>}
          {(plan.status==="draft" || plan.status==="approved") && <details className="planner-cancel"><summary>Plan actions</summary>{!confirmCancel ? <button className="text-link" disabled={busy!==null} onClick={()=>setConfirmCancel(true)}>Cancel this plan</button> : <><p>Cancel this list and release its open reservation?</p><button className="button button-outline" disabled={busy!==null} onClick={()=>void perform("Cancelling plan",signal=>mutate(signal,"cancel",{expected_version:plan.version}))}>Confirm cancellation</button><button className="text-link" onClick={()=>setConfirmCancel(false)}>Keep list</button></>}</details>}
        </aside>
      </div>
    </>}
  </section>;
}
