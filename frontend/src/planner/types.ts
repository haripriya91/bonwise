export type Unit = "pack" | "each" | "kg" | "l";
export type Estimate = {
  basis: "history" | "manual" | "offer" | "unknown";
  label: string;
  reference: string | null;
  line_total_cents: number | null;
  unit_price_cents: number | null;
  purchased_on?: string;
  demo?: boolean;
  checkout_packs?: number;
};
export type Item = {
  id: string; product_id: string | null; name: string;
  quantity: string | null; unit: Unit | null; excluded: boolean; done: boolean;
  manual_unit_price_cents: number | null; offer_id: string | null; offer_batch_id: string | null;
  reason?: string; identity_status?: "linked" | "unmatched"; estimate?: Estimate;
};
export type Figures = {
  item_count: number; priced_count: number; unknown_count: number; missing_quantities: number;
  subtotal_cents: number; buffer_cents: number; minimum_reservation_cents: number; over_target: boolean;
};
export type Budget = {
  id: string; version: number; amount_cents: number; spent_cents: number;
  reserved_cents: number; available_cents: number;
};
export type Plan = {
  id: string; household_id: string; week_start: string; planned_on: string;
  status: "draft" | "approved" | "cancelled" | "settled";
  phase: string; version: number; is_revision: boolean;
  content: { items: Item[]; summary?: Figures; target_cents: number | null; buffer_cents: number; notice?: string | null; generation_method?: string };
  budget: Budget | null; reservation_cents: number; commitment_id: string | null;
};
export type TextPreview = { token: string; base_version: number; changes: string[]; clarification: string | null; proposed_items: Item[] };
export type ProductOption = { id: string; name: string; base_unit: string | null; pack_base_quantity: string | null; category: string };
export type Offer = {
  offer_id: string; batch_id: string; product_id: string; product_name: string; chain: string;
  advertised_price_cents: number; required_packs: number; minimum_checkout_cents: number;
  pack_base_quantity: string; comparison_unit: string; valid_from: string; valid_until: string;
  relation: "same_product" | "compatible_alternative";
};
export const money = (cents: number) => new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" }).format(cents / 100);
export function euros(value: string): number | null {
  if (!/^\d+(?:[.,]\d{1,2})?$/.test(value.trim())) return null;
  const amount = Math.round(Number(value.replace(",", ".")) * 100);
  return Number.isSafeInteger(amount) && amount <= 1_000_000_000 ? amount : null;
}
export function dayInput(day: Date): string {
  return `${day.getFullYear()}-${String(day.getMonth()+1).padStart(2,"0")}-${String(day.getDate()).padStart(2,"0")}`;
}
export function weekStart(value: string): string {
  const day = new Date(`${value}T12:00:00`);
  day.setDate(day.getDate() - (day.getDay()+6)%7);
  return dayInput(day);
}
export function defaultShoppingDate(): string {
  const day = new Date();
  const weekday = (day.getDay()+6)%7;
  if (weekday < 5) day.setDate(day.getDate()+5-weekday);
  return dayInput(day);
}
export function editable(item: Item) {
  const { id, product_id, name, quantity, unit, excluded, done, manual_unit_price_cents, offer_id, offer_batch_id } = item;
  return { id, product_id, name, quantity, unit, excluded, done, manual_unit_price_cents, offer_id, offer_batch_id };
}
export function exportList(plan: Plan, estimates: boolean): string {
  const lines = ["BONWISE — WEEKLY SHOPPING LIST", `Shopping date: ${plan.planned_on}`, `Status: ${plan.status}`, ""];
  for (const item of plan.content.items.filter(item => !item.excluded)) {
    lines.push(`${item.done ? "[x]" : "[ ]"} ${item.name} — ${item.quantity ?? "quantity to confirm"} ${item.unit ?? ""}`);
    if (estimates) lines.push(`  ${item.estimate?.line_total_cents != null ? `Estimate ${money(item.estimate.line_total_cents)}` : "Price unknown"}. ${item.estimate?.label ?? "No price evidence"}.`);
  }
  if (estimates && plan.content.summary) lines.push("", `Priced subtotal: ${money(plan.content.summary.subtotal_cents)}`, `Price coverage: ${plan.content.summary.priced_count}/${plan.content.summary.item_count}`, "Estimates are not guaranteed checkout prices.");
  lines.push("", "Checking off items does not record spending.");
  return lines.join("\n");
}
