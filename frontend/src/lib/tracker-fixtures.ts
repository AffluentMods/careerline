// SPDX-License-Identifier: Apache-2.0
/** Fictional test data shared by tracker tests. */
import type { ApplicationSummary } from "./tracker";

export function summary(over: Partial<ApplicationSummary>): ApplicationSummary {
  return {
    id: "a",
    title: "Security Engineer",
    company: "Northwind Tools",
    location: "",
    work_mode: null,
    pay_min_cents: null,
    pay_max_cents: null,
    pay_period: null,
    currency: "USD",
    stage: "saved",
    stage_changed_at: "2026-11-01T12:00:00Z",
    applied_on: null,
    deadline: null,
    priority: "normal",
    updated_at: "2026-11-01T12:00:00Z",
    last_activity_at: "2026-11-01T12:00:00Z",
    next_due: null,
    contact_count: 0,
    has_offer: false,
    ...over,
  };
}
