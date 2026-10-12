# EU FG IV Tracker - web version

A small Python web app that reads official EU Careers vacancy listings and filters FG IV rows. Not affiliated with EPSO.

## Deploy with Render

1. Upload these files to a new GitHub repository.
2. In Render, choose **New > Blueprint** and connect the repository. Render will use `render.yaml`.
3. Open the public URL provided by Render. Click **Actualiser les offres** to retrieve listings.

No Python installation is needed on the visitor's computer. Render free instances may sleep when idle. Free instance storage is **ephemeral**, so saved favorites and previously fetched listings may disappear after a restart/redeploy. For durable data, use a paid persistent disk or hosted database. The scraper can break if EU Careers changes its site structure; the site might also block hosted traffic. The app does not parse vacancy PDFs and does not guarantee completeness.

## Local test

```sh
python -m pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:8765. In production, the app binds to `0.0.0.0:$PORT` automatically.

## Important privacy note

This is a public web app. Do not enter personal data. Favorites are shared across visitors to the same deployment (there is no authentication). For private favorites, add authentication and per-user storage before distributing the site widely.
