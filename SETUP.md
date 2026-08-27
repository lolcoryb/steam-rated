# Getting it live

About ten minutes, most of it waiting. At the end you have a URL like
`https://yourname.github.io/steam-rated/` that you can send to anyone.

## 1. Make the repository

Sign in at [github.com](https://github.com) (a free account is fine), then
[create a new repository](https://github.com/new):

- **Name:** `steam-rated` — this becomes part of your URL
- **Public** — required. Private repos don't get free Pages hosting, and public
  repos get unlimited Actions minutes. Nothing secret is in here.
- Leave "Add a README" unchecked.

## 2. Upload the files

On the empty repo page, click **uploading an existing file**. Drag in
everything from the unzipped folder. Keep the folder structure — GitHub's
uploader preserves it if you drag the whole folders in at once:

```
.github/workflows/refresh.yml
docs/index.html
scripts/fetch_steam.py
scripts/test_parser.py
README.md
SETUP.md
```

Then **Commit changes**.

> If the `.github` folder doesn't upload (some browsers hide dotfolders), create
> it by hand: **Add file → Create new file**, type
> `.github/workflows/refresh.yml` as the name — typing the slashes creates the
> folders — then paste the contents of that file in.

## 3. Let the workflow write to the repo

**Settings → Actions → General**, scroll to **Workflow permissions**, choose
**Read and write permissions**, then **Save**. Without this the crawl runs but
can't commit its results.

## 4. Turn on Pages

**Settings → Pages**. Under **Source** pick **Deploy from a branch**, then set
the branch to **main** and the folder to **/docs**. Save.

## 5. Run the crawl once by hand

**Actions** tab → **Refresh Steam data** in the left sidebar → **Run workflow**
→ **Run workflow**.

The first run takes 10–15 minutes: it crawls about 180 days of releases, then
spends most of that time looking up developer and genre for up to 400 games at
Steam's rate limit. Later runs are quick — the lookups are cached and committed,
so a daily run only handles what's new.

If you'd rather not wait, cancel it and run it again with a smaller window; the
page works fine without the extra details.

## 6. Open it

`https://<your-username>.github.io/steam-rated/`

Pages can take a couple of minutes to publish the first time. If you see the
"Could not load data.json" message, the crawl hasn't finished committing yet —
wait for the Action to go green and reload.

From then on it refreshes itself every morning around 7am Eastern.

---

## Things that will eventually go wrong

**The daily refresh stops after a couple of months.** GitHub disables scheduled
workflows in repos with no activity for 60 days. It emails you first; there's a
button in the email, or push any commit to reset the clock.

**The Action goes red and the page shows old data.** Open the failed run. If
`test_parser.py` failed, Steam changed its search markup and the regexes in
`scripts/fetch_steam.py` need updating. If the crawl itself failed, it was
probably rate limiting — re-run it. The page keeps showing the last good data
either way, which is why the script refuses to write an empty file.

**You want a different schedule or window.** Both are in
`.github/workflows/refresh.yml`: the `cron` line (in UTC) and the `--days`
value.

## Running it locally

```
python scripts/fetch_steam.py --days 90 --verify-budget 0
cd docs && python -m http.server 8000
```

Then open `http://localhost:8000`. It needs a server rather than opening the
file directly, because the page fetches `data.json` and browsers block that
over `file://`.
