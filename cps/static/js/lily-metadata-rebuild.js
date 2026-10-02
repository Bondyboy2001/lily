/*
 * Import & Metadata → Rebuild metadata (templates/cwa_settings.html): confirm, queue the
 * task, then show its progress in the row's help line until it finishes. Opening the page
 * while a rebuild runs picks it up again.
 */
(function () {
  "use strict";

  var btn = document.getElementById("rebuild_metadata");
  if (!btn) { return; }
  var help = btn.closest(".lp-row").querySelector(".lp-help");
  var glyph = btn.querySelector(".glyphicon");
  var idleText = help.textContent;
  var timer = null;
  help.setAttribute("role", "status");

  function busy(on) {
    btn.disabled = on;
    glyph.classList.toggle("glyphicon-spin", on);
  }

  function follow() {
    if (!timer) { timer = setInterval(poll, 2000); }
  }

  function stop() {
    clearInterval(timer);
    timer = null;
    busy(false);
  }

  // stat: 0 waiting, 1 failed, 2 started, 3 finished, 4 ended, 5 cancelled (services/worker.py)
  function show(task) {
    if (task.stat === 0 || task.stat === 2) {
      busy(true);
      help.textContent = task.stat === 0 ? "Waiting to start…" : task.taskMessage.replace(/^[^:]*:\s*/, "");
      follow();
    } else {
      stop();
      if (task.stat === 3) {
        help.textContent = task.taskMessage.replace(/^[^:]*:\s*/, "");
      } else if (task.stat === 1) {
        help.textContent = "The rebuild stopped: " + (task.error || "unknown error");
      } else {
        help.textContent = idleText;
      }
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
    var token = document.querySelector('input[name="csrf_token"]');
    fetch(btn.dataset.url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRFToken": token ? token.value : "", Accept: "application/json" }
    })
      .then(function (r) { if (!r.ok) { throw new Error(r.status); } return r.json(); })
      .then(function () {
        help.textContent = "Waiting to start…";
        follow();
      })
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

  poll();
})();
