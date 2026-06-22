import type { FC } from "react";
import { IconBasketball, IconPlay, IconSpinner } from "../Icons";

interface Props {
  canSimulate: boolean;
  phase: "idle" | "submitting";
  onSimulate: () => void;
}

// Corner accent marks for the simulate button
const CORNERS = [
  "top-2 left-2 border-t-2 border-l-2",
  "top-2 right-2 border-t-2 border-r-2",
  "bottom-2 left-2 border-b-2 border-l-2",
  "bottom-2 right-2 border-b-2 border-r-2",
] as const;

const SimulateHub: FC<Props> = ({ canSimulate, phase, onSimulate }) => {
  const isSubmitting = phase === "submitting";
  const isReady = canSimulate && !isSubmitting;

  return (
    <section className="lg:col-span-4 flex flex-col justify-center relative px-4 py-6 border-x border-outline-variant/20">
      {/* Decorative vertical lines */}
      <div className="absolute top-0 left-1/2 -translate-x-1/2 w-px h-10 bg-gradient-to-b from-transparent to-outline-variant/40" />
      <div className="absolute bottom-0 left-1/2 -translate-x-1/2 w-px h-10 bg-gradient-to-t from-transparent to-outline-variant/40" />

      <div className="flex flex-col items-center gap-5 z-10">
        {/* Basketball mark — generic SVG, not an NBA/league trademark */}
        <IconBasketball size={44} className="text-outline/30" />

        {/* Status badge */}
        <div className="flex items-center gap-2 bg-surface-container-highest px-3 py-1.5 border border-outline-variant/60">
          <span
            className={`w-2 h-2 flex-shrink-0 transition-colors ${
              isSubmitting
                ? "bg-primary animate-pulse"
                : isReady
                ? "bg-neon-volt"
                : "bg-outline/40"
            }`}
          />
          <span className="text-[10px] font-mono text-on-surface-variant uppercase tracking-widest">
            {isSubmitting ? "Running..." : isReady ? "Ready" : "Assign 10 players"}
          </span>
        </div>

        {/* Simulate button — primary action of the entire page */}
        <button
          disabled={!canSimulate || isSubmitting}
          onClick={onSimulate}
          className={[
            "w-full max-w-[210px] aspect-square flex flex-col items-center justify-center gap-3 relative overflow-hidden transition-all duration-200 group",
            isSubmitting
              ? "border border-primary/40 bg-surface-container cursor-wait"
              : isReady
              ? "border-2 border-primary bg-surface-container-high cursor-pointer glow-simulate"
              : "border border-outline-variant/25 bg-surface-container/40 cursor-not-allowed",
          ].join(" ")}
        >
          {/* Cyan tint on hover (only when ready) */}
          {isReady && (
            <div className="absolute inset-0 bg-primary opacity-0 group-hover:opacity-[0.04] transition-opacity pointer-events-none" />
          )}

          {/* Icon */}
          {isSubmitting ? (
            <IconSpinner size={38} className="text-primary" />
          ) : (
            <IconPlay
              size={38}
              className={`transition-colors ${
                isReady
                  ? "text-primary group-hover:scale-110"
                  : "text-outline/30"
              }`}
            />
          )}

          {/* Label */}
          <div className="text-center">
            <div
              className={`text-xl font-bold tracking-widest uppercase transition-colors ${
                isReady ? "text-primary" : isSubmitting ? "text-primary/60" : "text-outline/30"
              }`}
            >
              {isSubmitting ? "Running" : "Simulate"}
            </div>
            <div
              className={`text-[10px] font-mono tracking-widest uppercase mt-0.5 ${
                isReady ? "text-primary/50" : "text-outline/20"
              }`}
            >
              10,000 Iterations
            </div>
          </div>

          {/* Corner accent marks — only when ready */}
          {isReady &&
            CORNERS.map((cls, i) => (
              <div
                key={i}
                className={`absolute w-3 h-3 ${cls} border-primary/50 group-hover:border-primary transition-colors`}
              />
            ))}
        </button>

        {/* Telemetry strip — compact */}
        <div className="w-full font-mono text-[10px] text-outline/60 border border-outline-variant/25 p-3 bg-obsidian-base/50 space-y-2">
          <div className="flex justify-between uppercase tracking-wider">
            <span>Convergence CI</span>
            <span>±0.0</span>
          </div>
          <div className="h-px w-full bg-surface-container-highest">
            <div className="h-full w-0 bg-primary transition-all duration-700" />
          </div>
          <div className="flex justify-between uppercase tracking-wider mt-1">
            <span>Sims Done</span>
            <span>0 / 10k</span>
          </div>
          <div className="h-px w-full bg-surface-container-highest">
            <div className="h-full w-0 bg-gradient-to-r from-primary to-neon-volt transition-all duration-700" />
          </div>
          <div className="mt-1 h-16 bg-obsidian-base border border-outline-variant/20 p-2 overflow-hidden flex flex-col gap-0.5">
            <span className="text-on-surface-variant/50 text-[10px]">[SYS] AWAITING INPUT...</span>
            <span className="text-on-surface-variant/50 text-[10px]">[SYS] MATRIX IDLE.</span>
            <span className="text-primary/70 text-[10px] animate-pulse">_</span>
          </div>
        </div>
      </div>
    </section>
  );
};

export default SimulateHub;
