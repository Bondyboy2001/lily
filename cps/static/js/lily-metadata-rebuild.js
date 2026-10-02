/*
 * Import & Metadata → Rebuild metadata (templates/cwa_settings.html): confirm, start the
 * rebuild, then show its progress in the row's help line until it finishes, with Stop
 * beside it meanwhile. Opening the page while a rebuild runs picks it up again.
 */
(function () {
  "use strict";

  var btn = document.getElementById("rebuild_metadata");
  if (!btn) { return; }
  var stopBtn = document.getElementById("rebuild_metadata_stop");
  var help = btn.closest(".lp-row").querySelector(".lp-help");
  var glyph = btn.querySelector(".glyphicon");
  var timer = null;
  help.setAttribute("role", "status");

  function csrfHeaders() {
    var token = document.querySelector('input[name="csrf_token"]');
    return { "X-CSRFToken": token ? token.value : "", Accept: "application/json" };
  }

  function post(url) {
    return fetch(url, { method: "POST", credentials: "same-origin", headers: csrfHeaders() })
      .then(function (r) { if (!r.ok) { throw new Error(r.status); } return r.json(); });
  }

  function busy(on) {
    btn.disabled = on;
    glyph.classList.toggle("glyphicon-spin", on);
    stopBtn.hidden = !on;
    stopBtn.disabled = false;
  }

  function follow() {
    if (!timer) { timer = setInterval(poll, 2000); }
  }

  function done() {
    clearInterval(timer);
    timer = null;
    busy(false);
  }

  // The task's own line, without the "Rebuild metadata: " name in front
  function messageOf(task) {
    return task.taskMessage.replace(/^[^:]*:\s*/, "");
  }

  // stat: 0 waiting, 1 failed, 2 started, 3 finished, 4 stopped, 5 cancelled (services/worker.py)
  function show(task) {
    if (task.stat === 0 || task.stat === 2) {
      busy(true);
      help.textContent = task.stat === 0 ? "Waiting to start…" : messageOf(task);
      follow();
      return;
    }
    done();
    if (task.stat === 1) {
      help.textContent = "The rebuild failed: " + (task.error || "see the logs");
    } else {
      help.textContent = messageOf(task);
    }
  }

  function poll() {
    fetch(btn.dataset.statusUrl, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (tasks) {
        var mine = (tasks || []).filter(function (t) { return t.kind === "TaskRebuildMetadata"; });
        if (mine.length) { show(mine[mine.length - 1]); }
      })
      .catch(function () { /* the next tick tries again */ });
  }

  function start() {
    busy(true);
    help.textContent = "Waiting to start…";
    post(btn.dataset.url)
      .then(follow)
      .catch(function () {
        busy(false);
        help.textContent = "Couldn’t start the rebuild. Try again.";
      });
  }

  btn.addEventListener("click", function () { $("#rebuildMetadataModal").modal("show"); });
  document.getElementById("rebuild_metadata_confirm").addEventListener("click", function () {
    $("#rebuildMetadataModal").modal("hide");
    start();
  });
  stopBtn.addEventListener("click", function () {
    stopBtn.disabled = true;
    help.textContent = "Stopping after this book…";
    post(stopBtn.dataset.url).then(poll).catch(function () { stopBtn.disabled = false; });
  });

  poll();
})();
