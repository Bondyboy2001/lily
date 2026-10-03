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

// Submits a POST form; the server answers with the redirect, so a page to land on (the
// optional redirectLocation) is sent along and only followed once the POST has succeeded.
function postButton(event, action, redirectLocation=""){
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
    if(redirectLocation !== "") {
        newForm.append(jQuery('<input>', {
            'name': 'location',
            'value': redirectLocation,
            'type': 'hidden'
        })).appendTo('body');
    }
    newForm.submit();
}

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

$("#back").click(function() {
   window.location.href = $(this).data("back");
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
    if (bookFormat) {
        postButton(event, getPath() + "/delete/" + deleteId + "/" + bookFormat);
    } else {
        var loc = $(this).data("back");
        postButton(event, getPath() + "/delete/" + deleteId, loc);
    }
});

//triggered when modal is about to be shown
$("#deleteModal").on("show.bs.modal", function(e) {
    //get data-id attribute of the clicked element and store in button
    var bookId = $(e.relatedTarget).data("delete-id");
    var bookfomat = $(e.relatedTarget).data("delete-format");
    // Name what is about to go: "“Quiet Machines”", or "this book" when the opener doesn't say.
    var title = $(e.relatedTarget).data("delete-title");
    $(e.currentTarget).find("#book_complete .delete-title").text(title ? "“" + title + "”" : "This book");
    $(e.currentTarget).find("#book_format .delete-title").text(title ? "“" + title + "”" : "this book");
    $(e.currentTarget).find(".delete-format").text(bookfomat || "");
    $(e.currentTarget).find("#metaDeleteLabel").text(bookfomat ? "Delete " + bookfomat + " File?" : "Delete Book?");
    if (bookfomat) {
        $("#book_format").removeClass('hidden');
        $("#book_complete").addClass('hidden');
    } else {
        $("#book_complete").removeClass('hidden');
        $("#book_format").addClass('hidden');
    }
    $(e.currentTarget).find("#delete_confirm").data("delete-id", bookId);
    $(e.currentTarget).find("#delete_confirm").data("delete-format", bookfomat);
});

$(function() {
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

    $(".author-expand").click(function() {
        $(this).parent().find("a.author-name").slice($(this).data("authors-max")).toggle();
        $(this).parent().find("span.author-hidden-divider").toggle();
        $(this).html() === $(this).data("collapse-caption") ? $(this).html("(...)") : $(this).html($(this).data("collapse-caption"));
    });

    // Grid/List icons in the list toolbar: the page switches in place and the choice is saved
    $(".lily-view-switch [data-view]").click(function(e) {
        var $btn = $(this);
        var view = $btn.data("view");
        e.preventDefault();
        if ($btn.attr("aria-pressed") === "true") { return; }
        document.body.setAttribute("data-book-view", view);
        $btn.siblings("[data-view]").addBack().each(function() {
            $(this).attr("aria-pressed", $(this).data("view") === view ? "true" : "false");
        });
        $.ajax({
            method: "post",
            contentType: "application/json; charset=utf-8",
            dataType: "json",
            url: getPath() + "/ajax/view",
            data: JSON.stringify({books: {view: view}})
        });
    });
});
