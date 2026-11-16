// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { Link } from "react-router-dom";

/**
 * A picture walkthrough. Screenshots are of the demo account (fictional data) and are
 * regenerated with `npm run guide:screenshots`.
 */
type Step = {
  id: string;
  title: string;
  image: string;
  alt: string;
  to: string;
  action: string;
  points: string[];
};

const STEPS: Step[] = [
  {
    id: "vault",
    title: "1. Put your experience in the Career Vault",
    image: "/guide/vault.jpg",
    alt: "The Career Vault with roles, achievements, and skills, some verified and some drafts.",
    to: "/vault",
    action: "Open the vault",
    points: [
      "Fastest: Import a resume (PDF or Word). Every line becomes a draft.",
      "Tick the drafts that are true. Only verified facts are used for fit scores and resumes, so nothing gets made up.",
      "Add numbers to achievements (\"cut costs 20%\"): they make the biggest difference to resumes.",
    ],
  },
  {
    id: "sources",
    title: "2. Tell CareerLine where to look",
    image: "/guide/sources.jpg",
    alt: "The source picker, grouped into alert emails, search engines, remote boards, and companies.",
    to: "/jobs/sources",
    action: "Add sources",
    points: [
      "Pick a few kinds: alert emails for LinkedIn and Indeed, a remote board or two, and companies you'd love to work for.",
      "Company boards and remote boards need no account or key.",
      "Each source checks for new jobs on its own schedule, in the background.",
    ],
  },
  {
    id: "alerts",
    title: "3. Get LinkedIn and Indeed jobs through your alert emails",
    image: "/guide/email-alerts.jpg",
    alt: "The job alert emails source form, with mail server, folder, sites, and app password fields.",
    to: "/jobs/sources",
    action: "Set up alert emails",
    points: [
      "On LinkedIn (and Indeed, Glassdoor, ZipRecruiter), save a search and turn on daily email alerts.",
      "Create an app password for your email (Gmail: myaccount.google.com/apppasswords). Not your normal password.",
      "Add the \"Job alert emails\" source with it. CareerLine opens your mailbox read-only and only reads the alert emails.",
      "Tip: a Gmail filter that labels alerts \"Job alerts\", then use that as the folder.",
    ],
  },
  {
    id: "jobs",
    title: "4. Go through your matches",
    image: "/guide/jobs.jpg",
    alt: "The Jobs list with fit scores, pay, and work mode for each posting.",
    to: "/jobs",
    action: "See matches",
    points: [
      "Best fits are at the top. The number is how well your verified facts cover the job's requirements.",
      "Set filters (pay, places, remote, keywords to avoid) under Jobs → Filters so you only see what you'd take.",
      "Save the good ones, dismiss the rest. Duplicates from different sources are merged.",
    ],
  },
  {
    id: "posting",
    title: "5. Check the fit before you apply",
    image: "/guide/posting.jpg",
    alt: "A job posting with matched requirements, gaps, and red flags.",
    to: "/jobs",
    action: "Open a job",
    points: [
      "See which requirements you meet (with the facts that prove it) and which are gaps.",
      "Red flags like unpaid trials or fees are called out.",
      "Track it to move it into your pipeline, and write a resume for it.",
    ],
  },
  {
    id: "document",
    title: "6. Send a tailored resume that passes the screeners",
    image: "/guide/document.jpg",
    alt: "A tailored resume with each line linked to facts, the applicant tracking check, and the second-opinion review.",
    to: "/materials",
    action: "Open materials",
    points: [
      "Every line links to a verified fact. Anything that doesn't is flagged and blocks export until you fix it.",
      "With AI on, a second pass reviews it like a recruiter and tightens weak lines (only with things your facts support).",
      "Run the applicant tracking check: it reads the file the way screening software does and shows which of the job's keywords you cover.",
    ],
  },
  {
    id: "tracker",
    title: "7. Track every application",
    image: "/guide/tracker.jpg",
    alt: "The tracker board with columns from Saved to Offer.",
    to: "/tracker",
    action: "Open the tracker",
    points: [
      "Drag cards from Saved to Applied, Interviewing, and Offer. Each keeps its timeline, files, and contacts.",
      "Attach the exact resume you sent, so you know what they read before an interview.",
    ],
  },
  {
    id: "followups",
    title: "8. Follow up when it goes quiet",
    image: "/guide/followups.jpg",
    alt: "The agenda with applications that have gone quiet and a follow-up draft.",
    to: "/tracker/agenda",
    action: "See follow-ups",
    points: [
      "After 10 days without news, the application shows up here with a ready-to-send note.",
      "It opens in your own email app; CareerLine never sends anything. Mark it sent when you have.",
      "At most two follow-ups per application. After that, silence is an answer.",
    ],
  },
  {
    id: "interview",
    title: "9. Prepare for the interview",
    image: "/guide/interview.jpg",
    alt: "Interview prep with likely questions matched to the user's STAR stories.",
    to: "/interview",
    action: "Prepare",
    points: [
      "Likely questions for that job, each matched to one of your stories.",
      "Practice with a mock interview and get feedback on each answer.",
    ],
  },
];

function Shot({ step }: { step: Step }) {
  const [missing, setMissing] = useState(false);
  if (missing) return null;
  return (
    <a href={step.image} target="_blank" rel="noreferrer" className="guide-shot">
      <img src={step.image} alt={step.alt} loading="lazy" onError={() => setMissing(true)} />
    </a>
  );
}

export function GuidePage() {
  return (
    <div className="page">
      <header className="page-head">
        <h1>How to use CareerLine</h1>
        <p className="muted">
          From an empty account to interviews, in nine steps. Screenshots use the demo account, so the data is made up.
        </p>
      </header>
      <nav aria-label="Steps" className="guide-toc">
        {STEPS.map((s) => (
          <a key={s.id} href={`#${s.id}`} className="small">
            {s.title.replace(/^\d+\.\s*/, "")}
          </a>
        ))}
      </nav>
      <ol className="guide">
        {STEPS.map((s) => (
          <li key={s.id} id={s.id} className="guide-step card">
            <h2>{s.title}</h2>
            <div className="guide-body">
              <ul className="guide-points">
                {s.points.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
              <Shot step={s} />
            </div>
            <div>
              <Link to={s.to} className="btn btn-secondary">
                {s.action}
              </Link>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
