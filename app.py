import os
import secrets
import sys
from datetime import datetime
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, redirect, render_template_string, request, session
from markupsafe import Markup

import db
import links
import worker
import ytdlp_update
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
FINISHED_AT_FORMAT = "%a %d %b %Y, %H:%M"
MAX_URLS_PER_SUBMIT = 50
MAX_FORM_BYTES = 512 * 1024
app.config["MAX_CONTENT_LENGTH"] = MAX_FORM_BYTES


def auth_enabled():
    return bool(WEB_USERNAME and WEB_PASSWORD)


def matches_secret(submitted, expected):
    return secrets.compare_digest(submitted.encode("utf-8"), expected.encode("utf-8"))


def requires_auth(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not auth_enabled():
            return view_func(*args, **kwargs)
        auth = request.authorization
        if auth and matches_secret(auth.username or "", WEB_USERNAME) and matches_secret(auth.password or "", WEB_PASSWORD):
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
        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
        "font-src 'self'; script-src 'self' 'unsafe-inline'",
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
    return matches_secret(submitted_token, expected)


CSS = """
@font-face { font-family: "IBM Plex Sans"; font-style: normal; font-weight: 400; font-display: swap; src: url(/static/fonts/ibm-plex-sans-latin-400-normal.woff2) format("woff2"); }
@font-face { font-family: "IBM Plex Sans"; font-style: normal; font-weight: 500; font-display: swap; src: url(/static/fonts/ibm-plex-sans-latin-500-normal.woff2) format("woff2"); }
@font-face { font-family: "IBM Plex Sans"; font-style: normal; font-weight: 600; font-display: swap; src: url(/static/fonts/ibm-plex-sans-latin-600-normal.woff2) format("woff2"); }
@font-face { font-family: "IBM Plex Mono"; font-style: normal; font-weight: 400; font-display: swap; src: url(/static/fonts/ibm-plex-mono-latin-400-normal.woff2) format("woff2"); }
:root {
    color-scheme: dark;
    --bg: #19191c;
    --surface: #222226;
    --text: #eeeef0;
    --muted: #b4b4bd;
    --faint: #9a9aa5;
    --accent: #e5383b;
    --accent-fill: #d52c30;
    --accent-fill-hover: #e5383b;
    --accent-text: #ff7a7c;
    --on-accent: #ffffff;
    --ok: #5fd990;
    --line: color-mix(in srgb, var(--text) 10%, transparent);
    --line-strong: color-mix(in srgb, var(--text) 18%, transparent);
    --line-control: color-mix(in srgb, var(--text) 46%, transparent);
    --accent-tint: color-mix(in srgb, var(--accent) 14%, transparent);
    --ok-tint: color-mix(in srgb, var(--ok) 12%, transparent);
    --r-lg: 16px;
    --r-pill: 999px;
    --control-h: 36px;
    --touch: 44px;
    --sans: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
    --mono: "IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;
    --fs-xs: .75rem;
    --fs-sm: .8125rem;
    --fs-md: .875rem;
    --fs-lg: .9375rem;
    --dur: .16s;
    --dur-rise: .24s;
    --ease: cubic-bezier(.2, .7, .3, 1);
    --focus: color-mix(in srgb, var(--accent-text) 72%, transparent);
    --ring: 0 0 0 3px var(--accent-tint);
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; text-size-adjust: 100%; background: var(--bg); }
body {
    font-family: var(--sans);
    font-size: var(--fs-md);
    background: var(--bg);
    color: var(--text);
    margin: 0;
    padding: 0;
    line-height: 1.55;
    min-height: 100vh;
    -webkit-font-smoothing: antialiased;
    -webkit-tap-highlight-color: transparent;
}
::selection { background: color-mix(in srgb, var(--accent) 35%, transparent); }
* { scrollbar-width: thin; scrollbar-color: var(--line-strong) transparent; }
.container { max-width: 900px; margin: 0 auto; padding: 0 24px calc(64px + env(safe-area-inset-bottom)); }
h1, h2 { color: var(--text); font-weight: 600; letter-spacing: -.012em; }
h1 { font-size: 1.0625rem; margin: 0; }
.wordmark-accent { color: var(--accent-text); }
h2 { font-size: var(--fs-lg); margin: 0 0 16px; }
.section {
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--r-lg);
    padding: 24px;
    margin-bottom: 16px;
    animation: rise-in var(--dur-rise) var(--ease);
}
.topbar {
    display: flex; align-items: center; gap: 16px;
    padding: calc(14px + env(safe-area-inset-top)) 24px 14px;
    margin: 0 0 32px;
    background: var(--bg);
    border-bottom: 1px solid var(--line);
}
textarea, select {
    display: block;
    width: 100%;
    min-width: 0;
    margin-bottom: 12px;
    font-family: var(--sans);
    font-size: var(--fs-sm);
    color: var(--text);
    background-color: var(--bg);
    border: 1px solid var(--line-strong);
    appearance: none;
    -webkit-appearance: none;
    transition: border-color var(--dur) var(--ease), box-shadow var(--dur) var(--ease);
}
textarea { padding: 12px 16px; border-radius: var(--r-lg); line-height: 1.5; resize: vertical; }
select {
    height: var(--control-h);
    padding: 0 36px 0 16px;
    border-radius: var(--r-pill);
    background-image: linear-gradient(45deg, transparent 50%, var(--muted) 50%), linear-gradient(135deg, var(--muted) 50%, transparent 50%);
    background-repeat: no-repeat;
    background-position: calc(100% - 20px) 52%, calc(100% - 15px) 52%;
    background-size: 5px 5px;
    cursor: pointer;
}
select option { background: var(--surface); color: var(--text); }
textarea::placeholder { color: var(--faint); opacity: 1; }
textarea:hover, select:hover { border-color: var(--line-control); }
textarea:focus, select:focus { outline: none; border-color: var(--focus); box-shadow: var(--ring); }
.home-button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    height: var(--control-h);
    padding: 0 16px;
    font-family: var(--sans);
    font-size: var(--fs-sm);
    font-weight: 600;
    background: var(--accent-fill);
    border: 1px solid transparent;
    color: var(--on-accent);
    border-radius: var(--r-pill);
    white-space: nowrap;
    cursor: pointer;
    transition: background-color var(--dur) var(--ease), transform var(--dur) var(--ease);
}
.home-button:hover { background: var(--accent-fill-hover); }
.home-button:active { transform: scale(.97); }
.cancel-form { margin: 8px 0 0; }
.secondary-button {
    height: 28px;
    padding: 0 12px;
    font-family: var(--sans);
    font-size: var(--fs-xs);
    font-weight: 500;
    color: var(--text);
    background: transparent;
    border: 1px solid var(--line-strong);
    border-radius: var(--r-pill);
    cursor: pointer;
    transition: border-color var(--dur) var(--ease);
}
.secondary-button:hover { border-color: var(--line-control); }
.warning-banner {
    background: var(--accent-tint);
    color: var(--accent-text);
    border-radius: var(--r-lg);
    padding: 12px 16px;
    margin: 0 0 16px 0;
    font-size: var(--fs-md);
}
.queue-item { padding: 12px 0; border-bottom: 1px solid var(--line); }
.queue-item:first-child { padding-top: 0; }
.queue-item:last-child { border-bottom: none; padding-bottom: 0; }
.queue-item-title { font-weight: 500; margin-bottom: 8px; overflow-wrap: anywhere; }
.queue-progress-track { width: 100%; height: 4px; background: var(--line-strong); border-radius: var(--r-pill); overflow: hidden; margin-bottom: 6px; }
.queue-progress-fill { height: 100%; background: var(--accent-fill); width: 0%; transition: width .3s var(--ease); }
.queue-item-meta { font-size: var(--fs-xs); color: var(--muted); }
table { width: 100%; border-collapse: collapse; font-size: var(--fs-sm); }
thead tr { border-bottom: 1px solid var(--line-strong); }
th { text-align: left; padding: 0 8px 8px; color: var(--faint); font-size: var(--fs-xs); font-weight: 500; }
th:first-child, td:first-child { padding-left: 0; }
th:last-child, td:last-child { padding-right: 0; }
tbody tr { border-bottom: 1px solid var(--line); }
tbody tr:last-child { border-bottom: none; }
td { padding: 12px 8px; vertical-align: top; word-break: break-word; }
td[data-label="File / Error"] { color: var(--muted); font-family: var(--mono); font-size: var(--fs-xs); }
.status-pill { display: inline-block; border-radius: var(--r-pill); padding: 2px 10px; font-size: var(--fs-xs); font-weight: 500; white-space: nowrap; }
.status-success { background: var(--ok-tint); color: var(--ok); }
.status-error { background: var(--accent-tint); color: var(--accent-text); }
.status-cancelled { background: var(--line); color: var(--muted); }
.empty-state { color: var(--muted); padding: 16px; border: 1px dashed var(--line-strong); border-radius: var(--r-lg); text-align: center; }
@keyframes rise-in { from { opacity: 0; transform: translateY(8px); } }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: .001ms !important; animation-iteration-count: 1 !important; transition-duration: .001ms !important; }
}
:focus { outline: none; }
:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
@media (max-width: 640px) {
    .topbar { padding-left: 16px; padding-right: 16px; margin-bottom: 24px; }
    .container { padding-left: 16px; padding-right: 16px; }
    .section { padding: 20px 16px; }
    thead { display: none; }
    tbody tr { display: block; padding: 12px 0; }
    tbody tr:first-child { padding-top: 0; }
    td { display: flex; align-items: baseline; gap: 12px; padding: 2px 0; }
    td::before { content: attr(data-label); flex: 0 0 64px; color: var(--faint); font-family: var(--sans); font-size: var(--fs-xs); }
    .home-button { width: 100%; }
}
@media (max-width: 820px), (pointer: coarse) {
    textarea, select { font-size: 16px; }
    select, .home-button { height: var(--touch); }
}
@media (max-width: 820px), (pointer: coarse) {
    textarea, select { font-size: 16px; min-height: 44px; }
    .home-button { min-height: 44px; }
}
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
<style>{{ css }}</style>
</head>
<body>
<header class="topbar"><h1>Video<span class="wordmark-accent">DL</span></h1></header>
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
        <form method="post" action="/jobs/{{ job.id }}/cancel" class="cancel-form">
          <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
          <button type="submit" class="secondary-button">Cancel</button>
        </form>
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
          <td data-label="Title">{{ job.title or job.url }}</td>
          <td data-label="Quality">{{ job.quality_display }}</td>
          <td data-label="Status"><span class="status-pill status-{{ job.status }}">{{ job.status }}</span></td>
          <td data-label="Finished">{{ job.finished_display }}</td>
          <td data-label="File / Error">{% if job.status == 'success' %}{{ job.filepath }}{% else %}{{ job.error or '' }}{% endif %}</td>
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


def describe_finished_at(finished_at):
    if not finished_at:
        return ""
    try:
        finished = datetime.fromisoformat(finished_at)
    except ValueError:
        return finished_at
    return finished.astimezone().strftime(FINISHED_AT_FORMAT)


def _serialize_history_job(job):
    return {**job, "quality_display": _quality_display(job),
            "finished_display": describe_finished_at(job.get("finished_at"))}


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


@app.errorhandler(413)
def form_too_large(error):
    return Response(f"The form was larger than {MAX_FORM_BYTES // 1024} KB. Paste fewer links at a time.",
                    status=413, mimetype="text/plain")


def first_link_problem(urls):
    if len(urls) > MAX_URLS_PER_SUBMIT:
        return f"Paste at most {MAX_URLS_PER_SUBMIT} links at a time ({len(urls)} were pasted)."
    for line_number, url in enumerate(urls, start=1):
        problem = links.link_refusal(url)
        if problem:
            return f"Line {line_number} {problem}. Nothing was added."
    return None


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

    link_problem = first_link_problem(urls)
    if link_problem:
        return _render_index(
            error=link_problem,
            submitted_urls=raw_urls,
            submitted_quality=quality,
        ), 400

    with db.connect() as conn:
        for url in urls:
            job_id = db.create_job(conn, url, quality)
            worker.enqueue(job_id)

    return redirect("/")


@app.route("/jobs/<int:job_id>/cancel", methods=["POST"])
@requires_auth
def cancel_job(job_id):
    if not has_valid_csrf(request.form.get("csrf_token")):
        return Response("Invalid CSRF token", status=403)
    worker.cancel_job(job_id)
    return redirect("/")


@app.route("/status")
@requires_auth
def status():
    with db.connect() as conn:
        active_jobs = db.list_active_jobs(conn)
    return jsonify([_serialize_active_job(j) for j in active_jobs])


if __name__ == "__main__":
    ytdlp_update.update_and_restart_if_needed(ytdlp_update.auto_update_enabled())
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
