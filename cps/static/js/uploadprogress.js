/*
 * bootstrap-uploadprogress
 * github: https://github.com/jakobadam/bootstrap-uploadprogress
 *
 * Copyright (c) 2015 Jakob Aarøe Dam
 * Version 1.0.0
 * Licensed under the MIT license.
 */

(function($) {
    "use strict";

    $.support.xhrFileUpload = !!(window.FileReader && window.ProgressEvent);
    $.support.xhrFormData = !!window.FormData;

    var ua = (navigator && navigator.userAgent) ? navigator.userAgent : "";
    var isSafari = /safari/i.test(ua) && !/chrome|chromium|crios|android/i.test(ua);

    if (!$.support.xhrFileUpload || !$.support.xhrFormData || isSafari) {
        // Skip decorating form in Safari, but provide a no-op uploadprogress
        // function so we do not break the jQuery bindings that make use of it.
        $.fn.uploadprogress = function(){};
        return;
    }

    // A plain Lily dialog (§5.7): title, the bar and a status line; Close appears only after a failure.
    var template = "<div class=\"modal fade\" id=\"file-progress-modal\" tabindex=\"-1\" role=\"dialog\" aria-labelledby=\"file-progress-title\">" +
    "<div class=\"modal-dialog upload-modal-dialog\">" +
    "  <div class=\"modal-content\">" +
    "    <div class=\"modal-header\">" +
    "      <h4 class=\"modal-title\" id=\"file-progress-title\">Uploading</h4>" +
    "    </div>" +
    "    <div class=\"modal-body\">" +
    "      <div class=\"progress\">" +
    "        <div class=\"progress-bar\" role=\"progressbar\" aria-valuenow=\"0\" aria-valuemin=\"0\" aria-valuemax=\"100\"></div>" +
    "      </div>" +
    "      <p class=\"modal-message\" role=\"status\">0%</p>" +
    "    </div>" +
    "    <div class=\"modal-footer\" hidden>" +
    "      <button type=\"button\" class=\"btn btn-default\" data-dismiss=\"modal\">Close</button>" +
    "    </div>" +
    "  </div>" +
    "</div>" +
    "</div>";

    var UploadProgress = function(element, options) {
        this.options = options;
        this.$element = $(element);
    };

    UploadProgress.prototype = {

        constructor: function() {
            this.$form = this.$element;
            this.$form.on("submit", $.proxy(this.submit, this));
            this.$modal = $(this.options.template);
            this.$modalTitle = this.$modal.find(".modal-title");
            this.$modalFooter = this.$modal.find(".modal-footer");
            this.$modalBar = this.$modal.find(".progress-bar");
            this.$modalMessage = this.$modal.find(".modal-message");

            // Translate texts
            this.$modalTitle.text(this.options.modalTitle);
            this.$modalFooter.children("button").text(this.options.modalFooter);

            this.$modal.on("hidden.bs.modal", $.proxy(this.reset, this));
        },

        reset: function() {
            this.$modalTitle.text(this.options.modalTitle);
            this.$modalFooter.prop("hidden", true);
            this.$modalBar.removeClass("progress-bar-danger");
            this.setProgress(0);
            if (this.xhr) {
                this.xhr.abort();
            }
        },

        submit: function(e) {
            e.preventDefault();

            this.$modal.modal({
                backdrop: "static",
                keyboard: false
            });

            // We need the native XMLHttpRequest for the progress event
            var xhr = new XMLHttpRequest();
            this.xhr = xhr;

            xhr.addEventListener("load", $.proxy(this.success, this, xhr));
            xhr.addEventListener("error", $.proxy(this.error, this, xhr));

            xhr.upload.addEventListener("progress", $.proxy(this.progress, this));

            var form = this.$form;

            xhr.open(form.attr("method"), form.attr("action"));
            xhr.setRequestHeader("X-REQUESTED-WITH", "XMLHttpRequest");

            var data = new FormData(form.get(0));
            xhr.send(data);
        },

        success: function(xhr) {
            if (xhr.status === 0 || xhr.status >= 400) {
                // HTTP 500 ends up here!?!
                return this.error(xhr);
            }
            this.setProgress(100);
            var url;
            var contentType = xhr.getResponseHeader("Content-Type");

            // make it possible to return the redirect URL in
            // a JSON response
            if (contentType.indexOf("application/json") !== -1) {
                var response = $.parseJSON(xhr.responseText);
                url = response.location;
                // lily.js picks this up on the next page and follows the import in its toast.
                if (response.uploads && response.uploads.length && response.status_url) {
                    try {
                        window.sessionStorage.setItem("lily.pendingUploads", JSON.stringify({
                            statusUrl: response.status_url,
                            uploads: response.uploads,
                            since: Date.now()
                        }));
                    } catch (err) { /* storage optional: the book still arrives */ }
                }
            } else {
                url = this.options.redirect_url;
            }
            window.location.href = url;
        },

        // handle form error
        // we replace the form with the returned one
        error: function(xhr) {
            this.$modalTitle.text(this.options.modalTitleFailed);

            this.setProgress(100);
            this.$modalBar.addClass("progress-bar-danger");
            this.$modalFooter.prop("hidden", false);

            // Say what broke and what to do next; a plain-text reply from the server is its own explanation.
            var contentType = xhr.getResponseHeader("Content-Type") || "";
            if (contentType.indexOf("text/plain") !== -1 && xhr.responseText) {
                this.$modalMessage.text(xhr.responseText);
            } else if (xhr.status === 502 || xhr.status === 0 || xhr.status === 413) {
                this.$modalMessage.text(this.options.tooLargeMsg);
            } else {
                this.$modalMessage.text(this.options.failedMsg);
            }
        },

        setProgress: function(percent) {
            var txt = percent + "%";
            if (percent === 100) {
                txt = this.options.uploadedMsg;
            }
            this.$modalBar.attr("aria-valuenow", percent);
            this.$modalBar.css("width", percent + "%");
            this.$modalMessage.text(txt);
        },

        progress: function(/*ProgressEvent*/e) {
            var percent = Math.round((e.loaded / e.total) * 100);
            this.setProgress(percent);
        },

        // replaceForm replaces the contents of the current form
        // with the form in the html argument.
        // We use the id of the current form to find the new form in the html
        replaceForm: function(html) {
            var newForm;
            var formId = this.$form.attr("id");
            if ( typeof formId !== "undefined") {
                newForm = $(html).find("#" + formId);
            } else {
                newForm = $(html).find("form");
            }
            // add the filestyle again
            newForm.find(":file").filestyle({buttonBefore: true});
            this.$form.html(newForm.children());
        }
    };

    $.fn.uploadprogress = function(options) {
        return this.each(function() {
            var _options = $.extend({}, $.fn.uploadprogress.defaults, options);
            var fileProgress = new UploadProgress(this, _options);
            fileProgress.constructor();
        });
    };

    $.fn.uploadprogress.defaults = {
        template: template,
        uploadedMsg: "Uploaded. Adding to the library…",
        modalTitle: "Uploading",
        modalFooter: "Close",
        modalTitleFailed: "Upload Failed",
        tooLargeMsg: "The upload didn’t finish. The file may be larger than the server accepts; try a smaller file.",
        failedMsg: "The server couldn’t add this file. Check it’s a supported format, then try again."
        //redirect_url: ...
        // need to customize stuff? Add here, and change code accordingly.
    };

})(window.jQuery);
