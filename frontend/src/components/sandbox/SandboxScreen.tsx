import { useEffect, useRef, useState, type FC } from "react";
import type { Player } from "../../lib/types";
import EmptySlot from "./EmptySlot";
import PlayerCard from "./PlayerCard";
import PlayerPickerModal from "./PlayerPickerModal";
import SimulateHub from "./SimulateHub";

const POSITIONS = ["PG", "SG", "SF", "PF", "C"];

function calcEfg(p: Player): number {
  if (p.rim_fg_pct == null || p.fg3_pct == null || p.mid_fg_pct == null) return 0.52;
  return (p.rim_fg_pct * 0.45 + p.mid_fg_pct * 0.175 + p.fg3_pct * 0.375 * 1.5) / 0.8;
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

interface Props {
  teamA: (Player | null)[];
  teamB: (Player | null)[];
  canSimulate: boolean;
  phase: "idle" | "submitting";
  onSetPlayer: (team: "a" | "b", slot: number, player: Player | null) => void;
  onClearTeam: (team: "a" | "b") => void;
  onSimulate: () => void;
  teamAName: string;
  teamBName: string;
  onSetTeamName: (team: "a" | "b", name: string) => void;
}

interface PickerState {
  team: "a" | "b";
  slot: number;
}

const SandboxScreen: FC<Props> = ({
  teamA,
  teamB,
  canSimulate,
  phase,
  onSetPlayer,
  onClearTeam,
  onSimulate,
  teamAName,
  teamBName,
  onSetTeamName,
}) => {
  const [picker, setPicker] = useState<PickerState | null>(null);
  const dragSrcRef = useRef<{ team: "a" | "b"; slot: number } | null>(null);

  const allUsedIds = new Set<number>(
    [...teamA, ...teamB].filter(Boolean).map((p) => p!.athlete_id),
  );

  const openPicker = (team: "a" | "b", slot: number) => setPicker({ team, slot });
  const closePicker = () => setPicker(null);

  const handleSelect = (player: Player) => {
    if (!picker) return;
    onSetPlayer(picker.team, picker.slot, player);
  };

  const handleDragStart = (team: "a" | "b", slot: number) => {
    dragSrcRef.current = { team, slot };
  };

  const handleDrop = (dstTeam: "a" | "b", dstSlot: number) => {
    const src = dragSrcRef.current;
    dragSrcRef.current = null;
    if (!src) return;
    const { team: srcTeam, slot: srcSlot } = src;
    if (srcTeam === dstTeam && srcSlot === dstSlot) return;
    const srcPlayer = (srcTeam === "a" ? teamA : teamB)[srcSlot];
    const dstPlayer = (dstTeam === "a" ? teamA : teamB)[dstSlot];
    onSetPlayer(srcTeam, srcSlot, dstPlayer ?? null);
    onSetPlayer(dstTeam, dstSlot, srcPlayer!);
  };

  return (
    <>
      <div className="flex-1 flex flex-col h-full">
        {/* Page header */}
        <header className="flex justify-between items-center mb-5 flex-shrink-0">
          <div className="flex items-center gap-3">
            <h2 className="text-lg font-bold text-on-surface tracking-tight">Matchup Simulator</h2>
            <span className="text-[10px] font-mono text-primary bg-primary/10 border border-primary/20 px-2 py-0.5 uppercase tracking-widest">
              Sandbox
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => { onClearTeam("a"); onClearTeam("b"); }}
              className="text-[11px] font-mono uppercase tracking-wider text-on-surface-variant/60 hover:text-on-surface px-3 py-1.5 transition-colors"
            >
              Clear All
            </button>
          </div>
        </header>

        {/* 3-column grid — fills remaining height */}
        <div className="flex-1 grid grid-cols-1 lg:grid-cols-12 gap-4 lg:gap-6 min-h-0">
          <TeamColumn
            team="a"
            players={teamA}
            teamName={teamAName}
            onOpen={openPicker}
            onSetTeamName={(name) => onSetTeamName("a", name)}
            onDragStart={(slot) => handleDragStart("a", slot)}
            onDrop={(slot) => handleDrop("a", slot)}
          />
          <SimulateHub canSimulate={canSimulate} phase={phase} onSimulate={onSimulate} />
          <TeamColumn
            team="b"
            players={teamB}
            teamName={teamBName}
            onOpen={openPicker}
            onSetTeamName={(name) => onSetTeamName("b", name)}
            onDragStart={(slot) => handleDragStart("b", slot)}
            onDrop={(slot) => handleDrop("b", slot)}
          />
        </div>
      </div>

      {picker && (
        <PlayerPickerModal
          team={picker.team}
          usedIds={allUsedIds}
          onSelect={handleSelect}
          onClose={closePicker}
        />
      )}
    </>
  );
};

// ── Team column ───────────────────────────────────────────────────────────────

interface TeamColumnProps {
  team: "a" | "b";
  players: (Player | null)[];
  teamName: string;
  onOpen: (team: "a" | "b", slot: number) => void;
  onSetTeamName: (name: string) => void;
  onDragStart: (slot: number) => void;
  onDrop: (slot: number) => void;
}

const TeamColumn: FC<TeamColumnProps> = ({
  team,
  players,
  teamName,
  onOpen,
  onSetTeamName,
  onDragStart,
  onDrop,
}) => {
  const [editing, setEditing] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const accentColor = team === "a" ? "text-team-a" : "text-team-b";
  const dotBg = team === "a" ? "bg-team-a" : "bg-team-b";
  const dotGlow =
    team === "a"
      ? "0 0 6px rgba(76,215,246,0.7)"
      : "0 0 6px rgba(255,184,115,0.7)";
  const inputBorder = team === "a" ? "border-team-a/60" : "border-team-b/60";

  const offRtg = computeOffRtg(players);
  const defRtg = computeDefRtg(players);

  const commitEdit = (val: string) => {
    onSetTeamName(val.trim() || (team === "a" ? "Home" : "Away"));
    setEditing(false);
  };

  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  const ratingPills = (
    <div className="flex gap-1">
      <span className="text-[10px] font-mono text-on-surface-variant/60 bg-surface-container-high px-1.5 py-0.5 border border-outline-variant/40">
        {offRtg}
      </span>
      <span className="text-[10px] font-mono text-on-surface-variant/60 bg-surface-container-high px-1.5 py-0.5 border border-outline-variant/40">
        {defRtg}
      </span>
    </div>
  );

  const nameEl = editing ? (
    <input
      ref={inputRef}
      type="text"
      defaultValue={teamName}
      maxLength={20}
      className={`text-[11px] font-mono font-semibold uppercase tracking-widest bg-transparent border-b ${inputBorder} focus:outline-none w-28 ${accentColor}`}
      onBlur={(e) => commitEdit(e.currentTarget.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
        if (e.key === "Escape") setEditing(false);
      }}
    />
  ) : (
    <span
      className={`text-[11px] font-mono font-semibold ${accentColor} uppercase tracking-widest cursor-text hover:opacity-70 transition-opacity`}
      onClick={() => setEditing(true)}
      title="Click to rename"
    >
      {teamName}
    </span>
  );

  const heading = (
    <div className="flex items-center gap-2">
      <span className={`w-2 h-2 ${dotBg} flex-shrink-0`} style={{ boxShadow: dotGlow }} />
      {nameEl}
    </div>
  );

  return (
    <section className="lg:col-span-4 flex flex-col gap-1.5">
      {/* Column header */}
      <div className="flex items-center justify-between mb-2 flex-shrink-0">
        {team === "a" ? (
          <>
            {heading}
            {ratingPills}
          </>
        ) : (
          <>
            {ratingPills}
            {heading}
          </>
        )}
      </div>

      {/* Slot list — flex-1 so slots share all remaining column height */}
      <div className="flex-1 flex flex-col gap-2">
        {players.map((player, slot) =>
          player ? (
            <PlayerCard
              key={slot}
              player={player}
              team={team}
              slot={slot}
              onClick={() => onOpen(team, slot)}
              onDragStart={() => onDragStart(slot)}
              onDrop={() => onDrop(slot)}
            />
          ) : (
            <EmptySlot
              key={slot}
              label={POSITIONS[slot]}
              team={team}
              onClick={() => onOpen(team, slot)}
              onDrop={() => onDrop(slot)}
            />
          ),
        )}
      </div>
    </section>
  );
};

export default SandboxScreen;
