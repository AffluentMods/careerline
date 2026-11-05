// SPDX-License-Identifier: Apache-2.0
import { useState, type FormEvent } from "react";
import { Button, Card, Field, FullPageLoader, Notice, TextAreaField } from "../../components/ui";
import { errorMessage, fieldErrors } from "../../lib/api";
import { useProfile, useVaultMutations, type Profile } from "../../lib/vault";

export function ProfileTab() {
  const profile = useProfile();
  if (profile.isPending) return <FullPageLoader />;
  if (profile.isError) return <Notice>{errorMessage(profile.error)}</Notice>;
  return <ProfileForm initial={profile.data} />;
}

function ProfileForm({ initial }: { initial: Profile }) {
  const m = useVaultMutations();
  const [values, setValues] = useState<Profile>(initial);
  const [saved, setSaved] = useState(false);
  const errs = fieldErrors(m.saveProfile.error);

  const set = (k: keyof Profile) => (v: string) => {
    setSaved(false);
    setValues((cur) => ({ ...cur, [k]: v }));
  };

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSaved(false);
    try {
      await m.saveProfile.mutateAsync(values);
      setSaved(true);
    } catch {
      // shown below
    }
  }

  return (
    <form onSubmit={submit} className="stack">
      <Card title="About you" description="Used at the top of generated resumes and cover letters.">
        <div className="form-grid">
          <Field label="Full name" value={values.full_name} maxLength={200} onChange={(e) => set("full_name")(e.target.value)} />
          <Field
            label="Headline"
            value={values.headline}
            maxLength={200}
            hint="For example: Security engineer focused on detection"
            onChange={(e) => set("headline")(e.target.value)}
          />
          <Field
            label="Location"
            value={values.location_text}
            maxLength={200}
            hint="City and region only"
            onChange={(e) => set("location_text")(e.target.value)}
          />
          <div className="span-2">
            <TextAreaField
              label="Summary"
              rows={4}
              value={values.summary}
              maxLength={4000}
              onChange={(e) => set("summary")(e.target.value)}
            />
          </div>
        </div>
      </Card>
      <Card title="Contact details" description="Encrypted in the database. Only shown to you.">
        <div className="form-grid">
          <Field
            label="Email"
            type="email"
            value={values.email}
            maxLength={320}
            autoComplete="email"
            hint={errs.email}
            onChange={(e) => set("email")(e.target.value)}
          />
          <Field
            label="Phone"
            type="tel"
            value={values.phone}
            maxLength={50}
            autoComplete="tel"
            onChange={(e) => set("phone")(e.target.value)}
          />
          <div className="span-2">
            <Field
              label="Mailing address"
              value={values.address}
              maxLength={300}
              autoComplete="street-address"
              hint="Optional. Most resumes only need a city."
              onChange={(e) => set("address")(e.target.value)}
            />
          </div>
        </div>
      </Card>
      {m.saveProfile.error ? <Notice>{errorMessage(m.saveProfile.error)}</Notice> : null}
      {saved ? <Notice tone="success">Profile saved.</Notice> : null}
      <div className="row">
        <Button type="submit" busy={m.saveProfile.isPending}>
          Save profile
        </Button>
      </div>
    </form>
  );
}
