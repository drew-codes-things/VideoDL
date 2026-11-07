import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ytdlp_update


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)


def test_a_newer_yt_dlp_restarts_videodl_once():
    environ, execv = {}, Recorder()
    assert ytdlp_update.update_and_restart_if_needed(True, environ, execv, update=lambda: ("2026.7.4", "2026.10.26"))
    assert execv.calls[0][0] == sys.executable
    assert environ[ytdlp_update.ALREADY_UPDATED_FLAG] == "1"
    assert not ytdlp_update.update_and_restart_if_needed(True, environ, execv, update=lambda: ("a", "b"))
    assert len(execv.calls) == 1


def test_no_restart_when_current_or_turned_off():
    execv = Recorder()
    assert not ytdlp_update.update_and_restart_if_needed(True, {}, execv, update=lambda: ("2026.10.26", "2026.10.26"))
    assert not ytdlp_update.update_and_restart_if_needed(False, {}, execv, update=lambda: ("a", "b"))
    assert execv.calls == []


def test_pip_failure_keeps_the_installed_version(monkeypatch):
    monkeypatch.setattr(ytdlp_update, "installed_version", lambda: "2026.10.26")

    def failing_pip(command, **options):
        assert command[-1] == "yt-dlp[default]"
        return subprocess.CompletedProcess(command, 1, "", "Permission denied")

    assert ytdlp_update.update_ytdlp(failing_pip) == ("2026.10.26", "2026.10.26")


def test_auto_update_is_on_unless_turned_off(monkeypatch):
    monkeypatch.delenv("YTDLP_AUTO_UPDATE", raising=False)
    assert ytdlp_update.auto_update_enabled()
    monkeypatch.setenv("YTDLP_AUTO_UPDATE", "false")
    assert not ytdlp_update.auto_update_enabled()


def test_restart_message_is_flushed_before_the_process_is_replaced(monkeypatch):
    class BufferedOutput:
        def __init__(self):
            self.pending = ""
            self.flushed = ""

        def write(self, text):
            self.pending += text
            return len(text)

        def flush(self):
            self.flushed += self.pending
            self.pending = ""

    output = BufferedOutput()
    monkeypatch.setattr(sys, "stdout", output)
    seen_at_exec = []
    ytdlp_update.update_and_restart_if_needed(
        True,
        environ={},
        execv=lambda *_: seen_at_exec.append(output.flushed),
        update=lambda: ("2026.7.4", "2026.10.26"),
    )
    assert "restarting" in seen_at_exec[0]
