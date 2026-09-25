"""`stack errors` reports a line only when its log format marks it as an error.

The lines below are written in the formats the stack's services actually
emit. The ordinary ones are copied from a running instance; an error line is
the same format with its level changed, because the counterpart of every
INFO line is what each service prints when something fails.
"""

from stack.logscan import is_error, scan


# ── What counts as an error ────────────────────────────────────────────────

ERROR_LINES = {
    "synapse": "2026-09-24 22:26:57,521 - synapse.http.server - 124 - ERROR - GET-42 - Failed handle request",
    "loguru bot": "2026-09-25 13:01:35 | ERROR   | [archivist-bot] Paperless upload failed",
    "loguru curator": "2026-09-25 08:48:40.162 | ERROR    | __main__:_run_command:789 - [curator] wiki build failed",
    "paperless": "[2026-09-25 15:30:00,167] [ERROR] [paperless.consumer] Error while consuming document",
    "postgres": "2026-09-25 13:27:57.951 UTC [28] ERROR:  relation \"x\" does not exist",
    "postgres fatal": "2026-09-25 13:27:57.951 UTC [28] FATAL:  password authentication failed for user \"synapse\"",
    "forgejo": "2026/09/25 15:31:07 ...eb/routing/logger.go:102:func1() [E] router: failed to serve",
    "tika": "ERROR [main] 13:00:36,716 org.apache.tika.server.core.TikaServerProcess crashed",
    "nginx": "2026/09/25 15:31:07 [error] 29#29: *1 open() \"/usr/share/nginx/html/x\" failed",
    "watchtower": "time=\"2026-09-25T15:31:07Z\" level=error msg=\"Could not do a head request\"",
    "caddy": "{\"level\":\"error\",\"ts\":1727270000.1,\"logger\":\"http.log.error\",\"msg\":\"dial tcp: connection refused\"}",
    "python traceback": "Traceback (most recent call last):",
}

ORDINARY_LINES = {
    "synapse warning": "2026-09-24 22:26:57,522 - synapse.config.logger - 378 - WARNING - main - Server version 1.161.0",
    "loguru info": "2026-09-25 13:01:35 | INFO    | [stacker-bot] Running",
    "paperless info": "[2026-09-25 15:30:00,256] [INFO] [celery.app.trace] Task succeeded: 'No new documents were added'",
    "forgejo info": "2026/09/25 15:31:07 ...eb/routing/logger.go:102:func1() [I] router: completed GET /family/brain.git 200 OK",
    "postgres log": "2026-09-25 13:28:02.511 UTC [28] LOG:  checkpoint complete: wrote 45 buffers (0.3%)",
    "the word, lowercase": "2026-09-25 13:01:35 | INFO    | [archivist-bot] retried: 0 errors",
    "a path": "2026/09/25 15:31:07 ...logger.go:102:func1() [I] router: completed GET /api/error_reports 200 OK",
    "a traceback frame": '  File "/app/bot.py", line 12, in run',
}


def test_every_services_error_format_is_recognised():
    missed = [name for name, line in ERROR_LINES.items() if not is_error(line)]
    assert missed == []


def test_ordinary_lines_that_mention_errors_are_not_counted():
    flagged = [name for name, line in ORDINARY_LINES.items() if is_error(line)]
    assert flagged == []


# ── Summarising a container's log ──────────────────────────────────────────

def _stamped(at: str, line: str) -> str:
    return f"2026-09-25T{at}.000000000Z {line}"


def test_a_quiet_log_reports_nothing():
    log = "\n".join(_stamped("10:00:00", line) for line in ORDINARY_LINES.values())
    assert scan(log) == {"count": 0, "last_at": None, "lines": []}


def test_stdout_and_stderr_are_merged_back_into_time_order():
    # docker logs hands both streams back one after the other, so the
    # newest error can come first in the text.
    stdout = _stamped("12:00:00", ERROR_LINES["loguru bot"])
    stderr = _stamped("09:00:00", ERROR_LINES["python traceback"])
    result = scan(stdout + "\n" + stderr)
    assert result["count"] == 2
    assert result["last_at"] == "2026-09-25T12:00:00.000000000Z"
    assert [line["text"] for line in result["lines"]] == [
        ERROR_LINES["python traceback"], ERROR_LINES["loguru bot"]]


def test_only_the_newest_errors_are_kept_but_all_are_counted():
    log = "\n".join(_stamped(f"10:00:{s:02d}", f"| ERROR | failure {s}") for s in range(20))
    result = scan(log, keep=3)
    assert result["count"] == 20
    assert [line["text"] for line in result["lines"]] == [
        "| ERROR | failure 17", "| ERROR | failure 18", "| ERROR | failure 19"]
