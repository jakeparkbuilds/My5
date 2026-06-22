/**
 * Player picker modal — client-side search over the full player list.
 * List is downloaded once; every keystroke filters in-memory.
 */
import { useEffect, useMemo, useRef, useState, type FC } from "react";
import { fetchPlayers } from "../../lib/api";
import type { Player } from "../../lib/types";
import { IconSearch, IconClose } from "../Icons";

interface Props {
  team: "a" | "b";
  /** IDs already selected on both teams — these are greyed out */
  usedIds: Set<number>;
  onSelect: (player: Player) => void;
  onClose: () => void;
}

const PlayerPickerModal: FC<Props> = ({ team, usedIds, onSelect, onClose }) => {
  const [players, setPlayers] = useState<Player[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const inputRef = useRef<HTMLInputElement>(null);
  const accentBorder = team === "a" ? "border-team-a/40" : "border-team-b/40";

  useEffect(() => {
    fetchPlayers().then((list) => {
      setPlayers(list);
      setLoading(false);
    });
    const t = setTimeout(() => inputRef.current?.focus(), 50);
    return () => clearTimeout(t);
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);

  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    if (!q) return players;
    return players.filter(
      (p) =>
        p.display_name.toLowerCase().includes(q) ||
        p.team_abbr.toLowerCase().includes(q) ||
        p.team_name.toLowerCase().includes(q),
    );
  }, [players, query]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      {/* Backdrop */}
      <div className="absolute inset-0 bg-obsidian-base/80 backdrop-blur-sm" />

      {/* Panel */}
      <div
        className={`relative z-10 w-full max-w-md bg-surface-container-low border ${accentBorder} flex flex-col`}
        style={{ maxHeight: "80vh" }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-outline-variant">
          <h2 className="text-sm font-mono font-semibold text-on-surface uppercase tracking-wider">
            Select Player — {team === "a" ? "Home" : "Away"}
          </h2>
          <button
            onClick={onClose}
            className="text-on-surface-variant hover:text-on-surface transition-colors"
          >
            <IconClose size={18} />
          </button>
        </div>

        {/* Search */}
        <div className="px-4 py-3 border-b border-outline-variant">
          <div className="relative">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-outline">
              <IconSearch size={16} />
            </span>
            <input
              ref={inputRef}
              type="text"
              placeholder="Name or team..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="w-full bg-surface-container border border-outline-variant pl-9 pr-3 py-2 text-sm text-on-surface placeholder-outline focus:border-primary focus:outline-none font-mono"
            />
          </div>
        </div>

        {/* List */}
        <div className="overflow-y-auto flex-1">
          {loading && (
            <div className="flex items-center justify-center py-8 text-on-surface-variant text-sm font-mono">
              Loading players...
            </div>
          )}
          {!loading && filtered.length === 0 && (
            <div className="flex items-center justify-center py-8 text-outline text-sm font-mono">
              No players found
            </div>
          )}
          {!loading &&
            filtered.map((p) => {
              const used = usedIds.has(p.athlete_id);
              const usg = p.usage_rate ? `${(p.usage_rate * 100).toFixed(1)}%` : "";
              return (
                <button
                  key={p.athlete_id}
                  disabled={used}
                  onClick={() => {
                    if (!used) {
                      onSelect(p);
                      onClose();
                    }
                  }}
                  className={`w-full flex items-center gap-3 px-4 py-2.5 border-b border-outline-variant/30 text-left transition-colors ${
                    used
                      ? "opacity-30 cursor-not-allowed"
                      : "hover:bg-surface-container-high cursor-pointer"
                  }`}
                >
                  <div className="w-8 h-8 bg-surface-container border border-outline-variant flex items-center justify-center overflow-hidden flex-shrink-0">
                    <img
                      src={p.headshot_href}
                      alt=""
                      className="w-full h-full object-cover object-top"
                      onError={(e) => {
                        (e.currentTarget as HTMLImageElement).style.display = "none";
                      }}
                    />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm text-on-surface font-medium truncate">
                      {p.display_name}
                    </div>
                    <div className="text-xs font-mono text-on-surface-variant">{p.team_abbr}</div>
                  </div>
                  {usg && (
                    <span className="text-xs font-mono text-outline flex-shrink-0">{usg} USG</span>
                  )}
                  {used && (
                    <span className="text-xs font-mono text-outline flex-shrink-0">IN USE</span>
                  )}
                </button>
              );
            })}
        </div>

        {!loading && (
          <div className="px-4 py-2 border-t border-outline-variant">
            <p className="text-xs font-mono text-outline">
              {query
                ? `${filtered.length} of ${players.length} players`
                : `${players.length} players available`}{" "}
              · 2024-25 NBA
            </p>
          </div>
        )}
      </div>
    </div>
  );
};

export default PlayerPickerModal;
