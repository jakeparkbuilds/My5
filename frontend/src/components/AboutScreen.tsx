import type { FC, ReactNode } from "react";
import { IconFlask, IconBolt, IconTerminal } from "./Icons";

interface InfoBlockProps {
  icon: ReactNode;
  title: string;
  body: string;
}

const InfoBlock: FC<InfoBlockProps> = ({ icon, title, body }) => (
  <div className="bg-surface-container-low border border-outline-variant/40 p-4">
    <div className="flex items-center gap-2 mb-2">
      {icon}
      <h3 className="text-[11px] font-mono font-semibold text-on-surface uppercase tracking-wider">{title}</h3>
    </div>
    <p className="text-[12px] font-mono text-on-surface-variant leading-relaxed">{body}</p>
  </div>
);

const AboutScreen: FC = () => (
  <div className="flex-1 overflow-y-auto">
    <div className="max-w-[680px] mx-auto">
      <div className="mb-8">
        <div className="flex items-center gap-3 mb-4">
          <div className="w-9 h-9 bg-primary flex items-center justify-center flex-shrink-0">
            <span className="text-[13px] font-black text-obsidian-base font-mono tracking-tighter">M5</span>
          </div>
          <div>
            <h2 className="text-xl font-bold text-primary tracking-tight">My5 Lab</h2>
            <p className="text-[10px] font-mono text-on-surface-variant uppercase tracking-wider mt-0.5">
              Engineered Basketball
            </p>
          </div>
        </div>
        <p className="text-sm font-mono text-on-surface-variant leading-relaxed">
          A distributed basketball lineup simulation sandbox. Build any five-man unit from the 2024-25 NBA season,
          pit it against any other lineup, and a Monte Carlo engine plays out tens of thousands of possessions —
          streaming the outcome distribution back live.
        </p>
        <p className="text-[11px] font-mono text-outline/60 mt-2 leading-relaxed">
          This is a <span className="text-neon-volt">sandbox</span>, not a forecast. The engineering is the point.
        </p>
      </div>

      <div className="flex flex-col gap-3">
        <InfoBlock
          icon={<IconFlask size={14} className="text-primary" />}
          title="Sandbox, Not a Forecast"
          body="Historical lineups are reconstructed from play-by-play data. Novel lineups use empirically parameterized player models blended toward a league baseline via shrinkage. The simulator estimates relative lineup strength — it does not predict actual game outcomes."
        />
        <InfoBlock
          icon={<IconBolt size={14} className="text-team-b" />}
          title="Monte Carlo Possession Model"
          body="Each possession is a finite-state Markov chain. Transition probabilities blend offensive tendency × opponent tendency / league baseline via log5. The simulation runs until the margin confidence interval tightens to a target width — convergence-based stopping, not a fixed iteration count."
        />
        <InfoBlock
          icon={<IconTerminal size={14} className="text-neon-volt" />}
          title="Architecture"
          body="Python simulation engine → SQS job queue → Lambda workers → DynamoDB Streams → API Gateway WebSocket fanout → Next.js live progress. Results cached in DynamoDB via TTL attribute — no Redis, no ElastiCache, zero idle cost. Player parameters aggregated from 52 games of 2024-25 NBA play-by-play via sportsdataverse."
        />
      </div>

      <div className="mt-8 pt-4 border-t border-outline-variant/30 flex flex-col gap-1">
        <p className="text-[10px] font-mono text-outline/50 uppercase tracking-wider">
          Data: 2024-25 NBA Season · 405 players · sportsdataverse-py
        </p>
      </div>
    </div>
  </div>
);

export default AboutScreen;
