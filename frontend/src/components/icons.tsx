// SPDX-License-Identifier: Apache-2.0
/** Simple stroke icons drawn for this project (24px grid, currentColor). */
import type { ReactNode } from "react";

function Icon({ children }: { children: ReactNode }) {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

export const HomeIcon = () => (
  <Icon>
    <path d="M4 11.5 12 5l8 6.5" />
    <path d="M6 10v9h12v-9" />
  </Icon>
);
export const VaultIcon = () => (
  <Icon>
    <rect x="4" y="5" width="16" height="14" rx="2" />
    <circle cx="12" cy="12" r="3" />
    <path d="M12 9v-.5M15 12h.5M12 15v.5M9 12h-.5" />
  </Icon>
);
export const SearchIcon = () => (
  <Icon>
    <circle cx="11" cy="11" r="6" />
    <path d="m20 20-4.2-4.2" />
  </Icon>
);
export const BoardIcon = () => (
  <Icon>
    <rect x="4" y="5" width="4.5" height="14" rx="1.2" />
    <rect x="10" y="5" width="4.5" height="9" rx="1.2" />
    <rect x="16" y="5" width="4" height="11" rx="1.2" />
  </Icon>
);
export const ChatIcon = () => (
  <Icon>
    <path d="M5 6h14v9H10l-4 3v-3H5z" />
  </Icon>
);
export const MicIcon = () => (
  <Icon>
    <rect x="9" y="4" width="6" height="10" rx="3" />
    <path d="M6 11a6 6 0 0 0 12 0M12 17v3" />
  </Icon>
);
export const ChartIcon = () => (
  <Icon>
    <path d="M5 19V5M5 19h14" />
    <path d="M9 15v-4M13 15V8M17 15v-6" />
  </Icon>
);
export const ShieldIcon = () => (
  <Icon>
    <path d="M12 4 5 7v5c0 4 3 6.6 7 8 4-1.4 7-4 7-8V7z" />
    <path d="m9.5 12 2 2 3.5-3.5" />
  </Icon>
);
export const MenuIcon = () => (
  <Icon>
    <path d="M4 7h16M4 12h16M4 17h16" />
  </Icon>
);
export const SunIcon = () => (
  <Icon>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6 7 7M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4" />
  </Icon>
);
export const MoonIcon = () => (
  <Icon>
    <path d="M19 14.5A7.5 7.5 0 0 1 9.5 5 7.5 7.5 0 1 0 19 14.5Z" />
  </Icon>
);
export const MonitorIcon = () => (
  <Icon>
    <rect x="3.5" y="5" width="17" height="11" rx="1.5" />
    <path d="M9 20h6M12 16v4" />
  </Icon>
);
export const LogoutIcon = () => (
  <Icon>
    <path d="M14 5h4a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1h-4" />
    <path d="M10 8l-4 4 4 4M6 12h9" />
  </Icon>
);
export const DocIcon = () => (
  <Icon>
    <path d="M7 3h7l4 4v14H7z" />
    <path d="M14 3v4h4M10 12h5M10 16h5" />
  </Icon>
);
