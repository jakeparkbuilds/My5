import { useState, type FC } from "react";
import type { Player } from "../../lib/types";

const POSITIONS = ["PG", "SG", "SF", "PF", "C"];

interface Props {
  player: Player;
  team: "a" | "b";
  slot: number;
  onClick: () => void;
  onDragStart?: () => void;
  onDrop?: () => void;
}

function calcEfg(p: Player): number | null {
  if (p.rim_fg_pct == null || p.fg3_pct == null || p.mid_fg_pct == null) return null;
  return (p.rim_fg_pct * 0.45 + p.mid_fg_pct * 0.175 + p.fg3_pct * 0.375 * 1.5) / 0.8;
}

function computeOvr(p: Player): number {
  const usg = p.usage_rate ?? 0.15;
  const efg = calcEfg(p) ?? 0.52;
  return Math.round(Math.min(99, Math.max(60, 50 + usg * 80 + efg * 30)));
}

const PlayerCard: FC<Props> = ({ player, team, slot, onClick, onDragStart, onDrop }) => {
  const [imgFailed, setImgFailed] = useState(false);
  const [isOver, setIsOver] = useState(false);

  const accentColor = team === "a" ? "text-team-a" : "text-team-b";
  const accentBg = team === "a" ? "bg-team-a" : "bg-team-b";
  const hoverClass = team === "a" ? "player-card-a" : "player-card-b";
  const overBorder = team === "a" ? "border-team-a bg-surface-container-high" : "border-team-b bg-surface-container-high";

  const pos = POSITIONS[slot];
  const usg = player.usage_rate != null ? (player.usage_rate * 100).toFixed(1) : "—";
  const efgNum = calcEfg(player);
  const efg = efgNum != null ? (efgNum * 100).toFixed(1) : "—";
  const tov = player.tov_rate != null ? (player.tov_rate * 100).toFixed(1) : "—";
  const ovr = computeOvr(player);

  const initials = player.display_name
    .split(" ")
    .map((w) => w[0])
    .join("")
    .toUpperCase()
    .slice(0, 2);

  return (
    <div
      className={`relative flex-1 bg-surface-container border flex items-stretch overflow-hidden group transition-all ${hoverClass} ${
        isOver ? overBorder : "border-outline-variant/70"
      } ${onDragStart ? "cursor-grab active:cursor-grabbing" : "cursor-pointer"}`}
      onClick={onClick}
      draggable={!!onDragStart}
      onDragStart={(e) => {
        e.dataTransfer.effectAllowed = "move";
        onDragStart?.();
      }}
      onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; }}
      onDragEnter={(e) => { e.preventDefault(); setIsOver(true); }}
      onDragLeave={() => setIsOver(false)}
      onDrop={(e) => { e.preventDefault(); setIsOver(false); onDrop?.(); }}
    >
      {/* Left team-color accent strip */}
      <div className={`w-0.5 flex-shrink-0 ${accentBg} opacity-40 group-hover:opacity-80 transition-opacity`} />

      {/* Headshot column */}
      <div className="w-10 flex-shrink-0 flex flex-col border-r border-outline-variant/40">
        <div className="h-10 flex items-center justify-center overflow-hidden bg-surface-container-low">
          {player.headshot_href && !imgFailed ? (
            <img
              src={player.headshot_href}
              alt={player.display_name}
              className="w-full h-full object-cover object-top"
              onError={() => setImgFailed(true)}
            />
          ) : (
            <span className={`text-[11px] font-bold font-mono ${accentColor}`}>{initials}</span>
          )}
        </div>
        <div className="flex-1 flex items-center justify-center">
          <span className="text-[9px] font-mono text-on-surface-variant/60 uppercase">{pos}</span>
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 min-w-0 px-2.5 py-2">
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-[13px] font-semibold text-on-surface leading-tight truncate">
            {player.short_name}
          </span>
          <span className={`text-[12px] font-bold font-mono ${accentColor} flex-shrink-0`}>
            {ovr}
            <span className="text-[9px] font-normal text-outline ml-0.5">OVR</span>
          </span>
        </div>

        <div className="text-[10px] font-mono text-on-surface-variant/60 mt-0.5 uppercase tracking-wider">
          {player.team_abbr} · {pos}
        </div>

        <div className="flex items-center gap-3 mt-1.5 pt-1.5 border-t border-outline-variant/25">
          <span className="text-[10px] font-mono">
            <span className={`font-medium ${accentColor}`}>{usg}</span>
            <span className="text-outline/50 ml-0.5 text-[9px]">USG</span>
          </span>
          <span className="text-[10px] font-mono">
            <span className="text-on-surface-variant">{efg}</span>
            <span className="text-outline/50 ml-0.5 text-[9px]">eFG</span>
          </span>
          <span className="text-[10px] font-mono">
            <span className="text-on-surface-variant">{tov}</span>
            <span className="text-outline/50 ml-0.5 text-[9px]">TOV</span>
          </span>
        </div>
      </div>

      {/* Hover tint */}
      <div
        className={`absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none ${
          team === "a"
            ? "bg-gradient-to-r from-team-a/5 to-transparent"
            : "bg-gradient-to-r from-team-b/5 to-transparent"
        }`}
      />
    </div>
  );
};

export default PlayerCard;
