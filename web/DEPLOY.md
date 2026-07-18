# Deploying NBAI (first-timer guide)

The site is a **static site**: plain files in `web/` (`index.html` + `data.js`). No server
needed. `data.js` is generated from the model by `scripts/export_web.py`.

## Preview it locally (no tools)
Just open `web/index.html` in your browser (double-click it). It loads `data.js` from the
same folder. To refresh the numbers after the model changes:

```
python scripts/export_web.py     # regenerates web/data.js
```

## Put it online with Vercel (recommended, free)

**One-time setup:**
1. Make a free account at **github.com** and **vercel.com** (sign into Vercel *with* GitHub).
2. Push this project to a GitHub repo (from the project folder):
   ```
   git remote add origin https://github.com/<you>/nbai.git
   git push -u origin main
   ```
3. In Vercel: **Add New → Project → import your repo**.
   - **Root Directory:** set to `web`
   - **Framework Preset:** Other
   - Leave build/output empty (it's static). Click **Deploy**.
4. You get a live URL like `nbai.vercel.app` in ~30 seconds.

**Every future update:** run `python scripts/export_web.py`, then
`git add -A && git commit -m "update data" && git push` — Vercel redeploys automatically.

## Custom domain
In Vercel → your project → **Settings → Domains → Add**. It walks you through buying one
(or connecting one you own). `nbai.gg`, `nbai.app`, `nbai.io` are good candidates (~$10–15/yr).

## Keeping it fresh during the season (later)
A scheduled job (GitHub Actions) can run the scraper + `export_web.py` + push each morning,
so the site updates itself. We'll set that up once daily games are flowing.
