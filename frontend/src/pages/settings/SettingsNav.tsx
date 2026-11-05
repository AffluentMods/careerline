// SPDX-License-Identifier: Apache-2.0
import { NavLink } from "react-router-dom";

export const SETTINGS_TABS = [
  { slug: "security", label: "Security" },
  { slug: "ai", label: "AI" },
  { slug: "notifications", label: "Notifications" },
  { slug: "data", label: "Data" },
  { slug: "api", label: "API access" },
];

export function SettingsNav() {
  return (
    <nav className="tabs" aria-label="Settings sections">
      {SETTINGS_TABS.map((t) => (
        <NavLink key={t.slug} to={`/settings/${t.slug}`} className="tab">
          {t.label}
        </NavLink>
      ))}
    </nav>
  );
}
