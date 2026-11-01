import test from "node:test";
import assert from "node:assert/strict";
import { causalBurst, LatestOnlySender, VoiceSegmenter, pcmWav, resamplePCM, frameDifference, RealtimePCMChannel, settleAudioAdmission, mountLive, validateReplayFile } from "../../dist/live.js";

const tick = () => new Promise((resolve) => setImmediate(resolve));

test("causal bursts retain ordered motion samples, exclude the future and bound context", () => {
  const samples = [0, 1, 2, 3, 4, 5, 6, 7, 8, 100].map((source_seconds) => ({ source_seconds, jpeg_base64: String(source_seconds) }));
  assert.deepEqual(causalBurst(samples.reverse(), 8).map((sample) => sample.source_seconds), [2, 5, 8]);
  assert.deepEqual(causalBurst(samples, 8, { limit: 1 }).map((sample) => sample.source_seconds), [8]);
  assert.deepEqual(causalBurst(samples, NaN), []);
  assert.deepEqual(causalBurst(samples, 8, { limit: 0 }), []);
});

test("replay files are validated before creating a carrier session or uploading", () => {
  assert.throws(() => validateReplayFile(null), /Elige el vídeo/);
  assert.throws(() => validateReplayFile({ size: 0, type: "video/mp4" }), /tener contenido/);
  assert.throws(() => validateReplayFile({ size: 150 * 1024 * 1024 + 1, type: "video/mp4" }), /150 MiB/);
  assert.throws(() => validateReplayFile({ size: 20, type: "text/plain" }), /archivo de vídeo/);
  const file = { size: 20, type: "video/mp4" }; assert.equal(validateReplayFile(file), file);
});

test("latest-only transport serializes uploads and replaces a backlog with the newest observation", async () => {
  let release, simultaneous = 0, maximum = 0;
  const calls = [], gate = new Promise((resolve) => { release = resolve; });
  const sender = new LatestOnlySender(async (value) => {
    simultaneous++; maximum = Math.max(maximum, simultaneous); calls.push(value);
    if (value === 1) await gate;
    simultaneous--;
  });
  sender.offer(1); sender.offer(2); sender.offer(3); sender.offer(4);
  assert.equal(sender.pending, 4); release(); await tick();
  assert.deepEqual(calls, [1, 4]); assert.equal(maximum, 1);
  sender.close(); sender.offer(5); await tick(); assert.deepEqual(calls, [1, 4]);
});

test("a stopped sender drops pending camera work and handles a failed in-flight request", async () => {
  let release; const seen = [], errors = [];
  const gate = new Promise((resolve) => { release = resolve; });
  const sender = new LatestOnlySender(async (value) => { seen.push(value); await gate; throw new Error("network"); }, (error) => errors.push(error));
  sender.offer("active"); sender.offer("obsolete"); sender.close(); release(); await tick();
  assert.deepEqual(seen, ["active"]); assert.deepEqual(errors, []);
});

test("the speech gate stays held until WAV admission and is released after failures", async () => {
  let admit, held = true; const order = [];
  const receipt = new Promise((resolve) => { admit = resolve; });
  const pending = settleAudioAdmission(async () => { order.push("upload"); await receipt; order.push("accepted"); return 202; }, () => { held = false; order.push("release"); });
  await tick(); assert.equal(held, true); assert.deepEqual(order, ["upload"]);
  admit(); assert.equal(await pending, 202); assert.equal(held, false);
  assert.deepEqual(order, ["upload", "accepted", "release"]);
  held = true;
  await assert.rejects(settleAudioAdmission(async () => { throw new Error("upload rejected"); }, () => { held = false; }), /upload rejected/);
  assert.equal(held, false);
});

test("PCM WAV contains actual mono 24 kHz samples and preserves utterance duration", async () => {
  const source = new Float32Array(48000).map((_, index) => index % 2 ? 0.5 : -0.5);
  const blob = pcmWav(source, 48000); const buffer = await blob.arrayBuffer(), view = new DataView(buffer);
  assert.equal(blob.type, "audio/wav"); assert.equal(view.getUint16(22, true), 1); assert.equal(view.getUint32(24, true), 24000);
  assert.equal(view.getUint16(34, true), 16); assert.equal(view.getUint32(40, true), 48000);
  assert.equal((buffer.byteLength - 44) / 2 / 24000, 1);
  assert.equal(resamplePCM(new Float32Array(44100), 44100).length, 24000);
  assert.throws(() => resamplePCM(source, Infinity), /sample rate/);
});

test("VAD waits for an utterance boundary and quarantines the agent voice", () => {
  const events = [], starts = [], vad = new VoiceSegmenter({ onStart: (event) => starts.push(event), onEnd: (event) => events.push(event) });
  const voice = new Float32Array(2400).fill(0.2), silence = new Float32Array(2400);
  for (let index = 0; index < 8; index++) vad.push(voice, 24000, index / 10, { suppressed: true });
  assert.equal(starts.length, 0); assert.equal(events.length, 0);
  for (let index = 0; index < 6; index++) vad.push(voice, 24000, 10 + index / 10);
  assert.equal(starts.length, 1); assert.equal(events.length, 0);
  for (let index = 0; index < 8; index++) vad.push(silence, 24000, 10.6 + index / 10);
  assert.equal(events.length, 1); assert.equal(events[0].discarded, undefined);
  assert.equal(events[0].sampleRate, 24000); assert.ok(events[0].duration_seconds >= 1.2);
  assert.ok(events[0].start_seconds < events[0].end_seconds);
});

test("frame change detection is bounded and does not infer any physical quantity", () => {
  assert.equal(frameDifference(null, new Uint8Array([1, 2])), 1);
  assert.equal(frameDifference(new Uint8Array([5, 10]), new Uint8Array([5, 10])), 0);
  assert.equal(frameDifference(new Uint8Array([0, 0]), new Uint8Array([255, 255])), 1);
});

test("realtime audio waits for ACK, sends real 24 kHz PCM and zeros during TTS quarantine", () => {
  const previousSocket = globalThis.WebSocket, previousLocation = globalThis.location;
  class FakeSocket {
    static OPEN = 1;
    constructor(url) { this.url = url; this.readyState = 1; this.bufferedAmount = 0; this.sent = []; this.listeners = {}; FakeSocket.instance = this; }
    send(data) { this.sent.push(data); }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    close() { this.readyState = 3; this.onclose?.(); this.listeners.close?.(); }
  }
  globalThis.WebSocket = FakeSocket; globalThis.location = { href: "http://127.0.0.1:8017/#live", protocol: "http:" };
  try {
    const channel = new RealtimePCMChannel({ runId: "run", origin: "microphone", speakerName: "David", speakerRole: "observer", clock: () => ({ source_seconds: 40, scene_epoch: 2, answer_to: "new-question" }) });
    const socket = FakeSocket.instance; socket.onopen();
    assert.equal(JSON.parse(socket.sent[0]).sample_rate, 24000);
    assert.equal(channel.send(new Float32Array(4800).fill(0.2), 48000), false);
    socket.onmessage({ data: JSON.stringify({ type: "ready" }) });
    assert.equal(channel.send(new Float32Array(4800).fill(0.2), 48000), true);
    const pcm = socket.sent.at(-1); assert.equal(pcm.byteLength, 4800); assert.ok(new DataView(pcm).getInt16(0, true) > 0);
    const onset = { utterance_id: "utterance-one", speech_started_at: 123.2, capture_started_at: 123, answer_to: "spoken-at-onset", scene_epoch: 1 };
    channel.send(new Float32Array(4800).fill(0.2), 48000, { metadata: onset });
    assert.deepEqual(JSON.parse(socket.sent.at(-2)), { type: "clock", source_seconds: 40, ...onset });
    channel.send(new Float32Array(4800), 48000, { metadata: { answer_to: null } });
    assert.equal(JSON.parse(socket.sent.at(-2)).answer_to, null, "silence does not inherit a newly delivered question");
    channel.send(new Float32Array(4800).fill(0.2), 48000, { silence: true });
    assert.equal(new Int16Array(socket.sent.at(-1)).some((value) => value !== 0), false);
    channel.close(); assert.equal(JSON.parse(socket.sent.at(-1)).type, "end"); socket.close();
  } finally { globalThis.WebSocket = previousSocket; globalThis.location = previousLocation; }
});

test("a remote terminal snapshot stops camera and SSE without echoing another stop; unresolved doubts clear the active question", async () => {
  const saved = new Map(["document", "navigator", "fetch", "EventSource", "MediaRecorder"].map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  class Node {
    constructor() { this.listeners = {}; this.value = ""; this.checked = false; this.children = []; this.readyState = 0; this.currentTime = 0; }
    addEventListener(type, callback) { this.listeners[type] = callback; }
    removeEventListener(type) { delete this.listeners[type]; }
    querySelectorAll() { return []; }
    getContext() { return {}; }
    play() { this.paused = false; return Promise.resolve(); }
    pause() { this.paused = true; }
    removeAttribute() {}
    load() {}
  }
  const nodes = new Map(), node = (name) => { if (!nodes.has(name)) nodes.set(name, new Node()); return nodes.get(name); };
  const container = { set innerHTML(value) {}, querySelector(selector) { return node(selector.match(/"([^"]+)"/)[1]); } };
  let stopped = 0, cleanup; const requests = [];
  const stream = { getTracks: () => [{ stop: () => { stopped++; } }] };
  const snapshot = { id: "run", session_id: "lesson", status: "running", cards: [], events: [] };
  class FakeEvents {
    constructor() { this.listeners = {}; FakeEvents.instance = this; }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    close() { this.closed = true; }
    emit(data) { this.listeners.snapshot({ data: JSON.stringify(data) }); }
  }
  try {
    Object.defineProperty(globalThis, "document", { configurable: true, value: { createElement: () => new Node() } });
    Object.defineProperty(globalThis, "navigator", { configurable: true, value: { mediaDevices: { getUserMedia: async () => stream } } });
    globalThis.EventSource = FakeEvents; globalThis.MediaRecorder = undefined;
    globalThis.fetch = async (url, options) => { requests.push([url, options]); return { ok: true, json: async () => snapshot }; };
    cleanup = mountLive(container);
    node("source").value = "phone"; node("teacher").value = "Papá"; node("speaker").value = "David"; node("role").value = "observer"; node("consent").checked = true;
    await node("start").listeners.click();
    const events = FakeEvents.instance;
    events.emit({ ...snapshot, question: { id: "q1", status: "spoken", text: "¿Qué señal buscas?" } });
    assert.equal(node("question").textContent, "¿Qué señal buscas?");
    events.emit({ ...snapshot, question: { id: "q1", status: "unresolved", text: "¿Qué señal buscas?" } });
    assert.equal(node("repeat").disabled, true); assert.equal(node("postpone").disabled, true);
    assert.match(node("question").textContent, /sigue sin una explicación/);
    events.emit({ ...snapshot, status: "interrupted" }); await tick();
    assert.equal(stopped, 1); assert.equal(events.closed, true); assert.equal(node("video").paused, true);
    assert.equal(node("send-answer").disabled, true); assert.match(node("status").textContent, /captura detenida/);
    assert.equal(requests.filter(([url]) => url.endsWith("/control")).length, 0, "server stop is not recursively reported back");
    cleanup(); cleanup = null;
  } finally {
    cleanup?.();
    for (const [key, descriptor] of saved) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; }
  }
});

test("uploaded replay is isolated, speaks after the quiet deadline without another snapshot, and only pauses when requested", async () => {
  for (const pauseReplay of [false, true]) {
    const keys = ["document", "fetch", "EventSource", "MediaRecorder", "WebSocket", "location", "Audio", "performance", "setTimeout", "clearTimeout"];
    const saved = new Map(keys.map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
    const nodes = new Map(), timers = new Map(), requests = []; let now = 0, nextTimer = 0, cleanup;
    class Node {
      constructor() { this.listeners = {}; this.value = ""; this.checked = false; this.children = []; this.readyState = 0; this.currentTime = 0; this.paused = true; }
      addEventListener(name, callback) { this.listeners[name] = callback; }
      removeEventListener(name) { delete this.listeners[name]; }
      querySelectorAll() { return []; }
      getContext() { return {}; }
      play() { this.paused = false; this.listeners.play?.(); return Promise.resolve(); }
      pause() { this.paused = true; this.listeners.pause?.(); }
      removeAttribute() {}
      load() {}
    }
    const node = (name) => { if (!nodes.has(name)) nodes.set(name, new Node()); return nodes.get(name); };
    const container = { set innerHTML(value) {}, querySelector(selector) { return node(selector.match(/"([^"]+)"/)[1]); } };
    let state = { id: "fresh-run", session_id: "fresh-run", status: "running", cards: [], events: [] };
    class Events {
      constructor() { this.listeners = {}; Events.instance = this; }
      addEventListener(name, callback) { this.listeners[name] = callback; }
      close() {}
      emit(snapshot) { state = snapshot; this.listeners.snapshot({ data: JSON.stringify(snapshot) }); }
    }
    class Voice {
      play() { this.onplaying?.(); return Promise.resolve(); }
      pause() {}
    }
    const install = (key, value) => Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
    try {
      install("document", { createElement: () => new Node() }); install("EventSource", Events); install("MediaRecorder", undefined); install("WebSocket", undefined);
      install("location", { href: "http://localhost/#teach", protocol: "http:" }); install("Audio", Voice); install("performance", { now: () => now });
      install("setTimeout", (callback, delay) => { const id = ++nextTimer; timers.set(id, { callback, due: now + delay }); return id; });
      install("clearTimeout", (id) => timers.delete(id));
      install("fetch", async (url, options) => {
        requests.push({ url, body: options.body }); let result = state;
        if (url === "/api/sessions") result = { id: "carrier-only" };
        else if (url.endsWith("/evidence")) result = { id: "uploaded-original" };
        else if (url.endsWith("/control") && JSON.parse(options.body).action === "spoken") state = result = { ...state, question: { ...state.question, status: "spoken" } };
        return { ok: true, json: async () => result, blob: async () => new Blob(["voice"], { type: "audio/wav" }) };
      });
      cleanup = mountLive(container, { demo: { original_evidence_id: "family-original" } });
      node("source").value = "file"; node("teacher").value = "Otra persona"; node("speaker").value = "David"; node("role").value = "observer";
      node("consent").checked = true; node("pause-replay").checked = pauseReplay;
      node("file").files = [new File(["video"], "second-video.mp4", { type: "video/mp4" })];
      await node("start").listeners.click(); Events.instance.onopen();
      const create = JSON.parse(requests.find(({ url }) => url === "/api/live").body);
      assert.equal(create.source_evidence_id, "uploaded-original"); assert.equal(create.source_kind, "replay");
      assert.equal(create.lesson_session_id, undefined, "source carrier summaries are never inherited");
      assert.ok(requests.find(({ url }) => url === "/api/sessions/carrier-only/evidence"));
      assert.match(node("resource-html").href, /fresh-run\/learning-resource\?format=html&download=true/);
      Events.instance.emit({ ...state, question: { id: "q1", status: "pending", text: "¿Qué señal te orienta?" } });
      assert.equal(requests.filter(({ url }) => url.endsWith("/voice")).length, 0);
      const delayed = [...timers.entries()].find(([, timer]) => timer.due === 700); assert.ok(delayed, "quiet gate schedules its own retry");
      now = 701; timers.delete(delayed[0]); delayed[1].callback(); await tick();
      assert.equal(requests.filter(({ url }) => url.endsWith("/voice")).length, 1);
      assert.equal(node("video").paused, pauseReplay, "the continuous replay keeps advancing during the actual voice start");
      assert.ok(requests.some(({ url, body }) => url.endsWith("/control") && JSON.parse(body).action === "spoken"));
      cleanup(); cleanup = null;
      assert.equal(timers.size, 0, "cleanup cancels pending quiet/network/event timers");
    } finally {
      cleanup?.();
      for (const [key, descriptor] of saved) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; }
    }
  }
});
