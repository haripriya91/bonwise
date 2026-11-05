import type { components } from "../api/types";

type DraftCreate = components["schemas"]["DraftCreate"];
type DraftPayload = Omit<DraftCreate, "client_request_id">;

type PendingSubmission = {
  payloadKey: string;
  body: DraftCreate;
};

export type SourceContext =
  | { type: "text"; text: string; receiptLineTotalsCents: (number | null)[] }
  | { type: "voice"; text: string; receiptLineTotalsCents: (number | null)[] }
  | { type: "image"; dataUrl: string; receiptLineTotalsCents: (number | null)[] };

const pendingSubmissions = new Map<string, PendingSubmission>();

export function getDraftScopeKey(userId: string, householdId: string): string {
  return `${userId}:${householdId}`;
}

export function prepareDraftSubmission(scopeKey: string, payload: DraftPayload): DraftCreate {
  const payloadKey = JSON.stringify(payload);
  const previous = pendingSubmissions.get(scopeKey);
  if (previous?.payloadKey === payloadKey) {
    return previous.body;
  }

  const body: DraftCreate = {
    ...payload,
    client_request_id: crypto.randomUUID(),
  };
  pendingSubmissions.set(scopeKey, { payloadKey, body });
  return body;
}

export function getPendingDraftSubmission(scopeKey: string): DraftCreate | undefined {
  return pendingSubmissions.get(scopeKey)?.body;
}

export function clearPendingDraftSubmission(scopeKey?: string): void {
  if (scopeKey) {
    pendingSubmissions.delete(scopeKey);
    return;
  }
  pendingSubmissions.clear();
}

export function getSourceSnapshotKey(
  userId: string,
  householdId: string,
  draftId: string,
): string {
  return `${userId}:${householdId}:${draftId}`;
}
