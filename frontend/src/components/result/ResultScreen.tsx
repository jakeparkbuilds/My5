import type { FC } from "react";
import type { Player, SimResult } from "../../lib/types";
import StatBar from "./StatBar";
import {
  IconPlay,
  IconEdit,
  IconDice,
  IconTerminal,
  IconBolt,
  IconCheck,
} from "../Icons";

interface Props {
  result: SimResult;
  fromCache: boolean;
  teamA: (Player | null)[];
  teamB: (Player | null)[];
  teamAName: string;
  teamBName: string;
  onRerun: () => void;
  onEdit: () => void;
}

function calcEfg(p: Player): number {
  if (p.rim_fg_pct == null || p.fg3_pct == null || p.mid_fg_pct == null) return 0.52;
  return (p.rim_fg_pct * 0.45 + p.mid_fg_pct * 0.175 + p.fg3_pct * 0.375 * 1.5) / 0.8;
}

function avgStat(players: (Player | null)[], fn: (p: Player) => number): number {
  const filled = players.filter(Boolean) as Player[];
  if (!filled.length) return 0;
  return filled.reduce((s, p) => s + fn(p), 0) / filled.length;
}

function computeOffRtg(players: (Player | null)[]): string {
  const filled = players.filter(Boolean) as Player[];
  if (!filled.length) return "—";
  const avgEfg = filled.reduce((s, p) => s + calcEfg(p), 0) / filled.length;
  const avgTov = filled.reduce((s, p) => s + (p.tov_rate ?? 0.14), 0) / filled.length;
  return (113 + (avgEfg - 0.535) * 120 + (0.14 - avgTov) * 80).toFixed(1);
}

function computeDefRtg(players: (Player | null)[]): string {
  const filled = players.filter(Boolean) as Player[];
  if (!filled.length) return "—";
  const avgUsg = filled.reduce((s, p) => s + (p.usage_rate ?? 0.2), 0) / filled.length;
  const avgTov = filled.reduce((s, p) => s + (p.tov_rate ?? 0.14), 0) / filled.length;
  return (112 + (avgTov - 0.14) * 40 + (avgUsg - 0.2) * -10).toFixed(1);
}

const POSITIONS = ["PG", "SG", "SF", "PF", "C"];

const ResultScreen: FC<Props> = ({
  result,
  fromCache,
  teamA,
  teamB,
  teamAName,
  teamBName,
  onRerun,
  onEdit,
}) => {
  const { mean_margin, ci_half_width, n_sims, equiv_net_rating } = result;

  const teamAWins = mean_margin >= 0;
  const winnerLabel = teamAWins ? `${teamAName} Advantage` : `${teamBName} Advantage`;
  const winnerColor = teamAWins ? "text-primary" : "text-team-b";
  const netPtsText = `${equiv_net_rating >= 0 ? "+" : ""}${equiv_net_rating.toFixed(2)} Pts/100`;

  const efgA = avgStat(teamA, calcEfg);
  const efgB = avgStat(teamB, calcEfg);
  const efgSum = efgA + efgB || 1;

  const tovA = avgStat(teamA, (p) => p.tov_rate ?? 0.14);
  const tovB = avgStat(teamB, (p) => p.tov_rate ?? 0.14);
  const tovSum = tovA + tovB || 1;

  const absNet = Math.abs(equiv_net_rating);
  const netPctA = Math.min(90, Math.max(10, teamAWins ? 50 + absNet * 4 : 50 - absNet * 4));

  const offA = computeOffRtg(teamA);
  const defA = computeDefRtg(teamA);
  const offB = computeOffRtg(teamB);
  const defB = computeDefRtg(teamB);

  return (
    <div className="flex-1 overflow-y-auto overflow-x-hidden p-8">
      <div className="max-w-[1600px] mx-auto w-full flex flex-col lg:grid lg:grid-cols-12 gap-8">

        {/* LEFT RAIL — Team A */}
        <aside className="lg:col-span-3 flex flex-col gap-3 opacity-80 hover:opacity-100 transition-opacity order-2 lg:order-1">
          <div className="flex items-center justify-between pb-2 border-b border-outline-variant/50">
            <h2 className="text-[10px] font-mono text-team-a uppercase tracking-widest">{teamAName}</h2>
            <span className="text-[10px] font-mono px-2 py-0.5 bg-surface-container text-data-gray border border-outline-variant">
              HOME
            </span>
          </div>
          <div className="flex flex-col gap-1">
            {teamA.map((p, i) =>
              p ? (
                <div
                  key={i}
                  className="flex items-center gap-2.5 px-2.5 py-2 bg-surface-container-low border border-surface-variant/60 hover:border-team-a/40 transition-colors"
                >
                  <div className="w-6 h-6 bg-surface-variant flex items-center justify-center text-[9px] font-mono text-on-surface-variant flex-shrink-0">
                    {POSITIONS[i]}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-[12px] text-on-surface truncate">{p.short_name}</div>
                    <div className="text-[10px] font-mono text-data-gray mt-0.5">
                      {p.usage_rate != null ? `USG: ${(p.usage_rate * 100).toFixed(1)}%` : p.team_abbr}
                    </div>
                  </div>
                </div>
              ) : null,
            )}
          </div>
        </aside>

        {/* CENTER — Result billboard */}
        <section className="lg:col-span-6 flex flex-col justify-center relative order-1 lg:order-2 py-8 lg:py-0 min-h-[480px]">
          {/* Ambient glow */}
          <div className="absolute inset-0 top-1/4 bottom-1/4 bg-primary/5 blur-[100px] -z-10 pointer-events-none" />

          <div className="flex flex-col items-center text-center gap-5 w-full">
            {/* Cache / status badge */}
            <div className="inline-flex items-center gap-2 px-3 py-1.5 bg-surface-container border border-outline-variant/50">
              {fromCache ? (
                <>
                  <IconBolt size={14} className="text-neon-volt" />
                  <span className="text-[10px] font-mono text-on-surface uppercase tracking-wider">
                    Instant Result{" "}
                    <span className="text-data-gray">(Cached)</span>
                  </span>
                </>
              ) : (
                <>
                  <IconCheck size={14} className="text-secondary" />
                  <span className="text-[10px] font-mono text-on-surface uppercase tracking-wider">
                    Simulation Complete · {n_sims.toLocaleString()} sims
                  </span>
                </>
              )}
            </div>

            {/* Winner headline */}
            <div>
              <h1 className={`text-4xl font-extrabold tracking-tight leading-tight uppercase ${winnerColor}`}>
                {winnerLabel}
              </h1>
              <p className={`text-xl font-bold mt-2 font-mono ${winnerColor}`}>{netPtsText}</p>
            </div>

            {/* Lineup ratings boxes */}
            <div className="flex flex-col md:flex-row gap-3 w-full max-w-xl mx-auto">
              <div className="bg-surface-container-high border border-outline-variant/50 p-3 flex-1">
                <div className="text-[10px] font-mono text-data-gray mb-2 uppercase tracking-wider text-left">
                  {teamAName}
                </div>
                <div className="flex gap-4">
                  <div>
                    <div className="text-[10px] font-mono text-outline uppercase tracking-wider">Off Rtg</div>
                    <div className="text-lg font-bold text-primary font-mono">{offA}</div>
                  </div>
                  <div className="w-px bg-outline-variant/40" />
                  <div>
                    <div className="text-[10px] font-mono text-outline uppercase tracking-wider">Def Rtg</div>
                    <div className="text-lg font-bold text-primary font-mono">{defA}</div>
                  </div>
                </div>
              </div>
              <div className="bg-surface-container-high border border-outline-variant/50 p-3 flex-1">
                <div className="text-[10px] font-mono text-data-gray mb-2 uppercase tracking-wider text-left">
                  {teamBName}
                </div>
                <div className="flex gap-4">
                  <div>
                    <div className="text-[10px] font-mono text-outline uppercase tracking-wider">Off Rtg</div>
                    <div className="text-lg font-bold text-team-b font-mono">{offB}</div>
                  </div>
                  <div className="w-px bg-outline-variant/40" />
                  <div>
                    <div className="text-[10px] font-mono text-outline uppercase tracking-wider">Def Rtg</div>
                    <div className="text-lg font-bold text-team-b font-mono">{defB}</div>
                  </div>
                </div>
              </div>
            </div>

            {/* Stat bars */}
            <div className="w-full max-w-lg bg-surface-container-low border border-surface-variant p-5 space-y-5">
              <StatBar
                label="Expected eFG%"
                aValue={`${(efgA * 100).toFixed(1)}%`}
                bValue={`${(efgB * 100).toFixed(1)}%`}
                aPct={(efgA / efgSum) * 100}
              />
              <StatBar
                label="Turnover %"
                aValue={`${(tovA * 100).toFixed(1)}%`}
                bValue={`${(tovB * 100).toFixed(1)}%`}
                aPct={(tovA / tovSum) * 100}
                reverseBar
              />
              <StatBar
                label="Net Rating"
                aValue={`${teamAWins ? "+" : ""}${equiv_net_rating.toFixed(1)}`}
                bValue={`${teamAWins ? "" : "+"}${(-equiv_net_rating).toFixed(1)}`}
                aPct={netPctA}
              />
            </div>

            {/* CI disclaimer */}
            <p className="text-[10px] font-mono text-outline/70 max-w-sm text-center leading-relaxed">
              Novel lineups use league-average defense via shrinkage.
              CI: ±{ci_half_width.toFixed(2)} pts · {n_sims.toLocaleString()} sims.
              Sandbox — not a forecast.
            </p>

            {/* Action buttons — all in normal flow, no absolute positioning */}
            <div className="flex flex-wrap items-center justify-center gap-2 pt-2 w-full">
              <button
                onClick={onRerun}
                className="flex items-center gap-2 px-5 py-2 bg-primary text-obsidian-base hover:bg-primary-fixed-dim transition-colors text-sm font-medium glow-primary"
              >
                <IconPlay size={16} />
                Re-run
              </button>
              <button
                onClick={onEdit}
                className="flex items-center gap-2 px-5 py-2 border border-outline-variant text-on-surface hover:bg-surface-container hover:border-outline transition-colors text-sm"
              >
                <IconEdit size={16} />
                Edit Lineups
              </button>
              <button
                onClick={onEdit}
                className="flex items-center gap-2 px-4 py-2 text-primary hover:bg-surface-container transition-colors text-sm"
              >
                <IconDice size={16} />
                Randomize
              </button>
            </div>

            {/* Telemetry — in flow, low opacity */}
            <div className="flex justify-center opacity-30 hover:opacity-100 transition-opacity">
              <button className="flex items-center gap-1.5 text-[10px] font-mono text-data-gray hover:text-on-surface transition-colors">
                <IconTerminal size={12} />
                View Telemetry Logs
              </button>
            </div>
          </div>
        </section>

        {/* RIGHT RAIL — Team B */}
        <aside className="lg:col-span-3 flex flex-col gap-3 opacity-70 hover:opacity-100 transition-opacity order-3">
          <div className="flex items-center justify-between pb-2 border-b border-outline-variant/50">
            <span className="text-[10px] font-mono px-2 py-0.5 bg-surface-container text-data-gray border border-outline-variant">
              AWAY
            </span>
            <h2 className="text-[10px] font-mono text-team-b uppercase tracking-widest">
              {teamBName}
            </h2>
          </div>
          <div className="flex flex-col gap-1">
            {teamB.map((p, i) =>
              p ? (
                <div
                  key={i}
                  className="flex items-center gap-2.5 px-2.5 py-2 bg-surface-container-lowest border border-surface-variant/40 hover:border-team-b/40 transition-colors flex-row-reverse"
                >
                  <div className="w-6 h-6 bg-surface-variant flex items-center justify-center text-[9px] font-mono text-on-surface-variant flex-shrink-0">
                    {POSITIONS[i]}
                  </div>
                  <div className="flex-1 min-w-0 text-right">
                    <div className="text-[12px] text-on-surface truncate">{p.short_name}</div>
                    <div className="text-[10px] font-mono text-data-gray mt-0.5">
                      {p.usage_rate != null ? `USG: ${(p.usage_rate * 100).toFixed(1)}%` : p.team_abbr}
                    </div>
                  </div>
                </div>
              ) : null,
            )}
          </div>
        </aside>

      </div>
    </div>
  );
};

export default ResultScreen;
