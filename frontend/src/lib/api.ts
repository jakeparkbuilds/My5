/**
 * HTTP client for the My5 FastAPI layer.
 * All functions throw on non-2xx responses.
 */
import { API_URL } from "./config";
import type { JobStatus, Player, SimResult, SimulateResponse } from "./types";

async function _json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`HTTP ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

/**
 * Fetch the full player list once and cache it.
 * The backend serves this from memory (one-time DynamoDB scan at startup).
 * The client filters locally — no per-query requests.
 */
let _playerCache: Player[] | null = null;

export async function fetchPlayers(): Promise<Player[]> {
  if (_playerCache) return _playerCache;
  const res = await fetch(`${API_URL}/api/players`);
  _playerCache = await _json<Player[]>(res);
  return _playerCache;
}

/**
 * Submit a matchup simulation.
 *
 * On cache hit: returns immediately with cached_result populated.
 * On cache miss: returns job_id; caller should connect to WebSocket.
 */
export async function submitSimulation(
  teamAIds: number[],
  teamBIds: number[],
  seed?: number,
): Promise<SimulateResponse> {
  const res = await fetch(`${API_URL}/api/simulate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      team_a_player_ids: teamAIds,
      team_b_player_ids: teamBIds,
      seed: seed ?? 42,
    }),
  });
  return _json<SimulateResponse>(res);
}

/**
 * Read a job record directly from DynamoDB (via the HTTP layer).
 * Used as the source of truth on WebSocket reconnect / 60-second timeout.
 */
export async function getJobStatus(jobId: string): Promise<JobStatus> {
  const res = await fetch(`${API_URL}/api/jobs/${jobId}`);
  return _json<JobStatus>(res);
}
