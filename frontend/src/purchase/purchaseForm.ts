import type { components } from "../api/types";

type Extraction = components["schemas"]["ExtractionOut"] | components["schemas"]["ImageExtractionOut"];
type DraftContent = components["schemas"]["PurchaseContent-Output"];
type DraftContentInput = components["schemas"]["PurchaseContent-Input"];
type ItemInput = components["schemas"]["ItemInput-Input"];
type Category = ItemInput["category"];
type Unit = ItemInput["quantity_unit"];
type Adjustment = components["schemas"]["AdjustmentInput"];

export type PurchaseItemForm = {
  productId: string | null;
  description: string;
  quantity: string;
  grams: string | null;
  unit: Unit | "";
  unitPrice: string;
  category: Category;
  receiptAmountCents: number | null;
};

export type PurchaseAdjustmentForm = {
  kind: Adjustment["kind"];
  label: string;
  amount: string;
  category: Category;
};

export type PurchaseForm = {
  merchant: string;
  purchasedOn: string;
  reportedTotal: string;
  items: PurchaseItemForm[];
  adjustments: PurchaseAdjustmentForm[];
};

export type FieldIssue = {
  id: string;
  message: string;
};

export type PurchaseFormResult = {
  content: DraftContentInput | null;
  errors: FieldIssue[];
  calculatedTotalCents: number | null;
};

export const categories: { value: Category; label: string }[] = [
  { value: "groceries", label: "Groceries" },
  { value: "clothing", label: "Clothing & accessories" },
  { value: "household", label: "Household" },
  { value: "personal_care", label: "Personal care" },
  { value: "transport", label: "Transport" },
  { value: "dining_out", label: "Dining out" },
  { value: "other", label: "Other" },
];

export const units: { value: NonNullable<Unit>; label: string }[] = [
  { value: "pack", label: "pack" },
  { value: "each", label: "each" },
  { value: "kg", label: "kg" },
  { value: "l", label: "l" },
];

export const moneyFormat = new Intl.NumberFormat("de-DE", {
  style: "currency",
  currency: "EUR",
});

export function centsInput(value: number | null | undefined): string {
  if (value === null || value === undefined) return "";
  const absolute = Math.abs(value);
  return `${Math.floor(absolute / 100)},${String(absolute % 100).padStart(2, "0")}`;
}

export function parseCents(value: string, maximum = 1_000_000_000): number | null {
  const match = /^(\d+)(?:[,.](\d{1,2}))?$/.exec(value.trim());
  if (!match) return null;
  const cents = BigInt(match[1]) * 100n + BigInt((match[2] ?? "").padEnd(2, "0"));
  if (cents > BigInt(maximum)) return null;
  return Number(cents);
}

export function parseQuantity(value: string): string | null {
  const match = /^(\d+)(?:[,.](\d{1,3}))?$/.exec(value.trim());
  if (!match) return null;
  const milli = BigInt(match[1]) * 1000n + BigInt((match[2] ?? "").padEnd(3, "0"));
  if (milli <= 0n || milli > 1_000_000_000n) return null;
  return `${match[1]}${match[2] ? `.${match[2]}` : ""}`;
}

export function quantityToGrams(quantity: string): string {
  const match = /^(\d+)(?:[,.](\d{1,3}))?$/.exec(quantity.trim());
  if (!match) return "";
  return String(BigInt(match[1]) * 1000n + BigInt((match[2] ?? "").padEnd(3, "0")));
}

export function gramsToQuantity(grams: string): string {
  if (!/^\d+$/.test(grams.trim())) return "";
  const milli = BigInt(grams.trim());
  if (milli <= 0n || milli > 1_000_000_000n) return "";
  const whole = milli / 1000n;
  const remainder = String(milli % 1000n).padStart(3, "0");
  return remainder === "000" ? String(whole) : `${whole}.${remainder}`;
}

function packageCountCorrection(description: string, quantity: string, unit: Unit | null): string {
  if (unit !== "pack" || !quantity.trim()) return quantity.replace(",", ".");
  if (/(?:^|\s)\d+\s*[x×]\s*/i.test(description)) return quantity.replace(",", ".");
  const packageSize = /\b(\d+(?:[.,]\d+)?)\s*(g|kg|ml|l)\b/i.exec(description);
  if (!packageSize) return quantity.replace(",", ".");

  const parsedQuantity = parseQuantity(quantity);
  const sizeValue = Number(packageSize[1].replace(",", "."));
  if (!parsedQuantity || !Number.isFinite(sizeValue)) return quantity.replace(",", ".");

  const unitCount = packageSize[2].toLowerCase() === "g" || packageSize[2].toLowerCase() === "ml"
    ? sizeValue
    : sizeValue * 1000;
  return Number(parsedQuantity) === unitCount && unitCount > 1 ? "1" : parsedQuantity;
}

export function formFromExtraction(extraction: Extraction): PurchaseForm {
  const candidate = extraction.candidate;
  const extracted = extraction.extracted;
  return {
    merchant: candidate?.merchant ?? extracted.merchant ?? "",
    purchasedOn: candidate?.purchased_on ?? extracted.purchased_on ?? "",
    reportedTotal: centsInput(candidate?.reported_total_cents ?? extracted.reported_total_cents),
    items: extracted.items.map((item, index) => {
      const candidateItem = candidate?.items[index];
      const quantity = candidateItem?.quantity == null
        ? item.quantity == null ? "" : String(item.quantity)
        : String(candidateItem.quantity);
      const unit = candidateItem?.quantity_unit ?? item.quantity_unit ?? "";
      return {
        productId: null,
        description: candidateItem?.original_text ?? item.original_text,
        quantity: packageCountCorrection(item.original_text, quantity, unit || null),
        grams: null,
        unit,
        unitPrice: centsInput(candidateItem?.unit_price_cents ?? item.unit_price_cents),
        category: candidateItem?.category ?? item.category,
        receiptAmountCents: item.line_total_cents,
      };
    }),
    adjustments: (candidate?.adjustments ?? extracted.adjustments).map((adjustment) => ({
      kind: adjustment.kind,
      label: adjustment.label,
      amount: centsInput(adjustment.amount_cents),
      category: adjustment.category,
    })),
  };
}

export function formFromDraft(content: DraftContent, receiptAmounts: (number | null)[] = []): PurchaseForm {
  return {
    merchant: content.merchant ?? "",
    purchasedOn: content.purchased_on,
    reportedTotal: centsInput(content.reported_total_cents),
    items: content.items.map((item, index) => ({
      productId: item.product_id ?? null,
      description: item.original_text,
      quantity: item.quantity.replace(".", ","),
      grams: null,
      unit: item.quantity_unit,
      unitPrice: centsInput(item.unit_price_cents),
      category: item.category,
      receiptAmountCents: receiptAmounts[index] ?? null,
    })),
    adjustments: (content.adjustments ?? []).map((adjustment) => ({
      kind: adjustment.kind,
      label: adjustment.label,
      amount: centsInput(adjustment.amount_cents),
      category: adjustment.category,
    })),
  };
}

function quantityPrompt(unit: Unit | ""): string {
  if (unit === "pack") return "enter how many packs you bought";
  if (unit === "each") return "enter how many items you bought";
  if (unit === "kg") return "enter how many kilograms you bought";
  if (unit === "l") return "enter how many litres you bought";
  return "enter the quantity you bought";
}

function pricePrompt(unit: Unit | ""): string {
  if (unit === "kg") return "the price per kg (€)";
  if (unit === "pack") return "the price per pack (€)";
  if (unit === "each") return "the price per item (€)";
  if (unit === "l") return "the price per litre (€)";
  return "the unit price (€)";
}

function lineTotalCents(quantity: string, unitPriceCents: number): number | null {
  const match = /^(\d+)(?:[,.](\d{1,3}))?$/.exec(quantity.trim());
  if (!match) return null;
  const milliQuantity = BigInt(match[1]) * 1000n + BigInt((match[2] ?? "").padEnd(3, "0"));
  const rounded = (milliQuantity * BigInt(unitPriceCents) + 500n) / 1000n;
  return rounded <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(rounded) : null;
}

export function validatePurchaseForm(form: PurchaseForm, idPrefix: string): PurchaseFormResult {
  const errors: FieldIssue[] = [];
  const addIssue = (id: string, message: string) => errors.push({ id, message });

  const date = /^\d{4}-\d{2}-\d{2}$/.test(form.purchasedOn)
    ? new Date(`${form.purchasedOn}T00:00:00Z`)
    : null;
  if (!date || Number.isNaN(date.getTime()) || date.toISOString().slice(0, 10) !== form.purchasedOn) {
    addIssue(`${idPrefix}-date`, "Enter a valid purchase date.");
  }
  if (form.merchant.trim().length > 200) {
    addIssue(`${idPrefix}-merchant`, "Merchant name must be 200 characters or fewer.");
  }
  if (form.items.length < 1) addIssue(`${idPrefix}-items`, "Add at least one item.");
  if (form.items.length > 200) addIssue(`${idPrefix}-items`, "A purchase can contain at most 200 items.");
  if (form.adjustments.length > 30) addIssue(`${idPrefix}-adjustments`, "A purchase can contain at most 30 adjustments.");

  const items = form.items.map((item, index) => {
    const itemName = item.description.trim() || `Item ${index + 1}`;
    const descriptionId = `${idPrefix}-item-description-${index}`;
    const quantityId = `${idPrefix}-item-quantity-${index}`;
    const unitId = `${idPrefix}-item-unit-${index}`;
    const priceId = `${idPrefix}-item-price-${index}`;
    const quantity = parseQuantity(item.quantity);
    const unitPriceCents = parseCents(item.unitPrice);

    if (!item.description.trim()) addIssue(descriptionId, `Item ${index + 1}: enter a description.`);
    if (item.description.trim().length > 300) addIssue(descriptionId, `${itemName}: description must be 300 characters or fewer.`);
    if (!quantity) addIssue(quantityId, `${itemName}: ${quantityPrompt(item.unit)}.`);
    if (!item.unit) addIssue(unitId, `${itemName}: choose a unit.`);
    if (unitPriceCents === null) addIssue(priceId, `${itemName}: enter ${pricePrompt(item.unit)}.`);

    return {
      ...(item.productId ? { product_id: item.productId } : {}),
      original_text: item.description.trim(),
      quantity: quantity ?? "",
      quantity_unit: item.unit,
      unit_price_cents: unitPriceCents,
      category: item.category,
    };
  });

  const reportedTotalCents = form.reportedTotal.trim()
    ? parseCents(form.reportedTotal, 1_000_000_000_000)
    : null;
  if (form.reportedTotal.trim() && reportedTotalCents === null) {
    addIssue(`${idPrefix}-reported-total`, "Enter a valid printed receipt total or leave it blank.");
  }

  const adjustments = form.adjustments.map((adjustment, index) => {
    const labelId = `${idPrefix}-adjustment-label-${index}`;
    const amountId = `${idPrefix}-adjustment-amount-${index}`;
    const amountCents = parseCents(adjustment.amount, 1_000_000_000_000);
    if (!adjustment.label.trim()) addIssue(labelId, `Adjustment ${index + 1}: enter a description.`);
    if (adjustment.label.trim().length > 120) addIssue(labelId, `Adjustment ${index + 1}: description must be 120 characters or fewer.`);
    if (amountCents === null || amountCents === 0) addIssue(amountId, `Adjustment ${index + 1}: enter a valid non-zero amount.`);
    return {
      kind: adjustment.kind,
      label: adjustment.label.trim(),
      amount_cents: adjustment.kind === "discount" ? -(amountCents ?? 0) : (amountCents ?? 0),
      category: adjustment.category,
    };
  });

  const lineTotals = form.items.map((item) => {
    const quantity = parseQuantity(item.quantity);
    const price = parseCents(item.unitPrice);
    return quantity && price !== null ? lineTotalCents(quantity, price) : null;
  });
  const adjustmentsAreValid = adjustments.every((adjustment) => adjustment.label.length > 0 && adjustment.amount_cents !== 0);
  const calculatedTotalCents = lineTotals.every((total) => total !== null) && adjustmentsAreValid
    ? lineTotals.reduce<number>((sum, total) => sum + (total ?? 0), 0)
      + adjustments.reduce((sum, adjustment) => sum + adjustment.amount_cents, 0)
    : null;

  if (errors.length > 0) return { content: null, errors, calculatedTotalCents };

  return {
    content: {
      merchant: form.merchant.trim() || null,
      purchased_on: form.purchasedOn,
      currency: "EUR",
      items: items.map((item) => ({
        ...(item.product_id ? { product_id: item.product_id } : {}),
        original_text: item.original_text,
        quantity: item.quantity,
        quantity_unit: item.quantity_unit as NonNullable<Unit>,
        unit_price_cents: item.unit_price_cents as number,
        category: item.category,
      })),
      adjustments,
      reported_total_cents: reportedTotalCents,
    },
    errors,
    calculatedTotalCents,
  };
}

export function lineTotalForItem(item: PurchaseItemForm): number | null {
  const quantity = parseQuantity(item.quantity);
  const unitPriceCents = parseCents(item.unitPrice);
  return quantity && unitPriceCents !== null ? lineTotalCents(quantity, unitPriceCents) : null;
}

export function purchaseFormSignature(form: PurchaseForm): string {
  return JSON.stringify({
    merchant: form.merchant,
    purchasedOn: form.purchasedOn,
    reportedTotal: form.reportedTotal,
    items: form.items.map(({ productId, description, quantity, unit, unitPrice, category }) => ({
      productId,
      description,
      quantity,
      unit,
      unitPrice,
      category,
    })),
    adjustments: form.adjustments,
  });
}

export function apiQuantityFromGrams(grams: string): string | null {
  const quantity = gramsToQuantity(grams);
  return quantity ? parseQuantity(quantity) : null;
}
