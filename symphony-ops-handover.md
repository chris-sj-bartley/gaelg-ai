# Symphony-Ops Handover — Agentic management of manx-ai.shef.ac.uk

Orchestration pattern mirrors the Titan symphony setup exactly: same `acp24csb`
account, same default `~/.claude` auth, same `symphony@.service` user unit.
No separate OS user, no `CLAUDE_CONFIG_DIR`.

Net-new work vs Titan: **staging environment + CI auto-merge gate** (Titan just
opens PRs; this setup adds a staging instance and a pipeline that can promote
safe-class changes automatically).

---

## Current state

| Thing | Status |
|---|---|
| Prod API (`:8000`) | Running — `manx-tts.service` user unit |
| Telegram bot | Running — `gaelg-bot.service` user unit |
| Staging API (`:8001`) | Running — `manx-api@staging.service` user unit (CPU-only) |
| `staging` branch + worktree | Created — `/exp/exp1/acp24csb/web_platform_staging` |
| Smoke / deploy scripts | `scripts/smoke.sh`, `deploy-backend.sh`, `promote.sh` |
| Linger | Enabled — user session survives logout |
| `~/symphony/` | **Missing** — needs rsync from Titan |
| Node.js | **Missing** — needs nvm install |
| nginx staging vhost | **Blocked** — needs sysadmin (sudo) |

---

## PHASE 0 — Staging (done)

Backend staging instance is up on `:8001` as a user service.
Smoke and deploy scripts are in `scripts/`.

**One-time fix after root filesystem has space** (currently 100% full — see
storage issue in ops notes):
```bash
cp /run/user/1108/systemd/user/manx-api@.service ~/.config/systemd/user/
systemctl --user enable manx-api@staging
```
Until then the template lives in tmpfs and survives until next reboot.

**Nginx staging vhost** — hand to sysadmin along with the storage cleanup
request. They need to:
1. Add `staging.manx-ai.shef.ac.uk` vhost (basic-auth, proxy to `:8001`)
2. Confirm wildcard TLS cert covers that subdomain

**Frontend relative API paths** — confirm frontend calls `/api/...` not a
hardcoded hostname, so the same build works on both vhosts.

---

## PHASE 1 — Copy symphony from Titan

No new OS user needed. Everything runs as `acp24csb`.

### 1.1 Install Node (nvm, same version as Titan)

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
source ~/.bashrc
nvm install 22.22.3
node --version   # v22.22.3
```

### 1.2 Copy the orchestrator from Titan

```bash
rsync -av titan:~/symphony/ ~/symphony/
cd ~/symphony/symphony-claude
npm ci
npm run build
```

### 1.3 Secrets

```bash
mkdir -p ~/symphony
cat > ~/symphony/.env <<'EOF'
LINEAR_API_KEY=...
GH_TOKEN=...        # fine-grained: repo contents + PRs on gaelg-ai
EOF
chmod 600 ~/symphony/.env
```

### 1.4 Write the WORKFLOW.md for this repo

```bash
cat > ~/symphony/projects/web_platform/WORKFLOW.md <<'EOF'
# Manx AI web-platform workflow

Repo: https://github.com/c-bartley/gaelg-ai
Working dir: /exp/exp1/acp24csb/web_platform_staging   (staging branch)

## Safe classes (auto-merge eligible after CI passes)
- dep-bump
- content
- docs

## Agent rules
- Never restart prod directly; use scripts/deploy-backend.sh
- Never merge to master manually; open a PR and let CI promote
- Always run scripts/smoke.sh http://127.0.0.1:8001 before opening a PR
EOF
```

### 1.5 Symphony service unit

Create `~/.config/systemd/user/symphony@.service` (needs root space — use
`/run/user/1108/systemd/user/` as a temporary location if root is still full):

```ini
[Unit]
Description=Symphony orchestrator (%i)
After=network-online.target

[Service]
EnvironmentFile=/home/acp24csb/symphony/.env
WorkingDirectory=/home/acp24csb/symphony/symphony-claude
Environment="PATH=/home/acp24csb/.nvm/versions/node/v22.22.3/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=node dist/index.js /home/acp24csb/symphony/projects/%i/WORKFLOW.md --no-tui
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=symphony-%i

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user start symphony@web_platform
systemctl --user enable symphony@web_platform
```

---

## PHASE 2 — Linear + GitHub wiring

Same as Titan — already understood:
- Linear MCP + GitHub MCP in `~/.claude/` config (already present from Titan auth)
- Linear native GitHub integration: `Fixes ENG-123` in PR body auto-advances issue
- Label scheme:
  - `agent-ready` — trigger; symphony picks it up
  - **Auto-merge safe:** `dep-bump`, `content`, `docs`
  - `incident` — created by health watcher (Phase 4)

**Roll out in propose-only mode for ~1 week** before enabling CI auto-merge.

---

## PHASE 3 — CI/CD pipeline (GitHub Actions)

The pipeline enforces the auto-merge gate — not the agent's judgement.

```
PR opened
  → tests + lint + build
  → deploy to staging (:8001) + scripts/smoke.sh
  → IF labels ⊆ {dep-bump, content, docs} AND checks green:
         auto-merge → deploy prod + smoke prod
     ELSE:
         require human review → manual merge → deploy prod
```

Create `.github/workflows/ci.yml` in the repo. Key steps:
- SSH to Cassini, run `scripts/deploy-backend.sh staging`, then `scripts/smoke.sh http://127.0.0.1:8001`
- On safe-class auto-merge: run `scripts/deploy-backend.sh prod` then `scripts/smoke.sh http://143.167.8.81:8000`
- Use GitHub Environments with a required reviewer on the non-safe path

Rollback = `git revert` + redeploy. Agent never touches prod directly.

---

## PHASE 4 — Monitoring

Two cron entries running as `acp24csb` (add to crontab):

```cron
# Health watcher — every 5 min
*/5 * * * * /exp/exp1/acp24csb/web_platform/scripts/health-watch.sh

# Weekly housekeeping — dep scan + disk/GPU report
0 9 * * 1 /exp/exp1/acp24csb/web_platform/scripts/housekeeping.sh
```

Scripts to write:
- `scripts/health-watch.sh` — curl prod `/health`; on failure create Linear issue
  labelled `incident` + `agent-ready`
- `scripts/housekeeping.sh` — `pip list --outdated`, disk/GPU summary, post to
  Linear as a dep-bump issue if packages are stale

---

## Build order checklist

- [x] Phase 0: staging backend (`:8001`) + smoke/deploy scripts
- [ ] Root filesystem freed (sysadmin — remove `/home/acp20rm`, 59 GB)
- [ ] Nginx staging vhost (sysadmin)
- [x] Frontend uses relative `/api/`; confirm or fix *(confirmed 2026-07-07: `API_BASE=''`, endpoints at site root — nginx regex must list each one)*
- [x] Phase 1: nvm + Node, rsync symphony from Titan, .env, WORKFLOW.md, service unit *(done 2026-06-20)*
- [x] Phase 2: verify Linear + GitHub MCP in `~/.claude/`, native integration, labels *(labels incident/agent-ready/dep-bump auto-created by scripts/linear_issue.py)*
- [x] Phase 3: GitHub Actions CI/CD pipeline *(2026-07-07: `.github/workflows/ci.yml` — checks job live; deploy-staging job scaffolded, disabled until runner→cassini SSH confirmed; enable via repo var `DEPLOY_ENABLED` + CASSINI_* secrets)*
- [ ] Propose-only mode for ~1 week
- [ ] Enable auto-merge for safe-class labels
- [x] Phase 4: health-watch.sh + housekeeping.sh *(2026-07-07: running as systemd **user timers**, not cron — crontab edits impossible while root FS is full. health-watch every 5 min, housekeeping Mon 09:00. Persistent units in `/exp/exp1/acp24csb/.config/systemd/user/`, live copies in `/run/user/1108/systemd/user/` — tmpfs, re-copy after reboot)*

## Open items

- Confirm wildcard TLS cert covers `staging.manx-ai.shef.ac.uk`
- Confirm SSH access from GitHub Actions runner to Cassini (or use self-hosted runner)
- ~~Confirm Linear project/team ID for issue creation in health watcher~~ *(team CHR `42d1702f-62b4-4ed8-96b8-0e42b0c72871`; scripts resolve it by key at runtime)*
