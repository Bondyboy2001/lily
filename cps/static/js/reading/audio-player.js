/* global calibre, LilyProgress */

/* Lily audiobook player: a native <audio> element behind Lily controls.
 * The listening position is saved as cfi "time:SECONDS" through the same progress
 * endpoint as the readers, and the playback speed is remembered per browser. */
(function () {
    "use strict";

    var audio = document.getElementById("lily-audio");
    var player = document.querySelector(".lily-audio-player");
    if (!audio || !player) {
        return;
    }
    var playBtn = document.getElementById("audio-play");
    var seek = document.getElementById("audio-seek");
    var current = document.getElementById("audio-current");
    var duration = document.getElementById("audio-duration");
    var speed = document.getElementById("audio-speed");
    var volume = document.getElementById("audio-volume");
    var errorBox = document.getElementById("audio-error");

    var RATE_KEY = "lily.audio.rate";
    var VOLUME_KEY = "lily.audio.volume";
    var SAVE_EVERY = 5; // seconds of playback between position saves

    var progress = LilyProgress.create({
        url: calibre.progressUrl,
        storageKey: calibre.progressKey,
        format: calibre.progressFormat,
        statusEl: document.getElementById("progress-sync-status"),
        enabled: calibre.syncProgress === true
    });
    var restored = false;
    var lastSaved = -1;
    var seeking = false;

    function store(key, value) {
        try { localStorage.setItem(key, value); } catch (e) {}
    }

    function stored(key) {
        try { return localStorage.getItem(key); } catch (e) { return null; }
    }

    function clock(seconds) {
        if (!isFinite(seconds) || seconds < 0) {
            seconds = 0;
        }
        seconds = Math.floor(seconds);
        var h = Math.floor(seconds / 3600);
        var m = Math.floor(seconds % 3600 / 60);
        var s = seconds % 60;
        var mm = h ? String(m).padStart(2, "0") : String(m);
        return (h ? h + ":" : "") + mm + ":" + String(s).padStart(2, "0");
    }

    function save(force) {
        if (!restored || !isFinite(audio.duration) || audio.duration <= 0) {
            return;
        }
        var t = audio.currentTime;
        if (!force && Math.abs(t - lastSaved) < SAVE_EVERY) {
            return;
        }
        lastSaved = t;
        progress.save("time:" + t.toFixed(1), t / audio.duration);
    }

    function render() {
        if (!seeking) {
            seek.value = Math.floor(audio.currentTime);
        }
        current.textContent = clock(seeking ? parseFloat(seek.value) : audio.currentTime);
    }

    function setPlaying(playing) {
        player.classList.toggle("is-playing", playing);
        var label = player.getAttribute(playing ? "data-label-pause" : "data-label-play");
        playBtn.setAttribute("aria-label", label);
        playBtn.setAttribute("title", label);
    }

    function skip(delta) {
        if (!isFinite(audio.duration)) {
            return;
        }
        audio.currentTime = Math.min(audio.duration, Math.max(0, audio.currentTime + delta));
        render();
        save(true);
    }

    // Speed and volume, remembered in this browser.
    var savedRate = parseFloat(stored(RATE_KEY));
    if (savedRate > 0) {
        var option = speed.querySelector("option[value='" + savedRate + "']");
        if (!option) {
            option = document.createElement("option");
            option.value = String(savedRate);
            option.textContent = savedRate + "×";
            speed.appendChild(option);
        }
        speed.value = String(savedRate);
    }
    var savedVolume = parseFloat(stored(VOLUME_KEY));
    if (savedVolume >= 0 && savedVolume <= 1) {
        volume.value = savedVolume;
    }
    function applyRate() {
        audio.playbackRate = parseFloat(speed.value) || 1;
        audio.defaultPlaybackRate = audio.playbackRate;
    }
    applyRate();
    audio.volume = parseFloat(volume.value);

    speed.addEventListener("change", function () {
        applyRate();
        store(RATE_KEY, speed.value);
    });
    volume.addEventListener("input", function () {
        audio.volume = parseFloat(volume.value);
        store(VOLUME_KEY, volume.value);
    });

    playBtn.addEventListener("click", function () {
        if (audio.paused) {
            var played = audio.play();
            if (played && played.catch) {
                played.catch(function () {});
            }
        } else {
            audio.pause();
        }
    });
    document.getElementById("audio-back").addEventListener("click", function () { skip(-15); });
    document.getElementById("audio-forward").addEventListener("click", function () { skip(30); });

    seek.addEventListener("input", function () {
        seeking = true;
        render();
    });
    seek.addEventListener("change", function () {
        seeking = false;
        audio.currentTime = parseFloat(seek.value);
        render();
        save(true);
    });

    audio.addEventListener("play", function () { setPlaying(true); applyRate(); });
    audio.addEventListener("pause", function () { setPlaying(false); save(true); });
    audio.addEventListener("ended", function () { setPlaying(false); save(true); });
    audio.addEventListener("timeupdate", function () { render(); save(false); });
    audio.addEventListener("error", function () {
        if (errorBox) {
            errorBox.hidden = false;
        }
    });

    audio.addEventListener("loadedmetadata", function () {
        seek.max = Math.floor(audio.duration) || 0;
        seek.disabled = !(audio.duration > 0);
        duration.textContent = clock(audio.duration);
        applyRate();
        progress.load().then(function (saved) {
            var at = saved ? LilyProgress.parseTagged(saved.cfi, "time") : null;
            if (at === null && saved && saved.percent > 0 && isFinite(audio.duration)) {
                at = saved.percent * audio.duration;
            }
            // Don't resume in the last few seconds: start again instead.
            if (at !== null && at > 0 && at < audio.duration - 5) {
                audio.currentTime = at;
                lastSaved = at;
            }
        }).catch(function () {}).then(function () {
            restored = true;
            render();
        });
    });

    // Space plays/pauses, arrows skip, unless a control has focus.
    document.addEventListener("keydown", function (event) {
        var tag = (event.target && event.target.tagName) || "";
        if (/^(INPUT|SELECT|TEXTAREA|BUTTON|A)$/.test(tag) || event.metaKey || event.ctrlKey || event.altKey) {
            return;
        }
        if (event.key === " ") {
            event.preventDefault();
            playBtn.click();
        } else if (event.key === "ArrowLeft") {
            skip(-15);
        } else if (event.key === "ArrowRight") {
            skip(30);
        }
    });

    // The sync module flushes on these events too, but its listener ran first.
    window.addEventListener("pagehide", function () { save(true); progress.flush(); });
    document.addEventListener("visibilitychange", function () {
        if (document.visibilityState === "hidden") {
            save(true);
            progress.flush();
        }
    });
})();
