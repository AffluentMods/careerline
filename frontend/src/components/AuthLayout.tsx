// SPDX-License-Identifier: Apache-2.0
import type { ReactNode } from "react";
import { Logo } from "./ui";

export function AuthLayout({ title, subtitle, children, footer }: {
  title: string;
  subtitle?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="auth-page">
      <main className="auth-card">
        <div className="brand brand-lg">
          <Logo /> <span>CareerLine</span>
        </div>
        <h1>{title}</h1>
        {subtitle ? <p className="muted">{subtitle}</p> : null}
        {children}
        {footer ? <div className="auth-foot">{footer}</div> : null}
      </main>
      <p className="auth-tagline muted small">Self-hosted. Your data stays on your server.</p>
    </div>
  );
}
