import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Session } from "@supabase/supabase-js";
import { apiRequest, getErrorText } from "../api/client";
import type { components } from "../api/types";
import { AuthenticatedReceiptImage } from "./AuthenticatedReceiptImage";

type PurchaseDetailsOut = components["schemas"]["PurchaseDetailsOut"];
type Category = components["schemas"]["ItemOut"]["category"];

const categoryLabels: Record<Category, string> = {
  groceries: "Groceries",
  clothing: "Clothing & accessories",
  household: "Household",
  personal_care: "Personal care",
  transport: "Transport",
  dining_out: "Dining out",
  other: "Other",
};

const currency = new Intl.NumberFormat("de-DE", {
  style: "currency",
  currency: "EUR",
});
const dateFormat = new Intl.DateTimeFormat("de-DE", { dateStyle: "medium" });
const sourceLabels: Record<NonNullable<PurchaseDetailsOut["source_type"]>, string> = {
  image: "Receipt",
  voice: "Voice entry",
  text: "Text entry",
  seed: "Imported/demo entry",
};

function formatAmount(cents: number): string {
  return currency.format(cents / 100);
}

function formatQuantity(quantity: string, unit: string): string {
  return `${quantity.replace(".", ",")} ${unit}`;
}

export function ConfirmedPurchaseDetails({
  session,
  householdId,
  purchaseId,
  onBack,
}: {
  session: Session;
  householdId: string;
  purchaseId: string;
  onBack: () => void;
}) {
  const [receiptOpen, setReceiptOpen] = useState(true);
  const { data, error, isPending, isFetching, refetch } = useQuery({
    queryKey: ["user", session.user.id, "household", householdId, "purchase", purchaseId],
    queryFn: ({ signal }) =>
      apiRequest<PurchaseDetailsOut>(
        `/households/${encodeURIComponent(householdId)}/purchases/${encodeURIComponent(purchaseId)}`,
        session.access_token,
        { signal },
      ),
    refetchOnMount: "always",
  });

  return (
    <section className="page-section confirmed-purchase-details" aria-labelledby="purchase-details-title">
      <button className="text-link back-link" type="button" onClick={onBack}>Back to purchases</button>
      {isPending && <p className="status-message" role="status">Loading purchase details…</p>}
      {error && (
        <div className="error-block" role="alert">
          <p>{getErrorText(error)}</p>
          <button className="button button-outline" type="button" onClick={() => void refetch()} disabled={isFetching}>
            {isFetching ? "Trying again…" : "Retry"}
          </button>
        </div>
      )}
      {data && (data.id !== purchaseId || data.household_id !== householdId) && (
        <p className="error-message" role="alert">This purchase does not match the selected household.</p>
      )}
      {data && data.id === purchaseId && data.household_id === householdId && (
        <>
          <header className="confirmed-details-heading">
            <div className="confirmed-details-title-row">
              <h1 id="purchase-details-title">Purchase details</h1>
              <span className="confirmed-status">Confirmed</span>
            </div>
            <div className="confirmed-details-meta-row">
              <p className="confirmed-details-merchant">{data.merchant?.trim() || "Purchase"}</p>
              <span className="confirmed-source-label">{sourceLabels[data.source_type ?? "text"] ?? "Text entry"}</span>
              <p className="confirmed-details-date">{dateFormat.format(new Date(`${data.purchased_on}T12:00:00`))}</p>
            </div>
          </header>

          <div className={`confirmed-details-grid ${data.source_type === "image" ? "has-receipt" : "receipt-hidden"}`}>
            {data.source_type === "image" && (
              <aside className="confirmed-receipt-panel">
                <details
                  className="confirmed-receipt-details"
                  open={receiptOpen}
                  onToggle={(event) => setReceiptOpen(event.currentTarget.open)}
                >
                  <summary>Saved reviewed receipt</summary>
                  {receiptOpen && (
                    <div className="confirmed-receipt-content">
                      {data.receipt_available ? (
                        <AuthenticatedReceiptImage
                          session={session}
                          path={`/households/${encodeURIComponent(householdId)}/purchases/${encodeURIComponent(purchaseId)}/receipt`}
                          alt="Saved reviewed receipt"
                        />
                      ) : (
                        <p className="empty-state">Receipt image was not saved with this purchase.</p>
                      )}
                    </div>
                  )}
                </details>
              </aside>
            )}

            <section className="confirmed-items-panel" aria-labelledby="purchased-items-title">
              <h2 id="purchased-items-title">Purchased items</h2>
              <ul className="confirmed-item-list">
                {data.items.map((item) => {
                  const product = data.products.find(({ id }) => id === item.product_id);
                  return (
                    <li className="confirmed-item" key={item.id}>
                      <div className="confirmed-item-heading">
                        <strong>{item.original_text}</strong>
                        <strong>{formatAmount(item.line_total_cents)}</strong>
                      </div>
                      <p className="confirmed-item-facts">
                        {formatQuantity(item.quantity, item.quantity_unit)}
                        {" · "}{formatAmount(item.unit_price_cents)} per {item.quantity_unit}
                        {" · "}{categoryLabels[item.category]}
                      </p>
                      <p className={product ? "confirmed-linked-product" : "confirmed-unlinked-product"}>
                        {product ? (
                          <>
                            <strong>Catalogue product:</strong> {product.name}
                            {product.brand && ` · ${product.brand}`}
                            {product.pack_base_quantity && product.base_unit
                              ? ` · ${formatQuantity(product.pack_base_quantity, product.base_unit)}`
                              : ""}
                          </>
                        ) : "No product linked"}
                      </p>
                    </li>
                  );
                })}
              </ul>

              <dl className="confirmed-totals">
                <div>
                  <dt>Subtotal</dt>
                  <dd>{formatAmount(data.items.reduce((sum, item) => sum + item.line_total_cents, 0))}</dd>
                </div>
                {data.adjustments.map((adjustment, index) => (
                  <div key={`${adjustment.kind}-${adjustment.label}-${index}`}>
                    <dt>{adjustment.label}</dt>
                    <dd>{formatAmount(adjustment.amount_cents)}</dd>
                  </div>
                ))}
                <div className="confirmed-total">
                  <dt>Confirmed total</dt>
                  <dd>{formatAmount(data.total_cents)}</dd>
                </div>
              </dl>
              <p className="confirmed-details-note">Amounts use your saved purchase records.</p>
            </section>
          </div>
        </>
      )}
    </section>
  );
}
