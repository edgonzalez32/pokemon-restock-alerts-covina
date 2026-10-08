# Pokémon 30th Celebration restock alerts

Watches Target.com plus two Targets near 91722 (West Covina and West Covina South) for every
30th Celebration product and pushes an alert to your phone the moment one comes into stock.

- **Checks:** about once a minute, from GitHub Actions (free on a public repo).
- **Alerts:** [ntfy](https://ntfy.sh) push notifications. Tapping one opens the product page.
- **Also alerts on:** new 30th Celebration listings appearing on Target (often right before a drop),
  Friday-morning Target vendor reminders, Thursday Walmart reminders, Pokémon Center queue windows
  (Mon–Wed ~8 AM Pacific), and release days (Oct 30, Nov 6).
- **Safety net:** if Target checks fail 10 times in a row you get a warning, so silence means "nothing in stock", not "broken".
- **iPhone app (Restock Radar):** `docs/` is an installable web app served by GitHub Pages at
  https://edgonzalez32.github.io/pokemon-restock-alerts-covina/. Add it to your Home Screen from Safari,
  open it, tap **Turn on alerts**, and save the code it shows as the `WEBPUSH_SUBSCRIPTIONS` secret.
  Alerts then arrive as native iPhone notifications; tapping one opens the product page.
  Needs iOS 16.4+ and the `VAPID_PRIVATE_KEY` secret. Use the **Send test alert** workflow to check.

Alerts only. It never buys anything; checkout is yours.

## Setup (one time)

1. Install the **ntfy** app and subscribe to your topic.
2. Repo **Settings → Secrets and variables → Actions → New repository secret**:
   name `NTFY_TOPIC`, value = your topic.
3. Repo **Settings → Pages**: Source "Deploy from a branch", branch `main`, folder `/docs`.
4. **Actions** tab → "Restock check" → **Run workflow** to start it now.

## Changing what's watched

Edit `config.json`:
- `target_products`: Target item numbers (the digits after `A-` in a target.com product URL).
- `target_stores`: Target store IDs (in the store's target.com URL).
- `reminders` / `release_days`: timed nudges (Pacific time).

To see the Target stores nearest the zip with their IDs, run the **Find nearby Targets** workflow
(it also runs whenever `config.json` changes) or `python3 find_stores.py`.

## Limits worth knowing

- GitHub sometimes delays scheduled runs by 5–15 minutes when it's busy, so keep a second alert
  source (TrackaLacker or PokeWatcher) on for the 3–6 AM Target drops.
- Store stock comes from Target's own inventory feed, which lags shelves; a call still beats it.
- Pokémon Center, Walmart, Best Buy and GameStop block automated checks, so they're covered by
  reminders and dashboard links rather than live stock.

## Run locally

```sh
python3 -m unittest discover -s tests    # tests
NTFY_TOPIC=your-topic python3 checker.py # one live pass
```
