import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app


def use_utc():
    os.environ["TZ"] = "UTC"
    time.tzset()


def test_finish_time_reads_as_a_date_and_time():
    use_utc()
    assert app.describe_finished_at("2026-11-05T19:52:12.419746+00:00") == "Thu 05 Nov 2026, 19:52"


def test_finish_time_shows_in_the_server_time_zone():
    os.environ["TZ"] = "Europe/London"
    time.tzset()
    assert app.describe_finished_at("2026-07-01T09:30:00+00:00") == "Wed 01 Jul 2026, 10:30"


def test_missing_finish_time_is_blank():
    assert app.describe_finished_at(None) == ""


def test_unreadable_finish_time_is_shown_as_stored():
    assert app.describe_finished_at("not a date") == "not a date"


def test_history_rows_carry_the_readable_finish_time():
    use_utc()
    row = app._serialize_history_job({"quality": "720p", "resolved_quality": None, "status": "success",
                                      "finished_at": "2026-11-05T19:52:12+00:00"})
    assert row["finished_display"] == "Thu 05 Nov 2026, 19:52"


def test_status_badge_stays_on_one_line():
    rule = next(line for line in app.CSS.splitlines() if line.startswith(".status-pill"))
    assert "white-space: nowrap" in rule
