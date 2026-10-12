# Scheduling

autoapp can run automatically every day using systemd (Linux) or cron.

## Systemd user timer (recommended for Linux)

autoapp ships with systemd unit files that run the job search and application pipeline daily.

### Setup

1. **Copy the service and timer files:**
   ```bash
   mkdir -p ~/.config/systemd/user
   cp deploy/systemd/autoapp.service ~/.config/systemd/user/
   cp deploy/systemd/autoapp.timer ~/.config/systemd/user/
   ```

2. **Edit the files if needed:**
   - By default, the service assumes your workspace is at `~/dev/auto_app/workspace`
   - If you use a different path, edit `WorkingDirectory` in `autoapp.service`

3. **Reload and enable:**
   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now autoapp.timer
   ```

4. **Verify it's running:**
   ```bash
   systemctl --user list-timers
   ```

   You should see `autoapp.timer` with `NEXT` showing the next scheduled run (usually today or tomorrow at 08:00).

5. **View logs:**
   ```bash
   journalctl --user -u autoapp -f
   ```

   Or see the last run:
   ```bash
   journalctl --user -u autoapp -n 50
   ```

### Automatic startup when you log in

By default, systemd user timers stop if you log out. To run them while logged out:

```bash
loginctl enable-linger $USER
```

This allows the timer to run 24/7 (as a separate user service, not tied to your login session).

### Manual run

To run immediately without waiting for the schedule:

```bash
systemctl --user start autoapp
```

### Check what happens on each run

The timer runs `autoapp run --non-interactive`, which:
1. Searches for jobs across configured sources
2. Fetches and filters them
3. Evaluates each job
4. Prepares cover letters for high-scoring jobs
5. Handles emails according to `run.default_mode`: saves drafts in your mailbox (`review`, the default) or sends them (`automatic`)
6. Exports results to the CSV

A scheduled run never asks which mode to use. It uses `run.default_mode` from `config.yaml`, which is `review` unless you change it (it cannot be `test`). Change it in the main menu under Settings, or in the file:

```yaml
run:
  default_mode: review  # or automatic
```

Form applications are prepared in both modes. Unresolved employer/ATS links can use a bounded headless browser pass to find the form; filling, account creation and submission remain manual. Fill them later with the menu entry "Finish forms".

See the logs with `journalctl --user -u autoapp` to verify each step.

### Watching a scheduled run live

While a scheduled run is active, open `autoapp stats` in another terminal (or choose Statistics in the main menu). It shows that run live, with the run statistics on top and a feed of emails, drafts, folders and forms below. It never takes the run lock, so it cannot disturb the run. A run with no update for 15 minutes is marked stale. Use `autoapp stats --period 7d` afterwards to see what the last week produced.

## Cron alternative (if not using systemd)

If you prefer cron (or don't use systemd), run this command daily:

```bash
# Edit your crontab
crontab -e

# Add this line to run every day at 08:00
0 8 * * * /usr/bin/env uv run autoapp --workspace ~/dev/auto_app/workspace run --non-interactive
```

**Note:** The cron approach doesn't integrate with systemd logs, so monitoring is harder. The systemd timer is recommended.

## Monitoring scheduled runs

### With systemd

```bash
# List upcoming runs
systemctl --user list-timers

# View recent logs
journalctl --user -u autoapp -n 50

# Watch live logs (Ctrl+C to exit)
journalctl --user -u autoapp -f

# Check status
systemctl --user status autoapp
```

### With cron

Cron runs in the background with no output unless an error occurs. Errors are emailed to your user account (if mail is configured on the system).

To capture cron output, redirect to a log file:

```bash
0 8 * * * /usr/bin/env uv run autoapp --workspace ~/dev/auto_app/workspace run --non-interactive >> ~/autoapp.log 2>&1
```

Then view logs with `tail -f ~/autoapp.log`.

## Disable scheduling

### Systemd

```bash
systemctl --user disable autoapp.timer
```

To re-enable:
```bash
systemctl --user enable autoapp.timer
```

### Cron

```bash
crontab -e
# Remove the autoapp line
```

## Troubleshooting

**Timer not triggering:**
- Check that the timer is enabled: `systemctl --user list-timers`
- Verify linger is on if you log out: `loginctl show-user $USER` (look for `Linger: yes`)
- Check the systemd journal for errors: `journalctl --user -u autoapp`

**Wrong workspace path:**
- Edit `~/.config/systemd/user/autoapp.service`
- Change `WorkingDirectory=%h/dev/auto_app` to your actual workspace root
- Run `systemctl --user daemon-reload` and `systemctl --user restart autoapp.timer`

**Schedule time doesn't match your timezone:**
- The default schedule is 08:00 Europe/Berlin
- Edit the `autoapp.timer` file to change `OnCalendar=*-*-* 08:00:00`
- Or set your system timezone: `timedatectl set-timezone Europe/Berlin`

**Emails not sending on schedule:**
- Scheduled runs use `run.default_mode`, which is `review` by default. Review mode saves drafts in your mailbox and sends nothing. Set `run.default_mode: automatic` in `config.yaml` to send
- Run a manual test: `uv run autoapp run --dry-run` to see what would send, or `autoapp run --mode test` to get the emails yourself
- Run `autoapp drafts` to see saved drafts
- Check `journalctl` for SMTP errors
- Verify `.env` credentials are correct

**Logs too verbose:**
- By default, all runs are logged to `workspace/logs/`
- Rotate or delete old logs manually: `rm workspace/logs/run-*.log`
