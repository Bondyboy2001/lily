/* This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
 *    Copyright (C) 2021  OzzieIsaacs
 *
 *  This program is free software: you can redistribute it and/or modify
 *  it under the terms of the GNU General Public License as published by
 *  the Free Software Foundation, either version 3 of the License, or
 *  (at your option) any later version.
 *
 *  This program is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 *  GNU General Public License for more details.
 *
 *  You should have received a copy of the GNU General Public License
 *  along with this program. If not, see <http://www.gnu.org/licenses/>.
 */

// Clicking a book cover opens it in a light in-page viewer. The browser
// Fullscreen API was slow (macOS animates a new Space before showing it).
(function () {
  var cover = document.getElementById("detailcover");
  if (!cover) return;
  var viewer = null;

  function close() {
    if (!viewer) return;
    viewer.classList.remove("is-open");
    document.removeEventListener("keydown", onKey);
    cover.focus({preventScroll: true});
  }

  function onKey(event) {
    if (event.key === "Escape") close();
  }

  function open() {
    if (!viewer) {
      viewer = document.createElement("div");
      viewer.className = "lily-cover-viewer";
      viewer.setAttribute("role", "dialog");
      viewer.setAttribute("aria-modal", "true");
      viewer.setAttribute("aria-label", cover.alt || cover.title || "Cover");
      viewer.tabIndex = -1;
      var img = document.createElement("img");
      img.alt = "";
      img.decoding = "async";
      img.src = cover.currentSrc || cover.src;
      viewer.appendChild(img);
      viewer.addEventListener("click", close);
      document.body.appendChild(viewer);
      // Let the first frame paint closed so the fade runs.
      void viewer.offsetWidth;
    }
    viewer.classList.add("is-open");
    viewer.focus({preventScroll: true});
    document.addEventListener("keydown", onKey);
  }

  cover.tabIndex = 0;
  cover.setAttribute("role", "button");
  cover.addEventListener("click", open);
  cover.addEventListener("keydown", function (event) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      open();
    }
  });
})();
