import type { FC, ReactNode } from "react";

export type IconProps = { size?: number; className?: string };

const Base: FC<{ size: number; className?: string; children: ReactNode }> = ({
  size,
  className,
  children,
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={2}
    strokeLinecap="square"
    strokeLinejoin="miter"
    className={className}
    aria-hidden="true"
  >
    {children}
  </svg>
);

// Two-subpath flask: mouth line + body outline
export const IconFlask: FC<IconProps> = ({ size = 18, className }) => (
  <Base size={size} className={className}>
    <path d="M8 3h8M10 3v5L6 18h12L14 8V3" />
  </Base>
);

export const IconHistory: FC<IconProps> = ({ size = 18, className }) => (
  <Base size={size} className={className}>
    <circle cx="12" cy="12" r="9" />
    <polyline points="12 7 12 12 15 15" />
  </Base>
);

export const IconInfo: FC<IconProps> = ({ size = 18, className }) => (
  <Base size={size} className={className}>
    <circle cx="12" cy="12" r="9" />
    <line x1="12" y1="11" x2="12" y2="17" />
    <circle cx="12" cy="7.5" r="0.75" fill="currentColor" stroke="none" />
  </Base>
);

// Three horizontal lines, decreasing width (funnel shape = filter)
export const IconFilter: FC<IconProps> = ({ size = 18, className }) => (
  <Base size={size} className={className}>
    <line x1="4" y1="6" x2="20" y2="6" />
    <line x1="7" y1="12" x2="17" y2="12" />
    <line x1="10" y1="18" x2="14" y2="18" />
  </Base>
);

export const IconSearch: FC<IconProps> = ({ size = 18, className }) => (
  <Base size={size} className={className}>
    <circle cx="11" cy="11" r="7" />
    <line x1="16.5" y1="16.5" x2="21" y2="21" />
  </Base>
);

export const IconClose: FC<IconProps> = ({ size = 18, className }) => (
  <Base size={size} className={className}>
    <line x1="18" y1="6" x2="6" y2="18" />
    <line x1="6" y1="6" x2="18" y2="18" />
  </Base>
);

// Filled lightning bolt
export const IconBolt: FC<IconProps> = ({ size = 18, className }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="currentColor"
    className={className}
    aria-hidden="true"
  >
    <path d="M13 10V3L4 14h7v7l9-11h-7z" />
  </svg>
);

export const IconCheck: FC<IconProps> = ({ size = 18, className }) => (
  <Base size={size} className={className}>
    <circle cx="12" cy="12" r="9" />
    <path d="M8 12l3.5 3.5 5-6" strokeLinejoin="round" />
  </Base>
);

// Pencil edit icon (parallelogram body)
export const IconEdit: FC<IconProps> = ({ size = 18, className }) => (
  <Base size={size} className={className}>
    <path d="M15 5l4 4L7 21H3v-4z" />
    <line x1="15" y1="5" x2="19" y2="9" />
  </Base>
);

// Five-pip dice (randomize)
export const IconDice: FC<IconProps> = ({ size = 18, className }) => (
  <Base size={size} className={className}>
    <rect x="3" y="3" width="18" height="18" />
    <circle cx="9" cy="9" r="1.2" fill="currentColor" stroke="none" />
    <circle cx="15" cy="9" r="1.2" fill="currentColor" stroke="none" />
    <circle cx="9" cy="15" r="1.2" fill="currentColor" stroke="none" />
    <circle cx="15" cy="15" r="1.2" fill="currentColor" stroke="none" />
    <circle cx="12" cy="12" r="1.2" fill="currentColor" stroke="none" />
  </Base>
);

export const IconTerminal: FC<IconProps> = ({ size = 18, className }) => (
  <Base size={size} className={className}>
    <polyline points="4 17 10 11 4 5" />
    <line x1="12" y1="19" x2="20" y2="19" />
  </Base>
);

export const IconPerson: FC<IconProps> = ({ size = 18, className }) => (
  <Base size={size} className={className}>
    <circle cx="12" cy="8" r="4" />
    <path d="M4 20c0-3.5 3.6-6 8-6s8 2.5 8 6" />
  </Base>
);

// Filled play triangle
export const IconPlay: FC<IconProps> = ({ size = 20, className }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="currentColor"
    className={className}
    aria-hidden="true"
  >
    <path d="M8 5.14v14l11-7-11-7z" />
  </svg>
);

export const IconSpinner: FC<IconProps> = ({ size = 20, className }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    className={`animate-spin ${className ?? ""}`}
    aria-hidden="true"
  >
    <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="2" strokeOpacity="0.2" />
    <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="2" strokeLinecap="butt" />
  </svg>
);

export const IconError: FC<IconProps> = ({ size = 18, className }) => (
  <Base size={size} className={className}>
    <circle cx="12" cy="12" r="9" />
    <line x1="12" y1="8" x2="12" y2="13" />
    <circle cx="12" cy="16.5" r="0.75" fill="currentColor" stroke="none" />
  </Base>
);

// GitHub mark — simplified fill path, standard developer attribution use.
// TODO: verify this is acceptable for your portfolio context.
export const IconGitHub: FC<IconProps> = ({ size = 18, className }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="currentColor"
    className={className}
    aria-hidden="true"
  >
    <path d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.942.359.31.678.921.678 1.856 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z" />
  </svg>
);

// LinkedIn mark — rounded-rect frame with "in" letterform paths.
export const IconLinkedIn: FC<IconProps> = ({ size = 18, className }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="currentColor"
    className={className}
    aria-hidden="true"
  >
    <path d="M19 3a2 2 0 012 2v14a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h14m-.5 15.5v-5.3a3.26 3.26 0 00-3.26-3.26c-.85 0-1.84.52-2.32 1.3v-1.11h-2.79v8.37h2.79v-4.93c0-.77.62-1.4 1.39-1.4a1.4 1.4 0 011.4 1.4v4.93h2.79M6.88 8.56a1.68 1.68 0 001.68-1.68c0-.93-.75-1.69-1.68-1.69a1.69 1.69 0 00-1.69 1.69c0 .93.76 1.68 1.69 1.68m1.39 9.94v-8.37H5.5v8.37h2.77z" />
  </svg>
);
