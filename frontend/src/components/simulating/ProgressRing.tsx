/**
 * SVG circular progress ring — outer ring tracks convergence progress.
 * Follows the Stitch live-engine design exactly.
 */
import type { FC } from "react";

interface Props {
  simsMax?: number;
  simsDone: number;
  ciHalf: number;
  meanMargin: number;
}

const ProgressRing: FC<Props> = ({ simsMax = 5000, simsDone, ciHalf, meanMargin }) => {
  const RADIUS = 45;
  const CIRCUMFERENCE = 2 * Math.PI * RADIUS;
  const progress = Math.min(simsDone / simsMax, 1);
  const dashOffset = CIRCUMFERENCE * (1 - progress);
  const ciDisplay = ciHalf === Infinity || ciHalf === 0 ? "—" : `±${ciHalf.toFixed(2)}`;
  const marginSign = meanMargin >= 0 ? "+" : "";
  const marginDisplay = simsDone > 0 ? `${marginSign}${meanMargin.toFixed(1)}` : "—";

  return (
    <div className="relative w-72 h-72 flex items-center justify-center mx-auto">
      <svg
        className="absolute inset-0 w-full h-full -rotate-90"
        viewBox="0 0 100 100"
      >
        {/* Track */}
        <circle cx="50" cy="50" r={RADIUS} fill="none" stroke="#3d494c" strokeWidth="4" />
        {/* Progress arc */}
        <circle
          cx="50"
          cy="50"
          r={RADIUS}
          fill="none"
          stroke="#4cd7f6"
          strokeWidth="4"
          strokeDasharray={CIRCUMFERENCE}
          strokeDashoffset={dashOffset}
          strokeLinecap="butt"
          className="transition-all duration-700 ease-linear"
        />
      </svg>

      {/* Center content */}
      <div className="text-center z-10 flex flex-col items-center">
        <span className="text-xs font-mono text-on-surface-variant uppercase tracking-widest mb-2">
          Sims Executed
        </span>
        <span className="text-3xl font-bold text-secondary leading-tight">
          {simsDone.toLocaleString()}
          <span className="text-base text-on-surface-variant font-normal ml-1">/ {simsMax.toLocaleString()}</span>
        </span>

        <div className="mt-6 border-t border-outline-variant/50 pt-4 w-44">
          <span className="text-xs font-mono text-on-surface-variant block mb-1 uppercase tracking-wider">
            Convergence
          </span>
          <span
            className="text-4xl font-extrabold text-primary text-glow-cyan leading-none"
          >
            {ciDisplay}
          </span>
        </div>

        <div className="mt-4">
          <span className="text-xs font-mono text-on-surface-variant block mb-1 uppercase tracking-wider">
            Current Margin
          </span>
          <span className={`text-2xl font-semibold text-on-surface ${simsDone > 100 ? "animate-wobble" : ""}`}>
            {marginDisplay}
          </span>
        </div>
      </div>
    </div>
  );
};

export default ProgressRing;
