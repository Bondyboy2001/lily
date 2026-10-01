# djvu-html5

Vendored build of [djvu-html5](https://github.com/mateusz-matela/djvu-html5) v0.3.0-beta1 (GWT output).

Local change: in each `djvu_html5/*.cache.js`, `new Worker(c)` is `new $wnd.Worker(c)`. The
viewer runs inside GWT's hidden helper iframe, and current Chrome blocks a worker created from
that frame (`coep-frame-resource-needs-coep-header`), so the page never loaded a document.
Creating the worker from the top-level window avoids it. Reapply this if the library is updated.
