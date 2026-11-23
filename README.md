# LocalCircleHandler

Local pages for app tasks. This only talks to dev.

## Add a sticker pack

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python server.py
```

Open http://127.0.0.1:8765

Pass the folder of `.webp` or `.png` files, the pack name, and who should see it (male, female, or both). Check folder, then upload. The pack goes to `localcircle-stickers-ap-south-1`. The app reads that bucket. Pull to refresh on Stickers; it can take about a minute.

The AWS profile is `vinit`, the dev account. Prod is not uploaded from this page.
