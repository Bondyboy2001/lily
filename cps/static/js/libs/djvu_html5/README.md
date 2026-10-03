# djvu-html5

Vendored build of [djvu-html5](https://github.com/mateusz-matela/djvu-html5) v0.3.0-beta1 (GWT output).

Local change: in each `djvu_html5/*.cache.js`, `new Worker(c)` is `new $wnd.Worker(c)`. The
viewer runs inside GWT's hidden helper iframe, and current Chrome blocks a worker created from
that frame (`coep-frame-resource-needs-coep-header`), so the page never loaded a document.
Creating the worker from the top-level window avoids it. Reapply this if the library is updated.

Local change: in each `*.cache.js`, the toolbar's zoom-list rebuild (`Toolbar.zoomOptionsChanged`)
kept "Fit width"/"Fit page" selected with `newSize + (oldSize - index)`, which turns "Fit page"
into the last percentage, and it left the old list in place. Picking a zoom after that threw
and did nothing. It is now `newSize + (index - oldSize)` followed by `a.d=b` (store the new
list). Reapply this too if the library is updated.
