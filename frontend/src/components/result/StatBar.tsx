import type { FC } from "react";

interface Props {
  label: string;
  aValue: string;
  bValue: string;
  aPct: number;   // 0–100, Team A's share of the bar
  reverseBar?: boolean; // higher A value is WORSE (e.g. turnover %)
}

const StatBar: FC<Props> = ({ label, aValue, bValue, aPct, reverseBar }) => {
  const bPct = 100 - aPct;
  return (
    <div className="space-y-2">
      <div className="flex justify-between text-[12px] font-mono">
        <span className="text-primary">{aValue}</span>
        <span className="text-on-surface-variant uppercase tracking-wider">{label}</span>
        <span className="text-team-b">{bValue}</span>
      </div>
      {/* For reverseBar (TOV%), swap visual order so lower-TOV team owns more of its side */}
      <div className={`flex h-1.5 bg-surface-variant overflow-hidden gap-0.5 ${reverseBar ? "flex-row-reverse" : ""}`}>
        <div className={`h-full transition-all duration-700 ${reverseBar ? "bg-team-b" : "bg-primary"}`} style={{ width: `${reverseBar ? bPct : aPct}%` }} />
        <div className={`h-full transition-all duration-700 ${reverseBar ? "bg-primary" : "bg-team-b"}`} style={{ width: `${reverseBar ? aPct : bPct}%` }} />
      </div>
    </div>
  );
};

export default StatBar;
