// Server-Sent Events over fetch (EventSource can't POST or send an Authorization header).

import { API, ApiError, getAccessToken, refreshAccessToken } from "./api";

export interface SseEvent {
  event: string;
  data: unknown;
}

/** Parse an SSE byte stream into events; comment lines (": ping") are ignored. */
export async function* parseSse(body: ReadableStream<Uint8Array>): AsyncGenerator<SseEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary: number;
      while ((boundary = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        let event = "message";
        const data: string[] = [];
        for (const line of block.split("\n")) {
          if (line.startsWith(":")) continue;
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
        }
        if (data.length) yield { event, data: JSON.parse(data.join("\n")) };
      }
    }
  } finally {
    reader.releaseLock();
  }
}

/** POST a JSON body and stream SSE events back (refreshing the access token once on 401). */
export async function* postSse(path: string, body: unknown, signal?: AbortSignal): AsyncGenerator<SseEvent> {
  const send = (token: string | null) =>
    fetch(`${API}/${path}`, {
      method: "POST",
      credentials: "same-origin",
      signal,
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
    });

  let res = await send(getAccessToken());
  if (res.status === 401) {
    const token = await refreshAccessToken();
    if (token) res = await send(token);
  }
  if (!res.ok || !res.body) {
    let code = `http_${res.status}`;
    let message = res.statusText || "Request failed";
    try {
      const err = (await res.json()).error;
      code = err.code;
      message = err.message;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, code, message);
  }
  yield* parseSse(res.body);
}
