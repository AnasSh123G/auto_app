# Email

autoapp can save applications as drafts in your mailbox, send them, and read job alerts from a separate mailbox. Both use your own mail account(s), not a third-party service.

## Supported email providers

### Gmail

1. **Enable 2-Step Verification** (required for app passwords):
   - Go to [myaccount.google.com](https://myaccount.google.com)
   - Select Security in the left menu
   - Enable "2-Step Verification"

2. **Create an app password** (specific to autoapp):
   - Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
   - Select "Mail" and "Windows Computer" (or your device)
   - Google will generate a 16-character password
   - Copy it to `.env`:
     ```
     AUTOAPP_ALERTS_PASSWORD=xxxx xxxx xxxx xxxx
     AUTOAPP_SMTP_PASSWORD=xxxx xxxx xxxx xxxx
     ```

3. **Configure in `config.yaml`**:
   ```yaml
   mail:
     alerts:
       preset: gmail
       address: your.email@gmail.com
     sending:
       preset: gmail
       address: your.email@gmail.com
   ```

### Proton Mail

Proton Mail requires its own Bridge application for IMAP/SMTP access:

1. **Install Proton Mail Bridge**:
   - Download from [proton.me/mail/bridge](https://proton.me/mail/bridge) (requires paid Proton plan)
   - Install and run it on your machine

2. **Configure it as a systemd user service** (Linux):
   ```bash
   mkdir -p ~/.config/systemd/user
   # Create a service file that runs: protonmail-bridge --cli
   systemctl --user enable --now protonmail-bridge
   ```

3. **Get your Bridge password**:
   - Run `protonmail-bridge --cli` once to set up your account and get the Bridge password

4. **Pin the Bridge certificate** (first connection):
   - Run `autoapp setup` and when prompted, it will show the certificate fingerprint
   - Confirm that it matches what Bridge shows (prevents man-in-the-middle attacks)

5. **Configure in `config.yaml`**:
   ```yaml
   mail:
     alerts:
       preset: proton_bridge
       address: your.email@protonmail.com
       bridge_cert_sha256: "abc123..."  # Set by setup wizard
     sending:
       preset: proton_bridge
       address: your.email@protonmail.com
   ```

### Custom IMAP/SMTP servers

For any other provider (corporate email, custom domain, etc.):

1. Find your IMAP and SMTP server details (your provider's documentation)
2. Determine if they use SSL (port 465/993) or STARTTLS (port 587/143)
3. **Configure in `config.yaml`**:
   ```yaml
   mail:
     alerts:
       preset: custom
       address: your.email@example.com
       imap_host: imap.example.com
       imap_port: 993
       imap_tls: ssl
     sending:
       preset: custom
       address: your.email@example.com
       smtp_host: smtp.example.com
       smtp_port: 465
       smtp_tls: ssl
   ```

4. Store credentials in `.env`:
   ```
   AUTOAPP_ALERTS_PASSWORD=your-password-or-app-specific-password
   AUTOAPP_SMTP_PASSWORD=your-password-or-app-specific-password
   ```

### Why Outlook.com isn't supported yet

Microsoft disabled password-based logins for third-party applications on personal Outlook.com accounts in September 2024. Support requires OAuth2 and the Microsoft Graph API, which is a separate change planned for later. **Workaround:** Use Gmail, Proton Mail, or a custom IMAP/SMTP server.

## Separate alert and sending mailboxes

You can read alerts from one account and send from another:

```yaml
mail:
  alerts:
    preset: gmail
    address: alerts.only@gmail.com  # Receives LinkedIn and StepStone job alerts
  sending:
    preset: gmail
    address: my.name@gmail.com      # Sends application emails
```

This keeps alerts separate from professional correspondence.

## Run modes

Every interactive `autoapp run` (and "Run now" in the main menu) asks how to handle emails. `--mode automatic|review|test` skips the question.

| Mode | What happens |
|---|---|
| Full automatic | Emails that pass the safeguards below are sent to employers. It asks for confirmation (default no) and shows emails sent today against the daily cap |
| Review first | Every email is saved as a draft in your mailbox. Nothing is sent |
| Test run | Exactly like a real run, but every email a real run would send goes to your own sending address. Nothing is recorded |

Scheduled runs never ask. They use `run.default_mode`, which is `review` unless you change it. It can be `automatic` or `review`, not `test`:

```yaml
run:
  default_mode: review  # or automatic
```

You can also set it in the main menu under Settings, "Default run mode for scheduled runs".

Form applications are prepared the same way in all modes (application folder, `form_answers.md`, `apply.html`). A run never opens a browser. After an interactive run, autoapp offers to fill the prepared forms (default no).

### Migrating from `email.auto_send`

The old key `email.auto_send` was removed. An old `config.yaml` is migrated when it is loaded, and autoapp prints one line about it:

- `auto_send: true` becomes `run.default_mode: automatic`
- `auto_send: false` becomes `run.default_mode: review`
- If both keys exist, `run.default_mode` wins.

Run `autoapp setup --section email` or edit `config.yaml` to remove the old key.

## Review first: drafts

In review mode each email application is written straight into the Drafts folder of your sending mailbox, over IMAP, with all its documents attached (`Bewerbungsmappe.pdf` and any other requested documents). Nothing is sent and nothing is saved under `workspace/applications/` for it; the letter and the email text are kept in the database. Open your mail program, read the draft and send it yourself.

- The folder is found by its special-use attribute, so localized names such as Gmail's `[Gmail]/Entwürfe` work. If it cannot be found, set `mail.sending.drafts_folder`.
- A drafted job gets the new status `drafted`.
- Review mode drafts a job even if it is below `min_score`, is addressed to a free-mail domain, is in the company cooldown or is over the daily cap. These show as notes.
- Flagged jobs (claims that could not be verified, style issues) are drafted too; the feed, the run summary and `autoapp drafts` show a "check before sending" note with the reasons.
- A draft that fails to save (for example a rejected login) is retried by the next run or `autoapp draft <id>` without writing a new letter. `autoapp draft <id> --again` rebuilds the draft from the stored letter.

```yaml
mail:
  sending:
    preset: gmail
    address: your.email@gmail.com
    drafts_folder: "[Gmail]/Drafts"  # optional; found automatically when empty
```

Commands:

```bash
autoapp draft <id>            # save one prepared email application as a draft
autoapp draft <id> --again    # save a new draft even if one was saved before
autoapp drafts                # list saved drafts
autoapp drafts --check        # mark drafts you sent by hand as sent
```

`drafts --check` looks in your Sent folder for the same recipient and subject. When it finds them, the job becomes `sent`. It also runs at the start of every run that is not a dry run. If you send a draft by hand and also use `autoapp send <id>`, the employer gets two emails, so `send` warns you when a draft exists.

## Test run

A test run is exactly like a real run, but every email a real run would send goes to your own sending address. You get one email per application. The subject is the normal subject with `TEST_RUN: ` in front, and the first line of the text says "This email is what would be sent to <address> in a real run." Nothing is recorded: no status change, no effect on the daily cap or the company cooldown. The safeguards decide what a test run would send. Start one with `autoapp run --mode test` or from the menu under Tests.

A normal test run skips every job that is already in your history. To test everything again, use
`autoapp run --mode test --fresh` (menu: Tests, "Fresh test run"). It runs in a new folder
`workspace/test-runs/<time>/` with an empty job history: every ad found, including the alert emails of the last
30 days, is new there and is evaluated, prepared and test-sent again. Your real jobs, folders and
`applications.csv` are not touched, and the saved employer sites are reused. The AI calls and API requests it makes
are real, so they are added to your real statistics and limits; `limits.max_llm_calls_per_run` and `limits.max_cost_per_run_usd` still cap the cost.
Delete the test-runs folder when you are done with it.

### Redo everything that is not sent

`autoapp run --redo` (menu: **Redo unsent**) works in your real workspace. It shows what it would redo, asks (default
no), backs the database up to `workspace/backups/redo-<time>/` and sends every unsent job back to `new`. They are
evaluated again, their folders rebuilt in place (your old letter stays beside the new one as
`cover_letter.<time>.md`, other files are kept) and their application pages looked up again; then the run goes on as
usual in the mode you choose (review: drafts, automatic: sends what the safeguards allow, test: to you).

Left alone: `sent`, `submitted`, `interview`, `offer`, `rejected`, `withdrawn`, `expired`, `drafted` (a draft is in your
mailbox), `send_unconfirmed` (the mail may have left) and jobs your rules filtered out. The per-run limits
(`max_evaluate_per_run`, `max_prepare_per_run`) still apply, so a large redo is finished by the next normal runs;
do not pass `--redo` again or it starts over. `--redo --dry-run` only prints the plan.

## Email sending safeguards

The safeguards apply to automatic mode and to deciding what a test run would send. Review mode shows them as notes only. Sending one job yourself with `autoapp send <id>` asks for confirmation instead.

| Safeguard | Default | Description |
|---|---|---|
| `min_score` | `80` | Only jobs scoring 80+ are sent automatically (you must approve first with `evaluate`) |
| `daily_cap` | `100` | At most 100 emails per calendar day (Europe/Berlin) |
| `company_cooldown_days` | `14` | Never send twice to the same company within 14 days |
| `max_attempts` | `3` | Retry a failed send up to 3 times, then stop |
| `max_bundle_mb` | `10.0` | Refuse to send if attachments exceed 10 MB |
| Free-mail check | (configurable) | Addresses at gmail.com, gmx.de, web.de, etc. go to manual review, not automatic sending |
| `needs_review` flag | (blocking) | Nothing is sent automatically if the letter has warnings (claim violations, style issues); review mode still drafts it with a "check before sending" note |

Configure:

```yaml
email:
  daily_cap: 100
  min_score: 80
  company_cooldown_days: 14
  max_attempts: 3
  max_bundle_mb: 10.0
```

## Test one email on yourself

```bash
autoapp send --test            # newest prepared email application, or a plain test mail if none
autoapp send <job-id> --test   # a specific job
```

This delivers one email to your own sending address instead of the employer: the subject is the normal
subject with `TEST_RUN: ` in front, the first line of the text says "This email is what would be sent to
<address> in a real run.", and the letter and `Bewerbungsmappe.pdf` follow as they would be sent. Cc, Bcc and Reply-To are dropped, so nobody else can
receive it. Nothing is recorded: no status change, no effect on the daily cap or company cooldown,
and the job can still be sent for real afterwards. It also proves that your app password works.

## Recommended rollout

1. **Run `autoapp setup`** and configure your mailbox(es), then check delivery with `autoapp send --test`. The menu entry Tests, "Check the logins", also checks that your Drafts folder is found.
2. **Run `autoapp run --dry-run`** for a few days
   - Review the "would send" list (jobs marked `prepared` with score ≥ `min_score`)
   - Check one or two generated cover letters and application folders
3. **Start in review mode:** `autoapp run --mode review`. Read the drafts in your mailbox and send the good ones yourself.
4. **Do a test run:** `autoapp run --mode test`. Check that the emails that arrive are the ones you would want sent.
5. **Go automatic with a low cap:**
   ```yaml
   run:
     default_mode: automatic
   email:
     daily_cap: 2  # Start conservative
   ```
   Or choose "Full automatic" when a run asks.
6. **Monitor the first week** with `autoapp stats --period 7d`, `autoapp status` and `autoapp list --status sent`
7. **Raise the cap** once you're confident: `daily_cap: 3`, then `5`
8. **Enable the scheduler** (see [Scheduling](scheduling.md)). Scheduled runs use `run.default_mode`, so leave it on `review` until you trust automatic mode.

## Troubleshooting

**Failed SMTP connection:**
- Check that your `.env` credentials are correct
- For Gmail: ensure the app password was created (not your regular password)
- For custom servers: verify host, port and TLS mode with your provider
- Run `autoapp setup` again to test the connection

**Email never arrives:**
- Check the recipient address in the draft (review mode) or in `email.eml` (automatic mode)
- Some employers use no-reply addresses for alerts; these can't receive emails
- If the recipient is flagged `free_mail_domains`, automatic sending is blocked for manual review (review mode still drafts it)

**Drafts cannot be saved:**
- Check the logins in the menu (Tests, "Check the logins"); it reports the Drafts folder it found
- If your provider names the folder in an unusual way, set `mail.sending.drafts_folder`

**Too many sends per day:**
- Check `daily_cap` and the current day's count with `autoapp status`
- If you've hit the cap, wait until tomorrow (Europe/Berlin timezone)
