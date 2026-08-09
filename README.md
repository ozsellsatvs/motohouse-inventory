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
   questions, pressing **Enter** to accept every default is fine.
3. To make it keep running in the background automatically (so it works
   even when you're signed out), open **PowerShell as Administrator**,
   `cd` into the same `actions-runner` folder from step 2, then run:
   ```
   .\svc.exe install
   .\svc.exe start
   ```
   This registers it as a Windows service that starts automatically when
   your PC boots — you don't need to keep a window open.
4. Back in **Settings → Actions → Runners**, you should see your PC listed
   with a green **Idle** dot — that means it's connected and ready.

Your PC already has Git (from GitHub Desktop) and Python installed, so no
extra software is needed. If the "Commit updated inventory" step in a run
ever fails with a `bash` or `git` not found error, install
[Git for Windows](https://git-scm.com/download/win) (adds both to your PC's
PATH) and it'll pick up on the next run.

## 5. Turn on GitHub Pages

- Repo → **Settings → Pages**
- Under **Build and deployment**, Source: **Deploy from a branch**
- Branch: **main**, folder: **/docs** → **Save**
- GitHub will show you the live URL after a minute or two — something like:
  `https://YOUR-USERNAME.github.io/motohouse-inventory/`

## 6. Run the scraper once to get real data

The repo starts with demo/placeholder inventory. To pull real inventory right away instead of waiting for 6am:
- Repo → **Actions** tab → click **Scrape Motohouse inventory** (left sidebar) → **Run workflow** → **Run workflow**
- Wait ~1–2 minutes, refresh the page, confirm it finished with a green check
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
- **Site structure on motohousems.com changes** and the scraper stops finding
  fields correctly: the workflow will still run, just may produce fewer/blank
  fields for some vehicles. Bring the log output back and the parsing logic
  in `scrape.py` can be adjusted.
