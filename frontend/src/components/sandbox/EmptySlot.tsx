import { useState, type FC } from "react";

interface Props {
  label: string;
  team: "a" | "b";
  onClick: () => void;
  onDrop?: () => void;
}

const EmptySlot: FC<Props> = ({ label, team, onClick, onDrop }) => {
  const [isOver, setIsOver] = useState(false);
  const hoverBorder = team === "a" ? "hover:border-team-a/60" : "hover:border-team-b/60";
  const hoverBg = team === "a" ? "hover:bg-team-a/5" : "hover:bg-team-b/5";
  const overClass =
    team === "a"
      ? "border-team-a/70 bg-team-a/10 border-solid"
      : "border-team-b/70 bg-team-b/10 border-solid";

  return (
    <div
      className={`border border-dashed flex-1 min-h-16 flex items-center justify-center px-4 cursor-pointer transition-all group ${
        isOver
          ? overClass
          : `border-outline-variant/60 bg-surface-container-low ${hoverBorder} ${hoverBg}`
      }`}
      onClick={onClick}
      onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; }}
      onDragEnter={(e) => { e.preventDefault(); setIsOver(true); }}
      onDragLeave={() => setIsOver(false)}
      onDrop={(e) => { e.preventDefault(); setIsOver(false); onDrop?.(); }}
    >
      <span
        className={`text-sm font-mono tracking-widest transition-colors select-none uppercase ${
          isOver
            ? team === "a" ? "text-team-a" : "text-team-b"
            : "text-on-surface-variant/50 group-hover:text-on-surface-variant"
        }`}
      >
        + {label}
      </span>
    </div>
  );
};

export default EmptySlot;
