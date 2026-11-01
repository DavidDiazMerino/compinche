import { icon } from "./icons.js";

const esc = (text = "") => String(text ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
const clock = (time = 0) => `${String(Math.floor(time / 60)).padStart(2, "0")}:${String(Math.floor(time % 60)).padStart(2, "0")}`;

export function deviceMode(value) {
  return value === "glasses-replay" ? value : "phone";
}

export function channelSelector(mode) {
  const glasses = deviceMode(mode) === "glasses-replay";
  return `<section class="device-choice" aria-label="Choose how to learn"><div class="filter-tabs" role="group" aria-label="Learning device"><button data-action="device-mode" data-mode="phone" aria-pressed="${!glasses}" class="${!glasses ? "active" : ""}">${icon("video")} Phone</button><button data-action="device-mode" data-mode="glasses-replay" aria-pressed="${glasses}" class="${glasses ? "active" : ""}">${icon("volume")} Ray-Ban Meta</button></div><p class="fine-print">${glasses ? "Screenless glasses · recorded replay for testing. Use the phone as the controller; no physical glasses are connected by this page." : "Watch the recipe, record your attempt and review the original evidence on your phone."}</p></section>`;
}

export function replayClip(clips, seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return null;
  const clip = clips.find((item) => seconds >= item.start_seconds && seconds < item.start_seconds + item.duration_seconds);
  return clip ? { ...clip, local_seconds: seconds - clip.start_seconds } : null;
}

export function keptExplanation(data, id) {
  const owner = data.session.source_session_id || data.session.id;
  return data.knowledge.find((item) => item.id === id && item.session_id === owner && item.confirmed)?.text || null;
}

export function audioControls(mode) {
  if (deviceMode(mode) !== "glasses-replay") return "";
  return `<section class="glasses-controller"><p class="eyebrow">RECORDED GLASSES VIEW</p><h2>Your hands stay with the cooking.</h2><p>The recording supplies the camera view for this replay. The original sound plays on this controller; confirmed explanations can be read aloud.</p><div class="original-actions"><button class="button button-dark" data-action="replay-play">${icon("play")} Start replay</button><button class="button button-light" data-action="replay-pause">Pause</button><button class="text-link" data-action="stop-speaking">Stop reading</button><button class="text-link" data-action="observe-replay">Ask about this recorded moment</button></div><p id="replay-position" class="fine-print" aria-live="off">Ready to replay · 00:00</p><button class="text-link" data-action="refresh-sdk-input">Select the latest SDK capture</button><button class="text-link" data-action="sdk-help">Use the official MockDeviceKit adapter ${icon("arrow")}</button></section>`;
}

export function recipeMarkup(data, demo, mode) {
  const original = data.evidence.find((item) => item.id === demo.original_evidence_id);
  const kept = data.knowledge.filter((item) => item.session_id === (data.session.source_session_id || data.session.id) && item.confirmed);
  return `${channelSelector(mode)}<div class="compact-heading"><a class="back-link" href="#learn">${icon("back")} Your recipes</a><span class="tag sage">Real family recording</span></div><div class="story-heading"><p class="eyebrow">DAD’S KITCHEN</p><h1>His hands.<br><em>Your turn.</em></h1><p>${esc(demo.title)} · ${clock(demo.duration_seconds)} · Original recording</p></div><div class="experience-layout"><section class="experience-main"><div class="original-video"><video id="original-video" data-evidence-id="${esc(original?.id || "")}" src="${esc(original?.url || "")}" poster="${esc(demo.poster_url || "")}" controls playsinline preload="metadata" aria-label="The real original recording of Dad cooking"></video></div>${audioControls(mode)}<div class="original-actions"><button class="button button-dark" data-action="start-learning">Learn from this recipe ${icon("arrow")}</button><a class="text-link" href="#live/${esc(data.session.id)}">Review the original conversation ${icon("arrow")}</a></div><div class="section-heading"><h2>Moments worth returning to.</h2></div><div class="recipe-moments">${demo.clips.map((clip, index) => `<button class="journal-card" data-action="open-moment" data-id="${esc(clip.evidence_id)}" data-session="${esc(data.session.id)}"><span class="eyebrow">${clock(clip.start_seconds)} · MOMENT ${index + 1}</span><p>${esc(clip.summary || "A clip from the original recording, ready to review.")}</p><span class="fine-print">${clip.summary ? "Automatic visual description · needs review" : "Prepared original clip"}</span></button>`).join("")}</div></section><aside class="experience-aside"><div class="handoff-card"><p class="eyebrow">THE LESSON YOU KEEP</p><h2>The gesture.<br>The reason.<br>His way.</h2><p>The full recording remains the source. Review the discoveries before treating them as instructions.</p>${kept.length ? kept.map((item) => `<article class="knowledge-card"><span class="tag sage">Human reviewed</span><p>${esc(item.text)}</p><button class="text-link" data-action="speak-knowledge" data-id="${esc(item.id)}">${icon("volume")} Read this explanation</button><button class="text-link" data-action="open-source" data-id="${esc(item.id)}">Check the source</button></article>`).join("") : `<p>No explanation has been human confirmed yet. Watch the original, then review the candidate discoveries below.</p>`}</div><div class="handoff-card"><p class="eyebrow">PRESERVE THE PERSON</p><p>Automatic transcripts and visual descriptions remain unconfirmed. A question stays open until someone explicitly answers it. The recording is never replaced by a generated story.</p></div></aside></div>`;
}
