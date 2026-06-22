import type { FC } from "react";
import type { Player } from "../../lib/types";
import ProgressRing from "./ProgressRing";

const LOG_ENTRIES = [
  "[SYS] ALLOCATING CLUSTER RESOURCES...",
  "[NET] ESTABLISHING MONTE CARLO SEEDS...",
  "[STAT] INGESTING HISTORICAL VECTORS...",
  "[SYS] CORES ACTIVE.",
  "[SIM] BATCH 1 COMPLETE.",
  "[NET] POSSESSIONS PROCESSED...",
  "[STAT] VARIANCE DETECTED IN Q4 MODELS...",
  "[SYS] RECALIBRATING...",
  "[SIM] BATCH 2 COMPLETE.",
  "[STAT] CONVERGENCE IMPROVING...",
  "[NET] POSSESSIONS PROCESSING...",
  "[SYS] AGGREGATING CORES...",
  "[SIM] BATCH 3 COMPLETE.",
  "[STAT] CONVERGENCE AT 72%...",
  "[NET] POSSESSIONS PROCESSED...",
  "[SYS] ALLOCATING CLUSTER RESOURCES...",
  "[NET] ESTABLISHING MONTE CARLO SEEDS...",
  "[STAT] INGESTING HISTORICAL VECTORS...",
  "[SYS] CORES ACTIVE.",
  "[SIM] BATCH 1 COMPLETE.",
];

const POSITIONS = ["PG", "SG", "SF", "PF", "C"];

interface Props {
  teamA: (Player | null)[];
  teamB: (Player | null)[];
  simsDone: number;
  ciHalf: number;
  meanMargin: number;
}

const SimulatingScreen: FC<Props> = ({ teamA, teamB, simsDone, ciHalf, meanMargin }) => (
  <div className="flex-1 grid grid-cols-4 md:grid-cols-12 gap-6 h-full overflow-hidden">

    {/* LEFT RAIL — Team A (dimmed) */}
    <aside className="col-span-4 md:col-span-3 flex flex-col gap-2 opacity-40 transition-opacity duration-500">
      <div className="border-b border-outline-variant/50 pb-2 mb-1">
        <span className="text-[10px] font-mono text-team-a uppercase tracking-widest">Home</span>
      </div>
      <div className="flex flex-col gap-1">
        {teamA.map((p, i) =>
          p ? (
            <div
              key={i}
              className="flex items-center gap-2 px-2.5 py-1.5 bg-surface-container-low border border-outline-variant/30"
            >
              <span className="text-[9px] font-mono text-on-surface-variant bg-surface-variant px-1 py-0.5 flex-shrink-0">
                {POSITIONS[i]}
              </span>
              <span className="text-[12px] text-data-gray truncate">{p.short_name}</span>
              {p.usage_rate != null && (
                <span className="text-[10px] font-mono text-primary ml-auto flex-shrink-0">
                  {(p.usage_rate * 100).toFixed(0)}%
                </span>
              )}
            </div>
          ) : null,
        )}
      </div>
    </aside>

    {/* CENTER — Simulation hub */}
    <section className="col-span-4 md:col-span-6 flex flex-col items-center justify-center relative">
      {/* Live indicator */}
      <div className="absolute top-0 left-1/2 -translate-x-1/2 flex items-center gap-2 bg-surface-container-highest px-4 py-2 border border-primary/30">
        <div className="w-2 h-2 bg-primary animate-pulse-dot" />
        <span className="text-[10px] font-mono text-primary uppercase tracking-wider">
          Live Simulation
        </span>
      </div>

      {/* Progress ring */}
      <ProgressRing simsDone={simsDone} ciHalf={ciHalf} meanMargin={meanMargin} />

      {/* Team matchup indicator */}
      <div className="mt-4 flex items-center justify-center gap-6">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 bg-team-a" />
          <span className="text-[10px] font-mono text-on-surface-variant uppercase">Team A</span>
        </div>
        <span className="text-on-surface-variant/40 text-[10px] font-mono">VS</span>
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 bg-team-b" />
          <span className="text-[10px] font-mono text-on-surface-variant uppercase">Team B</span>
        </div>
      </div>

      {/* Telemetry log */}
      <div className="mt-4 w-full max-w-md bg-surface-container-lowest border border-outline-variant h-24 overflow-hidden relative">
        <div className="absolute inset-x-0 top-0 h-4 bg-gradient-to-b from-surface-container-lowest to-transparent z-10 pointer-events-none" />
        <div className="absolute inset-x-0 bottom-0 h-4 bg-gradient-to-t from-surface-container-lowest to-transparent z-10 pointer-events-none" />
        <div className="animate-scroll-up flex flex-col gap-0.5 p-3">
          {[...LOG_ENTRIES, ...LOG_ENTRIES].map((line, i) => (
            <span
              key={i}
              className={`text-[10px] font-mono whitespace-nowrap ${
                line.startsWith("[SIM]")
                  ? "text-secondary"
                  : line.startsWith("[STAT]")
                  ? "text-primary"
                  : "text-data-gray/60"
              }`}
            >
              {line}
            </span>
          ))}
        </div>
      </div>
    </section>

    {/* RIGHT RAIL — Team B (dimmed) */}
    <aside className="col-span-4 md:col-span-3 flex flex-col gap-2 opacity-40 transition-opacity duration-500">
      <div className="border-b border-outline-variant/50 pb-2 mb-1 text-right">
        <span className="text-[10px] font-mono text-team-b uppercase tracking-widest">Away</span>
      </div>
      <div className="flex flex-col gap-1">
        {teamB.map((p, i) =>
          p ? (
            <div
              key={i}
              className="flex items-center gap-2 px-2.5 py-1.5 bg-surface-container-low border border-outline-variant/30 flex-row-reverse"
            >
              <span className="text-[9px] font-mono text-on-surface-variant bg-surface-variant px-1 py-0.5 flex-shrink-0">
                {POSITIONS[i]}
              </span>
              <span className="text-[12px] text-data-gray truncate">{p.short_name}</span>
              {p.usage_rate != null && (
                <span className="text-[10px] font-mono text-tertiary mr-auto flex-shrink-0">
                  {(p.usage_rate * 100).toFixed(0)}%
                </span>
              )}
            </div>
          ) : null,
        )}
      </div>
    </aside>
  </div>
);

export default SimulatingScreen;
