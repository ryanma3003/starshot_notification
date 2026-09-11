# Starshot task notifier

Watches `https://starshot.scilliance.com/?broker=true` across **several accounts**
and posts to Discord when new tasks appear in any of their broker queues. Each
notification says which account it came from.

## What this site is, and why the design looks like this

Investigated before building, because it dictates the whole architecture:

- It's a React single-page app titled **"Annotation Tool"**. The raw HTML is an
  empty shell — nothing to scrape without running JavaScript.
- It sits behind **Apple SSO (AppleConnect)**. The URL redirects to
  `idmsac.apple.com`, then back through Keycloak (`auth.scilliance.com`, realm
  `tag-crowd`, client `aiml-tag-platform`).
- **The auth cookies are session-scoped.** Measured from a real login:

  | cookie | host | persistent? | lifetime |
  |---|---|---|---|
  | `aasp` | `.idmsac.apple.com` | **no** | dies on browser close |
  | `AUTH_SESSION_ID` | `auth.scilliance.com` | **no** | dies on browser close |
  | `idmsac-ext-aa` | `.apple.com` | yes | ~1 hour |
  | `KEYCLOAK_SESSION` | `auth.scilliance.com` | yes | ~3 hours |

`aasp` is AppleConnect's auth session cookie and it is deliberately
session-scoped. Chromium clears session cookies on a **graceful** shutdown — that
is what a session cookie means — so auth normally does not survive a restart.
Saving `storage_state` does not work; persisting the whole browser profile does
not reliably work either. Both were tried and both fell back to the sign-in page.

One caveat, observed in testing: when the browser is **killed abruptly** rather
than closed cleanly, Chromium never runs that cleanup, and the session cookies
are still in the profile on next launch — the watcher then comes up already
signed in. Treat this as a happy accident, not a feature. It depends on how the
process dies, and the server-side session TTL still applies regardless. Design
for having to sign in again; be pleased when you don't.

Two consequences shape everything below:

1. **The browser stays open for the life of the watcher.** Polling happens by
   reloading inside one long-lived browser, never by restarting it — restarting
   usually means signing in again.
2. **A human must sign in at a visible browser** when the watcher starts and
   whenever the session lapses. On a headless server, noVNC provides that
   visible browser.

## How it works

1. The container starts a virtual screen (Xvfb) and publishes it over noVNC.
2. It opens **one browser per account** and walks you through signing in to each
   one **in turn** — the log and the on-screen banner name the account, so you
   know which 2FA prompt on your phone you are approving. **Nothing here types
   your credentials. You do.**
3. Each account confirms with a ✅ message in Discord once it is signed in.
4. Every 5 minutes the watcher reloads the task page in each account's browser
   and extracts the task list.
5. New tasks go to Discord, labelled with the account.
6. If one account's session lapses, only that account stops. It alerts you, the
   others keep running, and it resumes by itself once you sign back in through
   that window — no restart needed.

## Files

| File | Purpose |
|---|---|
| `watch.py` | The poller. Holds one browser open per account, bootstraps sign-in, loops. |
| `accounts.py` | Loads and validates `accounts.json`. |
| `discover.py` | Run once, headed (`--account NAME`). Dumps page HTML, screenshot, every JSON response, and guessed CSS selectors so you can configure extraction. |
| `login.py` | Standalone interactive sign-in, for local testing. |
| `scraper.py` | Browser automation and task extraction. |
| `notifier.py` | Discord webhook delivery. |
| `state.py` | Remembers announced task ids so restarts don't re-notify. |
| `test_discord.py` | Sends one test message to verify the webhook. |
| `entrypoint.sh` | Starts Xvfb + x11vnc + noVNC, then the watcher. |

## Setup

### 1. Configure

```bash
cp .env.example .env
```

Set `DISCORD_WEBHOOK_URL` (Discord: channel → **Settings → Integrations →
Webhooks → New Webhook → Copy Webhook URL**) and `VNC_PASSWORD` (any strong
password — it protects a live authenticated Apple session).

### 2. Define your accounts

```bash
cp accounts.example.json accounts.json
```

One entry per account. `name` becomes a directory, so keep it filesystem-safe;
`label` is what shows up in Discord. Set `webhook` to send an account to its own
channel, or leave it `null` to use the global `DISCORD_WEBHOOK_URL`.

```json
[
  {"name": "acct-1", "label": "Main account", "webhook": null},
  {"name": "acct-2", "label": "Second account", "webhook": null}
]
```

Without this file the watcher runs a single account, which is handy for testing.

### 3. Verify Discord

```bash
python test_discord.py
```

### 4. Extraction is already configured

Discovery has been run against a real session, so this is settled. What it found:

**Starshot is a single-task broker, not a task list.** The page shows one task
slot, one `Done` button (`#starshot_submit_button`), a work timer, and — when the
queue is empty — a modal reading *"there are no available tasks at the moment"*
with a `Try Again` button.

So availability is binary, and `EXTRACT_MODE=broker` keys off stable hooks:

| Hook | Used for |
|---|---|
| modal text `no available tasks` | queue is empty |
| `h1[aria-label^="Show info about this task"]` | task id, from the `(...)` suffix |
| `#starshot_submit_button` | confirms a task is really loaded |

The page's `sc-gKkgUA`-style class names are **deliberately not used** — they are
styled-components hashes, regenerated on every frontend build.

If the page is in a state matching none of these, extraction raises rather than
guessing. An unrecognised error screen must never read as "task available".

## Deploy to the VPS

```bash
docker compose up -d --build
```

The noVNC port is bound to **loopback only**, on purpose — the container holds a
live authenticated Apple session, and anyone who can reach that port can use it.
Tunnel in from your laptop:

```bash
ssh -N -L 6080:localhost:6080 you@your-server
```

Then open <http://localhost:6080/vnc.html>, enter your `VNC_PASSWORD`, and sign
in to each account's Chromium window as it is presented. The watcher does them
**one at a time** and names the account each time, so four accounts means four
sequential sign-ins with four 2FA approvals. Polling starts once they are done.

Sizing: each account holds its own Chromium open permanently. Budget roughly
**2-4GB of RAM for four accounts**; the compose file sets a 6GB limit and 2GB of
shared memory.

```bash
docker compose logs -f
```

> Do **not** publish port 6080 to `0.0.0.0`, and do not put it behind a plain
> HTTP reverse proxy. If you need access without SSH, terminate TLS in front of
> it and add a second layer of authentication.

## Continuous deployment

`.github/workflows/ci.yml` runs on every push: syntax checks, compose validation,
a guard that fails if any secret-bearing path becomes tracked, and the full test
suite.

`.github/workflows/deploy.yml` is **manual only** — Actions → *Deploy to VPS* →
type `DEPLOY` in the confirm box. It is not wired to push, on purpose: a deploy
restarts the container, which closes every browser and signs out **all** accounts.
With five accounts that is five fresh 2FA sign-ins, which is far too expensive to
trigger from a routine commit.

It runs the tests, builds the image, pushes it to GHCR, then SSHes in and
restarts against the new image.

### Required GitHub secrets

| Secret | What it is |
|---|---|
| `TS_OAUTH_CLIENT_ID` | Tailscale OAuth client id |
| `TS_OAUTH_SECRET` | Tailscale OAuth client secret |
| `VPS_HOST` | the server's MagicDNS name or tailnet IP |
| `VPS_USER` | SSH user on the server |
| `VPS_PATH` | absolute path to the project directory on the server |
| `VPS_PORT` | SSH port — optional, defaults to 22 |
| `GHCR_TOKEN` | PAT with `read:packages`, so the server can pull. Not needed if the GHCR package is public |

`GITHUB_TOKEN` is injected automatically — do not create it.

**No SSH private key is stored in GitHub.** The runner joins the tailnet as an
ephemeral `tag:ci` node and authenticates through Tailscale SSH, so access is
governed by tailnet ACLs. Revoking deploy access is an ACL edit, not a key
rotation, and there is no long-lived credential to leak.

### Tailscale prerequisites

On the server, Tailscale SSH must be enabled:

```bash
sudo tailscale up --ssh
```

The OAuth client needs the `auth_keys` scope and must be allowed to use
`tag:ci`. In your tailnet policy:

```jsonc
"tagOwners": {
  "tag:ci":     ["autogroup:admin"],
  "tag:server": ["autogroup:admin"],
},
"ssh": [
  {
    "action": "accept",
    "src":    ["tag:ci"],
    "dst":    ["tag:server"],      // or the specific node
    "users":  ["your-deploy-user"],
  },
],
```

`action: "accept"` matters — `"check"` demands interactive re-authentication,
which a CI runner cannot satisfy, and the deploy will hang until it times out.

### Build architecture

The VPS is **arm64**, and GitHub's default runners are amd64, so the image is
cross-built under QEMU. That is correct but slow — expect the build job to take
tens of minutes, mostly emulating the apt install and the Chromium download.
Buildx layer caching means unchanged layers are reused on later deploys.

Two optional repository **variables** (not secrets) tune this:

| Variable | Default | Use |
|---|---|---|
| `BUILD_PLATFORMS` | `linux/arm64` | set to `linux/amd64,linux/arm64` to publish both |
| `BUILD_RUNNER` | `ubuntu-latest` | set to `ubuntu-24.04-arm` for a native, much faster build — needs native ARM runners, which are free for public repos and otherwise a Team/Enterprise feature |

If the emulated build becomes too slow to live with, the other option is to drop
GHCR and build on the VPS itself — it is arm64, so that build is native. It
costs VPS CPU and disk on every deploy instead of CI minutes.

### First-time server setup

Two files are gitignored and must exist on the server before the first deploy —
the deploy does a preflight check and aborts rather than tearing down a working
container if either is missing:

```bash
mkdir -p /path/to/starshot_notif/state
cd /path/to/starshot_notif
# create .env       (webhook, VNC password — see .env.example)
# create accounts.json (see accounts.example.json)
```

`state/` is a bind mount holding browser profiles and the seen-task lists. Keep
it across deploys or you will be re-notified about tasks you have already seen.

### Creating .env safely on the server

`.env` holds the Discord webhook and `VNC_PASSWORD`, and `VNC_PASSWORD` guards
live authenticated Apple sessions. Write it **by hand on the server**. It is a
one-time job that rarely changes, and keeping it off GitHub means one fewer
system holding the password to your accounts.

First make sure the directory exists and is yours — `install` does not create
parent directories, and a root-owned path will refuse the write:

```bash
sudo mkdir -p /your/vps/path/state && sudo chown -R "$USER:$USER" /your/vps/path && chmod 750 /your/vps/path
```

Then create the file with tight permissions *before* it has any content, so
there is no moment where a secret sits in a world-readable file:

```bash
install -m 600 /dev/null /your/vps/path/.env
```

Equivalently, if you would rather not use `install` — `umask 077` makes the new
file `600` from the moment it exists:

```bash
umask 077 && : > /your/vps/path/.env
```

Then fill it in with an editor — **not** `echo` or `cat >>`, which write the
secret into your shell history:

```bash
nano /your/vps/path/.env
```

Then verify, and lock down the directory:

```bash
stat -c '%a %U:%G %n' /your/vps/path/.env && chmod 750 /your/vps/path
```

You want `600` and your own user. Check for editor leftovers too — `vim` can
leave a `.env.swp` with the same contents and laxer permissions:

```bash
ls -la /your/vps/path | grep -iE '\.env'
```

Three honest limits on what those permissions buy you:

- **Anyone in the `docker` group can read the values regardless**, via
  `docker inspect` or `docker exec … env`. The docker group is effectively root.
  File permissions protect against other unprivileged users, not against someone
  who can already run containers.
- **VPS snapshots and backups contain the file.** If your provider stores
  snapshots, the secrets are in them.
- **Rotation is manual.** If the webhook leaks, delete it in Discord and write a
  new one here; there is no automated path.

If you would rather have a single source of truth, the alternative is to store
the values as GitHub secrets and have the deploy write `.env` over SSH. That
makes rotation a secrets edit, but it also puts the password to five
authenticated Apple sessions into GitHub and passes it through a CI runner on
every deploy. For five accounts you sign into by hand anyway, the manual file is
the smaller blast radius.

### Telling the five windows apart

Every account has a **fixed position** on the container's 1920×1080 virtual
screen, so the same account is always in the same place:

```
┌──────────────┬──────────────┬──────────────┐
│ Account 1    │ Account 2    │ Account 3    │
│ top-left     │ top-middle   │ top-right    │
├──────────────┼──────────────┼──────────────┤
│ Account 4    │ Account 5    │              │
│ bottom-left  │ bottom-middle│              │
└──────────────┴──────────────┴──────────────┘
```

The watcher prints the layout at startup and names the position in each sign-in
banner:

```
  >>> Sign in now for: Account 3: IT  <<<
  >>> Window: top-right of the screen  <<<
```

Browsers are also launched **one at a time, at their turn**, so the window that
just appeared is always the one asking to be signed in. Nothing else is
competing for your attention.

This matters more than it looks: five identical Chromium windows all showing
"AppleConnect Sign In" are otherwise indistinguishable, and signing an account
into the wrong window would store its session under a different account's
profile — every later notification would carry the wrong label.

### After every deploy

All accounts are signed out. Tunnel in and sign each back in:

```bash
ssh -N -L 6080:localhost:6080 you@your-server
```

Then <http://localhost:6080/vnc.html>, and sign in to each account as the
watcher presents it. The workflow summary repeats these steps with your own
host filled in.

## How it behaves

- **Polls every 5 minutes** (`POLL_INTERVAL_SECONDS`).
- **Only new tasks notify.** Ids are remembered for 7 days, then forgotten so a
  recycled id can notify again.
- **Discord failures don't lose tasks.** A task is marked "seen" only after
  Discord accepts the message.
- **Alerts fire once per outage**, not every cycle, with a "Recovered" message
  when it resumes. Backoff is exponential while broken, capped at 30 minutes.
- **It refuses to report an empty queue if the page didn't render.**
  `APP_READY_SELECTOR` guards the silent-failure mode where a broken page looks
  identical to "no tasks available".

## Limits, honestly

- **You will re-sign-in regularly.** The persistent cookies in the chain last
  ~1–3 hours; the server-side session may allow longer. This is the real cost of
  the tool and there is no way around it — plan on opening noVNC at least daily.
- **A container restart always means signing in again.** `restart: unless-stopped`
  will bring the process back, but not your session.
- **Extraction config ships empty on purpose.** `TASK_SELECTOR` is unset because
  the logged-in page was never observed during development. Run `discover.py`.
- **Sign-in is always manual, by design.** There is no credential automation in
  this tool and no place to put a password. Every account requires 2FA from your
  phone, so a human completes every sign-in.

## The hand-off

The point of this tool is to tell you work exists so you can do it **on your own
machine**. Two sessions for one account would collide, so when a task appears the
bot steps aside:

1. Discord gets the task notification, labelled with the account.
2. Discord gets a second message: **🔓 Handed over**.
3. The bot clears that account's cookies and stored token, so it holds no
   session at all, and parks the window at the login page.
4. That account is **paused**. It is checked passively each cycle — never
   navigated — so it cannot disturb a sign-in you are doing in that window.
5. You work the task on your laptop. When you're done, sign the bot back in
   through noVNC. It notices on the next cycle, announces **✅ Signed in**, and
   resumes polling by itself. No restart.

Other accounts keep polling throughout. Only the one with the task pauses.

`RELEASE_MODE=browser` instead closes that account's browser outright. Simpler,
but the account then stays stopped until you restart the watcher — so `session`
is the default.

## Does polling claim tasks?

**No.** Confirmed by the account owner: opening the broker page shows whether a
task is available without assigning it. So polling every 5 minutes is safe, and
the watcher reads availability without taking work it cannot do.

The app does issue its own request on page load — that is why a fresh load shows
the "no available tasks" modal rather than an idle screen — but that request does
not claim.

`CLICK_RETRY` stays `false` regardless. Pressing `Try Again` is a deliberate,
explicit request for work rather than a passive page load, and the watcher has no
reason to make it. Turn it on only if you later decide you want the bot actively
asking for tasks.

## Before you run this

This is Apple internal infrastructure, and you are presumably working on it under
a contributor or vendor agreement. Two things worth checking yourself:

1. **Whether automated polling of the task queue is permitted.** Many crowd-work
   agreements forbid scripted access, and enforcement is typically account
   termination rather than a warning.
2. **Whether live sessions may run on a server you control.** The VPS holds an
   authenticated Apple session in memory, for every account, for as long as the
   watcher runs.
3. **Whether operating several accounts this way is permitted.** Multi-accounting
   is restricted under some crowd-work agreements independently of automation.

The design is deliberately conservative — no credential automation, no 2FA
circumvention, a gentle 5-minute interval, your own normal interactive login, and
the remote-access port closed to the internet by default. But the policy question
is yours, and worth answering before deploying rather than after.
