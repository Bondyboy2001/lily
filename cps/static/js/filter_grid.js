/* This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
 *    Copyright (C) 2018 OzzieIsaacs
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

/*
 * Series grid (grid.html). The server sorts the cards and cuts them by letter (the letters
 * are plain links), and a CSS grid lays them out, so all that is left here is the direction:
 * the button (image.sort_dir_button) flips it (lilyToggleSortDir in lily.js relabels it), the
 * choice is saved and the page reloads in the new order.
 */
(function ($) {
    "use strict";

    $(document).on("click", "#lily-order-toggle", function (e) {
        e.preventDefault();
        var dir = lilyToggleSortDir(this);
        var view = {};
        view[$(this).data("id")] = { dir: dir };
        $.ajax({
            method: "post",
            contentType: "application/json; charset=utf-8",
            dataType: "json",
            url: getPath() + "/ajax/view",
            data: JSON.stringify(view)
        }).always(function () {
            window.location.reload();
        });
    });
})(window.jQuery);
