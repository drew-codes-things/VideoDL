import importlib
import importlib.metadata
import os
import subprocess
import sys

PACKAGE_SPEC = "yt-dlp[default]"
DISTRIBUTION = "yt-dlp"
UPDATE_TIMEOUT_SECONDS = 180
ALREADY_UPDATED_FLAG = "VIDEODL_YTDLP_UPDATED"
MAX_ERROR_LENGTH = 300


def installed_version():
    importlib.invalidate_caches()
    try:
        return importlib.metadata.version(DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError:
        return None


def update_ytdlp(run=subprocess.run):
    before = installed_version()
    command = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--upgrade", PACKAGE_SPEC]
    try:
        result = run(command, capture_output=True, text=True, timeout=UPDATE_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"WARNING: could not update yt-dlp ({error}), keeping {before}")
        return before, before
    if result.returncode != 0:
        print(f"WARNING: could not update yt-dlp ({(result.stderr or '').strip()[-MAX_ERROR_LENGTH:]}), keeping {before}")
        return before, before
    return before, installed_version()


def auto_update_enabled():
    return (os.getenv("YTDLP_AUTO_UPDATE") or "true").strip().lower() == "true"


def update_and_restart_if_needed(enabled, environ=os.environ, execv=os.execv, update=update_ytdlp):
    if not enabled or environ.get(ALREADY_UPDATED_FLAG):
        return False
    before, after = update()
    if before == after:
        print(f"yt-dlp {after} is the newest version")
        return False
    print(f"yt-dlp updated from {before} to {after}, restarting to load it", flush=True)
    environ[ALREADY_UPDATED_FLAG] = "1"
    execv(sys.executable, [sys.executable, *sys.argv])
    return True
