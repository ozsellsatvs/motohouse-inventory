# Motohouse Inventory App — Setup Guide

A mobile-installable app that shows Motohouse Picayune's live inventory as
swipeable cards, grouped by category (Utility ATV, Sport ATV, Side by Side,
Motorcycle, PWC, etc.) and sortable by brand or price. Inventory refreshes
automatically every day at 6:00 AM Central Time.

## What's in this folder

```
motohouse-inventory-app/
  docs/                  <- the app itself (this is what GitHub Pages serves)
    index.html
    manifest.json
    sw.js
    icons/
    data/inventory.json  <- current inventory data (starts as demo data)
  scraper/
    scrape.py            <- pulls inventory from motohousems.com
    requirements.txt
  .github/workflows/
    scrape.yml           <- runs scrape.py every day at 6am Central and
                             commits the fresh data
```

Nothing here needs editing — you're just getting it onto GitHub so it can
run automatically and be reachable from your phone.

## 1. Create a free GitHub account

Go to **github.com** → Sign up. Free plan is all you need.

## 2. Create a new repository

- Click the **+** in the top right → **New repository**
- Name it something like `motohouse-inventory`
- Set it to **Public** (required for free GitHub Pages hosting)
- Don't check any of the "initialize with" boxes
- Click **Create repository**

## 3. Upload these files

**Easiest option — GitHub Desktop (recommended):**
1. Install [GitHub Desktop](https://desktop.github.com) and sign in with your new account.
2. `File → Clone Repository` → pick the repo you just created → choose a folder on your computer.
3. Copy everything **inside** this `motohouse-inventory-app` folder (`docs`, `scraper`, `.github`, `README.md`) into that cloned folder. Make sure hidden files show up:
   - **Windows**: File Explorer → View → check "Hidden items"
   - **Mac**: Finder → press `Cmd+Shift+.`
4. Back in GitHub Desktop, you'll see all the new files listed → write a commit message like "Initial upload" → **Commit to main** → **Push origin**.

**Alternative — upload via github.com (no software install):**
1. On your repo's page, click **Add file → Upload files**.
2. Show hidden files (see above), then drag the `docs`, `scraper`, and `.github` folders (and `README.md`) straight into the upload box. Modern browsers preserve the folder structure.
3. Scroll down, add a commit message, click **Commit changes**.

## 4. Let the automation write to the repo

GitHub blocks workflows from pushing commits by default. Turn that on:
- Repo → **Settings → Actions → General**
- Scroll to **Workflow permissions** → select **Read and write permissions** → **Save**

## 4b. Set up the self-hosted runner (required)

motohousems.com's firewall blocks requests coming from GitHub's own cloud
servers — it doesn't block your home internet, just cloud/datacenter
traffic. So instead of running the scrape on GitHub's servers, this repo
is set up to have GitHub tell **your PC** to run it, using your normal home
connection. This is free and doesn't need any third-party sign-up — you
just need your PC turned on and online at 6am for that day's update to run
(if it's off, that day is skipped and the app keeps showing the last data
it had; it catches up the next time your PC is on).

1. In your repo: **Settings → Actions → Runners → New self-hosted runner**
   → choose **Windows**.
2. GitHub shows 4 commands to paste into PowerShell one at a time (Download,
   Extract, Configure, Run). Paste them exactly as shown — they include a
   security token that's unique to your repo. When `config.cmd` asks
   questions, pressing **Enter** to accept every default is fine, **except**
   the last one — "Would you like to run the runner as a service?" — answer
   **Y**. That installs it as a Windows service (so it runs in the
   background, even when you're signed out, and restarts automatically when
   your PC boots) and starts it right away. You don't need any separate
   `svc.cmd install` / `svc.cmd start` commands — that interactive prompt
   handles both.
3. Back in **Settings → Actions → Runners**, you should see your PC listed
   with a green **Idle** dot — that means it's connected and ready.

### Important: give the runner service access to Python and Git

The runner service runs under Windows' **NT AUTHORITY\NETWORK SERVICE**
account, not your normal login. That account has its own PATH (usually
empty) and, by default, no permission to read programs installed just for
your user account — like a per-user Python install or the copy of Git
bundled inside GitHub Desktop. If this isn't fixed, you'll see one of two
failures the first time the workflow runs:
- `python` / `pip not recognized` on the "Install dependencies" or "Run
  scraper" steps, or
- the "Commit updated inventory" step failing with
  `fatal: not a git repository` — this happens because `actions/checkout`
  couldn't find `git` either, so it silently downloaded a plain ZIP of the
  repo instead of doing a real `git clone`, leaving no `.git` folder to
  commit into.

Fix it once, in an **Administrator PowerShell** window:

1. Find where Python and Git actually live on your PC. Typical locations:
   - Python: `C:\Users\<you>\AppData\Local\Python\bin`
   - Git (bundled with GitHub Desktop):
     `C:\Users\<you>\AppData\Local\GitHubDesktop\app-<version>\resources\app\git\cmd`
2. Add both folders to the **Machine** (system-wide) PATH:
   ```powershell
   $paths = @(
     "C:\Users\<you>\AppData\Local\Python\bin",
     "C:\Users\<you>\AppData\Local\GitHubDesktop\app-<version>\resources\app\git\cmd"
   )
   $machinePath = [Environment]::GetEnvironmentVariable("Path","Machine")
   foreach ($p in $paths) {
     if ($machinePath -notlike "*$p*") { $machinePath += ";$p" }
   }
   [Environment]::SetEnvironmentVariable("Path", $machinePath, "Machine")
   ```
3. Grant the service account read + execute access to those folders:
   ```powershell
   icacls "C:\Users\<you>\AppData\Local\Python" /grant "NT AUTHORITY\NETWORK SERVICE:(OI)(CI)RX" /T
   icacls "C:\Users\<you>\AppData\Local\GitHubDesktop" /grant "NT AUTHORITY\NETWORK SERVICE:(OI)(CI)RX" /T
   ```
4. **Fully reboot your PC** — not just the runner service. Windows services
   cache their environment at boot, so a PATH change doesn't reach the
   service until a real restart.

You only need to do this once; it survives future runs and reboots. After
rebooting, check **Settings → Actions → Runners** — it should show **Idle**
again within a few seconds.

## 5. Turn on GitHub Pages

- Repo → **Settings → Pages**
- Under **Build and deployment**, Source: **Deploy from a branch**
- Branch: **main**, folder: **/docs** → **Save**
- GitHub will show you the live URL after a minute or two — something like:
  `https://YOUR-USERNAME.github.io/motohouse-inventory/`

## 6. Run the scraper once to get real data

The repo starts with demo/placeholder inventory. To pull real inventory right away instead of waiting for 6am:
- Repo → **Actions** tab → click **Scrape Motohouse inventory** (left sidebar) → **Run workflow** → **Run workflow**
- Wait ~3–4 minutes (most of that is the scraper itself working through ~200 vehicles), refresh the page, confirm it finished with a green check
- Your Pages URL will now show live inventory (it may take a minute to reflect after the run)

From now on, it re-runs automatically every day at 6:00 AM Central — no action needed.

## 7. Install it on your phone

**iPhone (Safari):**
1. Open your Pages URL in Safari
2. Tap the **Share** icon → **Add to Home Screen** → **Add**

**Android (Chrome):**
1. Open your Pages URL in Chrome
2. Tap the **⋮** menu → **Add to Home screen** / **Install app**

It'll now sit on your home screen and open full-screen like a normal app,
pulling the latest inventory (refreshed daily at 6am) every time you open it.
There's also a refresh button (top right, circular arrow icon) inside the
app to manually pull the latest saved data on demand.

## How it works, in short

- Every day at 6am Central, GitHub runs `scraper/scrape.py`, which reads
  motohousems.com's public inventory pages and writes the results to
  `docs/data/inventory.json`, then commits that file.
- The app (`docs/index.html`) is a static page hosted by GitHub Pages. Every
  time you open it (or hit refresh), it fetches the latest `inventory.json`.
- Categories, brands, and sort options are all generated automatically from
  whatever's actually in inventory that day — nothing is hardcoded, so new
  brands or vehicle types just show up on their own.

## If something breaks

- **Workflow fails / no new data**: Actions tab → click the failed run → read
  the log. Most likely cause is step 4 (workflow permissions) wasn't set.
- **`fatal: not a git repository` on the "Commit updated inventory" step**:
  the runner service can't find `git` on its PATH, so checkout fell back to
  a ZIP download with no `.git` folder. See "Important: give the runner
  service access to Python and Git" in step 4b.
- **`python`/`pip not recognized`**: same root cause as above — the service
  account's PATH doesn't include Python. See step 4b.
- **Runner shows Offline in Settings → Actions → Runners**: the PC is off,
  asleep, or the service didn't survive a reboot. Open **Services** (search
  it in the Start menu), find **GitHub Actions Runner (...)**, and make sure
  it's **Running** with **Startup type: Automatic**.
- **Site structure on motohousems.com changes** and the scraper stops finding
  fields correctly: the workflow will still run, just may produce fewer/blank
  fields for some vehicles. Bring the log output back and the parsing logic
  in `scrape.py` can be adjusted.
