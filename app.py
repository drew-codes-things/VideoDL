import os
import secrets
import sys
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, redirect, render_template_string, request, session
from markupsafe import Markup

import db
import worker
from quality import QUALITY_TIERS, quality_choices, resolve_quality

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

app = Flask(__name__)
app.secret_key = (os.getenv("SECRET_KEY") or "").strip() or secrets.token_hex(32)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = (os.getenv("SESSION_COOKIE_SECURE") or "false").strip().lower() == "true"

DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", os.path.join(BASE_DIR, "downloads"))

WEB_USERNAME = (os.getenv("WEB_USERNAME") or "").strip()
WEB_PASSWORD = (os.getenv("WEB_PASSWORD") or "").strip()


def auth_enabled():
    return bool(WEB_USERNAME and WEB_PASSWORD)


def requires_auth(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not auth_enabled():
            return view_func(*args, **kwargs)
        auth = request.authorization
        if auth and secrets.compare_digest(auth.username or "", WEB_USERNAME) and secrets.compare_digest(auth.password or "", WEB_PASSWORD):
            return view_func(*args, **kwargs)
        return Response(
            "Authentication required",
            status=401,
            headers={"WWW-Authenticate": 'Basic realm="VideoDL"'},
        )
    return wrapped


@app.after_request
def set_security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'",
    )
    return resp


def get_csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def has_valid_csrf(submitted_token):
    expected = session.get("_csrf_token")
    if not expected or not submitted_token:
        return False
    return secrets.compare_digest(expected, submitted_token)


CSS = """
:root {
    --void: #0a0a0b;
    --panel: rgba(255, 255, 255, .045);
    --panel-raised: rgba(255, 255, 255, .07);
    --line: rgba(255, 255, 255, .08);
    --line-bright: rgba(255, 255, 255, .14);
    --signal: #e5383b;
    --phosphor: #4ade80;
    --text: #ececec;
    --text-dim: #a1a1a6;
    --text-faint: #6b6b72;
    --mono: 'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
    --sans: 'IBM Plex Sans', -apple-system, BlinkMacSystemFont, sans-serif;
}
* { box-sizing: border-box; }
body {
    font-family: var(--sans);
    background: radial-gradient(120% 70% at 0% 0%, rgba(160, 20, 28, .38), transparent 60%), linear-gradient(165deg, #1c0608 0%, #0d0b0c 42%, #070708 100%) fixed, var(--void);
    color: var(--text);
    margin: 0;
    padding: 0;
    line-height: 1.6;
    min-height: 100vh;
}
.container { max-width: 900px; margin: 0 auto; padding: 40px 20px; position: relative; z-index: 1; }
h1, h2 { font-family: var(--sans); color: var(--text); }
h1 { font-size: clamp(1.15rem, 3vw, 1.35rem); font-weight: 500; margin: 0; }
h2 { font-size: 1.1rem; font-weight: 500; margin-top: 0; margin-bottom: 20px; padding-bottom: 10px; border-bottom: 1px solid var(--line); }
.section {
    position: relative;
    background-color: var(--panel);
    border: 1px solid var(--line);
    border-radius: 16px;
    backdrop-filter: blur(12px); box-shadow: 0 16px 40px rgba(0, 0, 0, .45);
    padding: 24px 26px;
    margin-bottom: 30px;
}
.topbar {
    position: relative; z-index: 10;
    display: flex; align-items: center; gap: 1rem;
    padding: 1rem 1.5rem; margin: 0 0 30px;
    background: rgba(10, 10, 11, .55);
    backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--line);
}
textarea, select {
    font-family: var(--sans);
    background: var(--panel-raised);
    border: 1px solid var(--line);
    color: var(--text);
    padding: 10px 12px;
    border-radius: 12px;
    width: 100%;
    box-sizing: border-box;
    margin-bottom: 12px;
    font-size: .9rem;
}
textarea:focus, select:focus { outline: none; border-color: rgba(229, 56, 59, .55); }
.home-button {
    display: inline-flex;
    align-items: center;
    gap: .4em;
    font-family: var(--sans);
    background: linear-gradient(135deg, var(--signal), #7a0f14); border: 1px solid transparent; color: #fff;
    font-size: .9rem;
    font-weight: 500;
    padding: 10px 18px;
    border-radius: 12px;
    transition: filter .15s ease;
    cursor: pointer;
}
.home-button:hover { filter: brightness(1.12); }
.warning-banner {
    background: rgba(229, 56, 59, .14);
    border: 1px solid rgba(229, 56, 59, .4);
    color: #f5c2c3;
    border-radius: 12px;
    padding: 10px 14px;
    margin: 0 0 20px 0;
    font-size: 0.9rem;
    font-family: var(--sans);
}
.queue-item { padding: 12px 0; border-bottom: 1px solid var(--line); }
.queue-item:last-child { border-bottom: none; }
.queue-item-title { font-family: var(--sans); font-size: .9rem; margin-bottom: 8px; word-break: break-all; }
.queue-progress-track { width: 100%; height: 6px; background: var(--panel-raised); border-radius: 999px; overflow: hidden; margin-bottom: 6px; }
.queue-progress-fill { height: 100%; background: var(--signal); width: 0%; transition: width .3s; }
.queue-item-meta { font-family: var(--sans); font-size: .78rem; color: var(--text-dim); }
table { width: 100%; border-collapse: collapse; font-size: .88rem; }
thead tr { border-bottom: 1px solid var(--line-bright); }
th { text-align: left; padding: 8px 6px; color: var(--text-dim); font-family: var(--sans); font-size: .78rem; }
tbody tr { border-bottom: 1px solid var(--line); }
td { padding: 10px 6px; vertical-align: top; word-break: break-word; }
.status-pill { border-radius: 999px; padding: 2px 9px; font-size: .72rem; font-family: var(--sans); }
.status-success { background: rgba(74, 222, 128, .12); color: var(--phosphor); }
.status-error { background: rgba(229, 56, 59, .14); color: #f5c2c3; }
.empty-state { color: var(--text-faint); padding: 20px 0; text-align: center; font-family: var(--sans); font-size: .85rem; }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: .001ms !important; animation-iteration-count: 1 !important; transition-duration: .001ms !important; }
}
:focus-visible { outline: 2px solid var(--signal); outline-offset: 2px; }
"""

STATUS_JS = """
(function() {
  const list = document.getElementById('queue-list');
  const section = document.getElementById('queue-section');
  if (!list || !section) return;

  function formatBytes(n) {
    if (!n) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return n.toFixed(1) + ' ' + units[i];
  }

  function knownIds() {
    return new Set(Array.from(list.querySelectorAll('[data-job-id]')).map(el => el.dataset.jobId));
  }

  function poll() {
    fetch('/status')
      .then(r => r.json())
      .then(jobs => {
        const before = knownIds();
        const after = new Set(jobs.map(j => String(j.id)));
        let finished = false;
        before.forEach(id => { if (!after.has(id)) finished = true; });
        if (finished) { window.location.reload(); return; }

        jobs.forEach(job => {
          const row = list.querySelector(`[data-job-id="${job.id}"]`);
          if (!row) { window.location.reload(); return; }
          const fill = row.querySelector('.queue-progress-fill');
          const meta = row.querySelector('[data-role="meta"]');
          if (fill) fill.style.width = job.progress_pct + '%';
          const speed = job.speed ? formatBytes(job.speed) + '/s' : '';
          if (meta) meta.textContent = (job.status + ' - ' + job.progress_pct + '% ' + speed).trim();
        });

        section.style.display = jobs.length ? '' : 'none';
        setTimeout(poll, 1500);
      })
      .catch(() => setTimeout(poll, 3000));
  }

  poll();
})();
"""

PAGE_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VideoDL</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>{{ css }}</style>
</head>
<body>
<header class="topbar"><h1>VideoDL</h1></header>
<div class="container">
  {% if error %}<div class="warning-banner">{{ error }}</div>{% endif %}

  <div class="section">
    <h2>Add to Queue</h2>
    <form method="post" action="/jobs">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <textarea name="urls" rows="5" placeholder="One URL per line" required>{{ submitted_urls or '' }}</textarea>
      <select name="quality">
        {% for key, label in quality_choices %}
        <option value="{{ key }}"{{ ' selected' if key == (submitted_quality or 'best') else '' }}>{{ label }}</option>
        {% endfor %}
      </select>
      <button type="submit" class="home-button">Add to Queue</button>
    </form>
  </div>

  <div class="section" id="queue-section" style="{{ 'display:none' if not active else '' }}">
    <h2>Queue</h2>
    <div id="queue-list">
      {% for job in active %}
      <div class="queue-item" data-job-id="{{ job.id }}">
        <div class="queue-item-title">{{ job.title or job.url }}</div>
        <div class="queue-progress-track"><div class="queue-progress-fill" style="width:{{ job.progress_pct }}%"></div></div>
        <div class="queue-item-meta" data-role="meta">{{ job.status }} - {{ job.progress_pct }}%</div>
      </div>
      {% endfor %}
    </div>
  </div>

  <div class="section">
    <h2>History</h2>
    {% if history %}
    <table>
      <thead><tr><th>Title</th><th>Quality</th><th>Status</th><th>Finished</th><th>File / Error</th></tr></thead>
      <tbody>
      {% for job in history %}
        <tr>
          <td>{{ job.title or job.url }}</td>
          <td>{{ job.quality_display }}</td>
          <td><span class="status-pill status-{{ job.status }}">{{ job.status }}</span></td>
          <td>{{ job.finished_at or '' }}</td>
          <td>{% if job.status == 'success' %}{{ job.filepath }}{% else %}{{ job.error or '' }}{% endif %}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
    {% else %}
    <div class="empty-state">No downloads yet.</div>
    {% endif %}
  </div>
</div>
<script>{{ status_js }}</script>
</body>
</html>
"""


def _serialize_active_job(job):
    progress = worker.get_progress(job["id"])
    downloaded = progress.get("downloaded_bytes") or 0
    total = progress.get("total_bytes") or 0
    pct = int(downloaded / total * 100) if total else 0
    return {
        "id": job["id"],
        "url": job["url"],
        "title": job["title"],
        "quality": job["quality"],
        "status": progress.get("status") or job["status"],
        "downloaded_bytes": downloaded,
        "total_bytes": total,
        "speed": progress.get("speed"),
        "eta": progress.get("eta"),
        "progress_pct": pct,
    }


def _quality_display(job):
    tier = QUALITY_TIERS.get(job["quality"])
    label = tier["label"] if tier else job["quality"]
    if job["quality"] == "best" and job.get("resolved_quality"):
        return f"{label} ({job['resolved_quality']})"
    return label


def _serialize_history_job(job):
    return {**job, "quality_display": _quality_display(job)}


def _render_index(error=None, submitted_urls=None, submitted_quality=None):
    with db.connect() as conn:
        active_jobs = db.list_active_jobs(conn)
        history = db.list_history(conn)
    active = [_serialize_active_job(j) for j in active_jobs]
    history = [_serialize_history_job(j) for j in history]
    return render_template_string(
        PAGE_TEMPLATE,
        css=Markup(CSS),
        status_js=Markup(STATUS_JS),
        csrf_token=get_csrf_token(),
        quality_choices=quality_choices(),
        active=active,
        history=history,
        error=error,
        submitted_urls=submitted_urls,
        submitted_quality=submitted_quality,
    )


@app.route("/")
@requires_auth
def index():
    return _render_index()


@app.route("/jobs", methods=["POST"])
@requires_auth
def add_jobs():
    if not has_valid_csrf(request.form.get("csrf_token")):
        return Response("Invalid CSRF token", status=403)

    raw_urls = request.form.get("urls", "")
    quality = request.form.get("quality", "best")
    urls = [line.strip() for line in raw_urls.splitlines() if line.strip()]

    try:
        resolve_quality(quality)
    except ValueError:
        quality = "best"

    if not urls:
        return _render_index(
            error="Paste at least one URL.",
            submitted_urls=raw_urls,
            submitted_quality=quality,
        ), 400

    with db.connect() as conn:
        for url in urls:
            job_id = db.create_job(conn, url, quality)
            worker.enqueue(job_id)

    return redirect("/")


@app.route("/status")
@requires_auth
def status():
    with db.connect() as conn:
        active_jobs = db.list_active_jobs(conn)
    return jsonify([_serialize_active_job(j) for j in active_jobs])


if __name__ == "__main__":
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    bind_host = os.getenv("BIND_HOST", "127.0.0.1")
    bind_port = int(os.getenv("PORT", "5002"))
    loopback = bind_host in ("127.0.0.1", "localhost", "::1")
    allow_insecure = os.getenv("ALLOW_INSECURE", "false").lower() == "true"

    if not loopback and not auth_enabled() and not allow_insecure:
        print(
            f"ERROR: refusing to bind to {bind_host} without auth.\n"
            "  Set WEB_USERNAME and WEB_PASSWORD in .env, bind to 127.0.0.1,\n"
            "  or set ALLOW_INSECURE=true if a reverse proxy handles auth."
        )
        sys.exit(1)

    worker.start_worker(DOWNLOAD_DIR)

    print("VideoDL")
    print(f"   -> Web UI: http://{bind_host}:{bind_port}")
    if not auth_enabled():
        print("WARNING: WEB_USERNAME/WEB_PASSWORD not set; web auth is disabled (loopback only).")
    app.run(host=bind_host, port=bind_port, debug=False)
