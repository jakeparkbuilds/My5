/**
 * useSimulation — orchestrates the two-path hit/miss state machine.
 *
 * State transitions:
 *   idle → submitting → (cache hit) → result (instant)
 *                     → (cache miss) → simulating → result
 *                                               → error → idle
 *
 * WebSocket resilience:
 *   On disconnect: immediately polls GET /api/jobs/{job_id} as source of truth.
 *   If done   → jumps to result.
 *   If running → re-opens WebSocket (up to MAX_RECONNECTS times).
 *   If failed  → shows error.
 *   60-second timeout: if no terminal frame arrives, polls job record.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { getJobStatus, submitSimulation } from "../lib/api";
import type { AppState, Player, SimResult } from "../lib/types";
import { openSimSocket } from "../lib/websocket";

const MAX_RECONNECTS = 3;
const TIMEOUT_MS = 60_000;

export function useSimulation() {
  const [state, setState] = useState<AppState>({ phase: "idle" });
  const [teamA, setTeamA] = useState<(Player | null)[]>(Array(5).fill(null));
  const [teamB, setTeamB] = useState<(Player | null)[]>(Array(5).fill(null));

  const closeSocketRef = useRef<(() => void) | null>(null);
  const reconnectCountRef = useRef(0);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const currentJobIdRef = useRef<string | null>(null);

  const _clearSocket = useCallback(() => {
    closeSocketRef.current?.();
    closeSocketRef.current = null;
  }, []);

  const _clearTimeout = useCallback(() => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
  }, []);

  const _handleResult = useCallback((result: SimResult, fromCache: boolean) => {
    _clearSocket();
    _clearTimeout();
    setState({ phase: "result", result, from_cache: fromCache });
  }, [_clearSocket, _clearTimeout]);

  const _handleError = useCallback((message: string) => {
    _clearSocket();
    _clearTimeout();
    setState({ phase: "error", message });
  }, [_clearSocket, _clearTimeout]);

  const _connectSocket = useCallback((jobId: string) => {
    _clearSocket();

    const close = openSimSocket(jobId, {
      onProgress: (sims_done, ci_half, _) => {
        setState((prev) =>
          prev.phase === "simulating"
            ? { ...prev, sims_done, ci_half }
            : prev
        );
      },
      onDone: (result) => {
        _clearTimeout();
        _handleResult(result, false);
      },
      onFailed: (error) => {
        _clearTimeout();
        _handleError(error);
      },
      onDisconnect: async () => {
        // Source of truth: read job record
        try {
          const job = await getJobStatus(jobId);
          if (job.status === "done" && job.result) {
            _handleResult(job.result, false);
            return;
          }
          if (job.status === "failed") {
            _handleError(job.error_message ?? "Job failed");
            return;
          }
        } catch {
          // Network error — fall through to reconnect
        }

        if (reconnectCountRef.current < MAX_RECONNECTS) {
          reconnectCountRef.current += 1;
          _connectSocket(jobId);
        } else {
          _handleError("Lost connection to simulation — refresh to retry.");
        }
      },
    });

    closeSocketRef.current = close;
  }, [_clearSocket, _clearTimeout, _handleResult, _handleError]);

  const simulate = useCallback(async () => {
    const aIds = teamA.map((p) => p?.athlete_id).filter(Boolean) as number[];
    const bIds = teamB.map((p) => p?.athlete_id).filter(Boolean) as number[];
    if (aIds.length !== 5 || bIds.length !== 5) return;

    setState({ phase: "submitting" });
    reconnectCountRef.current = 0;

    try {
      const resp = await submitSimulation(aIds, bIds, 42);

      if (resp.cache_hit && resp.cached_result) {
        _handleResult(resp.cached_result, true);
        return;
      }

      if (!resp.job_id) {
        _handleError("Server returned no job_id on cache miss");
        return;
      }

      currentJobIdRef.current = resp.job_id;
      setState({
        phase: "simulating",
        job_id: resp.job_id,
        sims_done: 0,
        ci_half: Infinity,
        mean_margin: 0,
      });

      // 60-second timeout guard: poll the job record if no terminal frame arrives
      timeoutRef.current = setTimeout(async () => {
        try {
          const job = await getJobStatus(resp.job_id!);
          if (job.status === "done" && job.result) {
            _handleResult(job.result, false);
          } else if (job.status === "failed") {
            _handleError(job.error_message ?? "Job failed (timeout)");
          } else {
            _handleError("Simulation timed out — refresh to retry.");
          }
        } catch {
          _handleError("Simulation timed out — refresh to retry.");
        }
      }, TIMEOUT_MS);

      _connectSocket(resp.job_id);
    } catch (err) {
      _handleError((err as Error).message ?? "Submission failed");
    }
  }, [teamA, teamB, _handleResult, _handleError, _connectSocket]);

  const reset = useCallback(() => {
    _clearSocket();
    _clearTimeout();
    setState({ phase: "idle" });
  }, [_clearSocket, _clearTimeout]);

  // Cleanup on unmount
  useEffect(() => () => { _clearSocket(); _clearTimeout(); }, [_clearSocket, _clearTimeout]);

  const setPlayer = useCallback((team: "a" | "b", slot: number, player: Player | null) => {
    if (team === "a") setTeamA((prev) => prev.map((p, i) => i === slot ? player : p));
    else setTeamB((prev) => prev.map((p, i) => i === slot ? player : p));
  }, []);

  const clearTeam = useCallback((team: "a" | "b") => {
    if (team === "a") setTeamA(Array(5).fill(null));
    else setTeamB(Array(5).fill(null));
  }, []);

  const canSimulate =
    teamA.every(Boolean) && teamB.every(Boolean) && state.phase === "idle";

  return { state, teamA, teamB, setPlayer, clearTeam, simulate, reset, canSimulate };
}
