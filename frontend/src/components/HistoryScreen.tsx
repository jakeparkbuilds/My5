import type { FC } from "react";
import type { Player, SimResult } from "../lib/types";
import { IconHistory, IconEdit } from "./Icons";

export interface HistoryEntry {
  id: string;
  timestamp: Date;
  teamAName: string;
  teamBName: string;
  teamA: (Player | null)[];
  teamB: (Player | null)[];
  result: SimResult;
  fromCache: boolean;
}

interface Props {
  entries: HistoryEntry[];
  onRestore: (entry: HistoryEntry) => void;
  onClearHistory: () => void;
}

const POSITIONS = ["PG", "SG", "SF", "PF", "C"];

const HistoryScreen: FC<Props> = ({ entries, onRestore, onClearHistory }) => {
  if (entries.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-4 p-8">
        <div className="w-12 h-12 border border-outline-variant/40 flex items-center justify-center">
          <IconHistory size={20} className="text-outline/60" />
        </div>
        <div className="text-center">
          <p className="text-sm font-mono text-on-surface-variant">No simulations this session</p>
          <p className="text-[11px] font-mono text-outline/50 mt-1 uppercase tracking-wider">
            Run a simulation to see results here
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-[900px] mx-auto">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-lg font-bold text-on-surface">Session History</h2>
            <p className="text-[11px] font-mono text-outline/70 mt-0.5 uppercase tracking-wider">
              {entries.length} simulation{entries.length !== 1 ? "s" : ""} this session
            </p>
          </div>
          <button
            onClick={onClearHistory}
            className="text-[11px] font-mono text-outline/60 hover:text-error transition-colors uppercase tracking-wider px-2 py-1 border border-outline-variant/30 hover:border-error/40"
          >
            Clear All
          </button>
        </div>

        <div className="flex flex-col gap-3">
          {entries.map((entry) => {
            const teamAWins = entry.result.mean_margin >= 0;
            const winnerName = teamAWins ? entry.teamAName : entry.teamBName;
            const net = entry.result.equiv_net_rating;
            const netText = `${net >= 0 ? "+" : ""}${net.toFixed(2)} pts/100`;
            const time = entry.timestamp.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

            return (
              <div
                key={entry.id}
                className="bg-surface-container-low border border-outline-variant/50 hover:border-outline-variant transition-colors p-4"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    {/* Winner headline */}
                    <div className="flex items-center gap-2 mb-3 flex-wrap">
                      <span className={`text-sm font-bold font-mono uppercase ${teamAWins ? "text-team-a" : "text-team-b"}`}>
                        {winnerName} Advantage
                      </span>
                      <span className="text-[11px] font-mono text-outline/70">{netText}</span>
                      {entry.fromCache && (
                        <span className="text-[10px] font-mono text-outline/50 bg-surface-container px-1.5 py-0.5 border border-outline-variant/30">
                          CACHED
                        </span>
                      )}
                    </div>

                    {/* Lineup pair */}
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <div className="text-[10px] font-mono text-team-a uppercase tracking-widest mb-1.5">
                          {entry.teamAName}
                        </div>
                        <div className="flex flex-col gap-0.5">
                          {entry.teamA.map((p, i) =>
                            p ? (
                              <div key={i} className="text-[11px] font-mono text-on-surface-variant flex gap-1.5">
                                <span className="text-outline/50 w-5 flex-shrink-0">{POSITIONS[i]}</span>
                                <span className="truncate">{p.short_name}</span>
                              </div>
                            ) : null,
                          )}
                        </div>
                      </div>
                      <div>
                        <div className="text-[10px] font-mono text-team-b uppercase tracking-widest mb-1.5">
                          {entry.teamBName}
                        </div>
                        <div className="flex flex-col gap-0.5">
                          {entry.teamB.map((p, i) =>
                            p ? (
                              <div key={i} className="text-[11px] font-mono text-on-surface-variant flex gap-1.5">
                                <span className="text-outline/50 w-5 flex-shrink-0">{POSITIONS[i]}</span>
                                <span className="truncate">{p.short_name}</span>
                              </div>
                            ) : null,
                          )}
                        </div>
                      </div>
                    </div>
                  </div>

                  <div className="flex flex-col items-end gap-3 flex-shrink-0">
                    <span className="text-[10px] font-mono text-outline/50">{time}</span>
                    <button
                      onClick={() => onRestore(entry)}
                      className="flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-mono border border-outline-variant/60 text-on-surface-variant hover:border-primary/60 hover:text-primary transition-colors"
                    >
                      <IconEdit size={12} />
                      Restore
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

export default HistoryScreen;
