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
