/* Shared TypeScript types for the My5 frontend. */

export interface Player {
  athlete_id: number;
  display_name: string;
  short_name: string;
  team_abbr: string;
  team_name: string;
  team_id: number | null;
  headshot_href: string;
  usage_rate: number | null;
  fg3_pct: number | null;
  rim_fg_pct: number | null;
  mid_fg_pct: number | null;
  tov_rate: number | null;
  ft_pct: number | null;
}

export interface SimResult {
  mean_margin: number;       // mean(team_a_pts - team_b_pts)
  ci_half_width: number;     // 1.96 × σ/√n — the ± on the margin
  n_sims: number;
  equiv_net_rating: number;  // mean_margin / poss_per_side × 100 (pts per 100)
  converged: boolean;
}

export interface SimulateResponse {
  job_id: string | null;
  cache_hit: boolean;
  cached_result: SimResult | null;
}

export interface JobStatus {
  job_id: string;
  status: "queued" | "running" | "done" | "failed";
  result: SimResult | null;
  error_type: string | null;
  error_message: string | null;
  sims_done: number;
  ci_half: number;
}

/** WebSocket frames from the fanout Lambda */
export type WsFrame =
  | { type: "progress"; sims_done: number; ci_half: number }
  | { type: "done"; n_sims: number; mean_margin: number; ci_half_width: number; equiv_net_rating: number; converged: boolean }
  | { type: "failed"; error_type: string; error_message: string };

/** Frontend app state machine */
export type AppState =
  | { phase: "idle" }
  | { phase: "submitting" }
  | { phase: "simulating"; job_id: string; sims_done: number; ci_half: number; mean_margin: number }
  | { phase: "result"; result: SimResult; from_cache: boolean }
  | { phase: "error"; message: string };

export type TeamSlot = Player | null;
