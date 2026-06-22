/**
 * WebSocket manager for live simulation progress.
 *
 * Design:
 *  - Opens wss://{WS_URL}?job_id={job_id}
 *  - Calls onProgress({ sims_done, ci_half }) on each "progress" frame
 *  - Calls onDone(result) on the "done" terminal frame and closes the socket
 *  - Calls onFailed(error) on the "failed" terminal frame
 *  - Calls onDisconnect() on close/error so the caller can fall back to
 *    GET /api/jobs/{job_id} as the source of truth
 *
 * The WebSocket is display-only. The job record in DynamoDB is the authoritative
 * state; never trust the socket as the store.
 */
import { WS_URL } from "./config";
import type { SimResult, WsFrame } from "./types";

export interface WsCallbacks {
  onProgress: (sims_done: number, ci_half: number, mean_margin: number) => void;
  onDone: (result: SimResult) => void;
  onFailed: (error: string) => void;
  onDisconnect: () => void;
}

export function openSimSocket(jobId: string, callbacks: WsCallbacks): () => void {
  if (!WS_URL) {
    callbacks.onFailed("NEXT_PUBLIC_WS_URL is not configured");
    return () => {};
  }

  const url = `${WS_URL}?job_id=${encodeURIComponent(jobId)}`;
  let ws: WebSocket | null = new WebSocket(url);
  let closed = false;

  ws.onmessage = (ev) => {
    let frame: WsFrame;
    try {
      frame = JSON.parse(ev.data) as WsFrame;
    } catch {
      return;
    }

    if (frame.type === "progress") {
      callbacks.onProgress(frame.sims_done, frame.ci_half, 0);
    } else if (frame.type === "done") {
      callbacks.onDone({
        mean_margin: frame.mean_margin,
        ci_half_width: frame.ci_half_width,
        n_sims: frame.n_sims,
        equiv_net_rating: frame.equiv_net_rating,
        converged: frame.converged,
      });
      closed = true;
      ws?.close();
    } else if (frame.type === "failed") {
      callbacks.onFailed(`${frame.error_type}: ${frame.error_message}`);
      closed = true;
      ws?.close();
    }
  };

  ws.onerror = () => {
    if (!closed) callbacks.onDisconnect();
  };

  ws.onclose = () => {
    if (!closed) callbacks.onDisconnect();
  };

  return () => {
    closed = true;
    ws?.close();
    ws = null;
  };
}
