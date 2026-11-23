# reddit-atom-feeds

Turns subreddit listings into Atom feeds, with no API key. A GitHub Actions job
runs the script on a schedule and commits the feeds to the repo, and GitHub
Pages serves them so your feed reader can subscribe.

## Setup

1. Create a **public** GitHub repo and push these files to it.
2. Edit `feeds.json`: set your Reddit username in `user_agent` and list the
   subreddits you want.
3. In the repo go to **Settings > Pages**, choose **Deploy from a branch**,
   pick your main branch and the `/docs` folder, and save.
4. Go to the **Actions** tab, open **Update feeds** and click **Run workflow**.
5. Once it finishes, your feeds are at
   `https://<your-username>.github.io/<repo>/feeds/<id>.xml`. The index page at
   `https://<your-username>.github.io/<repo>/` lists them all.

The workflow then re-runs hourly. Edit the `cron` line in
`.github/workflows/update-feeds.yml` to change that.

## Feed options (`feeds.json`)

| Key | Meaning |
| --- | --- |
| `subreddit` | Subreddit name. `a+b` combines several. |
| `sort` | `new` (default), `hot`, `top`, `rising`, `controversial` |
| `time` | For `top` / `controversial`: `hour`, `day`, `week`, `month`, `year`, `all` |
| `limit` | Posts requested per run (max 100, default 25) |
| `min_score` | Skip posts below this score |
| `max_items` | History kept in that feed (default `max_items_per_feed`) |
| `title` | Custom feed title |

The feed id (used in the filename) is `<subreddit>_<sort>` plus `_<time>` for
`top` / `controversial`, for example `selfhosted_top_week.xml`.

## Run it locally

```
python3 fetch_feeds.py
```

Needs Python 3.9+ and nothing else. Output goes to `docs/feeds/`. Set
`FEED_BASE_URL` if you want correct `self` links when running outside Actions.

## Known risks

- **Reddit may block the request.** This uses Reddit's unauthenticated `.json`
  listings, which Reddit is clearly trying to restrict. GitHub's runners use
  datacenter IPs, which Reddit often blocks (HTTP 403 or 429). If the Actions
  run fails with that, run the same script from home (cron on a Raspberry Pi,
  NAS or always-on PC) and push the result to the repo instead.
- **If a fetch fails, the old feed stays in place.** The run is only marked as
  failed when every feed fails.
- **Be gentle.** Hourly with a handful of subreddits is modest. Polling many
  subreddits every few minutes is more likely to get you blocked.
- Reddit's terms restrict automated access. Keep this to personal, low-volume use.
