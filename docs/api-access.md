# API access for AI agents

CareerLine can let your own AI agent work in your account: Claude Code, Claude Desktop, ChatGPT, or anything else that speaks the [Model Context Protocol](https://modelcontextprotocol.io) (MCP). Give it a key, and it can read your search, fill in your Career Vault from your resume or notes, add jobs, and propose changes for you to approve.

It works the same way as the built-in assistant, with the same guarantees:

- **Facts it adds are drafts.** Anything an agent adds to your Career Vault (roles, achievements, skills, STAR stories) arrives unverified, marked **From API**. Drafts are never used for resumes, cover letters, or fit scores until you verify them. An agent can't put words in your mouth.
- **Changes wait for you.** Reminders, stage moves, notes, saving or tracking a job, and profile edits become proposals in the **Assistant** page, under "Proposed by API key …". Nothing happens until you press Confirm.
- **Nothing leaves the server, and nothing is deleted.** There are no tools that send email or messages, delete or overwrite records, or touch your account (password, two-factor, sessions, keys, export, AI and notification settings).

## Create a key

1. Go to **Settings → API access**.
2. Name the key after the agent or device that will use it ("Claude on my laptop").
3. Choose what it may do:

   | Permission | Lets the agent |
   |---|---|
   | **Read** | See your profile (not contact details), vault entries and stories, jobs and filters, applications and agenda, and the status of its own proposals. |
   | **Add drafts** | Add vault entries and STAR stories as unverified drafts, and add a job from a link or pasted text. |
   | **Propose changes** | Propose reminders, stage moves, notes, saving or tracking a job, and profile changes (name, headline, location, summary). |

4. Pick when it expires (7 days to 1 year, or never), then **Create key**. You'll be asked for your password, as with exports.
5. Copy the key. **It's shown only once**; CareerLine stores only a SHA-256 fingerprint.

Revoke a key any time from the same page; it stops working at once. Proposals it already made stay in the Assistant for you to decide.

## Connect an agent

The page shows ready-to-paste setup with your key filled in. The endpoint is `<your CareerLine address>/api/mcp`, and the key goes in an `Authorization: Bearer …` header.

**Claude Code**

```bash
claude mcp add --transport http careerline http://192.168.1.50:8080/api/mcp --header "Authorization: Bearer cl_…"
```

**Other MCP clients** (Claude Desktop, Cursor, and others that take a JSON config):

```json
{
  "mcpServers": {
    "careerline": {
      "type": "http",
      "url": "http://192.168.1.50:8080/api/mcp",
      "headers": { "Authorization": "Bearer cl_…" }
    }
  }
}
```

The agent has to be able to reach your server. If CareerLine is on your home network, that means an AI app running on one of your own devices, or a cloud agent reaching it over a VPN such as Tailscale. Cloud AI services on the public internet can't reach a LAN-only server, which is usually what you want.

Then ask it things like:

- "Here's my resume. Add everything to my CareerLine vault, roles first, then each role's achievements."
- "Add this job posting to CareerLine and tell me how well I fit."
- "What should I follow up on this week? Propose reminders for anything overdue."

## Tools

| Tool | Permission | What it does |
|---|---|---|
| `get_overview` | Read | Counts of facts, stories, applications, and jobs; the key's permissions; waiting proposals. |
| `get_profile` | Read | Name, headline, location, summary. Never email, phone, or address. |
| `list_vault_entries` | Read | Every vault entry with its id, verified or draft. |
| `list_stories` | Read | STAR stories with linked entries. |
| `get_vault_facts` | Read | Only verified facts and stories (what resumes are built from). |
| `search_jobs`, `get_job` | Read | Jobs CareerLine has found, best fit first; one posting's details. |
| `get_job_filters` | Read | Your hard filters. |
| `list_applications`, `get_application`, `list_due` | Read | The tracker and agenda. Contacts appear by name and role only. |
| `list_my_proposals` | Read | What this key proposed and what you decided. |
| `add_vault_entry`, `add_story` | Add drafts | Add an unverified fact or story. |
| `import_job` | Add drafts | Add a job by link (robots.txt respected; LinkedIn, Indeed and similar sites refused) or pasted text. |
| `create_reminder`, `move_application`, `add_note`, `save_job`, `track_job`, `update_profile` | Propose changes | Create a proposal you confirm in the Assistant. |

Job descriptions and fetched pages are returned inside `<untrusted_document>` markers, and the server's instructions tell the agent never to follow instructions found in them.

## Limits

- 600 requests per key per 10 minutes, and 300 changes (drafts or proposals) per key per hour.
- At most 50 waiting proposals per key; the agent is told to ask you to review them first.
- 20 keys per account. 30 failed key attempts per IP per 15 minutes, then requests are refused for a while.
- Job imports share the account's limit of 30 per hour.

## Security notes

- Treat a key like a password for the permissions it has. Give each agent its own key with only what it needs, and an expiry.
- Every change made with a key is in the audit log (`api.tool`, with the key's id), as are key creation and revocation. The key itself is never logged.
- On plain `http://`, keys travel unencrypted, like your session cookie. That's acceptable only on a network you trust; put a TLS reverse proxy (or Tailscale) in front otherwise.
- Keys are bound to your account: an agent sees only your data, never other users' on the same server. Disabling an account stops its keys.
- Protocol details for developers: MCP Streamable HTTP, stateless, JSON responses (`POST /api/mcp`, protocol versions 2025-03-26 through 2025-11-25). `GET` returns 405 (no server-sent stream). Requests with an `Origin` header from another site are refused.
