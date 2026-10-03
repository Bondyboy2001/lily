/*
 * Settings → Metadata → Rebuild metadata and Redo PDF covers
 * (templates/cwa_settings.html): confirm, queue the task, then show its progress in a help
 * line under its own row until it finishes, with Stop beside it meanwhile. A stopped run is
 * followed until the books under way are done; only one runs at a time, and whichever runs
 * disables both buttons. Opening the page while one runs picks it up again (the status
 * endpoint's `kind` says which). After a rebuild that was stopped or cut short, the dialog
 * offers to continue from there. Retry failed starts a run of only the books whose last
 * lookup failed, and the dialog's Full rebuild one that forgets earlier lookups and looks
 * every book up again. Redo PDF covers needs no confirm: it only re-renders page 1 of each
 * PDF as its cover.
 */
(function () {
  "use strict";

  var btn = document.getElementById("rebuild_metadata");
  if (!btn) { return; }
  var stopBtn = document.getElementById("rebuild_metadata_stop");
  var confirmBtn = document.getElementById("rebuild_metadata_confirm");
  var resumeText = document.getElementById("rebuildMetadataResume");
  var retryBtn = document.getElementById("retry_failed");
  var fullBtn = document.getElementById("rebuild_metadata_full");
  var redoBtn = document.getElementById("redo_pdf_covers");
  var redoStopBtn = document.getElementById("redo_pdf_covers_stop");
  // How far an unfinished rebuild got, in words; empty when there is none to continue
  var resume = "";
  var glyphs = { metadata: btn.querySelector(".glyphicon"),
                 covers: redoBtn ? redoBtn.querySelector(".glyphicon") : null };
  var stops = { metadata: stopBtn, covers: redoStopBtn };
  var timer = null;
  // Each row shows its own progress: the rebuild row has no help text of its own, so the
  // line appears under its label; the covers row's goes after its static help
  var lines = {};
  lines.metadata = statusLine(btn);
  lines.covers = redoBtn ? statusLine(redoBtn) : null;

  function statusLine(button) {
    var help = document.createElement("p");
    help.className = "lp-help";
    help.hidden = true;
    help.setAttribute("role", "status");
    button.closest(".lp-row").querySelector(".lp-text").appendChild(help);
    return help;
  }

  function say(kind, text) {
    for (var k in lines) {
      if (lines[k]) {
        lines[k].textContent = k === kind ? text : "";
        lines[k].hidden = k !== kind || !text;
      }
    }
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

  // While anything runs, every start button is off and only the running kind's Stop shows
  function busy(active, kind) {
    btn.disabled = active;
    if (retryBtn) { retryBtn.disabled = active; }
    if (redoBtn) { redoBtn.disabled = active; }
    for (var k in glyphs) {
      if (glyphs[k]) { glyphs[k].classList.toggle("glyphicon-spin", active && k === kind); }
      if (stops[k]) {
        stops[k].hidden = !(active && k === kind);
        stops[k].disabled = false;
      }
    }
  }

  function follow() {
    if (!timer) { timer = setInterval(poll, 2000); }
  }

  // state: idle, running, stopping (finishing the books under way), stopped, done or failed
  // (cwa_functions/settings.py); kind: metadata or covers, which row the line belongs to
  function show(status) {
    var kind = status.kind || "metadata";
    var active = status.state === "running" || status.state === "stopping";
    busy(active, kind);
    if (active && stops[kind]) { stops[kind].disabled = status.state === "stopping"; }
    say(kind, status.message);
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

  // data: what to send (resume, or failed for Retry failed); kind: which button's url and line
  function start(data, kind) {
    kind = kind || "metadata";
    $("#rebuildMetadataModal").modal("hide");
    busy(true, kind);
    say(kind, "Waiting to start…");
    post(kind === "covers" ? redoBtn.dataset.url : btn.dataset.url, data)
      .then(function () {
        follow();
        poll();
      })
      .catch(function () {
        busy(false);
        say(kind, "Couldn’t start it. Try again.");
      });
  }

  btn.addEventListener("click", function () {
    resumeText.textContent = resume;
    resumeText.hidden = !resume;
    // The body holds only that line: no empty gap above the buttons without it
    resumeText.parentNode.hidden = !resume;
    confirmBtn.textContent = resume ? confirmBtn.dataset.continueLabel : confirmBtn.dataset.label;
    $("#rebuildMetadataModal").modal("show");
  });
  confirmBtn.addEventListener("click", function () { start(resume ? { resume: "1" } : null); });
  fullBtn.addEventListener("click", function () { start({ full: "1" }); });
  if (retryBtn) {
    retryBtn.addEventListener("click", function () { start({ failed: "1" }); });
  }
  if (redoBtn) {
    redoBtn.addEventListener("click", function () { start(null, "covers"); });
  }
  for (var k in stops) {
    (function (stop) {
      if (!stop) { return; }
      stop.addEventListener("click", function () {
        stop.disabled = true;
        post(stop.dataset.url).then(poll).catch(function () { stop.disabled = false; });
      });
    })(stops[k]);
  }

  poll();
})();
