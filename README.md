# iCloud Shared Album Static Gallery

This workspace builds a fast static gallery from a public iCloud Shared Album URL.

## What it does

- Fetches album metadata from Apple's shared-stream API
- Downloads the shared photo and video assets
- Converts photos to lighter WebP gallery images
- Generates WebP thumbnails and video poster frames
- Writes a static site into `dist/`

## Run it

Use the bundled Python runtime:

```powershell
$py = 'C:\Users\ssullivan\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $py .\tools\build_icloud_gallery.py --album-url 'https://www.icloud.com/sharedalbum/#YOUR_SHARED_ALBUM_TOKEN'
```

For a quick test run:

```powershell
$py = 'C:\Users\ssullivan\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $py .\tools\build_icloud_gallery.py --album-url 'https://www.icloud.com/sharedalbum/#YOUR_SHARED_ALBUM_TOKEN' --max-items 12
```

## Output

- `dist/index.html`
- `dist/styles.css`
- `dist/app.js`
- `dist/album.json`
- `dist/assets/...`

The `_build/` folder is a local cache for downloaded source files.

## Docker

This repo can run as a single container that:

- builds the gallery on startup
- serves the static site over HTTP
- refreshes the album on a timer
- syncs updated frontend files into `/data/dist` whenever a new container image starts

### Build

```powershell
docker build -t icloud-gallery .
```

### Run

Create a local `.env` first. A starter template is provided in `.env.example`.

```powershell
docker run -d `
  --name icloud-gallery `
  -p 8585:8585 `
  -e ALBUM_URL='https://www.icloud.com/sharedalbum/#YOUR_SHARED_ALBUM_TOKEN' `
  -e REFRESH_INTERVAL_SECONDS=43200 `
  --user 1000:1000 `
  -v ${PWD}\docker-data:/data `
  --restart unless-stopped `
  icloud-gallery
```

Open `http://YOUR-SERVER-IP:8585`.

### Docker Compose

There is also a ready-to-edit [docker-compose.yml](docker-compose.yml).

```powershell
Copy-Item .env.example .env
# then edit .env and set ALBUM_URL
docker compose up -d --build
```

### Runtime settings

- `ALBUM_URL`: required public iCloud Shared Album URL
- `PORT`: HTTP port inside the container, default `8585`
- `REFRESH_INTERVAL_SECONDS`: poll interval for new media, default `43200` (12 hours)
- `WORKERS`: concurrent download workers, default `8`
- `OUTPUT_DIR`: generated site path inside the container, default `/data/dist`
- `CACHE_DIR`: persistent source-media cache path inside the container, default `/data/cache`
- `MAX_ITEMS`: optional test limit

### Compose user mapping

- `PUID`: optional UID used by `docker-compose.yml`, default `10001`
- `PGID`: optional GID used by `docker-compose.yml`, default `10001`

Mount `/data` to persistent storage so the generated gallery and cache survive restarts. On Linux, set `PUID` and `PGID` in `.env` to a user that owns `docker-data` so the unprivileged container process can write to it.

When you deploy a newer image, recreate the container from that image. The startup process now rewrites the HTML/CSS/JS shell in `/data/dist` before the album refresh runs, so frontend changes from the image take effect without manually deleting the volume contents. The HTML and manifest are served with `no-store`, and the CSS/JS URLs are versioned for browser cache busting.
