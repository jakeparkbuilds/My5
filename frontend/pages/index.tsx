/**
 * My5 Lab — main page.
 *
 * State machine:
 *   idle        → SandboxScreen (builder)
 *   submitting  → SandboxScreen with center hub in "submitting" state
 *   simulating  → SimulatingScreen (live engine)
 *   result      → ResultScreen
 *   error       → SandboxScreen with error toast
 *
 * Views (independent of simulation state):
 *   sandbox  → shows the current simulation state screens above
 *   history  → session history of completed simulations
 *   about    → static about page
 */
import type { NextPage } from "next";
import { useEffect, useRef, useState } from "react";
import { useSimulation } from "../src/hooks/useSimulation";
import SideNav, { type NavView } from "../src/components/SideNav";
import SandboxScreen from "../src/components/sandbox/SandboxScreen";
import SimulatingScreen from "../src/components/simulating/SimulatingScreen";
import ResultScreen from "../src/components/result/ResultScreen";
import HistoryScreen, { type HistoryEntry } from "../src/components/HistoryScreen";
import AboutScreen from "../src/components/AboutScreen";
import { IconError } from "../src/components/Icons";

const Home: NextPage = () => {
  const {
    state,
    teamA,
    teamB,
    setPlayer,
    clearTeam,
    simulate,
    reset,
    canSimulate,
  } = useSimulation();

  const [view, setView] = useState<NavView>("sandbox");
  const [teamAName, setTeamAName] = useState("Home");
  const [teamBName, setTeamBName] = useState("Away");
  const [sessionHistory, setSessionHistory] = useState<HistoryEntry[]>([]);
  const prevPhaseRef = useRef<string>("");

  // Push each completed simulation into session history (once per transition).
  useEffect(() => {
    if (state.phase === "result" && prevPhaseRef.current !== "result") {
      setSessionHistory((prev) => [
        {
          id: `${Date.now()}`,
          timestamp: new Date(),
          teamAName,
          teamBName,
          teamA: [...teamA],
          teamB: [...teamB],
          result: state.result,
          fromCache: state.from_cache,
        },
        ...prev,
      ]);
    }
    prevPhaseRef.current = state.phase;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.phase]);

  const handleSetTeamName = (team: "a" | "b", name: string) => {
    if (team === "a") setTeamAName(name);
    else setTeamBName(name);
  };

  const handleRestoreFromHistory = (entry: HistoryEntry) => {
    for (let i = 0; i < 5; i++) {
      setPlayer("a", i, entry.teamA[i]);
      setPlayer("b", i, entry.teamB[i]);
    }
    setTeamAName(entry.teamAName);
    setTeamBName(entry.teamBName);
    reset();
    setView("sandbox");
  };

  const isBuilder = state.phase === "idle" || state.phase === "submitting" || state.phase === "error";
  const isSimulating = state.phase === "simulating";
  const isResult = state.phase === "result";
  const noBlueprint = view === "sandbox" && isResult;

  return (
    <div className="flex min-h-screen bg-obsidian-base text-on-surface">
      <SideNav activeView={view} onNav={setView} />

      {/* Mobile header */}
      <header className="md:hidden flex justify-between items-center w-full px-4 h-16 bg-obsidian-base/80 backdrop-blur-xl border-b border-outline-variant fixed top-0 z-40">
        <h1 className="text-xl font-bold text-primary tracking-tight">My5 Lab</h1>
        <span className="text-xs font-mono text-on-surface-variant uppercase">Basketball Lab</span>
      </header>

      <main
        className={`flex-1 md:ml-64 relative h-screen overflow-y-auto pt-16 md:pt-10 ${
          noBlueprint ? "" : "bg-blueprint p-6"
        }`}
      >
        <div className={`${noBlueprint ? "h-full" : "max-w-[1600px] mx-auto h-full flex flex-col"}`}>

          {view === "sandbox" && (
            <>
              {/* Error toast */}
              {state.phase === "error" && (
                <div className="mb-4 flex items-center gap-3 px-4 py-3 bg-error/10 border border-error text-error text-sm font-mono">
                  <IconError size={16} className="flex-shrink-0" />
                  {state.message}
                  <button onClick={reset} className="ml-auto underline hover:no-underline text-xs">
                    Dismiss
                  </button>
                </div>
              )}

              {isBuilder && (
                <SandboxScreen
                  teamA={teamA}
                  teamB={teamB}
                  canSimulate={canSimulate}
                  phase={state.phase === "submitting" ? "submitting" : "idle"}
                  onSetPlayer={setPlayer}
                  onClearTeam={clearTeam}
                  onSimulate={simulate}
                  teamAName={teamAName}
                  teamBName={teamBName}
                  onSetTeamName={handleSetTeamName}
                />
              )}

              {isSimulating && (
                <SimulatingScreen
                  teamA={teamA}
                  teamB={teamB}
                  simsDone={state.sims_done}
                  ciHalf={state.ci_half}
                  meanMargin={state.mean_margin}
                />
              )}

              {isResult && (
                <ResultScreen
                  result={state.result}
                  fromCache={state.from_cache}
                  teamA={teamA}
                  teamB={teamB}
                  teamAName={teamAName}
                  teamBName={teamBName}
                  onRerun={simulate}
                  onEdit={reset}
                />
              )}
            </>
          )}

          {view === "history" && (
            <HistoryScreen
              entries={sessionHistory}
              onRestore={handleRestoreFromHistory}
              onClearHistory={() => setSessionHistory([])}
            />
          )}

          {view === "about" && <AboutScreen />}

        </div>
      </main>
    </div>
  );
};

export default Home;
