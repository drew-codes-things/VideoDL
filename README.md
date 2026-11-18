<div align="center">

# VideoDL

**A self-hosted web UI for downloading video via yt-dlp. Paste one or more URLs, pick a quality tier, and downloads run in the background with live progress and a persistent history.**

[![Python](https://img.shields.io/badge/python-3.9+-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/flask-web%20ui-lightgrey?style=flat-square&logo=flask)](https://flask.palletsprojects.com/)
[![yt-dlp](https://img.shields.io/badge/yt--dlp-downloader-red?style=flat-square)](https://github.com/yt-dlp/yt-dlp)

</div>

![VideoDL's queue form and download history](screenshot.png)

---

## Overview

**Run it with one command: `python app.py`.** Paste one or more URLs into
the queue form, pick a quality tier, and each URL is downloaded sequentially
in the background using [yt-dlp](https://github.com/yt-dlp/yt-dlp). Progress
(percent, speed) is shown live in the Queue panel; finished downloads
(success or error) move into the History panel, backed by a local SQLite
database (`videodl.db`) so history survives restarts.

## Requirements

- Python 3.9+
- [`ffmpeg`](https://ffmpeg.org/) on your `PATH` - required by yt-dlp to
  merge video/audio streams and to extract MP3 for the audio-only tier.

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

Then open `http://127.0.0.1:5002` and make the owner account (see below).

## First run and sign-in

VideoDL has its own sign-in, switched on by default (see "Other ways to
sign in" below for the alternatives). There is one owner account and no
sign-up page.

1. Start it (`python app.py`) and open
   `http://127.0.0.1:5002`. With no account yet every page goes to
   **Set up**.
2. The app writes a one-time setup code to its log when it starts, a line
   like `no owner account yet: open /setup and enter the setup code ...`.
   Enter that code, a user name and a password (10 characters or more,
   twice). The code stops anyone else who reaches the page first from
   making the account, and it works once: if the owner file is ever
   deleted, the next visit to **Set up** writes a new code to the log.
3. After that, every page goes to **Sign in** until you sign in. A session
   lasts 30 days. `/signout` signs out on every device.

How it is protected:

- The password is stored as an scrypt hash in `owner.json` next to
  `videodl.db` (mode 600), never in plain text.
- 5 wrong passwords or setup codes from one address within 15 minutes lock
  that address out for the rest of the 15 minutes (`429`). Attempts sent
  at once are counted before they are checked, IPv6 addresses count by
  their `/64` network, and after 100 failures from everywhere within 15
  minutes each address gets one try and each network three (IPv4 by `/24`,
  IPv6 by `/48`). A right password from a locked address is refused too.
- During such an attack one wrong password from a neighbour on the same
  provider network does not keep your new browser out: that takes three
  wrong passwords within 15 minutes from three of their addresses (IPv4)
  or three of their home lines (IPv6, where a line's whole `/56` counts as
  one address).
- A browser that has signed in before keeps a signed device cookie and has
  its own 5 tries, so a stranger who locks out your address (or the
  address of the proxy in front) cannot lock you out of that browser.
  During an attack from many networks that browser is the way in.
  Making a new owner account ends every device cookie of the old one.
- The session cookie is `HttpOnly`, `SameSite=Lax` and signed with the app's
  secret key (`SECRET_KEY`, or `secret_key` next to `videodl.db`, made on
  first start, mode 600). It is `Secure` everywhere except plain `http` on
  `localhost`, `127.0.0.1` or `[::1]` (`SECURE_COOKIES=auto`). On a plain `http` address
  on your home network the browser drops a `Secure` cookie and the sign-in
  page says so: use https, or set `SECURE_COOKIES=false` there.
- Forget the password? Stop the app, delete `owner.json` next to
  `videodl.db`, start it again and set up a new owner with the new setup
  code from the log. Your data stays.

Behind a reverse proxy for https, with the own sign-in still on: list the
proxy's address in `TRUSTED_PROXIES` so the lockout counts each visitor's
address from `X-Forwarded-For` instead of the proxy's (the header is read
only from those addresses), and put the public host name in
`ALLOWED_HOSTS`. By default the own sign-in answers only on `localhost`,
`127.0.0.1`, `[::1]`, IP addresses and this machine's own name; any other
`Host` gets `421`, so a web page cannot reach a copy on your machine by
DNS rebinding.

Other ways to sign in:

- `WEB_USERNAME` and `WEB_PASSWORD` set: HTTP Basic Auth with those two
  values instead (no setup page, no lockout).
- `ALLOW_INSECURE=true`: VideoDL has no sign-in at all and leaves it to a
  reverse proxy in front (the author runs it this way behind the starfire
  sign-in, with the port reachable only from the proxy). Never set it on
  an address other people can reach directly.

## Configuration (`.env`)

| Key | Description |
|---|---|
| `WEB_USERNAME` / `WEB_PASSWORD` | Optional HTTP Basic Auth instead of the own sign-in. Leave blank to use the own sign-in. |
| `BIND_HOST` | Interface to bind to. Default `127.0.0.1`. |
| `PORT` | Port to listen on. Default `5002`. |
| `ALLOW_INSECURE` | Set `true` only behind a reverse proxy that signs people in: VideoDL then has no sign-in of its own. Default `false`. |
| `SECURE_COOKIES` | `auto` (default): the own sign-in's cookie is `Secure` except on plain `http` to `localhost`, `127.0.0.1` or `[::1]`. `true` or `false` force it. |
| `TRUSTED_PROXIES` | Own sign-in only: addresses or private networks of a reverse proxy in front, comma separated. Their `X-Forwarded-For` gives the visitor's address to the lockout. Empty by default. |
| `ALLOWED_HOSTS` | Own sign-in only: the only host names VideoDL answers on, comma separated (others get 421). Empty: `localhost`, `127.0.0.1`, `[::1]`, IP addresses and this machine's own name. |
| `DOWNLOAD_DIR` | Where finished files are saved. Default `./downloads`. |
| `YTDLP_AUTO_UPDATE` | `true` (default) updates yt-dlp every time VideoDL starts. |
| `SECRET_KEY` | Flask session key and the key that signs the own sign-in's cookie. Set it to any long random string so the form's CSRF token survives restarts. Without it the sign-in key is kept in `secret_key` next to `videodl.db`. With the own sign-in a key shorter than 32 bytes stops VideoDL at start-up. |
| `SESSION_COOKIE_SECURE` | Set `true` when VideoDL is served over HTTPS (for example behind a reverse proxy) so the session cookie is only sent over HTTPS. Default `false`. |
| `VIDEODL_DB_PATH` | Optional override for the SQLite history file. Default `videodl.db` next to `app.py`. The owner account (`owner.json`) and `secret_key` live in the same folder. |
| `MAX_DOWNLOAD_MB` | Largest download allowed, in MB. A video known to be bigger is refused, and a download that grows past it is stopped and its partial file deleted. Default `4096`. |
| `MAX_VIDEO_MINUTES` | Longest video allowed, in minutes. Default `240`. |
| `MIN_FREE_MB` | Disk space to keep free in `DOWNLOAD_DIR`, in MB. A download that would go below it is refused or stopped. `0` turns the check off. Default `2048`. |

Every page needs a sign-in unless `ALLOW_INSECURE=true` is set.

Live streams (and streams that have not started yet) are refused, since they
never finish and would hold the single download slot for good. Playlist,
channel and other links that hold several videos are refused too: paste the
link of each video you want.

Only `http://` and `https://` links to public hosts are accepted, at most 50
per submit and 2000 characters each. Links whose host resolves to a private,
loopback or link-local address (your LAN, the server itself) are refused, both
when they are added and again just before they are downloaded, so VideoDL
cannot be used to fetch pages from inside your network.

Each job in the Queue has a Cancel button. A download that a restart
interrupts is tried once more; if a second restart interrupts it as well,
it is marked as failed instead of being queued again.

## Quality tiers

| Label | What it does |
|---|---|
| Best available | Highest quality video+audio yt-dlp can find |
| 1080p / 720p / 480p | Caps resolution, falls back gracefully if a site doesn't offer that height |
| Audio only (MP3) | Extracts audio and converts to MP3 via ffmpeg |

## Keeping yt-dlp updated

YouTube changes how its player works every few weeks, and old copies of
yt-dlp then fail with `HTTP Error 403: Forbidden`. Installing from GitHub
only gets the yt-dlp that was newest on the day you installed, so VideoDL
keeps it fresh itself:

- Every time VideoDL starts it runs `pip install -U "yt-dlp[default]"` and,
  if a newer version arrived, restarts itself to load it. Turn this off with
  `YTDLP_AUTO_UPDATE=false` in `.env`.
- A copy that stays running for weeks keeps the version it started with, so
  restart it now and then (in Docker, `docker restart videodl` once a week
  from cron or a systemd timer does it).
- When YouTube still answers 403, the History panel says so and points here.

To update by hand:

```bash
pip install -U "yt-dlp[default]"
```

## Running the tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

The tests fake yt-dlp and DNS, so they download nothing and need no network.

## Tech

- Flask web UI, SQLite for job/history persistence, a single background
  worker thread processing one download at a time.
- No build step - inline templates and static assets.

## License

MIT, see [LICENSE](LICENSE).
