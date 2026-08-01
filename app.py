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
app.secret_key = secrets.token_hex(32)
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
        "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'",
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
    --void: #07090c;
    --panel: #0e1116;
    --panel-raised: #12161c;
    --line: #1e252b;
    --line-bright: #313a42;
    --signal: #ff4d2e;
    --phosphor: #35d67c;
    --text: #d7dbde;
    --text-dim: #7d8891;
    --text-faint: #4b545c;
    --mono: 'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
    --sans: 'IBM Plex Sans', -apple-system, BlinkMacSystemFont, sans-serif;
}
* { box-sizing: border-box; }
body {
    font-family: var(--sans);
    background-color: var(--void);
    background-image: radial-gradient(ellipse at 50% 0%, rgba(255, 77, 46, .05), transparent 60%);
    background-attachment: fixed;
    color: var(--text);
    margin: 0;
    padding: 0;
    line-height: 1.6;
    min-height: 100vh;
}
.container { max-width: 900px; margin: 0 auto; padding: 40px 20px; position: relative; z-index: 1; }
h1, h2 { font-family: var(--mono); color: var(--text); text-transform: uppercase; letter-spacing: .04em; }
h1 { font-size: clamp(1.4rem, 4vw, 2rem); font-weight: 600; margin: 0; }
h2 { font-size: 1.1rem; font-weight: 600; margin-top: 0; margin-bottom: 20px; padding-bottom: 10px; border-bottom: 1px solid var(--line); color: var(--signal); }
h2::before { content: "// "; color: var(--text-faint); }
.section {
    position: relative;
    background-color: var(--panel);
    border: 1px solid var(--line);
    border-radius: 2px;
    padding: 24px 26px;
    margin-bottom: 30px;
}
.section::before {
    content: "";
    position: absolute;
    inset: 0;
    pointer-events: none;
    background:
        linear-gradient(var(--signal), var(--signal)) top left / 12px 2px no-repeat,
        linear-gradient(var(--signal), var(--signal)) top left / 2px 12px no-repeat,
        linear-gradient(var(--signal), var(--signal)) top right / 12px 2px no-repeat,
        linear-gradient(var(--signal), var(--signal)) top right / 2px 12px no-repeat,
        linear-gradient(var(--signal), var(--signal)) bottom left / 12px 2px no-repeat,
        linear-gradient(var(--signal), var(--signal)) bottom left / 2px 12px no-repeat,
        linear-gradient(var(--signal), var(--signal)) bottom right / 12px 2px no-repeat,
        linear-gradient(var(--signal), var(--signal)) bottom right / 2px 12px no-repeat;
    opacity: .3;
    transition: opacity .25s ease;
}
.section:hover::before, .section:focus-within::before { opacity: 1; }
.topbar {
    position: relative; z-index: 10;
    display: flex; align-items: center; gap: 1rem;
    padding: 1rem 1.5rem; margin: 0 0 30px;
    background: var(--panel);
    border-bottom: 1px solid var(--line);
}
.topbar h1::after { content: "_"; color: var(--signal); animation: blink 1.1s steps(1) infinite; margin-left: 2px; }
textarea, select {
    font-family: var(--mono);
    background: var(--panel-raised);
    border: 1px solid var(--line);
    color: var(--text);
    padding: 10px 12px;
    border-radius: 2px;
    width: 100%;
    box-sizing: border-box;
    margin-bottom: 12px;
    font-size: .9rem;
}
textarea:focus, select:focus { outline: none; border-color: var(--signal); }
.home-button {
    display: inline-flex;
    align-items: center;
    gap: .4em;
    font-family: var(--mono);
    background: transparent;
    border: 1px solid var(--signal);
    color: var(--signal);
    font-size: .9rem;
    font-weight: 500;
    letter-spacing: .03em;
    padding: 10px 18px;
    border-radius: 2px;
    text-transform: uppercase;
    transition: background-color .15s ease, color .15s ease;
    cursor: pointer;
}
.home-button::before { content: "["; }
.home-button::after { content: "]"; }
.home-button:hover { background-color: var(--signal); color: var(--void); }
.warning-banner {
    background: #2a1210;
    border: 1px solid var(--signal);
    color: #ffb3a1;
    border-radius: 2px;
    padding: 10px 14px;
    margin: 0 0 20px 0;
    font-size: 0.9rem;
    font-family: var(--mono);
}
.queue-item { padding: 12px 0; border-bottom: 1px solid var(--line); }
.queue-item:last-child { border-bottom: none; }
.queue-item-title { font-family: var(--mono); font-size: .9rem; margin-bottom: 8px; word-break: break-all; }
.queue-progress-track { width: 100%; height: 8px; background: var(--panel-raised); border-radius: 4px; overflow: hidden; margin-bottom: 6px; }
.queue-progress-fill { height: 100%; background: var(--signal); width: 0%; transition: width .3s; }
.queue-item-meta { font-family: var(--mono); font-size: .78rem; color: var(--text-dim); }
table { width: 100%; border-collapse: collapse; font-size: .88rem; }
thead tr { border-bottom: 1px solid var(--line-bright); }
th { text-align: left; padding: 8px 6px; color: var(--text-dim); font-family: var(--mono); font-size: .78rem; text-transform: uppercase; }
tbody tr { border-bottom: 1px solid var(--line); }
td { padding: 10px 6px; vertical-align: top; word-break: break-word; }
.status-pill { border-radius: 20px; padding: 2px 9px; font-size: .72rem; font-family: var(--mono); text-transform: uppercase; }
.status-success { background: #123a24; color: var(--phosphor); }
.status-error { background: #3a1512; color: #ff8a70; }
.empty-state { color: var(--text-faint); padding: 20px 0; text-align: center; font-family: var(--mono); font-size: .85rem; }
.bg-grid { position: fixed; inset: 0; overflow: hidden; pointer-events: none; z-index: 0; }
.bg-grid::before {
    content: "";
    position: absolute;
    left: 0; right: 0;
    height: 140px;
    background: linear-gradient(to bottom, transparent, rgba(255, 77, 46, .06), transparent);
    animation: scan 9s linear infinite;
}
@keyframes scan { 0% { top: -140px; } 100% { top: 100%; } }
@keyframes blink { 0%, 49% { opacity: 1; } 50%, 100% { opacity: 0; } }
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
<div class="bg-grid" aria-hidden="true"></div>
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
