// SPDX-License-Identifier: Apache-2.0
import { useState, type ComponentType } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import type { User } from "../lib/api";
import { useAuthStatus, useSignOut } from "../lib/auth";
import { localToday, useTrackerSummary } from "../lib/tracker";
import { useSummary } from "../lib/vault";
import { useTheme, type ThemeChoice } from "../lib/theme";
import {
  BoardIcon,
  ChartIcon,
  ChatIcon,
  DocIcon,
  HomeIcon,
  LogoutIcon,
  MenuIcon,
  MicIcon,
  MonitorIcon,
  MoonIcon,
  SearchIcon,
  ShieldIcon,
  SunIcon,
  VaultIcon,
} from "./icons";
import { Logo } from "./ui";

interface NavItem {
  to: string;
  label: string;
  icon: ComponentType;
  end?: boolean;
}

const NAV: NavItem[] = [
  { to: "/", label: "Overview", icon: HomeIcon, end: true },
  { to: "/vault", label: "Career Vault", icon: VaultIcon },
  { to: "/jobs", label: "Jobs", icon: SearchIcon },
  { to: "/tracker", label: "Tracker", icon: BoardIcon },
  { to: "/materials", label: "Materials", icon: DocIcon },
  { to: "/interview", label: "Interview prep", icon: MicIcon },
  { to: "/assistant", label: "Assistant", icon: ChatIcon },
  { to: "/insights", label: "Insights", icon: ChartIcon },
];

const THEMES: { value: ThemeChoice; label: string; icon: ComponentType }[] = [
  { value: "light", label: "Light", icon: SunIcon },
  { value: "dark", label: "Dark", icon: MoonIcon },
  { value: "system", label: "System", icon: MonitorIcon },
];

export function AppShell({ user }: { user: User }) {
  const location = useLocation();
  // The mobile menu stays open only on the page where it was opened, so it closes on navigation.
  const [openOn, setOpenOn] = useState<string | null>(null);
  const open = openOn === location.pathname;
  const setOpen = (next: boolean) => setOpenOn(next ? location.pathname : null);
  const [theme, setTheme] = useTheme();
  const signOut = useSignOut();
  const demo = Boolean(useAuthStatus().data?.demo);
  const summary = useSummary();
  const review = summary.data?.needs_review ?? 0;
  const tracker = useTrackerSummary(localToday());
  const due = (tracker.data?.overdue ?? 0) + (tracker.data?.due_today ?? 0);
  const counts: Record<string, { n: number; label: string }> = {
    "/vault": { n: review, label: `${review} drafts to review` },
    "/tracker": { n: due, label: `${due} due today or overdue` },
  };

  return (
    <div className="shell" data-nav-open={open || undefined}>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <button
          className="icon-btn"
          aria-label="Open navigation"
          aria-expanded={open}
          aria-controls="sidebar"
          onClick={() => setOpen(!open)}
        >
          <MenuIcon />
        </button>
        <span className="brand">
          <Logo size={24} /> CareerLine
        </span>
      </header>

      <aside id="sidebar" className="sidebar">
        <div className="brand brand-lg">
          <Logo /> <span>CareerLine</span>
        </div>
        <nav aria-label="Main">
          <ul className="nav-list">
            {NAV.map(({ to, label, icon: IconC, end }) => (
              <li key={to}>
                <NavLink to={to} end={end} className="nav-link">
                  <IconC />
                  <span>{label}</span>
                  {counts[to]?.n ? (
                    <span className="nav-count" aria-label={counts[to].label} title={counts[to].label}>
                      {counts[to].n}
                    </span>
                  ) : null}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>

        <div className="sidebar-foot">
          <NavLink to="/settings" className="nav-link">
            <ShieldIcon />
            <span>Settings</span>
            {!user.mfa_enabled ? <span className="dot" title="Two-factor authentication is off" /> : null}
          </NavLink>
          <div className="theme-switch" role="radiogroup" aria-label="Theme">
            {THEMES.map(({ value, label, icon: IconC }) => (
              <button
                key={value}
                role="radio"
                aria-checked={theme === value}
                aria-label={label}
                title={label}
                className="theme-opt"
                onClick={() => setTheme(value)}
              >
                <IconC />
              </button>
            ))}
          </div>
          <div className="user-row">
            <div className="avatar" aria-hidden="true">
              {user.display_name.slice(0, 1).toUpperCase()}
            </div>
            <div className="user-meta">
              <strong>{user.display_name}</strong>
              <span className="muted small">{user.email}</span>
            </div>
            <button className="icon-btn" aria-label="Sign out" title="Sign out" onClick={() => void signOut()}>
              <LogoutIcon />
            </button>
          </div>
        </div>
      </aside>
      <div className="scrim" onClick={() => setOpen(false)} aria-hidden="true" />

      <main id="main" className="main" tabIndex={-1}>
        {demo ? (
          <p className="demo-banner" role="note">
            Demo mode: everything here is made up, and nothing is sent outside this server.
          </p>
        ) : null}
        <Outlet />
      </main>
    </div>
  );
}
