import type { FC, ReactElement } from "react";
import { IconFlask, IconGitHub, IconHistory, IconInfo, IconLinkedIn } from "./Icons";

export type NavView = "sandbox" | "history" | "about";

interface Props {
  activeView: NavView;
  onNav: (view: NavView) => void;
}

const NAV_ITEMS: { key: NavView; label: string; icon: (active: boolean) => ReactElement }[] = [
  {
    key: "sandbox",
    label: "Sandbox",
    icon: (active) => <IconFlask size={16} className={active ? "text-primary" : "text-on-surface-variant"} />,
  },
  {
    key: "history",
    label: "History",
    icon: (active) => <IconHistory size={16} className={active ? "text-primary" : "text-on-surface-variant"} />,
  },
  {
    key: "about",
    label: "About",
    icon: (active) => <IconInfo size={16} className={active ? "text-primary" : "text-on-surface-variant"} />,
  },
];

const SideNav: FC<Props> = ({ activeView, onNav }) => (
  <nav className="hidden md:flex flex-col h-screen w-64 fixed left-0 top-0 bg-surface-container-low border-r border-outline-variant py-8 px-4 gap-8 z-50">
    {/* Wordmark */}
    <div className="flex items-center gap-3 px-3">
      <div className="w-9 h-9 bg-primary flex items-center justify-center flex-shrink-0">
        <span className="text-[13px] font-black text-obsidian-base font-mono tracking-tighter">M5</span>
      </div>
      <div>
        <h1 className="text-base font-bold text-primary tracking-tighter leading-none">My5 Lab</h1>
        <p className="text-[10px] font-mono text-on-surface-variant uppercase tracking-wider mt-0.5">
          Engineered Basketball
        </p>
      </div>
    </div>

    <ul className="flex flex-col gap-0.5 mt-2">
      {NAV_ITEMS.map(({ key, label, icon }) => {
        const active = activeView === key;
        return (
          <li key={key}>
            <button
              onClick={() => onNav(key)}
              className={`w-full flex items-center gap-3 px-3 py-2.5 text-left transition-colors ${
                active
                  ? "text-primary font-semibold border-r-2 border-primary bg-surface-container-high"
                  : "text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface"
              }`}
            >
              {icon(active)}
              <span className="text-sm font-mono">{label}</span>
            </button>
          </li>
        );
      })}
    </ul>

    {/* Attribution — designer's signature, bottom-left */}
    <div className="mt-auto px-3 pt-5 border-t border-outline-variant/20">
      <p className="text-[10px] font-mono text-on-surface-variant/35 uppercase tracking-wider mb-3">
        Built by Jake Park
      </p>
      <div className="flex items-center gap-4">
        <a
          href="https://github.com/jakeparkbuilds/My5"
          target="_blank"
          rel="noopener noreferrer"
          aria-label="GitHub"
          className="text-on-surface-variant/25 hover:text-on-surface-variant/60 transition-colors"
        >
          <IconGitHub size={14} />
        </a>
        <a
          href="https://linkedin.com/in/jkeprk"
          target="_blank"
          rel="noopener noreferrer"
          aria-label="LinkedIn"
          className="text-on-surface-variant/25 hover:text-on-surface-variant/60 transition-colors"
        >
          <IconLinkedIn size={14} />
        </a>
      </div>
    </div>
  </nav>
);

export default SideNav;
