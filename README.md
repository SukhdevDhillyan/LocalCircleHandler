# LocalCircleHandler

Local pages for app tasks. This only talks to dev.

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python server.py
```

Open http://127.0.0.1:8765

The home page has the tasks.

## Add a sticker pack

Pass the folder of `.webp` or `.png` files, the pack name, and who should see it (male, female, or both). Check folder, then upload. The pack goes to `localcircle-stickers-ap-south-1`. The app reads that bucket. Pull to refresh on Stickers; it can take about a minute.

## Add a theme

Pass one wallpaper image (jpg, png, or webp), the theme name, and an optional preview image. Check image, then upload. The theme goes to `wallpapers/{theme_id}/` on the same dev bucket. Open a chat, then Theme store, and pull to refresh. It can take about a minute.

Themes already shipped in the app (`soft_embrace`, `childhood_swing`, `moonlit_romance`, `picnic_friends`) keep the bundled picture. Use a new theme id.

The AWS profile is `vinit`, the dev account. Prod is not uploaded from this page.

## Make a daily buddy

Open **Make a daily buddy**, enter two mobile numbers, and press the button. Those two dev accounts become today's daily buddies. Pull to refresh the chat list on each phone. If either already has a different open buddy today, that pair is left as it is.

## Deploy latest main to dev

Open **Deploy latest main to dev** on the home page, then press the button. That starts the GitHub Actions workflow in `LocalCircleAutomation` which builds `LokalCircleCore@main` and `LocalCircle@main` and rolls both dev servers. Needs [GitHub CLI](https://cli.github.com/) installed and `gh auth login` (with `workflow` scope). Takes about 15–20 minutes; the page links to the run.
