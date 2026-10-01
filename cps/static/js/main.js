/* This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
 *    Copyright (C) 2012-2019  mutschler, janeczku, jkrehm, OzzieIsaacs
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

function getPath() {
    var jsFileLocation = $("script[src*=jquery]").attr("src");  // the js file path
    return jsFileLocation.substr(0, jsFileLocation.search("/static/js/libs/jquery.min.js"));  // the js folder path
}

function postButton(event, action, location=""){
    event.preventDefault();
    var newForm = jQuery('<form>', {
        "action": action,
        'target': "_top",
        'method': "post"
    }).append(jQuery('<input>', {
        'name': 'csrf_token',
        'value': $("input[name=\'csrf_token\']").val(),
        'type': 'hidden'
    })).appendTo('body')
    if(location !== "") {
        newForm.append(jQuery('<input>', {
            'name': 'location',
            'value': location,
            'type': 'hidden'
        })).appendTo('body');
    }
    newForm.submit();
}

function elementSorter(a, b) {
    a = +a.slice(0, -2);
    b = +b.slice(0, -2);
    if (a > b) return 1;
    if (a < b) return -1;
    return 0;
}

// Generic control/related handler to show/hide fields based on a checkbox' value
// e.g.
//  <input type="checkbox" data-control="stuff-to-show">
//  <div data-related="stuff-to-show">...</div>
$(document).on("change", "input[type=\"checkbox\"][data-control]", function () {
    var $this = $(this);
    var name = $this.data("control");
    var showOrHide = $this.prop("checked");

    $("[data-related=\"" + name + "\"]").each(function () {
        $(this).toggle(showOrHide);
    });
});

// Generic control/related handler to show/hide fields based on a select' value
$(document).on("change", "select[data-control]", function() {
    var $this = $(this);
    var name = $this.data("control");
    var showOrHide = parseInt($this.val(), 10);
    // var showOrHideLast = $("#" + name + " option:last").val()
    for (var i = 0; i < $(this)[0].length; i++) {
        var element = parseInt($(this)[0][i].value, 10);
        if (element === showOrHide) {
            $("[data-related^=" + name + "][data-related*=-" + element + "]").show();
        } else {
            $("[data-related^=" + name + "][data-related*=-" + element + "]").hide();
        }
    }
});

// Generic control/related handler to show/hide fields based on a select' value
// this one is made to show all values if select value is not 0
$(document).on("change", "select[data-controlall]", function() {
    var $this = $(this);
    var name = $this.data("controlall");
    var showOrHide = parseInt($this.val(), 10);
    if (showOrHide) {
        $("[data-related=" + name + "]").show();
    } else {
        $("[data-related=" + name + "]").hide();
    }
});


$(document).on("click", ".postAction", function (event) {
    // $(".sendbutton").on("click", "body", function(event) {
    postButton(event, $(this).data('action'));
});


// Syntax has to be bind not on, otherwise problems with firefox
$(".container-fluid").bind("dragenter dragover", function () {
    if($("#btn-upload").length && !$('body').hasClass('shelforder')) {
        $(this).css('background', '#e6e6e6');
    }
    return false;
});

// Syntax has to be bind not on, otherwise problems with firefox
$(".container-fluid").bind("dragleave", function () {
    if($("#btn-upload").length && !$('body').hasClass('shelforder')) {
        $(this).css('background', '');
    }
    return false;
});

// Syntax has to be bind not on, otherwise problems with firefox
$(".container-fluid").bind('drop', function (e) {
    e.preventDefault()
    e.stopPropagation();
    if($("#btn-upload").length) {
        var files = e.originalEvent.dataTransfer.files;
        var test = $("#btn-upload")[0].accept;
        $(this).css('background', '');
        const dt = new DataTransfer();
        jQuery.each(files, function (index, item) {
            if (test.toLowerCase().indexOf(item.name.substr(item.name.lastIndexOf('.')).toLowerCase()) !== -1) {
                dt.items.add(item);
            }
        });
        if (dt.files.length) {
            $("#btn-upload")[0].files = dt.files;
            $("#form-upload").submit();
        }
    }
});

$("#btn-upload").change(function() {
    $("#form-upload").submit();
});


$("#form-upload").uploadprogress({
    redirect_url: getPath() + "/",
    uploadedMsg: $("#form-upload").data("message"),
    modalTitle: $("#form-upload").data("title"),
    modalFooter: $("#form-upload").data("footer"),
    modalTitleFailed: $("#form-upload").data("failed")
});

$(document).ready(function() {
    var inp = $('#query').first()
    if (inp.length) {
        var val = inp.val()
        if (val.length) {
            inp.val('').blur().focus().val(val)
        }
    }
});

$(".session").click(function() {
    window.sessionStorage.setItem("back", window.location.pathname);
    window.sessionStorage.setItem("search", window.location.search);
});

$("#back").click(function() {
   var loc = sessionStorage.getItem("back");
   var param = sessionStorage.getItem("search");
   if (!loc) {
       loc = $(this).data("back");
   }
   sessionStorage.removeItem("back");
   sessionStorage.removeItem("search");
   if (param === null) {
       param = "";
   }
   window.location.href = loc + param;

});

function confirmDialog(id, dialogid, dataValue, yesFn, noFn) {
    var $confirm = $("#" + dialogid);
    $("#btnConfirmYes-"+ dialogid).off('click').click(function () {
        yesFn(dataValue);
        $confirm.modal("hide");
    });
    $("#btnConfirmNo-"+ dialogid).off('click').click(function () {
        if (typeof noFn !== 'undefined') {
            noFn(dataValue);
        }
        $confirm.modal("hide");
    });
    $.ajax({
        method:"post",
        dataType: "json",
        url: getPath() + "/ajax/loaddialogtexts/" + id,
        success: function success(data) {
            $("#header-"+ dialogid).text(data.header);
            $("#text-"+ dialogid).text(data.main);
            if (data.button) { $("#btnConfirmYes-"+ dialogid).text(data.button); }
        }
    });
    $confirm.modal('show');
}

$("#delete_confirm").click(function(event) {
    //get data-id attribute of the clicked element
    var deleteId = $(this).data("delete-id");
    var bookFormat = $(this).data("delete-format");
    var ajaxResponse = $(this).data("ajax");
    if (bookFormat) {
        postButton(event, getPath() + "/delete/" + deleteId + "/" + bookFormat);
    } else {
        if (ajaxResponse) {
            path = getPath() + "/ajax/delete/" + deleteId;
            $.ajax({
                method:"post",
                url: path,
                timeout: 900,
                success:function(data) {
                    data.forEach(function(item) {
                        if (!jQuery.isEmptyObject(item)) {
                            if (item.format != "") {
                                $("button[data-delete-format='"+item.format+"']").addClass('hidden');
                            }
                            $( ".navbar" ).after( '<div class="row-fluid text-center" >' +
                                '<div id="flash_'+item.type+'" class="alert alert-'+item.type+'">'+item.message+'</div>' +
                                '</div>');
                        }
                    });
                    $("#books-table").bootstrapTable("refresh");
                }
            });
        } else {
            var loc = sessionStorage.getItem("back");
            if (!loc) {
                loc = $(this).data("back");
            }
            sessionStorage.removeItem("back");
            postButton(event, getPath() + "/delete/" + deleteId, location=loc);
        }
    }
});

//triggered when modal is about to be shown
$("#deleteModal").on("show.bs.modal", function(e) {
    //get data-id attribute of the clicked element and store in button
    var bookId = $(e.relatedTarget).data("delete-id");
    var bookfomat = $(e.relatedTarget).data("delete-format");
    if (bookfomat) {
        $("#book_format").removeClass('hidden');
        $("#book_complete").addClass('hidden');
    } else {
        $("#book_complete").removeClass('hidden');
        $("#book_format").addClass('hidden');
    }
    $(e.currentTarget).find("#delete_confirm").data("delete-id", bookId);
    $(e.currentTarget).find("#delete_confirm").data("delete-format", bookfomat);
    $(e.currentTarget).find("#delete_confirm").data("ajax", $(e.relatedTarget).data("ajax"));
});

$(function() {
    // Allow ajax prefilters to be added/removed dynamically
    // eslint-disable-next-line new-cap
    var preFilters = $.Callbacks();
    $.ajaxPrefilter(preFilters.fire);

    // equip all post requests with csrf_token
    var csrftoken = $("input[name='csrf_token']").val();
    $.ajaxSetup({
        beforeSend: function(xhr, settings) {
            if (!/^(GET|HEAD|OPTIONS|TRACE)$/i.test(settings.type) && !this.crossDomain) {
                xhr.setRequestHeader("X-CSRFToken", csrftoken)
            }
        }
    });

    $(document).on("click", ".duplicate-scan-setup-dismiss", function() {
        var dismissUrl = $(this).data("dismiss-url");
        if (!dismissUrl) {
            return;
        }
        $.ajax({
            method: "post",
            url: dismissUrl
        });
    });

    let selectedLayoutMode;

    if ($("body").hasClass("blur")) {
        selectedLayoutMode = "fitRowsCentered";
    } else {
        selectedLayoutMode = "fitRows";
    }

    // Lily's cover grids (.lily-grid) are CSS grids; Isotope only lays out the older rows.
    $(".discover .row").not(".lily-grid").filter(function() {
        return $(this).find(".book").length > 0;
    }).isotope({
        // options
        itemSelector : ".book",
        layoutMode : selectedLayoutMode
    });

    // Compact pager: "…" opens a small jump-to-page form
    $(".pagination .page-jump").on("shown.bs.dropdown", function() {
        $(this).find("input[type=number]").trigger("focus");
    });
    $(".page-jump-form").on("submit", function(e) {
        e.preventDefault();
        var $input = $(this).find("input[type=number]");
        var max = parseInt($input.attr("max"), 10);
        var target = parseInt($input.val(), 10);
        if (isNaN(target)) { return; }
        target = Math.min(Math.max(target, 1), max);
        var template = String($(this).data("url-template"));
        var placeholder = String($(this).data("placeholder"));
        window.location.href = template.split(placeholder).join(String(target));
    });

    // Init all data control handlers to default
    $("input[data-control]").trigger("change");
    $("select[data-control]").trigger("change");
    $("select[data-controlall]").trigger("change");

    $("#bookDetailsModal")
        .on("show.bs.modal", function(e) {
            $("#flash_danger").remove();
            $("#flash_success").remove();
            var $modalBody = $(this).find(".modal-body");

            // Prevent static assets from loading multiple times
            var useCache = function(options) {
                options.async = true;
                options.cache = true;
            };
            preFilters.add(useCache);

            $.get(e.relatedTarget.href).done(function(content) {
                $modalBody.html(content);
                preFilters.remove(useCache);
                $("#back").remove();
            });
        })
        .on("hidden.bs.modal", function() {
            $(this).find(".modal-body").html("...");
        });

    $("#btndeluser").click(function() {
        confirmDialog(
            $(this).attr('id'),
            "GeneralDeleteModal",
            $(this).data('value'),
            function(value){
                var subform = $('#user_submit').closest("form");
                subform.submit(function(eventObj) {
                    $(this).append('<input type="hidden" name="delete" value="True" />');
                    return true;
                });
                subform.submit();
            }
        );
    });

    $("#user_submit").click(function() {
        this.closest("form").submit();
    });

    $('.collapse').on('shown.bs.collapse', function(){
        $(this).parent().find(".glyphicon-plus").removeClass("glyphicon-plus").addClass("glyphicon-minus");
    }).on('hidden.bs.collapse', function(){
    $(this).parent().find(".glyphicon-minus").removeClass("glyphicon-minus").addClass("glyphicon-plus");
    });

    $("#delete_shelf").click(function(event) {
        confirmDialog(
            $(this).attr('id'),
            "GeneralDeleteModal",
            $(this).data('value'),
            function(value){
                postButton(event, $("#delete_shelf").data("action"));
            }
        );

    });

    var isotopeResizeTimer = null;
    $(window).resize(function() {
        // Debounce: re-layout once resizing settles instead of on every event
        clearTimeout(isotopeResizeTimer);
        isotopeResizeTimer = setTimeout(function() {
            $(".discover .row").filter(function() {
                return !!$(this).data("isotope");
            }).isotope("layout");
        }, 150);
    });

    $(".author-expand").click(function() {
        $(this).parent().find("a.author-name").slice($(this).data("authors-max")).toggle();
        $(this).parent().find("span.author-hidden-divider").toggle();
        $(this).html() === $(this).data("collapse-caption") ? $(this).html("(...)") : $(this).html($(this).data("collapse-caption"));
        $(".discover .row").filter(function() {
            return !!$(this).data("isotope");
        }).isotope("layout");
    });

    // Grid/List icons in the list toolbar. Book pages switch in place; the series
    // page renders a different template per view, so it reloads.
    $(".lily-view-switch [data-view]").click(function(e) {
        var $btn = $(this);
        var view = $btn.data("view");
        var kind = $btn.closest(".lily-view-switch").data("kind");
        e.preventDefault();
        if ($btn.attr("aria-pressed") === "true") { return; }
        var settings = kind === "series" ? {series: {series_view: view}} : {books: {view: view}};
        if (kind !== "series") {
            document.body.setAttribute("data-book-view", view);
            $btn.siblings("[data-view]").addBack().each(function() {
                $(this).attr("aria-pressed", $(this).data("view") === view ? "true" : "false");
            });
        }
        $.ajax({
            method: "post",
            contentType: "application/json; charset=utf-8",
            dataType: "json",
            url: getPath() + "/ajax/view",
            data: JSON.stringify(settings),
            success: function success() {
                if (kind === "series") { location.reload(); }
            }
        });
    });
});
