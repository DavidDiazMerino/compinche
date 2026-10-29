const DB_NAME = "compinche-capture";
const DB_VERSION = 1;
let connection;
let sessionDraft = null;

function database() {
  if (!connection)
    connection = new Promise((resolve, reject) => {
      if (!globalThis.indexedDB) {
        reject(new Error("This browser cannot store video on this device."));
        return;
      }
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains("recordings")) {
          const store = db.createObjectStore("recordings", { keyPath: "id" });
          store.createIndex("createdAt", "createdAt");
        }
        if (!db.objectStoreNames.contains("drafts"))
          db.createObjectStore("drafts", { keyPath: "id" });
      };
      request.onsuccess = () => {
        request.result.onversionchange = () => {
          request.result.close();
          connection = null;
        };
        resolve(request.result);
      };
      request.onerror = () => {
        connection = null;
        reject(request.error);
      };
      request.onblocked = () => {
        connection = null;
        reject(new Error("Close other Compinche tabs before saving."));
      };
    });
  return connection;
}

async function transact(storeName, operation, write = false) {
  const db = await database();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, write ? "readwrite" : "readonly");
    let result;
    const request = operation(tx.objectStore(storeName));
    request.onsuccess = () => {
      result = request.result;
    };
    tx.oncomplete = () => resolve(result);
    tx.onerror = () => reject(tx.error || request.error);
    tx.onabort = () =>
      reject(tx.error || new Error("The recording could not be saved."));
  });
}

export async function listRecordings() {
  const records = await transact("recordings", (store) => store.getAll());
  return records
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
    .map(({ blob, ...metadata }) => metadata);
}

export async function getRecording(id) {
  return transact("recordings", (store) => store.get(id));
}

export async function deleteRecording(id) {
  return transact("recordings", (store) => store.delete(id), true);
}

function clock(seconds) {
  const safe = Math.max(0, Math.floor(seconds || 0));
  return `${String(Math.floor(safe / 60)).padStart(2, "0")}:${String(safe % 60).padStart(2, "0")}`;
}

const icons = {
  arrow: '<path d="m14 6-6 6 6 6M8 12h12"/>',
  camera:
    '<rect x="3" y="6" width="13" height="12" rx="3"/><path d="m16 10 5-3v10l-5-3"/>',
  upload:
    '<path d="M12 16V3m-5 5 5-5 5 5M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5"/>',
  lock: '<rect x="5" y="10" width="14" height="11" rx="3"/><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 5v2"/>',
  pause: '<path d="M8 5v14M16 5v14"/>',
  play: '<path d="m8 5 11 7-11 7Z"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  note: '<path d="M8 3h8l4 4v14H4V3h4Zm8 0v5h4M8 12h8M8 16h6"/>',
  spark:
    '<path d="m12 3 2.3 6.7L21 12l-6.7 2.3L12 21l-2.3-6.7L3 12l6.7-2.3Z"/>',
  download: '<path d="M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4"/>',
  mic: '<rect x="9" y="2" width="6" height="13" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8"/>',
  retry: '<path d="M3 10a9 9 0 1 1 1 7M3 3v7h7"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  leaf: '<path d="M19 3S5 2 4 12c-.6 6 5 9 10 6 5-3 5-15 5-15ZM4 21 15 9"/>',
};
const icon = (name) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.spark}</svg>`;

export function mountCapture(
  container,
  { onSaved = () => {}, onBack = () => {} } = {},
) {
  let phase = "idle";
  let stream = null;
  let recorder = null;
  let chunks = [];
  let blob = null;
  let source = "camera";
  let notes = [];
  let elapsed = 0;
  let started = 0;
  let previewUrl = null;
  let closed = false;
  let saving = false;
  let importing = false;
  let saved = false;
  let changed = false;
  let restored = false;
  let draftTimer = null;
  let captureTitle = "";
  let captureTeacher = "";
  let pendingNote = "";
  let draftWrites = Promise.resolve();

  container.innerHTML = `
    <section class="cap-page" aria-label="Capture a recipe">
      <header class="cap-header">
        <button class="cap-back" type="button" data-cap="back">${icon("arrow")}<span>Back</span></button>
        <span class="cap-private">${icon("lock")} Local recording</span>
      </header>
      <div class="cap-heading">
        <div><span class="cap-eyebrow">PASS IT ON</span><h1>Capture a <em>recipe.</em></h1><p>Let them cook. Keep the little things that make it theirs.</p></div>
      </div>
      <ol class="cap-steps" aria-label="Capture progress">
        <li class="is-current" data-cap-step="1"><span>01</span><div>Frame<small>Hands and pan</small></div></li>
        <li data-cap-step="2"><span>02</span><div>Record<small>Let them tell it</small></div></li>
        <li data-cap-step="3"><span>03</span><div>Keep<small>Make it a memory</small></div></li>
      </ol>
      <div class="cap-layout">
        <div class="cap-main">
          <div class="cap-viewfinder" data-cap="viewfinder">
            <video class="cap-video" data-cap="video" playsinline muted hidden></video>
            <div class="cap-camera-empty" data-cap="empty">
              <span class="cap-corner cap-corner-tl"></span><span class="cap-corner cap-corner-tr"></span><span class="cap-corner cap-corner-bl"></span><span class="cap-corner cap-corner-br"></span>
              <span class="cap-frame-label">THEIR HANDS. THEIR WAY.</span>
              <div class="cap-camera-mark">${icon("camera")}</div>
              <h2>Some things are<br>better shown.</h2>
              <button class="cap-button cap-button-primary" type="button" data-cap="activate">${icon("camera")} Enable camera</button>
              <span class="cap-camera-foot">You decide when recording starts.</span>
            </div>
            <div class="cap-recording-overlay" data-cap="overlay" hidden><span class="cap-live-status" data-cap="live">Camera ready</span><span class="cap-time" data-cap="time">00:00</span></div>
            <div class="cap-live-caption" data-cap="caption" hidden>${icon("mic")} Camera and microphone on</div>
          </div>
          <div class="cap-statusbar"><span data-cap="status">Keep the hands and pan in frame. Make room for their voice.</span><span class="cap-status-dot" data-cap="dot"></span></div>
          <div class="cap-error" role="alert" data-cap="error" hidden></div>
          <div class="cap-message" role="status" aria-live="polite" data-cap="message" hidden></div>
          <div class="cap-controls" data-cap="controls">
            <button class="cap-button cap-button-primary" type="button" data-cap="record" hidden><span class="cap-record-dot"></span> Start recording</button>
            <button class="cap-button cap-button-outline" type="button" data-cap="pause" hidden>${icon("pause")} Pause</button>
            <button class="cap-button cap-button-outline" type="button" data-cap="resume" hidden>${icon("play")} Resume</button>
            <button class="cap-button cap-button-primary" type="button" data-cap="finish" hidden>${icon("stop")} Finish</button>
            <button class="cap-button cap-button-outline" type="button" data-cap="download" hidden>${icon("download")} Download video</button>
            <button class="cap-button cap-button-subtle" type="button" data-cap="again" hidden>${icon("retry")} New take</button>
          </div>
          <div class="cap-import" data-cap="import-row"><span>Already have a video?</span><button type="button" class="cap-import-button" data-cap="import">${icon("upload")} Import a recording</button><input data-cap="file" type="file" accept="video/*,.mp4,.webm,.mov,.m4v" hidden></div>
          <aside class="cap-inspiration"><div class="cap-inspiration-icon">${icon("spark")}</div><div><span class="cap-eyebrow">A QUESTION WORTH KEEPING</span><p>“How do you know when it is ready?”</p><span>Capture their answer, and the gesture that goes with it.</span></div></aside>
        </div>
        <aside class="cap-sidebar">
          <section class="cap-details"><span class="cap-card-kicker">THE STORY BEHIND IT</span><h2>Make it theirs.</h2><p>A recipe, a person, a way of doing things.</p>
            <label class="cap-label" for="cap-title">What are you making?</label><input class="cap-input" id="cap-title" data-cap="title" maxlength="120" placeholder="Mum’s Sunday croquettes">
            <label class="cap-label" for="cap-teacher">Who is showing you?</label><input class="cap-input" id="cap-teacher" data-cap="teacher" maxlength="80" placeholder="Mum, a friend, you…">
            <div class="cap-notes-heading"><h3>${icon("note")} Keep this detail</h3><span data-cap="notes-count">0</span></div>
            <p class="cap-notes-help" data-cap="notes-help">Add a note while recording. We will keep its place in the video.</p>
            <div class="cap-note-list" data-cap="notes"></div>
            <form class="cap-note-form" data-cap="note-form"><label class="cap-sr-only" for="cap-note">Add a detail</label><input id="cap-note" class="cap-input" data-cap="note" maxlength="400" placeholder="That little turn of the spoon…"><button class="cap-note-add" data-cap="note-add" type="submit" aria-label="Add detail">${icon("plus")}</button></form>
            <button class="cap-button cap-button-dark cap-save" data-cap="save" type="button" disabled>${icon("check")} Save recording</button>
            <p class="cap-save-help" data-cap="save-help">Record or import a video to save it.</p>
          </section>
          <div class="cap-privacy-card">${icon("lock")}<div><strong>Nothing is sent while you record.</strong><p>Video and notes stay in this browser. Analysis is a separate step. Download a copy to keep it outside this device.</p></div></div>
        </aside>
      </div>
    </section>`;

  const $ = (key) => container.querySelector(`[data-cap="${key}"]`);
  const nodes = Object.fromEntries(
    [...container.querySelectorAll("[data-cap]")].map((el) => [
      el.dataset.cap,
      el,
    ]),
  );
  const video = nodes.video;
  const timer = setInterval(() => {
    if (!closed && (phase === "recording" || phase === "paused"))
      nodes.time.textContent = clock(duration());
  }, 250);
  function duration() {
    return (
      elapsed +
      (phase === "recording" ? (performance.now() - started) / 1000 : 0)
    );
  }
  function setError(message = "") {
    if (!closed) {
      nodes.error.textContent = message;
      nodes.error.hidden = !message;
    }
  }
  function setMessage(message = "") {
    if (!closed) {
      nodes.message.textContent = message;
      nodes.message.hidden = !message;
    }
  }
  function stopTracks() {
    if (stream) stream.getTracks().forEach((track) => track.stop());
    stream = null;
  }
  function releasePreview() {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = null;
  }

  function snapshot() {
    return {
      id: "current",
      title: captureTitle,
      teacher: captureTeacher,
      pendingNote,
      blob,
      duration: elapsed,
      notes: notes.map((note) => ({ ...note })),
      source,
      type: "video",
      mimeType: blob?.type || "",
      updatedAt: new Date().toISOString(),
    };
  }
  function persistDraft() {
    const draft = snapshot();
    if (!draft || saved) return Promise.resolve();
    sessionDraft = draft;
    draftWrites = draftWrites
      .catch(() => {})
      .then(() => transact("drafts", (store) => store.put(draft), true));
    draftWrites.catch(() => {
      if (!closed)
        setError(
          blob
            ? "The draft could not be saved in this browser. Your video is still here; download a copy to keep it."
            : "The draft could not be saved in this browser. Your details are still on screen; copy them before closing this tab.",
        );
    });
    return draftWrites;
  }
  function scheduleDraft() {
    clearTimeout(draftTimer);
    draftTimer = setTimeout(() => {
      draftTimer = null;
      persistDraft().catch(() => {});
    }, 350);
  }

  function updateUI() {
    if (closed) return;
    const hasVideo = Boolean(stream || blob);
    nodes.empty.hidden = hasVideo;
    video.hidden = !hasVideo;
    nodes.activate.disabled = phase === "requesting";
    nodes.activate.innerHTML = `${icon("camera")} ${phase === "requesting" ? "Opening camera…" : "Enable camera"}`;
    nodes.record.hidden = phase !== "ready";
    nodes.pause.hidden = phase !== "recording";
    nodes.resume.hidden = phase !== "paused";
    nodes.finish.hidden = !["recording", "paused", "finishing"].includes(phase);
    nodes.finish.disabled = phase === "finishing";
    nodes.download.hidden = !blob;
    nodes.again.hidden = !blob;
    nodes.again.disabled = saving || importing;
    nodes.back.disabled = saving;
    nodes.title.disabled = saving;
    nodes.teacher.disabled = saving;
    nodes.note.disabled = saving;
    nodes["note-add"].disabled = saving;
    nodes.notes.querySelectorAll(".cap-note-delete").forEach((button) => {
      button.disabled = saving;
    });
    nodes.save.disabled = !blob || saving || importing;
    nodes.save.innerHTML = `${icon("check")} ${saving ? "Saving…" : "Save recording"}`;
    nodes["save-help"].textContent = blob
      ? `${clock(elapsed)} recorded · ${(blob.size / (1024 * 1024)).toFixed(1)} MB on this device`
      : "Record or import a video to save it.";
    nodes["import-row"].hidden = ![
      "idle",
      "requesting",
      "ready",
      "ended",
    ].includes(phase);
    nodes.import.disabled = phase === "requesting" || saving || importing;
    nodes.overlay.hidden = !stream;
    nodes.caption.hidden = !stream;
    nodes.live.textContent =
      phase === "recording"
        ? "Recording"
        : phase === "paused"
          ? "Paused"
          : "Camera ready";
    nodes.live.classList.toggle("is-recording", phase === "recording");
    nodes.live.classList.toggle("is-paused", phase === "paused");
    nodes.time.textContent = clock(duration());
    nodes.dot.classList.toggle("is-live", Boolean(stream));
    nodes.status.textContent = {
      idle: "Keep the hands and pan in frame. Make room for their voice.",
      requesting: "Allow camera and microphone access to set up your shot.",
      ready: "Ready when you are. Press Start recording to begin.",
      recording: "Recording. Add a detail below whenever something stands out.",
      paused: "Take your time. The recording is paused.",
      finishing: "Preparing your video…",
      ended: "Your recording is ready. Review it, then save it.",
    }[phase];
    nodes.viewfinder.classList.toggle("has-video", hasVideo);
    const step = blob
      ? 3
      : ["recording", "paused", "finishing"].includes(phase)
        ? 2
        : 1;
    container.querySelectorAll("[data-cap-step]").forEach((el) => {
      el.classList.toggle("is-current", Number(el.dataset.capStep) === step);
      el.classList.toggle("is-done", Number(el.dataset.capStep) < step);
    });
    nodes["notes-help"].textContent = blob
      ? "Play the video and add a note at the moment you want to remember."
      : "Add a note while recording. We will keep its place in the video.";
  }

  function renderNotes() {
    if (closed) return;
    nodes.notes.replaceChildren();
    nodes["notes-count"].textContent = String(notes.length);
    for (const note of notes) {
      const row = document.createElement("div");
      row.className = "cap-note-row";
      const time = document.createElement("button");
      time.type = "button";
      time.className = "cap-note-time";
      time.textContent = clock(note.time);
      time.title = "Jump to this moment";
      time.disabled = !blob;
      time.addEventListener("click", () => {
        if (blob) {
          video.currentTime = note.time;
          video.focus();
        }
      });
      const text = document.createElement("span");
      text.textContent = note.text;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "cap-note-delete";
      remove.innerHTML = icon("close");
      remove.setAttribute("aria-label", "Remove note");
      remove.disabled = saving;
      remove.addEventListener("click", () => {
        if (saving) return;
        notes = notes.filter((item) => item.id !== note.id);
        changed = true;
        renderNotes();
        scheduleDraft();
      });
      row.append(time, text, remove);
      nodes.notes.append(row);
    }
  }

  function setPlayback() {
    if (closed || !blob) return;
    video.pause();
    video.srcObject = null;
    releasePreview();
    previewUrl = URL.createObjectURL(blob);
    video.src = previewUrl;
    video.controls = true;
    video.muted = false;
    video.loop = false;
    video.load();
    video.onerror = () =>
      setError(
        "This browser cannot play this format. The file is still available to save or download.",
      );
  }

  async function activate() {
    changed = true;
    setError();
    setMessage();
    if (!navigator.mediaDevices?.getUserMedia || !globalThis.MediaRecorder) {
      setError(
        "Recording needs a supported browser on HTTPS or localhost. You can also import an existing video.",
      );
      return;
    }
    phase = "requesting";
    updateUI();
    try {
      const incoming = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: "environment",
          width: { ideal: 1920 },
          height: { ideal: 1080 },
        },
        audio: true,
      });
      if (closed) {
        incoming.getTracks().forEach((track) => track.stop());
        return;
      }
      stream = incoming;
      video.onerror = null;
      video.removeAttribute("src");
      video.srcObject = stream;
      video.muted = true;
      video.controls = false;
      phase = "ready";
      updateUI();
      await video.play().catch(() => {});
      stream.getTracks().forEach((track) =>
        track.addEventListener("ended", () => {
          if (closed || !stream) return;
          setError(
            "The camera or microphone disconnected. We are keeping the footage recorded so far.",
          );
          if (recorder && recorder.state !== "inactive") finish();
          else {
            stopTracks();
            phase = blob ? "ended" : "idle";
            updateUI();
          }
        }),
      );
    } catch (error) {
      if (closed) return;
      phase = "idle";
      setError(
        error.name === "NotAllowedError" || error.name === "SecurityError"
          ? "Camera or microphone access was not allowed. Enable them in browser permissions, or import a video."
          : error.name === "NotFoundError"
            ? "No camera and microphone are available. You can import a recording instead."
            : error.name === "NotReadableError"
              ? "Another app is using the camera or microphone. Close it and try again."
              : "The camera could not be opened. Try again or import a video.",
      );
      updateUI();
    }
  }

  function beginRecording() {
    if (!stream || phase !== "ready") return;
    setError();
    setMessage();
    chunks = [];
    elapsed = 0;
    source = "camera";
    saved = false;
    try {
      const formats = [
        "video/webm;codecs=vp9,opus",
        "video/webm;codecs=vp8,opus",
        "video/webm",
        "video/mp4",
      ];
      const mimeType = formats.find((type) =>
        MediaRecorder.isTypeSupported(type),
      );
      recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      const activeRecorder = recorder;
      activeRecorder.ondataavailable = (event) => {
        if (event.data?.size) chunks.push(event.data);
      };
      activeRecorder.onerror = () => {
        setError(
          "Recording was interrupted. We are keeping any footage captured so far.",
        );
        if (activeRecorder.state !== "inactive") finish();
      };
      activeRecorder.onstop = () => {
        if (phase === "recording") elapsed = duration();
        phase = "ended";
        stopTracks();
        recorder = null;
        if (chunks.length) {
          blob = new Blob(chunks, {
            type: activeRecorder.mimeType || chunks[0].type || "video/webm",
          });
          chunks = [];
          persistDraft().catch(() => {});
          if (!closed) {
            setPlayback();
            renderNotes();
            setMessage(
              "Draft ready. Add the details you want to remember, then save.",
            );
          }
        } else {
          phase = "idle";
          setError(
            "No video was captured. Your notes are still here for the next take.",
          );
        }
        updateUI();
      };
      recorder.start(1000);
      started = performance.now();
      phase = "recording";
      changed = true;
      updateUI();
    } catch (error) {
      recorder = null;
      setError(
        "This browser cannot record the camera format. Try importing a video. Your notes are still here.",
      );
      updateUI();
    }
  }

  function finish() {
    if (!recorder || recorder.state === "inactive") return;
    elapsed = duration();
    phase = "finishing";
    try {
      recorder.stop();
    } catch {
      setError("The recording could not be finished. Please try again.");
      phase = recorder.state === "paused" ? "paused" : "recording";
      started = performance.now();
    }
    updateUI();
  }

  async function importVideo(file) {
    if (!file || importing) return;
    if (
      !file.type.startsWith("video/") &&
      !/\.(mp4|webm|mov|m4v|ogv|ogg|avi|mkv)$/i.test(file.name)
    ) {
      setError("Choose a video file such as MP4, MOV or WebM.");
      return;
    }
    if (
      blob &&
      !confirm(
        "Replace the video in this draft? To keep the current video, cancel and download it first.",
      )
    )
      return;
    const replacingVideo = Boolean(blob);
    changed = true;
    saved = false;
    importing = true;
    setError();
    setMessage();
    stopTracks();
    blob = file;
    source = "import";
    elapsed = 0;
    if (replacingVideo) notes = [];
    if (!captureTitle) {
      captureTitle = file.name.replace(/\.[^.]+$/, "").replace(/[_-]/g, " ");
      nodes.title.value = captureTitle;
    }
    phase = "ended";
    setPlayback();
    renderNotes();
    updateUI();
    await new Promise((resolve) => {
      let settled = false;
      const done = () => {
        if (settled) return;
        settled = true;
        clearTimeout(fallback);
        video.removeEventListener("loadedmetadata", metadata);
        video.removeEventListener("error", done);
        resolve();
      };
      const metadata = () => {
        if (Number.isFinite(video.duration)) elapsed = video.duration;
        done();
      };
      const fallback = setTimeout(done, 6000);
      video.addEventListener("loadedmetadata", metadata);
      video.addEventListener("error", done);
      if (video.readyState >= 1) metadata();
    });
    importing = false;
    if (closed) {
      persistDraft().catch(() => {});
      return;
    }
    updateUI();
    persistDraft().catch(() => {});
    setMessage(
      "Video imported. Play it back and note the details worth keeping.",
    );
  }

  async function saveRecording() {
    if (!blob || saving || importing) return;
    addNote();
    saving = true;
    setError();
    clearTimeout(draftTimer);
    draftTimer = null;
    updateUI();
    const recording = {
      id:
        globalThis.crypto?.randomUUID?.() ||
        `recording-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
      title: captureTitle.trim() || "A kitchen lesson",
      teacher: captureTeacher.trim() || "Someone who knows",
      createdAt: new Date().toISOString(),
      duration: elapsed,
      notes: notes.map((note) => ({ ...note })),
      type: "video",
      source,
      mimeType: blob.type,
      size: blob.size,
      blob,
    };
    try {
      await transact("recordings", (store) => store.put(recording), true);
      saved = true;
      changed = false;
      sessionDraft = null;
      await draftWrites.catch(() => {});
      await transact("drafts", (store) => store.delete("current"), true).catch(
        () => {},
      );
      saving = false;
      updateUI();
      setMessage("Recording saved on this device.");
      onSaved(recording);
    } catch (error) {
      saving = false;
      updateUI();
      setError(
        error?.name === "QuotaExceededError"
          ? "There is not enough browser storage. Your video is still here; download it before leaving."
          : "The recording could not be saved. Your draft is still here and you can download the video.",
      );
    }
  }

  function downloadVideo() {
    if (!blob) return;
    const url = URL.createObjectURL(blob);
    const ext =
      blob instanceof File && /\.[a-z0-9]+$/i.test(blob.name)
        ? blob.name.match(/\.[a-z0-9]+$/i)[0]
        : blob.type.includes("mp4")
          ? ".mp4"
          : blob.type.includes("quicktime")
            ? ".mov"
            : ".webm";
    const link = document.createElement("a");
    link.href = url;
    link.download = `${(captureTitle.trim() || "my-kitchen-story").replace(/[^\p{L}\p{N}_ -]/gu, "").slice(0, 80) || "my-kitchen-story"}${ext}`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  }

  async function reset() {
    if (
      blob &&
      !confirm(
        "Start a new take? This will replace the current draft. Cancel and download it first if you want to keep it.",
      )
    )
      return;
    if (saving) return;
    clearTimeout(draftTimer);
    draftTimer = null;
    blob = null;
    notes = [];
    elapsed = 0;
    saved = false;
    source = "camera";
    sessionDraft = null;
    releasePreview();
    video.pause();
    video.removeAttribute("src");
    video.load();
    phase = "idle";
    await draftWrites.catch(() => {});
    await transact("drafts", (store) => store.delete("current"), true).catch(
      () => {},
    );
    setError();
    setMessage();
    renderNotes();
    updateUI();
    persistDraft().catch(() => {});
  }

  nodes.activate.addEventListener("click", activate);
  nodes.record.addEventListener("click", beginRecording);
  nodes.pause.addEventListener("click", () => {
    if (!recorder || recorder.state !== "recording") return;
    try {
      recorder.pause();
      elapsed = duration();
      phase = "paused";
      updateUI();
    } catch {
      setError("The recording could not be paused. It is still running.");
    }
  });
  nodes.resume.addEventListener("click", () => {
    if (!recorder || recorder.state !== "paused") return;
    try {
      recorder.resume();
      started = performance.now();
      phase = "recording";
      updateUI();
    } catch {
      setError(
        "The recording could not be resumed. Finish the take to keep it.",
      );
    }
  });
  nodes.finish.addEventListener("click", finish);
  nodes.import.addEventListener("click", () => {
    nodes.file.value = "";
    nodes.file.click();
  });
  nodes.file.addEventListener("change", () =>
    importVideo(nodes.file.files?.[0]),
  );
  nodes.save.addEventListener("click", saveRecording);
  nodes.download.addEventListener("click", downloadVideo);
  nodes.again.addEventListener("click", reset);
  nodes.title.addEventListener("input", () => {
    if (saving) return;
    captureTitle = nodes.title.value;
    changed = true;
    scheduleDraft();
  });
  nodes.teacher.addEventListener("input", () => {
    if (saving) return;
    captureTeacher = nodes.teacher.value;
    changed = true;
    scheduleDraft();
  });
  nodes.note.addEventListener("input", () => {
    if (saving) return;
    pendingNote = nodes.note.value;
    changed = true;
    scheduleDraft();
  });
  function addNote() {
    if (saving) return;
    const text = nodes.note.value.trim();
    if (!text) return;
    notes.push({
      id: globalThis.crypto?.randomUUID?.() || `note-${Date.now()}`,
      time: blob
        ? Math.max(
            0,
            Number.isFinite(video.currentTime) ? video.currentTime : 0,
          )
        : duration(),
      text,
    });
    pendingNote = "";
    nodes.note.value = "";
    changed = true;
    renderNotes();
    scheduleDraft();
  }
  nodes["note-form"].addEventListener("submit", (event) => {
    event.preventDefault();
    if (saving) return;
    addNote();
    nodes.note.focus();
  });
  nodes.back.addEventListener("click", () => {
    if (saving) return;
    if (
      ["recording", "paused", "finishing"].includes(phase) &&
      !confirm(
        "Leave this recording? We will finish the take and keep a draft on this device.",
      )
    )
      return;
    onBack();
  });
  const beforeUnload = (event) => {
    if (
      ["recording", "paused", "finishing"].includes(phase) ||
      (!saved && (blob || draftTimer !== null))
    ) {
      event.preventDefault();
      event.returnValue = "";
    }
  };
  window.addEventListener("beforeunload", beforeUnload);

  async function restoreDraft() {
    try {
      const draft =
        sessionDraft ||
        (await transact("drafts", (store) => store.get("current")));
      if (!draft || changed || closed) return;
      restored = true;
      blob = draft.blob || null;
      captureTitle = draft.title || "";
      captureTeacher = draft.teacher || "";
      pendingNote = draft.pendingNote || "";
      elapsed = blob ? draft.duration || 0 : 0;
      notes = draft.notes || [];
      source = draft.source || "camera";
      nodes.title.value = captureTitle;
      nodes.teacher.value = captureTeacher;
      nodes.note.value = pendingNote;
      phase = blob ? "ended" : "idle";
      setPlayback();
      renderNotes();
      updateUI();
      setMessage("Your last draft is back. Pick up where you left off.");
    } catch {
      /* Camera and import remain available when browser storage is disabled. */
    }
  }
  restoreDraft();
  updateUI();

  return function cleanup() {
    if (closed) return;
    clearInterval(timer);
    clearTimeout(draftTimer);
    draftTimer = null;
    window.removeEventListener("beforeunload", beforeUnload);
    if (recorder && recorder.state !== "inactive") finish();
    closed = true;
    stopTracks();
    video.pause();
    video.srcObject = null;
    video.removeAttribute("src");
    video.load();
    releasePreview();
    if (!saved && (changed || restored || blob)) persistDraft().catch(() => {});
  };
}
