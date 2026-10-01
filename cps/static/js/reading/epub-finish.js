/* End-of-book card for the epub reader (#finish-card in read.html).
 *
 * Shown on the last page, or from 99% on (the point where the server marks the book as
 * read), with "Read next in series" when there is one and "Back to library". Closing it
 * keeps it away until the reader leaves the end and comes back. */
(function () {
    "use strict";

    var FINISHED_AT = 0.99;
    var card = document.getElementById("finish-card");
    if (!card) {
        return;
    }
    var closer = card.querySelector(".reader-finish-close");
    var dismissed = false;

    function show() {
        if (card.hidden) {
            card.hidden = false;
        }
    }

    function hide() {
        card.hidden = true;
    }

    window.addEventListener("lily:reader-progress", function (event) {
        var detail = event.detail || {};
        var finished = detail.atEnd || (typeof detail.fraction === "number" && detail.fraction >= FINISHED_AT);
        if (!finished) {
            dismissed = false;
            hide();
        } else if (!dismissed) {
            show();
        }
    });

    if (closer) {
        closer.addEventListener("click", function () {
            dismissed = true;
            hide();
        });
    }
    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && !card.hidden) {
            dismissed = true;
            hide();
        }
    });
})();
