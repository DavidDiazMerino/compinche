// Keep an unresolved send recoverable across timeouts and page reloads.
const PREFIX = "compinche-pending-turn-v1:";

export function pendingTurn(session, storage = globalThis.localStorage) {
  const raw = storage.getItem(PREFIX + session);
  if (!raw) return null;
  const value = JSON.parse(raw);
  if (!value?.idempotency_key || !value?.text || !Array.isArray(value.evidence_ids))
    throw new Error("The pending message could not be read. Check the conversation before clearing the retry.");
  return value;
}

export function prepareTurn(session, payload, storage = globalThis.localStorage, newKey = () => crypto.randomUUID()) {
  const normalized = {
    text: payload.text.trim(),
    evidence_ids: [...(payload.evidence_ids || [])],
    answer_to: payload.answer_to || null,
  };
  const previous = pendingTurn(session, storage);
  if (previous) {
    const { idempotency_key, ...priorPayload } = previous;
    if (JSON.stringify(priorPayload) !== JSON.stringify(normalized))
      throw new Error("Recover the previous send before starting a different message. Its retry is available in this conversation.");
    return previous;
  }
  const intent = { ...normalized, idempotency_key: newKey() };
  storage.setItem(PREFIX + session, JSON.stringify(intent));
  return intent;
}

export function clearPendingTurn(session, key, storage = globalThis.localStorage) {
  const pending = pendingTurn(session, storage);
  if (pending?.idempotency_key === key) storage.removeItem(PREFIX + session);
}

export function discardPendingTurn(session, storage = globalThis.localStorage) {
  storage.removeItem(PREFIX + session);
}

export async function deliverTurn(session, payload, send, storage = globalThis.localStorage, newKey) {
  const intent = prepareTurn(session, payload, storage, newKey);
  let result;
  try {
    result = await send(intent);
  } catch (error) {
    if (error.status >= 400 && error.status < 500 && ![408, 409, 429].includes(error.status))
      clearPendingTurn(session, intent.idempotency_key, storage);
    throw error;
  }
  clearPendingTurn(session, intent.idempotency_key, storage);
  return result;
}
