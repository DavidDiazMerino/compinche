const TERMINAL_QUESTION = new Set(["answered", "deferred", "postponed", "cancelled", "expired", "stale", "unresolved", "delivered", "superseded"]);
const SPEAKABLE_QUESTION = new Set(["pending", "ready"]);
const esc = (value = "") => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
const clock = (seconds) => `${Math.floor(Math.max(0, seconds || 0) / 60).toString().padStart(2, "0")}:${Math.floor(Math.max(0, seconds || 0) % 60).toString().padStart(2, "0")}`;
const identifier = () => globalThis.crypto?.randomUUID?.() || `live-${Date.now()}-${Math.random().toString(36).slice(2)}`;
export function validateReplayFile(file) {
  if (!file) throw new Error("Elige el vídeo que quieres probar.");
  if (file.size <= 0 || file.size > 150 * 1024 * 1024) throw new Error("El vídeo debe tener contenido y ocupar como máximo 150 MiB.");
  if (file.type && !file.type.startsWith("video/")) throw new Error("Selecciona un archivo de vídeo.");
  return file;
}

/** Select an ordered temporal burst without using any sample after the source watermark. */
export function causalBurst(samples, watermark, { limit = 3, windowSeconds = 6 } = {}) {
  if (!Number.isFinite(watermark) || limit <= 0) return [];
  const eligible = samples.filter((sample) => Number.isFinite(sample.source_seconds) && sample.source_seconds <= watermark && sample.source_seconds >= Math.max(0, watermark - windowSeconds)).sort((a, b) => a.source_seconds - b.source_seconds);
  if (eligible.length <= limit) return eligible;
  if (limit === 1) return eligible.slice(-1);
  return Array.from({ length: limit }, (_, index) => eligible[Math.round(index * (eligible.length - 1) / (limit - 1))]);
}
export function frameDifference(previous, current) {
  if (!previous || previous.length !== current?.length || !current.length) return 1;
  let sum = 0;
  for (let i = 0; i < current.length; i++) sum += Math.abs(current[i] - previous[i]);
  return sum / current.length / 255;
}
export function pcmRms(samples) {
  if (!samples?.length) return 0;
  let squared = 0;
  for (const sample of samples) squared += sample * sample;
  return Math.sqrt(squared / samples.length);
}
export function resamplePCM(samples, fromRate, toRate = 24000) {
  if (!Number.isFinite(fromRate) || !Number.isFinite(toRate) || !(fromRate > 0) || !(toRate > 0)) throw new Error("Invalid PCM sample rate");
  if (fromRate === toRate) return samples;
  const result = new Float32Array(Math.floor(samples.length * toRate / fromRate));
  const step = fromRate / toRate;
  for (let i = 0; i < result.length; i++) {
    const position = i * step, left = Math.floor(position), fraction = position - left;
    result[i] = (samples[left] || 0) * (1 - fraction) + (samples[Math.min(left + 1, samples.length - 1)] || 0) * fraction;
  }
  return result;
}
export function pcmWav(samples, sampleRate, targetRate = 24000) {
  const pcm = resamplePCM(samples, sampleRate, targetRate);
  const bytes = new ArrayBuffer(44 + pcm.length * 2), view = new DataView(bytes);
  const ascii = (offset, value) => { for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i)); };
  ascii(0, "RIFF"); view.setUint32(4, bytes.byteLength - 8, true); ascii(8, "WAVE"); ascii(12, "fmt "); view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); view.setUint16(22, 1, true); view.setUint32(24, targetRate, true); view.setUint32(28, targetRate * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); ascii(36, "data"); view.setUint32(40, pcm.length * 2, true);
  for (let i = 0; i < pcm.length; i++) view.setInt16(44 + i * 2, Math.round(Math.max(-1, Math.min(1, pcm[i])) * (pcm[i] < 0 ? 32768 : 32767)), true);
  return new Blob([bytes], { type: "audio/wav" });
}
/** Exactly one transport request at a time, with a bounded pending slot containing the newest burst. */
export class LatestOnlySender {
  constructor(send, onError = () => {}) { this.send = send; this.onError = onError; this.pending = null; this.running = false; this.closed = false; }
  offer(value) { if (this.closed) return; this.pending = value; if (!this.running) this.drain(); }
  async drain() {
    this.running = true;
    while (!this.closed && this.pending) {
      const value = this.pending; this.pending = null;
      try { await this.send(value); } catch (error) { if (!this.closed) this.onError(error); }
    }
    this.running = false;
  }
  close() { this.closed = true; this.pending = null; }
}
/** Keep the speech gate closed until the server has admitted the audio to ASR. */
export async function settleAudioAdmission(send, release) {
  try { return await send(); } finally { release(); }
}
/** Local energy endpointing. It is a speech candidate detector, never speaker identification. */
export class VoiceSegmenter {
  constructor({ onStart = () => {}, onEnd = () => {}, silenceSeconds = 0.75, maxSeconds = 12, minimumSeconds = 0.4 } = {}) {
    Object.assign(this, { onStart, onEnd, silenceSeconds, maxSeconds, minimumSeconds }); this.reset(); this.noise = 0.004;
  }
  reset() { this.active = false; this.pending = []; this.preRoll = []; this.duration = 0; this.silence = 0; this.hot = 0; this.startedAt = null; }
  push(samples, sampleRate, sourceSeconds, { suppressed = false } = {}) {
    if (suppressed) { this.reset(); return; }
    const duration = samples.length / sampleRate, rms = pcmRms(samples), threshold = Math.max(0.014, this.noise * 3.2);
    const hot = rms > threshold;
    if (!this.active && !hot) this.noise = this.noise * 0.97 + Math.min(rms, 0.02) * 0.03;
    this.preRoll.push({ samples: samples.slice(), duration, sourceSeconds });
    while (this.preRoll.length > 4) this.preRoll.shift();
    if (!this.active) {
      this.hot = hot ? this.hot + duration : 0;
      if (this.hot < 0.2) return;
      this.active = true; this.pending = this.preRoll.map((entry) => entry.samples); this.duration = this.preRoll.reduce((total, item) => total + item.duration, 0);
      this.startedAt = Math.max(0, (this.preRoll[0]?.sourceSeconds || 0) - (this.preRoll[0]?.duration || 0)); this.silence = 0;
      this.onStart({ start_seconds: this.startedAt, sampleRate }); return;
    }
    this.pending.push(samples.slice()); this.duration += duration; this.silence = hot ? 0 : this.silence + duration;
    if (this.silence >= this.silenceSeconds || this.duration >= this.maxSeconds) this.finish(sampleRate, sourceSeconds);
  }
  finish(sampleRate, sourceSeconds) {
    if (!this.active) return;
    const chunks = this.pending, length = chunks.reduce((total, item) => total + item.length, 0), pcm = new Float32Array(length);
    let cursor = 0; for (const chunk of chunks) { pcm.set(chunk, cursor); cursor += chunk.length; }
    const event = { pcm, sampleRate, start_seconds: this.startedAt, end_seconds: Math.max(this.startedAt, sourceSeconds), duration_seconds: length / sampleRate };
    const voicedSeconds = this.duration - this.silence; this.reset();
    if (voicedSeconds >= this.minimumSeconds) this.onEnd(event);
    else this.onEnd({ ...event, discarded: true });
  }
}

export async function createPCMInput(stream, { onFrame, onError = () => {} }) {
  const Context = globalThis.AudioContext || globalThis.webkitAudioContext;
  if (!Context) throw new Error("Este navegador no permite procesar el micrófono.");
  const context = new Context({ latencyHint: "interactive" });
  let source, processor;
  try {
    await context.audioWorklet.addModule(new URL("./live-audio-worklet.js", import.meta.url));
    source = context.createMediaStreamSource(stream);
    processor = new AudioWorkletNode(context, "compinche-pcm");
    processor.port.onmessage = ({ data }) => onFrame(data);
    processor.onprocessorerror = () => onError(new Error("Se interrumpió la escucha del micrófono."));
    source.connect(processor); processor.connect(context.destination); await context.resume();
    return { context, stop: () => { processor.port.onmessage = null; source.disconnect(); processor.disconnect(); return context.close(); } };
  } catch (error) { await context.close(); throw error; }
}

/** Persistent same-origin PCM bridge; the API key stays on the server. */
export class RealtimePCMChannel {
  constructor({ runId, origin, speakerName, speakerRole, clock, onPartial = () => {}, onStatus = () => {} }) {
    Object.assign(this, { clock, onPartial, onStatus }); this.ready = false; this.closed = false; this.socket = null;
    const url = new URL(`/api/live/${encodeURIComponent(runId)}/audio-stream`, location.href);
    url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
    try {
      const socket = this.socket = new WebSocket(url); socket.binaryType = "arraybuffer";
      socket.onopen = () => { if (this.closed) { socket.close(); return; } socket.send(JSON.stringify({ type: "start", origin, speaker_name: speakerName, speaker_role: speakerRole, sample_rate: 24000, ...clock() })); };
      socket.onmessage = ({ data }) => {
        if (this.closed) return;
        if (typeof data !== "string") return;
        let event; try { event = JSON.parse(data); } catch { return; }
        if (event.type === "ready") { this.ready = true; onStatus("ready"); }
        else if (event.type === "partial") onPartial(event.text || "");
        else if (event.type === "error") { const wasReady = this.ready; this.ready = false; onStatus(wasReady ? "interrupted" : "fallback"); socket.close(); }
      };
      socket.onerror = () => { this.ready = false; onStatus("fallback"); };
      socket.onclose = () => { this.ready = false; if (!this.closed) onStatus("fallback"); };
    } catch { onStatus("fallback"); }
  }
  send(samples, sampleRate, { silence = false, metadata = {} } = {}) {
    if (!this.ready || this.closed || this.socket?.readyState !== WebSocket.OPEN) return false;
    if (this.socket.bufferedAmount > 192000) { this.ready = false; this.onStatus("interrupted"); this.socket.close(); return false; }
    const pcm = resamplePCM(samples, sampleRate), bytes = new ArrayBuffer(pcm.length * 2), data = new DataView(bytes);
    for (let i = 0; i < pcm.length; i++) data.setInt16(i * 2, silence ? 0 : Math.round(Math.max(-1, Math.min(1, pcm[i])) * (pcm[i] < 0 ? 32768 : 32767)), true);
    this.socket.send(JSON.stringify({ type: "clock", ...this.clock(), ...metadata }));
    this.socket.send(bytes); return true;
  }
  close() {
    if (this.closed) return; this.closed = true; this.ready = false;
    const socket = this.socket;
    if (socket && socket.readyState === globalThis.WebSocket?.OPEN) {
      socket.send(JSON.stringify({ type: "end" }));
      const timer = setTimeout(() => socket.close(), 3000);
      socket.addEventListener("close", () => clearTimeout(timer), { once: true });
    } else socket?.close();
  }
}

/** Mount once; updates never replace the camera element or reset the live source. */
export function mountLive(container, { demo = null, sessionId = null, lessonId = null, onSession = () => {}, onPcm = null } = {}) {
  let closed = false, run = null, latest = null, running = false, paused = false, starting = false, epoch = 0;
  let sequence = 0, eventId = 0, wallStart = 0, captureTimer = null, pollTimer = null, frameBusy = false, lastObserved = -Infinity, lastSignature = null;
  let eventSource = null, eventsReady = false, eventsFallbackTimer = null, polling = false;
  let frames = [], sourceStream = null, micStream = null, micInput = null, sourceAudio = null, sourceAudioContext = null;
  let sourceSpeaking = false, humanSpeaking = false, agentSpeaking = false, quietUntil = 0, lastHumanEnd = 0, speechEpoch = 0, waitingForReply = false, replayWasPlaying = false;
  let voicePlayer = null, voiceUrl = null, questionFetch = null, lastCardSignature = "", spokenQuestionId = null, stoppedSourceSeconds = 0;
  let archiveRecorder = null, archiveChunks = [], archiveBytes = 0, archiveUrl = null, archiveStart = 0;
  let rollingRecorder = null, rollingTimer = null, latestMotion = null;
  let replayEvidenceId = demo?.original_evidence_id || null, uploadedReplay = null, lastError = "";
  let questionAvailableAt = null, questionAvailableId = null, lastVoiceStartMs = null;
  let questionTimer = null;
  const audioSockets = new Map();
  const controllers = new Set(), seenQuestions = new Set(), seenEvents = new Set();
  const localControllers = [];
  const sourceKind = () => $("source").value === "file" ? "replay" : $("source").value;
  const learnMode = () => $("mode").value === "learn";
  const $ = (name) => container.querySelector(`[data-live="${name}"]`);
  container.innerHTML = `<section class="live-studio" aria-label="Aprender durante la cocina">
    <div class="live-heading"><p class="eyebrow">COMPINCHE · APRENDER EN EL MOMENTO</p><h1>Que cocine.<br><em>Que nos lo cuente.</em></h1><p>Compinche observa, escucha y pregunta por las decisiones que todavía faltan por explicar.</p></div>
    <div class="live-setup" data-live="setup">
      <label>Qué vamos a hacer<select data-live="mode"><option value="record">Enseñar · Compinche observa y pregunta</option><option value="learn" ${lessonId ? "selected" : ""}>Aprender · Compinche me guía con lo que se confirmó</option></select></label>
      <label data-live="lesson-label" hidden>Aprender de<select data-live="lesson"><option value="">Cargando sesiones…</option></select><span class="live-fine" data-live="lesson-help">Solo se usan las fichas que una persona confirmó.</span></label>
      <label>Entrada de cámara<select data-live="source"><option value="replay" ${!demo?.original_evidence_id ? "disabled" : ""}>Vídeo de papá · replay causal</option><option value="file">Otro vídeo · simular la mirada de las gafas</option><option value="phone" ${!demo?.original_evidence_id ? "selected" : ""}>Cámara de este teléfono</option></select></label>
      <label class="live-file" data-live="file-label" hidden>Vídeo para esta prueba<input type="file" data-live="file" accept="video/*"><span class="live-fine">Hasta 150 MiB. Se conserva el original en el servidor local; el agente solo recibe lo que ya haya transcurrido.</span></label>
      <label>Quién cocina<input data-live="teacher" value="${esc(demo ? "Papá" : "")}" maxlength="100" placeholder="Nombre de quien enseña"></label>
      <label>Quién responderá al micrófono<input data-live="speaker" value="David" maxlength="100" placeholder="Nombre de quien va a responder"></label>
      <label>Su papel<select data-live="role"><option value="observer">Observador del vídeo</option><option value="cook">Persona que cocina</option><option value="wearer">Quien lleva la cámara</option></select></label>
      <label class="live-check"><input type="checkbox" data-live="microphone" checked> Escuchar respuestas por micrófono</label>
      <label class="live-check" data-live="original-audio-label"><input type="checkbox" data-live="original-audio"> Escuchar el vídeo original · recomendado con auriculares. Mientras suena, el micrófono espera al turno de respuesta para no mezclar voces.</label>
      <label class="live-check" data-live="pause-replay-label"><input type="checkbox" data-live="pause-replay"> Pausar el vídeo mientras respondo · ayuda opcional para la prueba. Sin marcar, la escena continúa como una cámara de gafas.</label>
      <label class="live-check live-consent"><input type="checkbox" data-live="consent"> Al iniciar, Compinche podrá enviar imágenes y voz de esta sesión a Meta automáticamente para observar y conversar.</label>
      <p class="live-fine" data-live="source-help">El vídeo simula una entrada de cámara: avanza sin anticipar el final, también durante las preguntas y respuestas. El audio original se transcribe y puedes activar su escucha. Responde tú por micrófono o por escrito; nunca se inventan respuestas de la persona grabada.</p>
      <button type="button" class="button button-dark" data-live="start">Iniciar aprendizaje</button>
    </div>
    <div class="live-toolbar"><span class="live-state" data-live="status" role="status">Preparado para una sesión nueva</span><span data-live="clock">00:00</span><button type="button" class="button button-light" data-live="pause" disabled>Pausar</button><button type="button" class="button button-light" data-live="stop" disabled>Terminar sesión</button><a data-live="lesson-link" class="text-link" hidden>Ver lo aprendido</a><a data-live="practice-link" class="button button-dark" hidden>Practicar con lo aprendido</a><a data-live="archive-link" class="text-link" hidden download="compinche-captura.webm">Descargar grabación</a></div>
    <p class="live-error" data-live="error" role="alert" hidden></p>
    <div class="live-layout"><section class="live-camera"><div class="live-video-wrap"><video data-live="video" playsinline muted preload="metadata" aria-label="Cámara de la sesión de aprendizaje"></video><span class="live-camera-label" data-live="source-label">La imagen será la evidencia</span></div><div class="live-tools" data-live="tools"></div><div class="live-measurement" data-live="measurement" hidden></div><div class="live-gesture" data-live="gesture" hidden></div><p class="live-fine" data-live="capture-info">La cámara y la conversación comparten una misma sesión.</p></section>
      <aside class="live-conversation"><p class="eyebrow">EL APRENDIZ ESTÁ ATENTO</p><h2 data-live="question-title">Dejar hacer. Saber preguntar.</h2><p class="live-question" data-live="question">Cuando aparezca una decisión sin explicar, Compinche podrá preguntar por ella.</p><p class="live-fine" data-live="question-reason"></p>
        <div class="live-question-actions"><button type="button" class="button button-light" data-live="interrupt" disabled>Interrumpir la voz</button><button type="button" class="button button-light" data-live="postpone" disabled>Ahora no</button><button type="button" class="text-link" data-live="repeat" disabled>Repetir pregunta</button></div>
        <p class="live-listening" data-live="listening">Micrófono sin activar</p>
        <form data-live="answer-form"><label class="field-label" for="live-answer">También puedes responder por escrito</label><textarea id="live-answer" data-live="answer" class="text-area" rows="2" maxlength="3000" placeholder="Tu explicación, con tus palabras…"></textarea><button type="submit" class="button button-dark" data-live="send-answer" disabled>Conservar esta respuesta</button></form>
        <p class="live-fine" data-live="voice-kind"></p>
      </aside>
    </div>
    <section class="live-timeline" data-live="timeline" hidden></section>
    <section class="live-learning"><div class="section-heading"><h2>Lo que vamos aprendiendo.</h2><span data-live="cards-count">Aún sin fichas de decisión</span></div><p class="live-fine">Las explicaciones pendientes siguen siendo un borrador. Confirmarlas requiere la revisión de una persona.</p><div class="live-cards" data-live="cards"></div><div class="live-exports" data-live="exports" hidden><a class="button button-light" data-live="resource-html">Descargar recurso de aprendizaje</a><a class="text-link" data-live="resource-json">Recurso JSON</a><a class="text-link" href="#teach" target="_blank" rel="noopener">Abrir otra prueba</a></div></section>
    <details class="live-diagnostics"><summary>Inspeccionar esta prueba y sus latencias</summary><p class="live-fine">Son las últimas medidas disponibles, no una media ni una promesa de respuesta instantánea. El reloj del vídeo y el tiempo de procesamiento son distintos.</p><dl data-live="diagnostics"></dl><p class="live-fine" data-live="diagnostic-error">Sin errores registrados en esta vista.</p><a class="text-link" data-live="diagnostic-download" hidden>Descargar diagnóstico y eventos completos</a></details>
    <details class="live-history"><summary>Conversación y tiempos de esta sesión</summary><ol data-live="events"></ol></details>
  </section>`;
  let video = $("video");
  const canvas = document.createElement("canvas"), diffCanvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d", { alpha: false }), diffCtx = diffCanvas.getContext("2d", { willReadFrequently: true });
  const controlPath = () => `/api/live/${encodeURIComponent(run.id)}/control`;
  function sourceSeconds() { return sourceKind() === "replay" ? Math.max(0, video.currentTime || 0) : running ? Math.max(0, (performance.now() - wallStart) / 1000) : stoppedSourceSeconds; }
  function error(message = "") { $("error").hidden = !message; $("error").textContent = message; if (message) { lastError = message; $("diagnostic-error").textContent = `Último aviso: ${lastError}`; } }
  function setStatus(message) { if (!closed) $("status").textContent = message; }
  function syncReplayMonitor() {
    const monitor = sourceAudio?.monitor;
    if (!monitor) return;
    const audible = running && !paused && !waitingForReply && !agentSpeaking && !video.paused && $("original-audio").checked;
    const wasAudible = monitor.gain.value > 0;
    if (monitor.gain.value && !audible) quietUntil = Math.max(quietUntil, performance.now() + 650);
    monitor.gain.value = audible ? 1 : 0;
    if (micInput && (audible || wasAudible) && !agentSpeaking) $("listening").textContent = audible ? "Original audible · el micrófono espera al turno de respuesta" : `Tu turno, ${$("speaker").value.trim()} · puedes responder`;
  }
  async function wire(path, { body, method = "GET", timeout = 18000, raw = false, signal } = {}) {
    const controller = new AbortController(); controllers.add(controller);
    const abort = () => controller.abort(); signal?.addEventListener("abort", abort, { once: true });
    const timer = setTimeout(abort, timeout);
    try {
      const response = await fetch(path, { method, body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body), headers: body instanceof FormData ? undefined : { "Content-Type": "application/json" }, signal: controller.signal });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail?.message || data.detail || `El servidor respondió ${response.status}.`);
      }
      return raw ? await response.blob() : await response.json();
    } finally { clearTimeout(timer); controllers.delete(controller); signal?.removeEventListener("abort", abort); }
  }
  async function control(action, extra = {}) {
    if (!run || closed) return;
    return wire(controlPath(), { method: "POST", body: { action, source_seconds: sourceSeconds(), ...extra } });
  }
  function logEvent(item) {
    item = { ...item, ...(item.data || {}) };
    const key = item.id ?? `${item.type}:${item.created_at}:${JSON.stringify(item)}`;
    if (seenEvents.has(key)) return; seenEvents.add(key);
    if (Number.isFinite(Number(item.id))) eventId = Math.max(eventId, Number(item.id));
    const descriptions = { question: "Compinche pregunta", transcript: "Palabras conservadas", observation: "Observación", decision: "Ficha de decisión", error: "Aviso", skipped: "Observación descartada", segmentation: "Seguimiento de la imagen", guide: "Compinche te guía", measurement: "Medición", gesture_comparison: "Comparación del gesto", heat: "Fuego", answer_unrelated: "Respuesta no relacionada · la pregunta sigue abierta" };
    const text = item.text || item.note?.text || item.question?.text || item.message || item.reason || item.summary || item.observation;
    if (!text) return;
    const li = document.createElement("li");
    li.innerHTML = `<time>${clock(item.source_seconds ?? item.end_seconds ?? sourceSeconds())}</time><span><strong>${esc(descriptions[item.type] || item.type || "Sesión")}</strong> ${esc(text)}</span>`;
    $("events").prepend(li); while ($("events").children.length > 60) $("events").lastElementChild.remove();
  }
  function refreshCards(cards = []) {
    const signature = JSON.stringify(cards); if (signature === lastCardSignature) return; lastCardSignature = signature;
    $("cards-count").textContent = cards.length ? `${cards.length} ficha${cards.length === 1 ? "" : "s"} con su evidencia` : "Aún sin fichas de decisión";
    $("cards").innerHTML = cards.map((card) => {
      const decision = card.decision_card || {};
      const title = card.title || decision.action || decision.decision || "Una decisión que conservar";
      const detail = decision.explanation || card.explanation || card.answer_text || card.quote || card.text || "";
      const who = decision.speaker || card.speaker_name || card.speaker?.name || card.speaker || card.author || "Autor por revisar";
      const confirmed = card.confirmed === true || card.status === "confirmed";
      const question = decision.question || card.question_text || card.question?.text || "";
      const evidence = card.evidence_ids || card.sources?.map((source) => source.evidence_id).filter(Boolean) || [];
      const sourceLinks = evidence.map((id) => `<button type="button" class="text-link" data-source="${esc(id)}">Abrir evidencia</button>`).join("");
      return `<article class="live-decision"><span class="live-card-status">${confirmed ? "Confirmado por una persona" : "Explicación pendiente de revisión"}</span><h3>${esc(title)}</h3>${question ? `<p class="live-fine">Pregunta: ${esc(question)}</p>` : ""}<p>${esc(detail)}</p><p class="live-fine">${esc(who)} · ${clock(decision.source_start_seconds ?? card.source_start_seconds ?? card.start_seconds ?? 0)}</p><div class="live-card-actions">${sourceLinks}${!confirmed ? `<button type="button" class="text-link" data-confirm="${esc(card.id)}">Confirmar que me representa</button>` : ""}</div></article>`;
    }).join("");
  }
  function refreshTools(snapshot) {
    const metrics = snapshot.metrics || {}, tools = snapshot.tools || {};
    const labels = { vision: "Visión", asr: "Escucha", voice: "Voz", segmentation: "Segmentación", sam: "Segmentación" };
    const statusLabels = { completed: "completada", idle: "en espera", ready: "preparada", running: "trabajando", busy: "trabajando", unavailable: "no disponible", failed: "no disponible", error: "no disponible" };
    const badges = Object.entries(tools).filter(([name]) => name in labels).map(([name, tool]) => {
      const raw = typeof tool === "string" ? tool : tool?.status || (tool?.configured === false ? "unavailable" : "ready");
      return `<span>${labels[name]} · ${esc(statusLabels[raw] || raw)}</span>`;
    });
    const timings = [["last_vision_ms", "Inferencia visual", 1000], ["last_question_ready_ms", "Observación admitida → pregunta elegible", 1000], ["last_asr_ms", "Transcripción", 1000], ["last_question_source_lag_seconds", "Desfase en el vídeo", 1]];
    $("tools").innerHTML = badges.join("") + timings.filter(([key]) => typeof metrics[key] === "number").map(([key, label, divisor]) => `<span>${label}: ${(metrics[key] / divisor).toFixed(1)} s</span>`).join("");
    refreshMeasurement(tools.measurement);
    refreshGesture(tools.gesture);
    scheduleTimeline(snapshot);
  }
  let timelineTimer = null, timelineAt = 0;
  function scheduleTimeline(snapshot) {
    if (!run || timelineTimer || performance.now() - timelineAt < 4000) return;
    timelineTimer = setTimeout(async () => {
      timelineTimer = null; timelineAt = performance.now();
      try { renderTimeline(await wire(`/api/live/${encodeURIComponent(run.id)}/timeline`, { timeout: 8000 })); } catch {}
    }, 300);
  }
  function renderTimeline(t) {
    const box = $("timeline");
    const span = Math.max(30, t.duration || 0);
    if (!t.phases?.length && !t.heat?.length && !t.questions?.length) { box.hidden = true; return; }
    box.hidden = false;
    const x = (s) => `${Math.min(100, Math.max(0, (s / span) * 100)).toFixed(2)}%`;
    const heatIcon = { on: "🔥", up: "▲", down: "▼", off: "○" }, heatWord = { on: "Enciende", up: "Sube el fuego", down: "Baja el fuego", off: "Apaga" };
    const flameMax = Math.max(1, ...(t.flame || []).map((f) => f.px));
    const flame = (t.flame || []).map((f) => `${(f.t / span) * 100},${30 - (f.px / flameMax) * 26}`).join(" ");
    box.innerHTML = `<div class="section-heading"><h2>Cómo va la preparación.</h2><span>${clock(t.duration || 0)}</span></div>
      <div class="live-timeline-track">
        ${t.phases.map((p, i) => `<span class="live-phase p${i % 4}" style="left:${x(p.start)};width:calc(${x(p.end)} - ${x(p.start)} + 2px)" title="${esc(p.name)}">${esc(p.name)}</span>`).join("")}
        ${flame ? `<svg class="live-flame" viewBox="0 0 100 30" preserveAspectRatio="none" aria-label="Llama medida bajo el recipiente"><polyline points="${flame}" /></svg>` : ""}
        ${(t.heat || []).map((h) => `<span class="live-mark heat" style="left:${x(h.t)}" title="${clock(h.t)} · ${heatWord[h.kind]}${h.source === "flame_meter" ? " (llama medida)" : ""}">${heatIcon[h.kind]}</span>`).join("")}
        ${(t.questions || []).map((q) => `<span class="live-mark ${q.kind === "question" ? "ask" : "guide"}" style="left:${x(q.t || 0)}" title="${clock(q.t || 0)} · ${esc(q.text)}">${q.kind === "question" ? "?" : "›"}</span>`).join("")}
        ${(t.cards || []).map((c) => `<span class="live-mark card ${c.confirmed ? "ok" : ""}" style="left:${x(c.t || 0)}" title="${clock(c.t || 0)} · ${esc(c.text)}">✓</span>`).join("")}
      </div>
      <p class="live-fine">Fases que ve Compinche · ▲▼ cambios de fuego (vistos o medidos por la llama) · ? preguntas · ✓ explicaciones guardadas.</p>`;
  }
  function refreshGesture(g) {
    const box = $("gesture");
    if (!g || !g.difference) { box.hidden = true; return; }
    if (box.dataset.id === g.id) return;
    box.dataset.id = g.id; box.hidden = false;
    box.innerHTML = `<p class="eyebrow">SU DEMOSTRACIÓN · TU INTENTO · MISMA FASE</p>
      ${g.split_video_evidence_id ? `<video src="/api/media/${encodeURIComponent(g.split_video_evidence_id)}" controls muted playsinline loop autoplay aria-label="Pantalla partida alineada"></video>` : ""}
      <p class="live-gesture-difference">${esc(g.difference)}</p><p class="live-fine">${esc(g.suggestion || "")}</p>
      <details><summary>Cómo se ha comparado</summary><p class="live-fine">CoTracker3 (Meta) sigue cientos de puntos en los dos vídeos; se alinean los movimientos con DTW para enfrentar la misma fase aunque uno vaya más lento. ${esc(g.models?.comparison || "Muse Spark")} compara los fotogramas alineados y nombra una diferencia visible. No decide si está en su punto.</p></details>`;
  }
  function refreshMeasurement(m) {
    const box = $("measurement");
    if (!m || typeof m.diameter_cm !== "number") { box.hidden = true; return; }
    const signature = JSON.stringify([m.id, m.diameter_cm, m.samples, m.view]);
    if (box.dataset.signature === signature) return;
    box.dataset.signature = signature; box.hidden = false;
    const ref = m.reference;
    const noun = m.vessel === "frying pan" ? "Sartén" : "Recipiente";
    box.innerHTML = `${m.overlay_evidence_id ? `<img src="/api/media/${encodeURIComponent(m.overlay_evidence_id)}" alt="${noun} segmentado con su borde y la referencia de escala">` : ""}
      <div><p class="eyebrow">${noun.toUpperCase()} · DIÁMETRO ESTIMADO</p><p class="live-measure-value">≈ ${Math.round(m.diameter_cm)} cm <small>${Math.round(m.range_cm[0])}–${Math.round(m.range_cm[1])} cm</small></p>
      <p class="live-fine">${m.samples || 1} ${(m.samples || 1) > 1 ? "vistas" : "vista"} ${m.view === "good" ? "desde arriba" : (m.samples || 1) > 1 ? "inclinadas" : "inclinada"} · inclinación ${Math.round(m.tilt_degrees)}°</p>
      <details><summary>Cómo se ha medido</summary><p class="live-fine">SAM 3.1 (Meta Model API) localiza ${noun.toLowerCase()}${ref ? ` y el ${esc(ref.label)}` : ""}. MapAnything (Meta, pesos abiertos, en local) estima la geometría métrica. ${ref ? `Escala contrastada con el ${esc(ref.label)}: medido ${ref.measured_raw_cm} cm sin corregir, se supone ${ref.assumed_cm[0]}–${ref.assumed_cm[1]} cm.` : "Sin referencia de tamaño conocido: el rango es amplio."} Muestras: ${(m.sample_diameters_cm || [m.diameter_cm]).map((d) => Math.round(d)).join(", ")} cm. Es una estimación de tamaño; no mide peso, temperatura ni punto.</p></details></div>`;
  }
  function refreshDiagnostics(snapshot = latest) {
    if (!snapshot) return;
    const metrics = snapshot.metrics || {}, counts = [["observations_received", "Observaciones admitidas"], ["observations_analysed", "Observaciones analizadas"], ["observations_superseded", "Sustituidas por una más reciente"], ["provider_failures", "Fallos del proveedor"], ["questions_ready", "Preguntas elegibles"], ["questions_spoken", "Inicios de voz reconocidos"], ["answers_received", "Respuestas vinculadas"]];
    const listening = $("microphone").checked ? sourceAudio?.monitor?.gain.value > 0 ? "En espera mientras suena el original · turnos separados" : audioSockets.get("microphone")?.ready ? "PCM continuo conectado" : "WAV al terminar de hablar" : "Desactivado · respuesta escrita";
    const rows = [["Sesión", snapshot.session_id || sessionId || "—"], ["Estado del servidor", snapshot.status || "—"], ["Entrada", sourceKind() === "replay" ? `Vídeo grabado · replay causal ${$("pause-replay").checked ? "con pausa de ayuda" : "continuo como una cámara"}, no gafas físicas` : "Cámara de este teléfono"], ["Original", sourceKind() === "replay" ? replayEvidenceId || "—" : "Se conserva al terminar"], ["Posición admitida del vídeo/cámara", `${Number(snapshot.source_seconds || 0).toFixed(1)} s · escena ${snapshot.scene_epoch ?? epoch}`], ["Actualizaciones", eventsReady ? "Eventos conectados (SSE)" : "Sondeo HTTP / conectando eventos"], ["Micrófono", listening], ["Colas del servidor", `${snapshot.queue_depth ?? "—"} observaciones · ${snapshot.speech_pending ?? "—"} transcripciones en curso`], ["Estado de la pregunta", snapshot.question?.status || "Sin pregunta activa"], ["Pregunta recibida en esta vista → inicio de reproducción", lastVoiceStartMs === null ? "Sin medir todavía" : `${(lastVoiceStartMs / 1000).toFixed(2)} s (reloj del navegador)`], ["Captura → audio oído", "No medido de extremo a extremo"]];
    for (const [key, label] of counts) if (typeof metrics[key] === "number") rows.push([label, String(metrics[key])]);
    const definitions = snapshot.metric_definitions || {};
    for (const [key, label, scale, fallback] of [["last_vision_ms", "Última inferencia visual", 1000, "Duración de la inferencia del servidor; no incluye toda la captura."], ["last_question_ready_ms", "Última pregunta elegible", 1000, "Desde la admisión de la observación hasta que se permite preguntar; no es inicio de voz."], ["last_question_source_lag_seconds", "Desfase de la pregunta en el vídeo", 1, "Diferencia entre posiciones del vídeo; no tiempo de procesamiento."]]) {
      if (typeof metrics[key] !== "number") continue;
      const definition = definitions[key];
      rows.push([label, `${(metrics[key] / scale).toFixed(2)} s · ${typeof definition === "string" ? definition : definition?.definition || definition?.description || fallback}`]);
    }
    $("diagnostics").innerHTML = rows.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join("");
  }
  function acceptSnapshot(snapshot) {
    if (closed || !snapshot || (run && snapshot.id && snapshot.id !== run.id)) return;
    const addedCard = (snapshot.cards?.length || 0) > (latest?.cards?.length || 0);
    latest = snapshot;
    if (running && ["stopped", "interrupted"].includes(snapshot.status)) stop({ notifyServer: false });
    for (const event of snapshot.events || []) logEvent(event);
    refreshCards(snapshot.cards || []); refreshTools(snapshot);
    const question = snapshot.question;
    if (question && SPEAKABLE_QUESTION.has(question.status) && question.id !== questionAvailableId) { questionAvailableId = question.id; questionAvailableAt = performance.now(); }
    if (question?.status === "spoken") spokenQuestionId = question.id;
    else if (!question || TERMINAL_QUESTION.has(question.status)) spokenQuestionId = null;
    if (question?.kind === "guide" && !TERMINAL_QUESTION.has(question.status)) {
      $("question-title").textContent = question.trigger === "reminder" ? "Antes de seguir, un detalle." : "Lo que te enseñó.";
      $("question").textContent = question.text || "";
      const card = question.lesson_cards?.[0];
      $("question-reason").textContent = card?.teacher_said ? `«${card.teacher_said}» — ${card.speaker || "quien enseñó"}` : question.reason || "";
      $("postpone").disabled = true; $("repeat").disabled = true;
    } else if (question && !TERMINAL_QUESTION.has(question.status)) {
      $("question-title").textContent = "Una pregunta que merece la pena.";
      $("question").textContent = question.text || "";
      $("question-reason").textContent = question.reason || "";
      $("postpone").disabled = !running || paused;
      $("repeat").disabled = !running || paused;
    } else {
      $("postpone").disabled = true; $("repeat").disabled = true;
      $("question").textContent = question?.status === "unresolved" ? "Esta duda sigue sin una explicación. Podemos retomarla cuando haya más evidencia." : "La conversación queda conservada mientras seguimos aprendiendo.";
      if (question?.status === "answered") { $("question-title").textContent = "Gracias. Eso era lo que faltaba."; $("question-reason").textContent = "La explicación queda vinculada a su pregunta y su evidencia."; }
      else if (question && TERMINAL_QUESTION.has(question.status)) { $("question-title").textContent = "Seguimos observando."; $("question-reason").textContent = "La pregunta no se marca como respondida."; }
      if (waitingForReply) {
        waitingForReply = false;
        $("question-title").textContent = addedCard ? "Gracias. Eso era lo que faltaba." : "Seguimos observando.";
        if (sourceKind() === "replay" && replayWasPlaying && running && !paused) video.play().catch(() => {});
      }
    }
    $("send-answer").disabled = !running || paused;
    refreshDiagnostics(snapshot);
    if (running && !paused) maybeSpeakQuestion();
  }
  async function poll() {
    if (closed || !run || !running || eventsReady || polling) return;
    polling = true;
    try { acceptSnapshot(await wire(`/api/live/${encodeURIComponent(run.id)}?after=${eventId}`, { timeout: 9000 })); }
    catch (exception) { if (!closed && running) error(`No se pudo actualizar la sesión: ${exception.message}`); }
    finally { polling = false; if (!closed && running && !eventsReady) pollTimer = setTimeout(poll, 850); }
  }
  function connectEvents() {
    if (!globalThis.EventSource) { poll(); return; }
    try {
      eventSource = new EventSource(`/api/live/${encodeURIComponent(run.id)}/events?after=${eventId}`);
      eventSource.onopen = () => { eventsReady = true; clearTimeout(pollTimer); clearTimeout(eventsFallbackTimer); refreshDiagnostics(); };
      eventSource.addEventListener("snapshot", ({ data }) => {
        if (closed || !running) return;
        try { acceptSnapshot(JSON.parse(data)); } catch { /* An incomplete event is retried by the next snapshot. */ }
      });
      eventSource.onerror = () => {
        eventsReady = false;
        if (!closed && running && !polling) { clearTimeout(pollTimer); poll(); }
      };
      eventsFallbackTimer = setTimeout(() => { if (!eventsReady && !closed && running) poll(); }, 2500);
    } catch { eventsReady = false; poll(); }
  }
  const sender = new LatestOnlySender(async (payload) => {
    if (!run || closed || !running || paused) return;
    const receipt = await wire(`/api/live/${encodeURIComponent(run.id)}/observe`, { method: "POST", body: payload });
    if (receipt?.snapshot) acceptSnapshot(receipt.snapshot);
  }, (exception) => error(`La cámara sigue activa; la observación no llegó: ${exception.message}`));
  async function captureFrame() {
    if (!running || paused || closed || frameBusy || video.readyState < 2 || video.videoWidth <= 0) return;
    if (sourceKind() === "replay" && video.paused) return;
    frameBusy = true;
    try {
      const watermark = sourceSeconds();
      const scale = Math.min(1, 640 / video.videoWidth, 480 / video.videoHeight);
      canvas.width = Math.max(2, Math.round(video.videoWidth * scale)); canvas.height = Math.max(2, Math.round(video.videoHeight * scale));
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      diffCanvas.width = 24; diffCanvas.height = 14; diffCtx.drawImage(video, 0, 0, 24, 14);
      const pixels = diffCtx.getImageData(0, 0, 24, 14).data, signature = new Uint8Array(24 * 14);
      for (let i = 0; i < signature.length; i++) signature[i] = Math.round(pixels[i * 4] * 0.3 + pixels[i * 4 + 1] * 0.59 + pixels[i * 4 + 2] * 0.11);
      // A just-opened decoder can expose a black surface before its first useful frame.
      if (!frames.length && signature.every((value) => value < 3)) return;
      const change = frameDifference(lastSignature, signature); lastSignature = signature;
      frames.push({ source_seconds: watermark, jpeg_base64: canvas.toDataURL("image/jpeg", 0.67).split(",")[1] });
      frames = frames.filter((frame) => frame.source_seconds >= watermark - 7).slice(-8);
      const chosen = causalBurst(frames, watermark);
      if (chosen.length >= 2 && watermark - lastObserved >= 4 && (change > 0.018 || watermark - lastObserved >= 12)) {
        lastObserved = watermark;
        const motion = latestMotion && latestMotion.epoch === epoch && latestMotion.start >= chosen[0].source_seconds && latestMotion.end <= watermark ? latestMotion.id : null;
        sender.offer({ sequence: sequence++, scene_epoch: epoch, start_seconds: chosen[0].source_seconds, end_seconds: watermark, frames: chosen, source_speaking: sourceSpeaking || humanSpeaking, agent_speaking: agentSpeaking, ...(motion ? { video_evidence_id: motion } : {}) });
      }
      $("clock").textContent = clock(watermark);
    } catch (exception) { if (!closed) error(`No se pudo leer la cámara: ${exception.message}`); }
    finally { frameBusy = false; }
  }
  function setSpeech(kind, value) {
    if (kind === "source") { if (sourceSpeaking === value) return; sourceSpeaking = value; }
    else { if (humanSpeaking === value) return; humanSpeaking = value; if (!value) lastHumanEnd = performance.now(); }
    if (run && running) control("speaking", { channel: kind, speaking: value }).catch(() => {});
    if (kind === "human") $("listening").textContent = value ? `Escuchando a ${$("speaker").value || "la persona"}…` : micInput ? "Micrófono activo · esperando una explicación" : "Micrófono sin activar";
    if (!value) maybeSpeakQuestion();
  }
  function connectAudioSocket(origin) {
    const original = origin === "recorded_source";
    const channel = new RealtimePCMChannel({ runId: run.id, origin,
      speakerName: original ? "Grabación original" : $("speaker").value.trim(), speakerRole: original ? "cook" : $("role").value,
      clock: () => ({ source_seconds: sourceSeconds(), scene_epoch: epoch, agent_speaking: agentSpeaking,
        ...(spokenQuestionId ? { answer_to: spokenQuestionId } : {}) }),
      onPartial: (text) => {
        if (!closed && text && !agentSpeaking) {
          if (original) $("capture-info").textContent = `Voz de la grabación · transcripción provisional: ${text}`;
          else $("listening").textContent = `Escuchando a ${$("speaker").value.trim()}: ${text}`;
        }
      },
      onStatus: (status) => {
        if (closed) return;
        if (status === "interrupted" && !original) error("Se interrumpió la escucha continua. Repite la última explicación o escríbela para conservarla.");
        if (!original) $("voice-kind").textContent = status === "ready" ? "Escucha continua conectada · transcripción en curso durante el habla." : "Escucha por intervenciones · se enviará cada explicación cuando termines de hablar.";
      },
    });
    audioSockets.set(origin, channel);
  }
  function createSegmenter(origin) {
    let metadata = null, transport = null, realtime = false, admissions = 0;
    const channel = origin === "recorded_source" ? "source" : "human";
    const refreshSpeech = () => setSpeech(channel, segmenter.active || admissions > 0);
    const segmenter = new VoiceSegmenter({
      onStart: () => {
        metadata = { answerTo: origin === "microphone" ? spokenQuestionId : null,
          utteranceId: identifier(), captureStarted: Date.now() / 1000 - segmenter.duration,
          speechStarted: Date.now() / 1000 - segmenter.hot, sceneEpoch: epoch,
          speakerName: origin === "recorded_source" ? "Grabación original" : $("speaker").value.trim(),
          speakerRole: origin === "recorded_source" ? "cook" : $("role").value };
        transport = realtime ? "stream" : "wav";
        setSpeech(origin === "recorded_source" ? "source" : "human", true);
      },
      onEnd: (segment) => {
        const current = metadata, used = transport; metadata = null; transport = null;
        if (!segment.discarded && current && running && !paused && !closed && used !== "stream") {
          admissions++;
          settleAudioAdmission(() => sendAudio(segment, origin, current), () => { admissions--; refreshSpeech(); })
            .catch((exception) => { if (!closed) error(`La explicación no se pudo enviar: ${exception.message}`); });
        }
        refreshSpeech();
      },
    });
    return {
      push: (data) => {
        const suppressed = !running || paused || (origin === "microphone" && (agentSpeaking || performance.now() < quietUntil || sourceAudio?.monitor?.gain.value > 0));
        const socket = audioSockets.get(origin);
        // Switch lanes only between utterances, never halfway through the same explanation.
        if (!segmenter.active && segmenter.hot === 0) realtime = !!socket?.ready;
        const external = onPcm?.({ ...data, origin, runId: run?.id, sourceSeconds: sourceSeconds(), suppressed,
          scene_epoch: epoch, speaker_name: origin === "recorded_source" ? "Grabación original" : $("speaker").value.trim(),
          speaker_role: origin === "recorded_source" ? "cook" : $("role").value,
          answer_to: spokenQuestionId }) === true;
        if (external && !segmenter.active && segmenter.hot === 0) realtime = true;
        const previous = metadata, previousTransport = transport;
        segmenter.push(data.samples, data.sampleRate, sourceSeconds(), { suppressed });
        // Endpointing resets the local segmenter. The final PCM block still belongs
        // to the utterance that just ended, never to a newer question from SSE.
        const current = metadata || previous, lane = transport || previousTransport;
        if (socket?.ready) socket.send(data.samples, data.sampleRate, {
          silence: suppressed || !realtime || lane === "wav",
          metadata: {
            utterance_id: current?.utteranceId ?? null,
            speech_started_at: current?.speechStarted ?? null,
            capture_started_at: current?.captureStarted ?? null,
            answer_to: current?.answerTo ?? null,
            scene_epoch: current?.sceneEpoch ?? epoch,
          },
        });
        refreshSpeech();
      },
      reset: () => { segmenter.reset(); metadata = null; transport = null; },
    };
  }
  const micSegmenter = createSegmenter("microphone"), sourceSegmenter = createSegmenter("recorded_source");
  async function sendAudio(segment, origin, metadata) {
    if (!run || closed) return;
    const body = new FormData();
    body.append("file", pcmWav(segment.pcm, segment.sampleRate), "utterance.wav");
    body.append("origin", origin); body.append("utterance_id", metadata.utteranceId);
    body.append("speaker_name", metadata.speakerName); body.append("speaker_role", metadata.speakerRole);
    body.append("start_seconds", String(segment.start_seconds)); body.append("end_seconds", String(segment.end_seconds));
    body.append("scene_epoch", String(metadata.sceneEpoch));
    body.append("capture_started_at", String(metadata.captureStarted)); body.append("capture_ended_at", String(Date.now() / 1000));
    body.append("speech_started_at", String(metadata.speechStarted));
    if (metadata.answerTo) body.append("answer_to", metadata.answerTo);
    await wire(`/api/live/${encodeURIComponent(run.id)}/audio`, { method: "POST", body, timeout: 25000 });
  }
  async function enableMicrophone(stream = null) {
    micStream = stream || await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false });
    micInput = await createPCMInput(micStream, { onFrame: micSegmenter.push, onError: (exception) => error(exception.message) });
    $("listening").textContent = `Micrófono activo · respuestas de ${$("speaker").value.trim()}`;
  }
  async function enableReplayAudio() {
    const Context = globalThis.AudioContext || globalThis.webkitAudioContext;
    if (!Context) return;
    sourceAudioContext = new Context({ latencyHint: "interactive" });
    await sourceAudioContext.audioWorklet.addModule(new URL("./live-audio-worklet.js", import.meta.url));
    const media = sourceAudioContext.createMediaElementSource(video), processor = new AudioWorkletNode(sourceAudioContext, "compinche-pcm"), monitor = sourceAudioContext.createGain();
    processor.port.onmessage = ({ data }) => sourceSegmenter.push(data);
    media.connect(processor); processor.connect(sourceAudioContext.destination);
    monitor.gain.value = 0; media.connect(monitor); monitor.connect(sourceAudioContext.destination);
    sourceAudio = { media, processor, monitor };
    // ASR always receives the original. The optional audible lane uses alternate turns;
    // microphone PCM is quarantined while that lane plays, not attributed to the observer.
    video.muted = false;
    await sourceAudioContext.resume();
  }
  function stopVoice({ preserveWaiting = false, notifyServer = true } = {}) {
    clearTimeout(questionTimer); questionTimer = null;
    speechEpoch++; questionFetch?.abort(); questionFetch = null;
    if (voicePlayer) { voicePlayer.onended = null; voicePlayer.onerror = null; voicePlayer.pause(); voicePlayer.src = ""; voicePlayer = null; }
    globalThis.speechSynthesis?.cancel();
    if (voiceUrl) { URL.revokeObjectURL(voiceUrl); voiceUrl = null; }
    if (agentSpeaking && notifyServer) control("speaking", { channel: "agent", speaking: false }).catch(() => {});
    agentSpeaking = false; quietUntil = performance.now() + 650; micSegmenter.reset();
    $("interrupt").disabled = true;
    if (!preserveWaiting) waitingForReply = false;
    syncReplayMonitor();
  }
  async function maybeSpeakQuestion() {
    clearTimeout(questionTimer); questionTimer = null;
    const question = latest?.question;
    if (!question || !SPEAKABLE_QUESTION.has(question.status) || seenQuestions.has(question.id) || !running || paused || closed || humanSpeaking || sourceSpeaking || agentSpeaking) return;
    const remainingQuiet = Math.max(lastHumanEnd + 700, quietUntil) - performance.now();
    if (remainingQuiet > 0) {
      const questionId = question.id;
      questionTimer = setTimeout(() => { questionTimer = null; if (latest?.question?.id === questionId) maybeSpeakQuestion(); }, remainingQuiet);
      return;
    }
    seenQuestions.add(question.id);
    const token = ++speechEpoch;
    let playbackStarted = false;
    questionFetch = new AbortController();
    const valid = () => !closed && running && !paused && token === speechEpoch && latest?.question?.id === question.id && !TERMINAL_QUESTION.has(latest?.question?.status);
    const started = () => {
      if (!valid()) { stopVoice(); return; }
      if (playbackStarted) return; playbackStarted = true; spokenQuestionId = question.id;
      if (questionAvailableId === question.id && questionAvailableAt !== null) lastVoiceStartMs = Math.max(0, performance.now() - questionAvailableAt);
      refreshDiagnostics();
      agentSpeaking = true; micSegmenter.reset(); waitingForReply = true;
      syncReplayMonitor();
      $("interrupt").disabled = false; $("listening").textContent = "Compinche pregunta · escucha una explicación después";
      replayWasPlaying = false;
      if (sourceKind() === "replay" && $("pause-replay").checked) { replayWasPlaying = !video.paused; video.pause(); syncReplayMonitor(); }
      control("speaking", { channel: "agent", speaking: true }).catch(() => {});
      control("spoken", { question_id: question.id }).then(acceptSnapshot).catch((exception) => error(exception.message));
    };
    const ended = () => {
      if (token !== speechEpoch) return;
      agentSpeaking = false; quietUntil = performance.now() + 650; micSegmenter.reset();
      $("interrupt").disabled = true;
      $("listening").textContent = micInput ? `Tu turno, ${$("speaker").value.trim()} · puedes responder ahora` : "Puedes responder por escrito";
      control("speaking", { channel: "agent", speaking: false }).catch(() => {});
      if (voiceUrl) { URL.revokeObjectURL(voiceUrl); voiceUrl = null; }
      voicePlayer = null;
    };
    try {
      const blob = await wire(`/api/live/${encodeURIComponent(run.id)}/questions/${encodeURIComponent(question.id)}/voice`, { raw: true, timeout: 12000, signal: questionFetch.signal });
      if (!valid() || humanSpeaking || sourceSpeaking || performance.now() < Math.max(lastHumanEnd + 700, quietUntil)) { seenQuestions.delete(question.id); if (valid()) maybeSpeakQuestion(); return; }
      voiceUrl = URL.createObjectURL(blob); voicePlayer = new Audio(voiceUrl);
      voicePlayer.onplaying = started; voicePlayer.onended = ended; voicePlayer.onerror = () => { ended(); error("No se pudo reproducir la voz. La pregunta sigue disponible."); };
      $("voice-kind").textContent = "Voz de Compinche sintetizada en el servidor.";
      await voicePlayer.play();
    } catch (exception) {
      if (!valid()) return;
      if (!globalThis.speechSynthesis || !globalThis.SpeechSynthesisUtterance) { error("La voz no está disponible. Puedes leer la pregunta y responder."); return; }
      if (humanSpeaking || sourceSpeaking || performance.now() < Math.max(lastHumanEnd + 700, quietUntil)) { seenQuestions.delete(question.id); maybeSpeakQuestion(); return; }
      const utterance = new SpeechSynthesisUtterance(question.text); utterance.lang = "es-ES"; utterance.rate = 1.02;
      utterance.onstart = started; utterance.onend = ended; utterance.onerror = ended;
      $("voice-kind").textContent = "Voz del navegador · alternativa a la síntesis del servidor.";
      speechSynthesis.speak(utterance);
    }
  }
  function startArchive(stream) {
    if (!globalThis.MediaRecorder) return;
    try {
      const type = ["video/webm;codecs=vp8,opus", "video/webm", "video/mp4"].find((candidate) => MediaRecorder.isTypeSupported(candidate));
      archiveStart = sourceSeconds();
      archiveRecorder = new MediaRecorder(stream, { ...(type ? { mimeType: type } : {}), videoBitsPerSecond: 1_000_000 });
      const originalSession = sessionId, originalRun = run.id;
      archiveRecorder.ondataavailable = ({ data }) => {
        if (!data?.size) return;
        archiveChunks.push(data); archiveBytes += data.size;
        if (archiveBytes >= 140 * 1024 * 1024 && archiveRecorder.state !== "inactive") {
          archiveRecorder.stop(); if (!closed) $("capture-info").textContent = "La copia local ha llegado a su límite. La cámara y el aprendizaje continúan.";
        }
      };
      archiveRecorder.onstop = async () => {
        if (!archiveChunks.length) return;
        const blob = new Blob(archiveChunks, { type: archiveRecorder.mimeType || archiveChunks[0].type }); archiveChunks = [];
        if (!closed) {
          archiveUrl = URL.createObjectURL(blob); $("archive-link").href = archiveUrl; $("archive-link").hidden = false;
          $("archive-link").download = `compinche-captura.${blob.type.includes("mp4") ? "mp4" : "webm"}`;
          $("capture-info").textContent = "Conservando la grabación original en esta sesión…";
        }
        const body = new FormData();
        body.append("file", blob, `live-original.${blob.type.includes("mp4") ? "mp4" : "webm"}`);
        body.append("capture_source", "phone_camera"); body.append("timestamp_seconds", String(archiveStart));
        body.append("idempotency_key", `live-archive-${originalRun}`);
        body.append("note", "Original continuous phone recording from the live learning session; preserved locally, not automatically sent to Meta.");
        try {
          // Do not tie saving the original to the view's cancellation controllers.
          const response = await fetch(`/api/sessions/${encodeURIComponent(originalSession)}/evidence`, { method: "POST", body });
          if (!response.ok) throw new Error(`El servidor respondió ${response.status}`);
          if (!closed) $("capture-info").textContent = "Grabación original conservada junto a las explicaciones de esta sesión.";
        } catch (exception) { if (!closed) error(`No se pudo conservar el original en el servidor. Descarga la copia de vídeo: ${exception.message}`); }
      };
      archiveRecorder.start(1000);
    } catch { $("capture-info").textContent = "La observación está activa; este navegador no permite guardar una copia de vídeo."; }
  }
  const motionSender = new LatestOnlySender(async (window) => {
    if (closed || !running || paused || window.epoch !== epoch) return;
    const body = new FormData(); body.append("file", window.blob, `gesture.${window.blob.type.includes("mp4") ? "mp4" : "webm"}`);
    body.append("capture_source", "phone_camera"); body.append("timestamp_seconds", String(window.start));
    body.append("idempotency_key", `live-motion-${run.id}-${window.sequence}`);
    body.append("note", `Live camera gesture window. Source ${window.start.toFixed(3)} to ${window.end.toFixed(3)} seconds; scene epoch ${window.epoch}.`);
    const evidence = await wire(`/api/sessions/${encodeURIComponent(sessionId)}/evidence`, { method: "POST", body, timeout: 20000 });
    if (!closed && running && !paused && epoch === window.epoch) latestMotion = { id: evidence.id, start: window.start, end: window.end, epoch };
  }, () => { if (!closed) $("capture-info").textContent = "La captura continúa con la secuencia de imágenes; el último intervalo de movimiento no llegó."; });
  let motionSequence = 0;
  function startMotionWindow() {
    if (closed || !running || sourceKind() !== "phone" || !globalThis.MediaRecorder) return;
    if (paused) { rollingTimer = setTimeout(startMotionWindow, 500); return; }
    try {
      const chunks = [], start = sourceSeconds(), windowEpoch = epoch;
      const type = ["video/webm;codecs=vp8", "video/webm", "video/mp4"].find((candidate) => MediaRecorder.isTypeSupported(candidate));
      const recorder = rollingRecorder = new MediaRecorder(new MediaStream(sourceStream.getVideoTracks()), { ...(type ? { mimeType: type } : {}), videoBitsPerSecond: 650000 });
      let end = start;
      recorder.ondataavailable = ({ data }) => { if (data?.size) chunks.push(data); };
      recorder.onstop = () => {
        if (closed || !running || paused || windowEpoch !== epoch || !chunks.length) return;
        motionSender.offer({ blob: new Blob(chunks, { type: recorder.mimeType || chunks[0].type }), start, end, epoch: windowEpoch, sequence: motionSequence++ });
      };
      recorder.onerror = () => { if (!closed) $("capture-info").textContent = "La captura conserva imágenes ordenadas; este navegador no permite las ventanas de vídeo."; };
      recorder.start();
      rollingTimer = setTimeout(() => { end = sourceSeconds(); if (recorder.state !== "inactive") recorder.stop(); startMotionWindow(); }, 4000);
    } catch { /* The bounded archive and ordered JPEG sequence remain available. */ }
  }
  async function start() {
    if (closed || starting || running) return;
    if (!$("consent").checked) { error("Marca el consentimiento para iniciar el análisis de esta sesión."); return; }
    if (!$("teacher").value.trim() || !$("speaker").value.trim()) { error("Indica quién cocina y quién responderá al micrófono."); return; }
    starting = true; error(); $("start").disabled = true; setStatus("Preparando cámara y escucha…");
    $("setup").querySelectorAll("input,select").forEach((field) => { field.disabled = true; });
    try {
      if (sourceKind() === "phone") {
        sourceStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment", width: { ideal: 1280 }, height: { ideal: 720 } }, audio: $("microphone").checked ? { echoCancellation: true, noiseSuppression: true, autoGainControl: true } : false });
        if (closed) { sourceStream.getTracks().forEach((track) => track.stop()); return; }
        video.srcObject = sourceStream; video.muted = true; await video.play();
      } else {
        if ($("source").value === "file") {
          const file = validateReplayFile($("file").files?.[0]);
          if (uploadedReplay?.file !== file) {
            setStatus("Guardando el original localmente · todavía no se envía a Meta…");
            const carrier = await wire("/api/sessions", { method: "POST", body: { title: `Original de prueba · ${file.name}`.slice(0, 140), teacher: $("teacher").value.trim(), mode: "record" } });
            const carrierId = carrier.id || carrier.session?.id;
            if (!carrierId) throw new Error("No se pudo preparar la sesión del vídeo original.");
            const body = new FormData(); body.append("file", file, file.name); body.append("capture_source", "upload"); body.append("timestamp_seconds", "0"); body.append("idempotency_key", `replay-original-${carrierId}`); body.append("note", "Original elegido para replay causal. Guardado localmente; esta carga no autoriza análisis de fotogramas futuros.");
            const result = await wire(`/api/sessions/${encodeURIComponent(carrierId)}/evidence`, { method: "POST", body, timeout: 240000 });
            const evidenceId = result.evidence?.id || result.id;
            if (!evidenceId) throw new Error("El servidor no ha devuelto el identificador del vídeo.");
            uploadedReplay = { file, evidenceId };
          }
          replayEvidenceId = uploadedReplay.evidenceId;
        } else replayEvidenceId = demo?.original_evidence_id || null;
        if (!replayEvidenceId) throw new Error("No está disponible el vídeo para esta prueba.");
        video.src = `/api/media/${encodeURIComponent(replayEvidenceId)}`; video.currentTime = 0;
        await enableReplayAudio();
      }
      if (closed) return;
      run = await wire("/api/live", { method: "POST", body: { consent: true, source_kind: sourceKind(), teacher: $("teacher").value.trim(), speaker_name: $("speaker").value.trim(), speaker_role: $("role").value, mode: learnMode() ? "learn" : "record", ...(learnMode() ? { lesson_session_id: $("lesson").value } : {}), ...(sourceKind() === "replay" ? { source_evidence_id: replayEvidenceId, replay_pause_on_question: $("pause-replay").checked, monitor_original_audio: $("original-audio").checked } : {}) } });
      if (closed) { fetch(`/api/live/${encodeURIComponent(run.id)}/control`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "stop" }), keepalive: true }).catch(() => {}); return; }
      running = true; paused = false; wallStart = performance.now();
      if (sourceKind() === "replay") connectAudioSocket("recorded_source");
      if ($("microphone").checked) connectAudioSocket("microphone");
      sessionId = run.session_id; onSession(sessionId, run);
      $("lesson-link").href = `#live/${encodeURIComponent(sessionId)}`; $("lesson-link").hidden = false;
      if (!learnMode()) { $("practice-link").href = `#practice/${encodeURIComponent(sessionId)}`; $("practice-link").hidden = false; }
      for (const [name, suffix] of [["resource-html", "learning-resource?format=html&download=true"], ["resource-json", "learning-resource?format=json&download=true"], ["diagnostic-download", "diagnostics?download=true"]]) { $(name).href = `/api/live/${encodeURIComponent(run.id)}/${suffix}`; $(name).hidden = false; }
      $("exports").hidden = false;
      $("setup").querySelectorAll("input,select").forEach((field) => { field.disabled = true; });
      $("original-audio").disabled = sourceKind() !== "replay";
      $("pause").disabled = false; $("stop").disabled = false; $("send-answer").disabled = false;
      $("source-label").textContent = sourceKind() === "replay" ? `Replay causal · ${$("pause-replay").checked ? "pausa opcional al responder" : "escena continua como una cámara"}` : "Cámara del teléfono · captura en directo";
      if ($("microphone").checked) {
        try { await enableMicrophone(sourceKind() === "phone" ? sourceStream : null); }
        catch (exception) { error(`El micrófono no está disponible: ${exception.message}. Puedes responder por escrito.`); }
      }
      if (sourceKind() === "phone") { startArchive(sourceStream); startMotionWindow(); } else { await video.play(); syncReplayMonitor(); }
      setStatus("Observando y escuchando"); acceptSnapshot(run);
      captureTimer = setInterval(captureFrame, 1000); captureFrame(); connectEvents();
    } catch (exception) {
      error(exception.message); setStatus("No se pudo iniciar la sesión");
      if (run) await control("stop").catch(() => {});
      running = false;
      for (const socket of audioSockets.values()) socket.close();
      sourceStream?.getTracks().forEach((track) => track.stop()); micStream?.getTracks().forEach((track) => track.stop());
      await micInput?.stop().catch(() => {}); micInput = null;
      if (sourceAudioContext) { await sourceAudioContext.close().catch(() => {}); sourceAudioContext = null; sourceAudio = null; }
      video.pause(); video.removeAttribute("src"); video.srcObject = null;
      // A MediaElementAudioSourceNode cannot be attached to the same element twice,
      // even after its AudioContext closes. A failed setup gets a fresh element.
      video.removeEventListener("ended", ended);
      video.removeEventListener("play", playbackChanged); video.removeEventListener("pause", playbackChanged);
      const replacement = video.cloneNode(false); video.replaceWith(replacement); video = replacement;
      video.addEventListener("ended", ended);
      video.addEventListener("play", playbackChanged); video.addEventListener("pause", playbackChanged);
      $("setup").querySelectorAll("input,select").forEach((field) => { field.disabled = false; });
      run = null;
    } finally { starting = false; if (!closed && !running) $("start").disabled = false; }
  }
  async function pauseResume() {
    if (!running) return;
    paused = !paused;
    if (paused) { stopVoice({ preserveWaiting: true }); micSegmenter.reset(); sourceSegmenter.reset(); setSpeech("human", false); setSpeech("source", false); if (sourceKind() === "replay") video.pause(); }
    await control(paused ? "pause" : "resume").catch((exception) => error(exception.message));
    if (!paused && sourceKind() === "replay" && (!waitingForReply || !$("pause-replay").checked)) video.play().catch(() => {});
    $("pause").textContent = paused ? "Continuar" : "Pausar"; setStatus(paused ? "Aprendizaje en pausa" : "Observando y escuchando");
  }
  async function stop({ notifyServer = true } = {}) {
    if (!running) return;
    stoppedSourceSeconds = sourceSeconds(); running = false; stopVoice({ notifyServer });
    clearInterval(captureTimer); clearTimeout(pollTimer); sender.close(); motionSender.close();
    eventSource?.close(); eventSource = null; eventsReady = false; clearTimeout(eventsFallbackTimer);
    paused = false;
    for (const socket of audioSockets.values()) socket.close();
    clearTimeout(rollingTimer); if (rollingRecorder?.state !== "inactive") rollingRecorder?.stop();
    if (archiveRecorder?.state !== "inactive") archiveRecorder?.stop();
    sourceStream?.getTracks().forEach((track) => track.stop()); micStream?.getTracks().forEach((track) => track.stop());
    video.pause(); setStatus(latest?.status === "interrupted" ? "La sesión se ha interrumpido · captura detenida" : "Sesión conservada"); $("pause").disabled = true; $("stop").disabled = true; $("send-answer").disabled = true;
    $("listening").textContent = "Micrófono detenido";
    if (notifyServer) { try { acceptSnapshot(await control("stop")); } catch (exception) { error(exception.message); } }
    await micInput?.stop().catch(() => {}); micInput = null;
    if (sourceAudioContext) await sourceAudioContext.close().catch(() => {});
  }
  function bind(name, event, handler) { const node = $(name); node.addEventListener(event, handler); localControllers.push(() => node.removeEventListener(event, handler)); }
  bind("start", "click", start);
  async function loadLessons() {
    try {
      const { sessions = [] } = await wire("/api/sessions", { timeout: 9000 });
      const lessons = sessions.filter((item) => item.mode === "record" && item.confirmed_count > 0);
      $("lesson").innerHTML = lessons.length ? lessons.map((item) => `<option value="${esc(item.id)}" ${item.id === lessonId ? "selected" : ""}>${esc(item.teacher || "Sesión")} · ${esc(item.title || "")} · ${item.confirmed_count} ficha${item.confirmed_count === 1 ? "" : "s"} confirmada${item.confirmed_count === 1 ? "" : "s"}</option>`).join("") : `<option value="">Todavía no hay fichas confirmadas</option>`;
      if (lessonId && !lessons.some((item) => item.id === lessonId)) $("lesson-help").textContent = "Esa sesión aún no tiene fichas confirmadas: confírmalas antes de practicar.";
    } catch (exception) { $("lesson").innerHTML = `<option value="">No se pudieron cargar las sesiones</option>`; }
  }
  function syncMode() {
    const learning = learnMode();
    $("lesson-label").hidden = !learning;
    $("start").textContent = learning ? "Empezar a cocinar con Compinche" : "Iniciar aprendizaje";
    if (learning && !$("teacher").value.trim()) $("teacher").value = "David";
    if (learning) { $("role").value = "cook"; loadLessons(); }
  }
  bind("mode", "change", syncMode);
  syncMode();
  bind("pause", "click", pauseResume);
  bind("stop", "click", () => stop());
  bind("source", "change", () => { $("file-label").hidden = $("source").value !== "file"; $("original-audio-label").hidden = sourceKind() !== "replay"; $("pause-replay-label").hidden = sourceKind() !== "replay"; $("role").value = sourceKind() === "phone" ? "cook" : "observer"; $("source-help").textContent = sourceKind() === "replay" ? "El vídeo simula una entrada de cámara: solo se observa lo ya transcurrido y la escena continúa durante la conversación. Puedes escuchar el original con turnos de voz separados. Responde quien esté presente; la persona grabada no responde automáticamente." : "La cámara y el micrófono siguen activos mientras el agente piensa. Indica el nombre de quien realmente responderá. Se conserva una copia del original al terminar."; });
  bind("original-audio", "change", () => { syncReplayMonitor(); refreshDiagnostics(); });
  bind("interrupt", "click", () => { const qid = latest?.question?.id; stopVoice({ preserveWaiting: true }); control("barge_in", { question_id: qid }).catch((exception) => error(exception.message)); $("listening").textContent = "Voz interrumpida · puedes responder al micrófono"; });
  bind("postpone", "click", async () => { const qid = latest?.question?.id; stopVoice(); try { acceptSnapshot(await control("postpone", { question_id: qid })); } catch (exception) { error(exception.message); } if (sourceKind() === "replay" && replayWasPlaying && running && !paused) video.play().catch(() => {}); });
  bind("repeat", "click", () => { if (latest?.question?.id) { seenQuestions.delete(latest.question.id); maybeSpeakQuestion(); } });
  bind("answer-form", "submit", async (event) => {
    event.preventDefault(); const text = $("answer").value.trim(); if (!text || !run || !running || paused) return;
    const button = $("send-answer"); button.disabled = true;
    try {
      const qid = spokenQuestionId;
      // A typed explanation remains explicitly authored; it is not an automatic transcription.
      const result = await wire(`/api/live/${encodeURIComponent(run.id)}/answer`, { method: "POST", body: {
        text, speaker_name: $("speaker").value.trim(), speaker_role: $("role").value, answer_to: qid,
        utterance_id: identifier(), source_seconds: sourceSeconds(), scene_epoch: epoch,
      } });
      $("answer").value = ""; if (result?.snapshot) acceptSnapshot(result.snapshot);
    } catch (exception) { error(exception.message); }
    finally { if (!closed) button.disabled = !running || paused; }
  });
  const cardsClick = async (event) => {
    const confirm = event.target.closest("[data-confirm]");
    if (confirm) { try { acceptSnapshot(await control("confirm", { card_id: confirm.dataset.confirm })); } catch (exception) { error(exception.message); } return; }
    const source = event.target.closest("[data-source]");
    if (source) {
      try {
        const data = await wire(`/api/sessions/${encodeURIComponent(sessionId)}/sources/${encodeURIComponent(source.dataset.source)}`);
        const entity = data.source || data;
        if (entity.url?.startsWith("/api/media/")) window.open(entity.url, "_blank", "noopener");
        else { const text = entity.text || entity.note || "La fuente no tiene vista multimedia."; error(text); }
      } catch (exception) { error(exception.message); }
    }
  };
  $("cards").addEventListener("click", cardsClick);
  const ended = () => { if (running && sourceKind() === "replay") { setStatus("El vídeo ha terminado · puedes responder o conservar la sesión"); } };
  const playbackChanged = () => { syncReplayMonitor(); refreshDiagnostics(); };
  video.addEventListener("ended", ended);
  video.addEventListener("play", playbackChanged); video.addEventListener("pause", playbackChanged);
  return () => {
    if (closed) return; closed = true;
    const lastRun = run, shouldStop = running; running = false;
    stopVoice(); sender.close(); motionSender.close(); clearInterval(captureTimer); clearTimeout(pollTimer);
    eventSource?.close(); eventSource = null; eventsReady = false; clearTimeout(eventsFallbackTimer);
    for (const socket of audioSockets.values()) socket.close();
    clearTimeout(rollingTimer); if (rollingRecorder?.state !== "inactive") rollingRecorder?.stop();
    for (const controller of controllers) controller.abort();
    localControllers.forEach((remove) => remove()); $("cards").removeEventListener("click", cardsClick); video.removeEventListener("ended", ended);
    video.removeEventListener("play", playbackChanged); video.removeEventListener("pause", playbackChanged);
    if (archiveRecorder?.state !== "inactive") archiveRecorder?.stop();
    sourceStream?.getTracks().forEach((track) => track.stop()); micStream?.getTracks().forEach((track) => track.stop());
    micInput?.stop().catch(() => {}); sourceAudio?.media.disconnect(); sourceAudio?.processor.disconnect(); sourceAudioContext?.close().catch(() => {});
    video.pause(); video.srcObject = null; video.removeAttribute("src"); video.load();
    if (archiveUrl) URL.revokeObjectURL(archiveUrl);
    if (lastRun && shouldStop) fetch(`/api/live/${encodeURIComponent(lastRun.id)}/control`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "stop" }), keepalive: true }).catch(() => {});
  };
}
