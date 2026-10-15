# Cover letters

autoapp generates custom cover letters for each job application. The writer follows a truth policy, avoids AI-like phrases and stock language, and claims only skills backed by your CV or profile.

## What the writer can and cannot claim

### Cannot claim (always blocked)
- Employers, job titles, dates, degrees, certifications or language levels you don't have
- Specific numbers of years of experience you don't have
- Any fact not sourced from your CV, profile or writing instructions

### Can claim (if bridgeable and not core)
For requirements marked "basic" or "unspecified" (not intermediate/advanced, not core, without years named in the ad), the writer may claim basic familiarity even if you don't have CV proof, up to a cap of 5 per letter.

Example: If the ad mentions "Docker" as a nice-to-have and your CV mentions personal projects with containers, you can claim "working knowledge of Docker". You'll see a "Before an interview, brush up on" list in the application folder.

### How claims are checked

The writer tags every factual statement it makes and gives an exact quote from the source as evidence. One statement often draws on several places, so the evidence may be several exact quotes joined by ` | `. Every quote must appear word for word in your CV, your profile, your writing instructions, the search profile's instructions, or a fact the app itself supplies (your earliest start date or the salary sentence). The job ad never counts as evidence. If a quote is not found, the application gets a `needs_review` reason like `the quoted evidence does not appear in the CV, the profile or your writing instructions: "…"`, which names the quote that was not found.

## Bridgeable skill policy

- **Max assumed skills per letter:** `writing.max_assumed_skills` (default: 5)
- **Allowed only for:** requirements with final status `bridgeable` (basic or unspecified proficiency, not core, no years named)
- **Allowed only if:** the job decision is `apply` (score ≥ apply_threshold)
- **Phrased as:** "working knowledge of X", "familiar with X", never as years or "expert"
- **Tracked:** every assumed skill appears under "Before an interview, brush up on" in the README and in the CSV export

Configure the cap:

```yaml
writing:
  max_assumed_skills: 5  # Default is fine for most users
```

## Style rules

The linter enforces a human tone and catches common AI patterns:

- **No dashes as punctuation** (em/en dashes `–` `—`, or spaced hyphens `—`). Hyphens inside words like "Backend-Entwicklung" are fine.
- **No AI phrases** from the built-in banned-phrases list (e.g., "I am writing to express my interest", "dynamisches team", "proven track record")
- **No exclamation marks** or question marks in the body
- **No excessive connector words** (at most 2 per letter: "Darüber hinaus", "Furthermore", "Zudem", etc.)
- **Word limit:** `writing.max_words` (default 300 characters for the body)

**After PDF rendering:** if the letter exceeds one page, autoapp rewrites it at 85% word count and re-renders. If it still doesn't fit, it sets `needs_review: too long`.

Configure:

```yaml
writing:
  max_words: 300
  language: auto  # auto (detect from ad), de, or en
  always_attach_certificates: false
```

### Extending the phrase lists

The built-in phrase lists live in `src/autoapp/writing/phrases_de.txt` and `phrases_en.txt`. To add your own:

**Ban a phrase:**
```yaml
writing:
  banned_phrases:
    - "my passion for coding"
    - "meine leidenschaft für"
```

**Whitelist a phrase** (allow a normally banned one):
```yaml
writing:
  allowed_phrases:
    - "dynamisches team"  # If this is legitimately in your writing voice
```

## Writing instructions

Before autoapp runs, write custom instructions in `workspace/writing_instructions.md`. These override the defaults and set the tone, emphasis, and facts:

**Example:**

```markdown
# Writing Instructions

## Voice & Tone
- Keep it direct and conversational; no flowery openings or corporate jargon.
- Be confident but honest. Show genuine interest in the company, not just the job.

## What to Emphasize
- Mention that I can start within 4 weeks.
- Highlight hands-on experience with backend systems.

## Things to Avoid
- Don't make it sound like I'm applying to every job in the city. Be specific about why *this* role.
- Don't apologize or minimize my experience.

## Examples of Good Openings
- "I'm drawn to [company] because [specific reason: product, tech stack, problem they solve]."
- "Your job description mentions X; I've spent the last two years solving exactly that."
```

**Good instructions:**
- Mention tone and voice
- Name specific technologies or domains to emphasize
- List facts or projects to mention when relevant
- Explain what matters most in your voice

**Instructions cannot override:**
- The truth policy (no invented facts)
- Word limit
- Style rules (no dashes, no banned phrases)

## Writing samples

Include writing samples to show your natural voice. The writer reads them for tone reference but never copies sentences.

In `profile.yaml`, add:

```yaml
documents:
  - file: cover-letter-example.md
    role: writing_sample
    language: de  # or en
```

Place the file in `workspace/documents/` and specify the language. The LLM will read it to understand your style and voice.

## Reviewing and editing

Before a run sends applications in automatic mode, use `autoapp render <id>` to review and edit the letter (in review mode, read the draft in your mailbox instead):

```bash
autoapp render <job-id>
```

This rebuilds the PDF from an edited `cover_letter.md` without re-running the LLM. Edits are never linted or blocked.

## Commands

- **Generate and prepare:** `autoapp prepare <job-id>` (generates cover letter, checks it, renders PDF)
- **Review and re-render:** `autoapp render <job-id>` (rebuild PDF from edited markdown, zero LLM calls)
- **Force regenerate:** `autoapp prepare --force <job-id>` (overwrite and re-generate from scratch)
