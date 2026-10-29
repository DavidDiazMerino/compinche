export class APIError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}
export async function request(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(
    () => controller.abort(),
    options.timeout || 180000,
  );
  try {
    const response = await fetch(path, {
      ...options,
      signal: controller.signal,
      headers: {
        ...(options.body instanceof FormData
          ? {}
          : { "Content-Type": "application/json" }),
        ...options.headers,
      },
    });
    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("application/json"))
      throw new APIError(
        "The local agent server is not running. Start Compinche with its backend.",
        503,
        { code: "backend_offline" },
      );
    const data = await response.json();
    if (!response.ok) {
      const detail = data.detail || data;
      throw new APIError(
        typeof detail === "string"
          ? detail
          : detail.message ||
            detail.error ||
            "This action could not be completed.",
        response.status,
        detail,
      );
    }
    return data;
  } catch (error) {
    if (error.name === "AbortError")
      throw new APIError(
        "The request took too long. Your recording is still saved. Check the session before trying again.",
        408,
        { code: "timeout" },
      );
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}
export const api = {
  health: () => request("/api/health", { timeout: 12000 }),
  sessions: () => request("/api/sessions"),
  session: (id) => request(`/api/sessions/${encodeURIComponent(id)}`),
  create: (payload) =>
    request("/api/sessions", { method: "POST", body: JSON.stringify(payload) }),
  turn: (id, payload) =>
    request(`/api/sessions/${encodeURIComponent(id)}/turn`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  note: (id, payload) =>
    request(`/api/sessions/${encodeURIComponent(id)}/notes`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  upload: (id, file, note = "") => {
    const body = new FormData();
    body.append(
      "file",
      file,
      file.name || `recording.${file.type?.includes("mp4") ? "mp4" : "webm"}`,
    );
    body.append("note", note);
    body.append("timestamp_seconds", "0");
    return request(`/api/sessions/${encodeURIComponent(id)}/evidence`, {
      method: "POST",
      body,
      timeout: 240000,
    });
  },
  segment: (id, evidence_id, target) =>
    request(`/api/sessions/${encodeURIComponent(id)}/segment`, {
      method: "POST",
      body: JSON.stringify({ evidence_id, target }),
    }),
  job: (id) => request(`/api/jobs/${encodeURIComponent(id)}`),
  confirm: (session, id, text) =>
    request(
      `/api/sessions/${encodeURIComponent(session)}/knowledge/${encodeURIComponent(id)}/confirm`,
      { method: "POST", body: JSON.stringify(text ? { text } : {}) },
    ),
};
