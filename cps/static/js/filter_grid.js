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
 * save it (lilyPickOption in lily.js ticks the choice) and reload in the new order.
 */
(function ($) {
    "use strict";

    function saveDirection(link, dir) {
        if (!lilyPickOption(link)) {
            return;
        }
        var view = {};
        view[$(link).data("id")] = { dir: dir };
        $.ajax({
            method: "post",
            contentType: "application/json; charset=utf-8",
            dataType: "json",
            url: getPath() + "/ajax/view",
            data: JSON.stringify(view)
        }).always(function () {
            window.location.reload();
        });
    }

    $(document).on("click", "#desc", function (e) {
        e.preventDefault();
        saveDirection(this, "desc");
    });

    $(document).on("click", "#asc", function (e) {
        e.preventDefault();
        saveDirection(this, "asc");
    });
})(window.jQuery);
