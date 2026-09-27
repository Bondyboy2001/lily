# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Profile picture routes."""

from flask import redirect, flash, url_for, request, jsonify, abort, make_response
from flask_babel import gettext as _

from ..usermanagement import user_login_required
from ..render_template import render_title_template
from ..cw_login import current_user

import json
import base64
import os
import tempfile
import threading
import zlib

from .common import profile_pictures, log

# ################################### Profile Pictures ###################################################

PROFILES_JSON_PATH = "/config/user_profiles.json"
_profiles_cache = {"mtime": None, "data": {}}
_profiles_lock = threading.Lock()


def _load_profiles():
    """Parsed user_profiles.json, re-read only when the file changes on disk."""
    mtime = os.stat(PROFILES_JSON_PATH).st_mtime_ns
    with _profiles_lock:
        if _profiles_cache["mtime"] != mtime:
            with open(PROFILES_JSON_PATH, "r") as file:
                _profiles_cache["data"] = json.load(file)
            _profiles_cache["mtime"] = mtime
        return _profiles_cache["data"]


@profile_pictures.app_template_global("lily_avatar_version")
def lily_avatar_version(username):
    """Cache-busting version of a user's profile picture, or None if they have none."""
    try:
        image_data = _load_profiles().get(username)
    except Exception:
        return None
    if not isinstance(image_data, str) or not image_data:
        return None
    return format(zlib.crc32(image_data.encode("utf-8")), "08x")


@profile_pictures.route("/me/avatar")
@user_login_required
def user_avatar():
    """The current user's profile picture as an image, so pages don't need the whole JSON."""
    try:
        image_data = _load_profiles().get(current_user.name)
    except Exception as e:
        log.error(f"Error reading user_profiles.json: {str(e)}")
        abort(404)
    if not isinstance(image_data, str) or not image_data.startswith("data:image/") or ";base64," not in image_data:
        abort(404)
    header, encoded = image_data.split(";base64,", 1)
    try:
        body = base64.b64decode(encoded)
    except Exception:
        abort(404)
    response = make_response(body)
    response.mimetype = header[len("data:"):]
    response.set_etag(lily_avatar_version(current_user.name))
    if request.args.get("v"):
        # URL carries the content version, so it can be cached until the picture changes
        response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    else:
        response.headers["Cache-Control"] = "private, no-cache"
    return response.make_conditional(request)


def _save_profile_picture(username, image_data):
    """Set one user's picture, replacing the JSON file atomically so a crash can't truncate it."""
    with _profiles_lock:
        try:
            with open(PROFILES_JSON_PATH, "r") as file:
                user_data = json.load(file)
        except FileNotFoundError:
            user_data = {}
        user_data[username] = image_data
        directory = os.path.dirname(PROFILES_JSON_PATH)
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".user_profiles.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as file:
                json.dump(user_data, file, indent=4)
                file.flush()
                os.fsync(file.fileno())
            os.replace(tmp_path, PROFILES_JSON_PATH)
        except BaseException:
            os.unlink(tmp_path)
            raise


@profile_pictures.route("/user_profiles.json")
@user_login_required
def user_profiles_json():
    """All pictures for admins; everyone else only sees their own."""
    try:
        profiles = _load_profiles()
        if current_user.role_admin():
            return jsonify(profiles)
        own = profiles.get(current_user.name)
        return jsonify({current_user.name: own} if own else {})
    except Exception as e:
        log.error(f"Error reading user_profiles.json: {str(e)}")
        return jsonify({}), 500

@profile_pictures.route("/me/profile-picture", methods=["GET", "POST"])
@user_login_required
def set_profile_picture():
    log.debug("Accessed /me/profile-picture route.")

    # Check if the user is an admin
    if not current_user.role_admin():
        flash(_("You must be an admin to access this page."), category="error")
        log.warning(f"Unauthorized access attempt by user: {current_user.name}")
        return redirect(url_for('web.profile'))

    if request.method == "POST":
        log.debug("POST request received on profile_pictures page.")

        # Get the form data (username and image data)
        username = request.form.get("username")
        image_data = request.form.get("image_data")

        log.debug(f"Form data received - Username: {username}, Image Data Length: {len(image_data) if image_data else 'None'}")

        # Validate form fields
        if not username or not image_data:
            flash(_("Both username and image data are required."), category="error")
            log.warning("Form submission missing username or image_data.")
            return redirect(url_for('profile_pictures.set_profile_picture'))

        # Validate Base64 image data format
        try:
            # Check if image_data starts with a valid data URI scheme
            if not image_data.startswith('data:image/'):
                flash(_("Invalid image data format. Must be a valid image."), category="error")
                log.warning(f"Invalid image data format from user: {username}")
                return redirect(url_for('profile_pictures.set_profile_picture'))
            
            # Verify it's a supported image type (PNG or JPEG)
            if not (image_data.startswith('data:image/png;base64,') or 
                    image_data.startswith('data:image/jpeg;base64,') or
                    image_data.startswith('data:image/jpg;base64,')):
                flash(_("Unsupported image type. Only PNG and JPEG are allowed."), category="error")
                log.warning(f"Unsupported image type from user: {username}")
                return redirect(url_for('profile_pictures.set_profile_picture'))
            
            # Extract and validate the Base64 portion
            if ';base64,' in image_data:
                base64_part = image_data.split(';base64,')[1]
                # Try to decode to verify it's valid Base64
                try:
                    decoded = base64.b64decode(base64_part, validate=True)
                    # Check size (limit to 500KB decoded)
                    if len(decoded) > 512000:
                        flash(_("Image is too large. Please use an image smaller than 500KB."), category="error")
                        log.warning(f"Image too large from user: {username}, size: {len(decoded)} bytes")
                        return redirect(url_for('profile_pictures.set_profile_picture'))
                except Exception as decode_error:
                    flash(_("Invalid Base64 image data."), category="error")
                    log.warning(f"Invalid Base64 data from user: {username}, error: {str(decode_error)}")
                    return redirect(url_for('profile_pictures.set_profile_picture'))
            else:
                flash(_("Invalid image data format."), category="error")
                log.warning(f"Invalid image data format (no base64 marker) from user: {username}")
                return redirect(url_for('profile_pictures.set_profile_picture'))
                
        except Exception as validation_error:
            flash(_("Error validating image data."), category="error")
            log.error(f"Image validation error: {str(validation_error)}")
            return redirect(url_for('profile_pictures.set_profile_picture'))

        try:
            _save_profile_picture(username, image_data)

            # Success feedback and logging
            flash(_("Profile picture updated successfully."), category="success")
            log.info(f"Profile picture updated for user: {username}")

        except Exception as e:
            # Error handling in case of an issue
            flash(f"Error: {str(e)}", category="error")
            log.error(f"Exception while updating profile picture JSON: {str(e)}")

        return redirect(url_for('profile_pictures.set_profile_picture'))

    # Handle the GET request and render the page
    log.debug("Rendering GET view for profile_pictures page.")
    return render_title_template("profile_pictures.html", 
                                title=_("Lily Profile Picture Management (WIP)"), 
                                page="profile-picture")
