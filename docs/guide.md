# User guide

A tour of CareerLine in the order you'd normally use it. Everything here works without an AI provider except where it says **(AI)**.

## 1. Build your Career Vault

Open **Career Vault**. Add your roles, then the responsibilities and achievements under each one, plus projects, certifications, education, skills, and links. Add a metric to an achievement when you have one ("18% fewer late deliveries").

Only **verified** facts are ever used to score jobs or write materials. Facts you type are verified when you save them; imported facts start as drafts. A fact under a draft role isn't usable until the role is verified too. The sidebar shows how many drafts are waiting.

**Import a resume** (PDF or Word, up to 5 MB) to get started faster. CareerLine creates drafts only; review them next to the extracted text and approve what's right.

**Stories:** write STAR stories (situation, task, action, result) and link each to the facts it's based on. Interview prep matches questions to these.

## 2. Find jobs

Open **Jobs** → **Sources** and add where to look:

| Source | What you need |
|---|---|
| Greenhouse, Lever, Ashby | The company's board name (from its careers page URL). No key. |
| Adzuna | A free app ID and key from developer.adzuna.com. |
| USAJOBS | A free API key and the email you registered with. |
| JSearch | A RapidAPI key. |
| RSS or Atom feed | The feed address. |

Each source shows its terms of use. CareerLine fetches on the schedule you set (every 12 hours by default), merges the same job seen on several sources, and never fetches LinkedIn, Indeed, Glassdoor, ZipRecruiter, Monster, SimplyHired, Dice, or CareerBuilder.

**Filters** (Jobs → Filters) decide what's a match: places and a radius, remote/hybrid/on-site, minimum and target pay (hourly; yearly pay is divided by 2,080), shifts, clearance, words to require or exclude, and companies to prefer or hide. Postings that fail a filter go to **Filtered** with the reason; nothing is deleted.

**Add a job** you found elsewhere: paste a link (read once, if the site allows it) or paste the text.

## 3. Score and decide

Each posting gets a **fit score** from 0 to 100 with what matches, the gaps, and red flags such as "pay a fee to start" or text that tries to give instructions to an AI.

- **By keywords** works offline and is always available.
- **(AI)** With a provider set up in Settings → AI (Claude, an OpenAI-compatible API, or a local Ollama), new matches are scored by the model. The model sees only your verified facts and the posting, which is marked as untrusted data. Settings → AI shows each call's tokens and estimated cost and enforces your monthly budget. An option (on by default) removes email addresses, phone numbers, and street addresses before text goes to a hosted provider.

Scores are redone automatically when your vault changes.

## 4. Tailor materials

On a posting (or a tracked application), choose **Resume** or **Cover letter**, or **(AI)** the "with AI" versions. Every line cites the vault facts it came from; open a line to see them. A line is **flagged** when it cites no verified fact, or contains a number that isn't in the facts it cites. Edit it, approve it as true, or remove it: export stays locked until nothing is flagged.

Compare a tailored resume with your master resume, then **Download** a PDF or Word file, or **Attach** it to the application's timeline.

## 5. Track applications

**Track this job** creates an application with a copy of the posting. Move it through Saved → Applied → Screening → Interviewing → Offer (and Accepted, Rejected, or Withdrawn). Each stage change is recorded on the timeline; add notes, what you sent, interviews, and calls, with files. Contacts, referrals, reminders, and the Agenda tab keep follow-ups from slipping. **Offers** compares offers side by side.

## 6. Prepare for interviews

On a posting or application, **Prepare questions** lists likely behavioral, technical, situational, and company questions, each matched to your stories, with space for notes. **(AI)** "Prepare with AI" tailors the questions to the posting, and **Start practice** runs a mock interview: one question at a time, feedback on each answer (strengths, what to improve, a 1–5 rating).

## 7. Ask the assistant (AI)

**Assistant** answers questions about your search using your data: "What should I follow up on this week?", "Which saved jobs fit me best?" It can look things up on its own. Anything that would change your data (add a reminder, move an application, add a note, save or track a job) appears as a **proposed change** with **Confirm** and **Reject**; nothing changes until you confirm. It never sends email or messages.

## 8. See what's working

**Insights** shows, for all time or the last 30/90/365 days:

- how many applications you sent and what share got an answer (any reply, including a rejection),
- the funnel from Applied to Accepted and the share that moved on at each step,
- response rate and typical response time by source (where you found the job),
- applications per week and how long employers took to answer,
- applications that have gone quiet for 30 days.

Each chart has a **Show table** button.

## 9. Stay informed

Settings → **Notifications**: a daily digest of what's due and alerts for strong new matches, by email, ntfy push, or a Discord, Slack, or JSON webhook. See [operations](operations.md#notifications) for setting up each channel.

## 10. Keep your data safe

- Settings → **Security**: two-factor authentication, active sessions, password, and linked single sign-on accounts.
- Settings → **Data**: download everything as a zip (after confirming your password), import an export into an empty account, and (owner only) see backups and back up now.

See [operations](operations.md) for backups, restores, and moving to another server.
