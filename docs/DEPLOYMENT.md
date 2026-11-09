# Linux VM deployment

Use a single VM and one service account. Ubuntu 24.04 LTS with Python 3.12 is a straightforward baseline. The app needs outbound DNS/HTTPS for sources and AI, and outbound TLS MongoDB connectivity to Atlas. It does not need a graphical desktop or an inbound MongoDB port.

## Atlas setup

1. Create an Atlas deployment and a database user separate from your Atlas login. Scope the app user's `readWrite` role to the `jobscrap` database. Use a dedicated account for the optional disposable database test.
2. In Atlas Network Access, allow the local machine's public egress IP as `/32` and the VM's public egress IP as `/32`. They are separate allowlist entries. An IP change requires an allowlist update. Prefer fixed VM egress or Atlas-supported private networking; avoid `0.0.0.0/0`.
3. Get the **Drivers → Python** `mongodb+srv://...` connection string and set `MONGODB_URI`. URL-encode reserved characters in the database username/password; never put raw credentials into source, shell history, logs or commands. Edit the protected `.env` file directly. `MONGODB_DATABASE=jobscrap` selects the database.
4. Allow the driver's SRV/TXT DNS lookups and outbound access to the Atlas hosts/ports provided for your cluster (normally TCP 27017). Corporate/VPN/cloud firewalls may need separate rules. Do not open port 27017 inbound on the VM. Keep CA certificates and the VM clock current.

The client explicitly enables TLS and verifies certificates/hostnames using certifi. Do not disable verification to fix an allowlist, DNS or certificate error. Run `jobscrap doctor` separately on the Mac and VM, then `jobscrap init-db` to verify write/index privileges. Neither live connection has been verified by this implementation session.

## Install

System administration commands below are for you to run on the VM; this repository has not provisioned a VM or modified Atlas.

```sh
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip ca-certificates
sudo useradd --system --create-home --home-dir /opt/jobscrap --shell /usr/sbin/nologin jobscrap
sudo install -d -o jobscrap -g jobscrap -m 750 /opt/jobscrap/var
```

Copy the project to `/opt/jobscrap` using your SSH account and `sudo` as appropriate. Exclude the Mac `.venv`, `__pycache__`, runtime files and development caches. Copy the private resume separately. Make the service account the owner of the checkout initially:

```sh
sudo chown -R jobscrap:jobscrap /opt/jobscrap
sudo -u jobscrap python3 -m venv /opt/jobscrap/.venv
sudo -u jobscrap /opt/jobscrap/.venv/bin/python -m pip install --upgrade pip
sudo -u jobscrap /opt/jobscrap/.venv/bin/python -m pip install -e '/opt/jobscrap[test]'
sudo -u jobscrap cp /opt/jobscrap/.env.example /opt/jobscrap/.env
sudo chmod 600 /opt/jobscrap/.env
```

Edit `/opt/jobscrap/.env` securely. Set absolute `RESUME_PATH=/opt/jobscrap/Pranav_Gaur_Resume.pdf` and `STATE_DIR=/opt/jobscrap/var`. Ensure the service user can read the PDF and `.env`. Keep `DASHBOARD_HOST=127.0.0.1`. Optional AI needs `pip install -e '/opt/jobscrap[ai,test]'` and the provider configuration from the README. Set a provider-side spending budget as well as the app's per-run cap.

```sh
cd /opt/jobscrap
sudo -u jobscrap env JOBSCRAP_ENV_FILE=/opt/jobscrap/.env .venv/bin/jobscrap doctor
sudo -u jobscrap env JOBSCRAP_ENV_FILE=/opt/jobscrap/.env .venv/bin/jobscrap init-db
sudo -u jobscrap .venv/bin/python -m unittest discover -s tests -v
sudo -u jobscrap env JOBSCRAP_ENV_FILE=/opt/jobscrap/.env .venv/bin/jobscrap scrape
```

Without configured company adapters, scraping is an explicitly recorded no-op. It must not be described as a successful real-source integration.

## Daily timer and dashboard

```sh
sudo cp deploy/jobscrap-daily.service deploy/jobscrap-daily.timer deploy/jobscrap-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jobscrap-daily.timer jobscrap-dashboard.service
sudo systemctl list-timers jobscrap-daily.timer
sudo systemctl status jobscrap-dashboard.service
```

The timer runs at **06:00 UTC / 11:30 IST**, plus up to five minutes of random delay. `Persistent=true` catches up a missed timer after downtime; it does not replay every missed day. The dashboard restarts after failure and at boot. The daily service retries failures after five minutes, up to the configured three-start limit per hour. Runs have a two-hour timeout; partial company work persists. Completed score caches reduce repeat costs after restarts. Interruptions are recorded as `interrupted` by the next successful pipeline start.

The service units allow writes only to `/opt/jobscrap/var`; configure runtime paths there. The VM must run CLI, dashboard and timer as `jobscrap`, sharing `STATE_DIR`, so the lock file is accessible and concurrency prevention works. A dashboard manual run is best-effort background work: a web-server restart can interrupt it, and the next manual/scheduled run resumes through idempotent upserts and caches.

Inspect or trigger a run:

```sh
sudo systemctl start jobscrap-daily.service
sudo journalctl -u jobscrap-daily.service --since today
sudo journalctl -u jobscrap-dashboard.service -f
```

Logs contain timestamps, IDs, counters and safe error types, not raw provider errors/keys/resume text. Source-specific details may require adding sanitized diagnostics when implementing adapters. History in MongoDB complements the journal, but cannot record an error while Atlas itself is unreachable.

From the local machine, open a tunnel:

```sh
ssh -N -L 8000:127.0.0.1:8000 YOUR_SSH_USER@YOUR_VM
```

Then visit `http://127.0.0.1:8000`. Keep port 8000 closed to the public internet. If public access is required later, put it behind a properly configured HTTPS reverse proxy, set both `DASHBOARD_USERNAME` and a strong `DASHBOARD_PASSWORD`, and configure trusted forwarded headers explicitly. The current CLI disables proxy-header trust; plain reverse-proxy termination without adapting trusted headers/origin handling is not a supported preconfigured deployment. Basic credentials must not travel over public HTTP. `/healthz` exposes only database readiness without requiring login; other dashboard data/actions require configured auth.

Use **either systemd or cron**, not both. A simpler cron alternative, running as the same `jobscrap` account, is:

```cron
0 6 * * * cd /opt/jobscrap && JOBSCRAP_ENV_FILE=/opt/jobscrap/.env /opt/jobscrap/.venv/bin/jobscrap scrape >> /opt/jobscrap/var/daily.log 2>&1
```

Cron uses the VM's local timezone and lacks systemd's persistent catch-up/retry behavior. Configure log rotation for that file. The default systemd setup uses journald's retention policy.

## Optional headless Playwright

Only install it when an inspected source requires JavaScript:

```sh
sudo -u jobscrap /opt/jobscrap/.venv/bin/python -m pip install -e '/opt/jobscrap[browser]'
sudo /opt/jobscrap/.venv/bin/python -m playwright install-deps chromium
sudo -u jobscrap env PLAYWRIGHT_BROWSERS_PATH=/opt/jobscrap/var/ms-playwright /opt/jobscrap/.venv/bin/python -m playwright install chromium
```

The production helper uses Python Playwright and headless Chromium with its sandbox enabled, independent of Codex/MCP. No desktop, Xvfb or MCP server is needed. It runs under the unprivileged service user. Hardened kernels/Ubuntu AppArmor user-namespace rules can block Chromium's sandbox. Validate on your actual VM; if required, have the administrator configure an appropriately scoped AppArmor/user-namespace policy for the installed Chromium binary. Do not solve it by disabling the browser sandbox globally. The units' `NoNewPrivileges` relies on a functioning user-namespace sandbox rather than a setuid fallback.

Before enabling a browser adapter, check headless launch as the service user using a local page (no job data):

```sh
sudo -u jobscrap env PLAYWRIGHT_BROWSERS_PATH=/opt/jobscrap/var/ms-playwright /opt/jobscrap/.venv/bin/python -c 'from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True, chromium_sandbox=True); page=b.new_page(); page.set_content("<title>Chromium check</title>"); print(page.title()); b.close(); p.stop()'
```

Also test under the actual systemd unit restrictions using a temporary service override or a separate smoke-test service before scheduling a real adapter. This session has not run Chromium on Linux; the helper and installation steps are prepared, not live-verified.

## Maintenance and recovery

- After dependency/source updates, run tests, `doctor`, `init-db`, one manual scrape and an unchanged rerun. Restart the dashboard. Keep source backups and Atlas backups appropriate to the data's value.
- To change `.env`, restart the dashboard; CLI/timer runs load it on each start. Resume replacement is detected by content on runs and by file metadata on dashboard reads; use `jobscrap rescore` after changing it.
- If Atlas is down, reads show an actionable notice, health returns 503, writes retry a bounded number of times, and the service fails for systemd to retry. Restore DNS/allowlists/credentials, then rerun. Do not reset the database to fix a connection issue.
- A failed source does not close missing jobs; a verified complete snapshot does. Old jobs still disappear after 72 hours. A successful run does not imply every source is configured—check company statuses and per-company history.
- Disk/memory usage, history retention and caching need measurement once real companies are added. No untested performance/scalability claims or VM sizing assumptions are made here.
