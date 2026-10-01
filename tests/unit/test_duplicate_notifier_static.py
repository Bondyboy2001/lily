"""The duplicate notice stays a sidebar badge: no polling unless a scan is pending, and the
dialog only for groups that are new since this browser last acknowledged them."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
JS = (REPO_ROOT / "cps/static/js/duplicate-notifier.js").read_text(encoding="utf-8")
LAYOUT = (REPO_ROOT / "cps/templates/layout.html").read_text(encoding="utf-8")


def test_status_comes_from_the_page_and_polling_waits_for_a_pending_scan():
    init = JS[JS.index("function init()"):]
    assert "cwaDuplicateBootstrap" in init and "fetchDuplicateStatus()" not in init
    assert "visibilitychange" not in JS and "setInterval" not in JS
    handler = JS[JS.index("function handleStatusResponse"):JS.index("function hideNotificationModal")]
    assert handler.index("scanPending(data)") < handler.index("startStatusPolling()")
    assert "stopStatusPolling()" in handler


def test_remind_me_later_snoozes_for_a_week_in_local_storage():
    assert "sessionStorage" not in JS
    assert "7 * 24 * 60 * 60 * 1000" in JS
    for fn in ("function readNumber", "function writeNumber"):
        body = JS[JS.index(fn):JS.index("}", JS.index("catch", JS.index(fn)))]
        assert "try {" in body and "localStorage" in body, fn
    assert "remindBtn.addEventListener('click', snoozeModal)" in JS
    assert "Remind me in a week" in LAYOUT


def test_dialog_only_for_new_groups():
    show = JS[JS.index("function maybeShowModal"):JS.index("function showNotificationModal")]
    assert "seen === null" in show and "isSnoozed()" in show and "count === seen" in show
    assert "innerHTML" not in JS
