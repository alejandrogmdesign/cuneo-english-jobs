# English teaching work near Cuneo

A one-page list of calls for English teachers and native-speaker experts ("esperto madrelingua inglese", lettorato, Cambridge/Trinity courses, conversation) posted by schools within about an hour of Cuneo. A gift for a friend: an American teacher who works on contract, so interpelli and supplenze are left out.

Everything is free and uses no AI:

- **GitHub Actions** runs `scanner/scan.py` every 3 hours (06:20 to 21:20 Italian time). It reads each school's notice board, keeps notices that match the English-teaching keywords, and commits `docs/jobs.json`.
- **GitHub Pages** serves `docs/` (the page and the list).
- **A Cloudflare Worker** (`worker/`) stores the websites she adds or removes in "Schools I follow". Her private link carries the key that allows changes; anyone else sees the page read-only.

## What's in here

| Path | What it is |
|---|---|
| `data/schools.json` | 138 state and private schools (no nurseries) within 70 minutes' drive of Cuneo, from the Ministry's open data (2026/27), with drive times from OSRM |
| `scanner/scan.py` | The scanner. Readers for Argo (public albo RSS), Spaggiari (albo search), Nuvola (public noticeboard), Karon "Scuolastico" (albo and circolari) and WordPress (search feed), plus a generic link reader for any site she adds |
| `scanner/match.py` | Keyword rules: what counts as an English contract call, what's an outcome ("Past calls"), and what's paperwork |
| `docs/index.html` | The page |
| `docs/config.js` | The Worker address (empty = read-only page) |
| `worker/` | The Cloudflare Worker for her site list |
| `.github/workflows/scan.yml` | The schedule |
| `scripts/check-page.mjs` | Headless screenshots and an add/remove test of the page |

Run a scan by hand with `python3 scanner/scan.py`, or `python3 scanner/scan.py --only CNIS00300E` for a single school.

## Setting it up (once)

1. **GitHub.** Create a public repository (public keeps Actions minutes free), push this folder, then in Settings > Pages choose "Deploy from a branch", branch `main`, folder `/docs`. The page appears at `https://<user>.github.io/<repo>/`.
2. **Cloudflare Worker.** In `worker/`:
   - `npx wrangler login`
   - `npx wrangler kv namespace create SOURCES` and paste the id into `wrangler.toml`
   - set `ALLOWED_ORIGIN` in `wrangler.toml` to `https://<user>.github.io`
   - `npx wrangler secret put EDIT_KEY` with a long random key (for example the output of `openssl rand -hex 24`)
   - `npx wrangler deploy`, which prints the Worker address
3. Put the Worker address in `docs/config.js` (`sourcesApi`), and in the GitHub repository under Settings > Secrets and variables > Actions > Variables as `SOURCES_API`.
4. Optional: to start a scan the moment she adds a site, create a fine-grained GitHub token for this repository with "Actions: read and write", then `npx wrangler secret put GH_TOKEN` and set `GH_REPO` in `wrangler.toml`.
5. Her link: `https://<user>.github.io/<repo>/#key=<EDIT_KEY>`. The page saves the key on her device and removes it from the address bar. Share the plain link (without `#key=`) with anyone else.

## Things to know

- GitHub pauses scheduled workflows in a public repository after 60 days without activity. The scan commits on every run, which counts as activity, but if the list stops updating, open the Actions tab and re-enable the workflow.
- The keyword rules are tuned on real notices from October and November 2026. If something irrelevant shows up or something is missed, adjust `scanner/match.py`.
- Schools post most of these calls from September to November and again in spring.
- Five schools in the list have dead websites in the Ministry data. They appear under "Needs attention" in "Schools I follow"; adding the right address fixes them.
