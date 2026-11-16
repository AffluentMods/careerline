# User guide

A tour of CareerLine in the order you'd normally use it. Everything here works without an AI provider except where it says **(AI)**. Inside the app, **Guide** in the sidebar has the same walkthrough with screenshots, and **Today** (the home page) shows what needs you: setup steps you haven't finished, your best new matches, applications to follow up on, and what's due this week.

## 1. Build your Career Vault

Open **Career Vault**. Add your roles, then the responsibilities and achievements under each one, plus projects, certifications, education, skills, and links. Add a metric to an achievement when you have one ("18% fewer late deliveries").

Only **verified** facts are ever used to score jobs or write materials. Facts you type are verified when you save them; imported facts start as drafts. A fact under a draft role isn't usable until the role is verified too. The sidebar shows how many drafts are waiting.

**Import a resume** (PDF or Word, up to 5 MB) to get started faster. CareerLine creates drafts only; review them next to the extracted text and approve what's right.

**Stories:** write STAR stories (situation, task, action, result) and link each to the facts it's based on. Interview prep matches questions to these.

## 2. Find jobs

Open **Jobs** → **Sources** and add where to look. The add-source screen groups them and shows how to set each one up:

| Source | What you need |
|---|---|
| **Job alert emails** (LinkedIn, Indeed, Glassdoor, ZipRecruiter, Monster) | Email alerts turned on at those sites, and an app password for the mailbox they arrive in (see below). |
| Adzuna | A free app ID and key from developer.adzuna.com. |
| USAJOBS | A free API key and the email you registered with. |
| JSearch (Google for Jobs: LinkedIn, Indeed, Glassdoor, company sites) | A RapidAPI key; the free plan works. |
| Arbeitnow (Germany and Europe) | Nothing. Keywords and a city to filter by. |
| RSS or Atom feed | The feed address. |
| Remotive, Remote OK, Himalayas, Jobicy (remote jobs) | Nothing. Your keywords. These boards ask to be checked at most a few times a day, so the shortest interval is 6 hours. |
| Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee, Personio (one company each) | The company's board name, from its careers page address. No key. |
| Hacker News "Who is hiring?" | Your keywords. Reads this month's thread of jobs posted by founders and hiring managers. |

CareerLine fetches each source on the schedule you set (every 12 hours by default), merges the same job seen on several sources, and never scrapes sites that forbid it: it doesn't fetch LinkedIn, Indeed, Glassdoor, ZipRecruiter, Monster, SimplyHired, Dice, or CareerBuilder pages.

**Getting LinkedIn and Indeed jobs through alert emails.** Those sites don't allow scraping, but they'll email you new jobs for a saved search. CareerLine reads those emails:

1. On LinkedIn (Jobs → search → **Set alert**, daily, by email), and on Indeed, Glassdoor, ZipRecruiter, or Monster, save searches and turn on email alerts.
2. Optional but tidy: a mail filter that labels them (for example "Job alerts"), so CareerLine only has to look in that folder. A separate mailbox just for alerts is the most private option.
3. Create an **app password** for that mailbox (Gmail: myaccount.google.com/apppasswords, which needs 2-step verification; Outlook: account.microsoft.com/security). Don't use your normal password.
4. Add the **Job alert emails** source with the mail server (filled in for Gmail), folder, and app password, and pick which sites' alerts to read.

The mailbox is opened read-only over TLS: nothing is marked read, moved, or deleted, and only messages from those sites' alert senders from the last week are read. Each job's link is cleaned of tracking codes. Alert emails only carry a title, company, and location, so paste the full description into a job (or open it from another source) for a better fit score.

**Filters** (Jobs → Filters) decide what's a match: places and a radius, remote/hybrid/on-site, minimum and target pay (hourly; yearly pay is divided by 2,080), shifts, clearance, words to require or exclude, and companies to prefer or hide. Postings that fail a filter go to **Filtered** with the reason; nothing is deleted.

**Add a job** you found elsewhere: paste a link (read once, if the site allows it) or paste the text.

## 3. Score and decide

Each posting gets a **fit score** from 0 to 100 with what matches, the gaps, and red flags such as "pay a fee to start" or text that tries to give instructions to an AI.

- **By keywords** works offline and is always available.
- **(AI)** With a provider set up in Settings → AI (Claude, an OpenAI-compatible API, or a local Ollama), new matches are scored by the model. The model sees only your verified facts and the posting, which is marked as untrusted data. Settings → AI shows each call's tokens and estimated cost and enforces your monthly budget. An option (on by default) removes email addresses, phone numbers, and street addresses before text goes to a hosted provider.

Scores are redone automatically when your vault changes.

## 4. Tailor materials

On a posting (or a tracked application), choose **Resume** or **Cover letter**, or **(AI)** the "with AI" versions. Every line cites the vault facts it came from; open a line to see them. A line is **flagged** when it cites no verified fact, or contains a number that isn't in the facts it cites. Edit it, approve it as true, or remove it: export stays locked until nothing is flagged.

**(AI) Second opinion.** Documents written with AI get a second, independent pass that reads the draft like a recruiter skimming it: vague lines, filler, and missing keywords. It rewrites only the lines that clearly get better, and a rewrite is applied only if it passes the same fact check (a rewrite that adds a number your facts don't have is thrown away and listed as rejected). It also lists what the job asks for that your facts support but the draft doesn't show, and the real gaps that it won't fake. Run it again, or on a document built without AI, with **Get a second opinion**.

**Applicant tracking check.** Screening software doesn't look at the page; it pulls the text out of the file. **Check the PDF** (or Word file) does the same and shows: whether your name and every contact detail come through as text, garbled characters, whether the sections read in order, page count, and **keyword match**: which of the posting's skills and terms the file covers, which are in your vault but not on this page (worth adding), and which aren't in your facts at all (gaps; don't add them unless they're true). **Show what the software reads** shows the extracted text.

Compare a tailored resume with your master resume, then **Download** a PDF or Word file, or **Attach** it to the application's timeline.

## 5. Track applications

**Track this job** creates an application with a copy of the posting. Move it through Saved → Applied → Screening → Interviewing → Offer (and Accepted, Rejected, or Withdrawn). Each stage change is recorded on the timeline; add notes, what you sent, interviews, and calls, with files. Contacts, referrals, reminders, and the Agenda tab keep follow-ups from slipping. **Offers** compares offers side by side.

**Follow-ups.** An open application (Applied, Screening, or Interviewing) with no news for 10 days shows up under **Gone quiet** on the Agenda tab and on Today. **Follow up** drafts a short, polite note: greeting your recruiter or hiring-manager contact by name if you've added one, mentioning the role and when you applied, asking about the timeline. **(AI)** "Write it with AI" can work in one relevant point, but only from the resume and cover letter linked to that application (what the employer already has); a number that isn't in them is flagged. CareerLine never sends email: **Open in email** opens the draft in your own email app, and **Mark as sent** records it on the timeline. It suggests at most two follow-ups per application.

## 6. Prepare for interviews

On a posting or application, **Prepare questions** lists likely behavioral, technical, situational, and company questions, each matched to your stories, with space for notes. **(AI)** "Prepare with AI" tailors the questions to the posting, and **Start practice** runs a mock interview: one question at a time, feedback on each answer (strengths, what to improve, a 1–5 rating).

## 7. Ask the assistant (AI)

**Assistant** answers questions about your search using your data: "What should I follow up on this week?", "Which saved jobs fit me best?" It can look things up on its own. Anything that would change your data (add a reminder, move an application, add a note, save or track a job) appears as a **proposed change** with **Confirm** and **Reject**; nothing changes until you confirm. It never sends email or messages.

**Use your own AI agent instead (or as well).** In **Settings → API access**, create a key and connect Claude, ChatGPT, or another MCP client. Ask it to fill in your vault from your resume, add jobs, or plan your follow-ups. Facts it adds show up in the Career Vault as drafts marked **From API** for you to verify, and its proposed changes wait under "Proposed by API key …" on the Assistant page. This works without setting up an AI provider in CareerLine. See [API access](api-access.md).

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
