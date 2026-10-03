/*
 * Import & Metadata → Rebuild metadata (templates/cwa_settings.html): confirm, start the
 * rebuild, then show its progress in a help line under the label until it finishes, with Stop
 * beside it meanwhile. A stopped rebuild is followed until the books under way are done.
 * Opening the page while a rebuild runs picks it up again. After a rebuild that was stopped
 * or cut short, the dialog offers to continue from there or start again. Retry failed starts
 * a run of only the books whose last lookup failed, shown the same way.
 */
(function () {
  "use strict";

  var btn = document.getElementById("rebuild_metadata");
  if (!btn) { return; }
  var stopBtn = document.getElementById("rebuild_metadata_stop");
  var confirmBtn = document.getElementById("rebuild_metadata_confirm");
  var restartBtn = document.getElementById("rebuild_metadata_restart");
  var resumeText = document.getElementById("rebuildMetadataResume");
  var retryBtn = document.getElementById("retry_failed");
  // How far an unfinished rebuild got, in words; empty when there is none to continue
  var resume = "";
  var glyph = btn.querySelector(".glyphicon");
  var timer = null;
  // The row has no help text of its own: the line appears once there is progress to show
  var help = document.createElement("p");
  help.className = "lp-help";
  help.hidden = true;
  help.setAttribute("role", "status");
  btn.closest(".lp-row").querySelector(".lp-text").appendChild(help);

  function say(text) {
    help.textContent = text;
    help.hidden = !text;
  }

  function csrfHeaders() {
    var token = document.querySelector('input[name="csrf_token"]');
    return { "X-CSRFToken": token ? token.value : "", Accept: "application/json" };
  }

  function json(r) {
    if (!r.ok) { throw new Error(r.status); }
    return r.json();
  }

  function post(url, data) {
    return fetch(url, { method: "POST", credentials: "same-origin", headers: csrfHeaders(),
                        body: data ? new URLSearchParams(data) : undefined })
      .then(json);
  }

  function busy(on) {
    btn.disabled = on;
    if (retryBtn) { retryBtn.disabled = on; }
    glyph.classList.toggle("glyphicon-spin", on);
    stopBtn.hidden = !on;
    stopBtn.disabled = false;
  }

  function follow() {
    if (!timer) { timer = setInterval(poll, 2000); }
  }

  // state: idle, running, stopping (finishing the books under way), stopped, done or failed
  // (cwa_functions/settings.py), with the line to show
  function show(status) {
    var active = status.state === "running" || status.state === "stopping";
    busy(active);
    stopBtn.disabled = status.state === "stopping";
    say(status.message);
    resume = status.resume || "";
    if (active) {
      follow();
    } else {
      clearInterval(timer);
      timer = null;
    }
  }

  function poll() {
    fetch(btn.dataset.statusUrl, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(json)
      .then(show)
      .catch(function () { /* the next tick tries again */ });
  }

  // data: what to send (resume, or failed for Retry failed)
  function start(data) {
    $("#rebuildMetadataModal").modal("hide");
    busy(true);
    say("Waiting to start…");
    post(btn.dataset.url, data)
      .then(function () {
        follow();
        poll();
      })
      .catch(function () {
        busy(false);
        say("Couldn’t start the rebuild. Try again.");
      });
  }

  btn.addEventListener("click", function () {
    resumeText.textContent = resume;
    resumeText.hidden = !resume;
    restartBtn.hidden = !resume;
    confirmBtn.textContent = resume ? confirmBtn.dataset.continueLabel : confirmBtn.dataset.label;
    $("#rebuildMetadataModal").modal("show");
  });
  confirmBtn.addEventListener("click", function () { start(resume ? { resume: "1" } : null); });
  restartBtn.addEventListener("click", function () { start(null); });
  if (retryBtn) {
    retryBtn.addEventListener("click", function () { start({ failed: "1" }); });
  }
  stopBtn.addEventListener("click", function () {
    stopBtn.disabled = true;
    post(stopBtn.dataset.url).then(poll).catch(function () { stopBtn.disabled = false; });
  });

  poll();
})();
