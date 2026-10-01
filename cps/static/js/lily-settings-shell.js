/*
 * Settings frame (templates/settings_layout.html): save bars that wait for an
 * edit, and on a phone, the open page's chip scrolled into view.
 */
(function () {
  "use strict";

  /* Armed here, not in the stylesheet alone, so a page without script keeps its Save button. */
  document.querySelectorAll(".lp-actions.is-save").forEach(function (bar) {
    var form = bar.closest("form");
    if (!form) { return; }
    bar.classList.add("is-armed");
    function dirty() { bar.classList.add("is-dirty"); }
    form.addEventListener("input", dirty);
    form.addEventListener("change", dirty);
  });

  /* On a phone the rail is a row of chips; bring the open page's chip into view. */
  var rail = document.querySelector(".lp-rail-list");
  if (!rail) { return; }
  function revealActive() {
    var li = rail.querySelector("li.active");
    if (li && rail.scrollWidth > rail.clientWidth) { rail.scrollLeft = Math.max(0, li.offsetLeft - 16); }
  }
  if (document.readyState === "complete") { setTimeout(revealActive, 0); }
  else { window.addEventListener("load", revealActive); }
})();
