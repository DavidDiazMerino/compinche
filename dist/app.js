import { mountLive } from "./live.js";
import {
  mountCapture,
  listRecordings,
  getRecording,
  deleteRecording,
} from "./capture.js";
import { icon, brandMark } from "./icons.js";
import { discoveries, scenes, steps } from "./data.js";
import { api } from "./api.js";
import { pendingTurn, deliverTurn, discardPendingTurn } from "./turns.js";
import { channelSelector, recipeMarkup, audioControls, keptExplanation, deviceMode } from "./experiences.js";
import { selectedInterval, elapsedReplayInterval } from "./analysis.js";
import { searchKey, findPersonalNotes } from "./memory.js";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = (value = "") =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const clock = (value) =>
  `${String(Math.floor((Number(value) || 0) / 60)).padStart(2, "0")}:${String(Math.floor((Number(value) || 0) % 60)).padStart(2, "0")}`;
const read = (key, fallback) => {
  try {
    return JSON.parse(localStorage.getItem(key)) || fallback;
  } catch {
    return fallback;
  }
};
const previous = read("compinche-design-v1", {});
let state = read("compinche-v2", {
  saved: previous.saved || [],
  notes: previous.notes || {},
  step: 0,
  links: {},
  jobs: [],
  checks: [],
});
state = {
  saved: [],
  notes: {},
  step: 0,
  links: {},
  jobs: [],
  checks: [],
  ...state,
};
let recordings = [],
  sessions = [],
  health = null,
  familyDemo = null,
  currentRoute = "home",
  currentId = "",
  scene = 2;
let cleanupCapture = null,
  cleanupExperience = null,
  modalCleanup = null,
  pageURLs = [],
  toastTimer,
  epoch = 0,
  dialogFocus,
  activeJobTimer;
let story = null,
  bundle = null,
  filter = "all",
  query = "",
  busy = false;
let sourceEpoch = 0, observationContext = null, memoryEpoch = 0, memoryTimer, sourceToOpen = null;
let realMemories = [];
state.selectedEvidence = state.selectedEvidence || {};
const routeToken = () => `${currentRoute}/${currentId}`;
const stillHere = (token) => routeToken() === token;
async function refreshHere(token) {
  if (stillHere(token)) await render();
}
const save = () => {
  try {
    localStorage.setItem("compinche-v2", JSON.stringify(state));
    return true;
  } catch {
    toast("Storage is full. Export your notes to keep a copy.");
    return false;
  }
};
const isMetaReady = () => !!health?.agent?.configured;
const isSAMReady = () => !!health?.sam?.configured;
const navigate = (path) => {
  if (location.hash === `#${path}`) render();
  else location.hash = path;
};
function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("visible"), 4500);
}
function objectURL(blob) {
  const url = URL.createObjectURL(blob);
  pageURLs.push(url);
  return url;
}
function label(text, cls = "") {
  return `<span class="tag ${cls}">${text}</span>`;
}
function pageIntro(eyebrow, title, description) {
  return `<div class="page-intro"><p class="eyebrow">${eyebrow}</p><h1>${title}</h1>${description ? `<p class="page-description">${description}</p>` : ""}</div>`;
}
function modeRail(active) {
  return `<div class="mode-rail" aria-label="Choose your experience"><a href="#teach" class="${active === "record" ? "active" : ""}">${icon("camera")} Capture their way</a><a href="#learn" class="${active === "learn" ? "active" : ""}">${icon("hand")} Learn their way</a></div>`;
}
function shell(content) {
  const active = ["teach", "record", "story", "demo"].includes(currentRoute)
    ? "record"
    : ["learn", "practice", "cook", "live", "recipe", "example-cook"].includes(currentRoute)
      ? "learn"
      : currentRoute;
  return `<div class="app-shell"><header class="app-header"><a class="brand" href="#home" aria-label="Compinche home">${brandMark}<span>compinche<span class="brand-dot">.</span></span></a><nav class="desktop-nav" aria-label="Main navigation"><a href="#teach" ${active === "record" ? 'aria-current="page"' : ""}>Capture a recipe</a><a href="#learn" ${active === "learn" ? 'aria-current="page"' : ""}>Learn a recipe</a><a href="#memory" ${active === "memory" ? 'aria-current="page"' : ""}>Your memories</a></nav><div class="header-actions"><a class="connection" href="#system" aria-label="Agent connection status"><span class="status-dot ${isMetaReady() ? "on" : ""}"></span><span>${isMetaReady() ? (health.agent.live_verified ? "Agent verified" : "Meta configured") : health ? "Local studio" : "Studio setup"}</span></a><button class="round-button" data-action="search" aria-label="Search your memories">${icon("search")}</button></div></header><main id="main" tabindex="-1" class="main-content ${currentRoute === "home" ? "home-content" : ""}">${content}</main><footer class="app-footer"><span>${brandMark} Good things are passed on.</span><button data-action="about">Made to remember ${icon("arrowUp")}</button></footer><nav class="bottom-nav" aria-label="Mobile navigation">${[
    ["home", "Home", "home"],
    ["record", "Capture", "camera"],
    ["learn", "Learn", "hand"],
    ["memory", "Memories", "memory"],
  ]
    .map(
      ([id, name, ic]) =>
        `<a href="#${id === "record" ? "teach" : id}" class="${active === id ? "active" : ""}" ${active === id ? 'aria-current="page"' : ""}>${icon(ic)}<span>${name}</span></a>`,
    )
    .join("")}</nav></div>`;
}
function modeCard(type) {
  const capture = type === "record";
  return `<a class="mode-card ${capture ? "capture-card" : "learn-card"}" href="#${capture ? "teach" : type}"><div class="mode-card-top"><span class="mode-number">${capture ? "01" : "02"}</span><span class="mode-icon">${icon(capture ? "camera" : "hand")}</span></div><div><p class="eyebrow">${capture ? "THEY COOK. YOU KEEP IT." : "THEIR KNOW-HOW. YOUR KITCHEN."}</p><h2>${capture ? "Capture<br>their way." : "Learn<br>their way."}</h2><p>${capture ? "The gestures, the decisions.<br>The things they never write down." : "Their small secrets, right there<br>when you need them."}</p></div><div class="mode-card-bottom"><span>${capture ? "Record a recipe" : "Start cooking"}</span>${icon("arrow")}</div></a>`;
}
function discoveryCard(d) {
  return `<button class="discovery-card" data-action="discovery" data-id="${d.id}"><div class="discovery-image"><img src="./assets/${d.image}" alt="Illustrative cooking detail" loading="lazy"><span class="image-play">${icon("play")}</span></div><div class="discovery-content"><p class="eyebrow">${d.label}</p><h3>${d.title}</h3><p>${d.text}</p><span class="card-foot">${state.saved.includes(d.id) ? "Saved to your memories" : "From our example story"} ${icon(state.saved.includes(d.id) ? "check" : "arrowUp")}</span></div></button>`;
}
function storyRows() {
  return recordings
    .map(
      (r) =>
        `<a class="story-row" href="#story/${encodeURIComponent(r.id)}"><span class="story-row-icon">${icon("video")}</span><div><h3>${esc(r.title || "Untitled recipe")}</h3><p>${esc(r.teacher || "Your kitchen")} <span>·</span> ${clock(r.duration)} <span>·</span> Your recording</p></div>${icon("arrow")}</a>`,
    )
    .join("");
}
function home() {
  if (familyDemo?.available) return familyHome();
  return `<section class="home-opening"><div class="home-intro"><p class="eyebrow"><span class="tiny-star">✳</span> A LITTLE OF THEM. ALWAYS WITH YOU.</p><h1>From their hands.<br><em>To yours.</em></h1><p class="home-description">Capture how someone cooks.<br>Keep the little things that make it theirs.</p><div class="home-prompt"><span class="drawn-line"></span><span>Two ways to make a memory.</span></div><div class="home-modes">${modeCard("record")}${modeCard("learn")}</div></div><a class="home-photo" href="#demo"><img src="./assets/hero-croquetas.webp" alt="Golden croquettes on a ceramic plate in a warm kitchen" fetchpriority="high"><span class="photo-note">“Just a little more.”</span><span class="photo-label">OUR FIRST STORY <span>EXAMPLE</span></span><div class="photo-bottom"><div><span class="little-avatar">m.</span><h2>Mom’s croquettes.</h2><p>One recipe. A whole way of doing things.</p></div><span class="photo-play">${icon("play")}</span></div></a></section><section class="home-discoveries"><div class="section-heading"><div><p class="eyebrow">THE DETAILS THAT STAY WITH YOU</p><h2>A recipe has layers.</h2></div><a href="#memory" class="text-link">Explore ${icon("arrow")}</a></div><div class="discovery-grid">${discoveries.map(discoveryCard).join("")}</div></section>${recordings.length ? `<section><div class="section-heading"><h2>Your kitchen stories.</h2><a href="#teach" class="text-link">${icon("plus")} Add one</a></div>${storyRows()}</section>` : `<div class="home-note"><span class="note-symbol">${brandMark}</span><p>Some things you can write down.<br><strong>Some things, someone has to show you.</strong></p></div>`}`;
}
function practiceSessions() {
  const lessons = sessions.filter((item) => item.mode === "record" && item.confirmed_count > 0);
  return lessons.length ? `<section lang="es"><div class="section-heading"><h2>Practica lo que te enseñaron.</h2>${label(`${lessons.length} sesión${lessons.length === 1 ? "" : "es"}`, "sage")}</div>${lessons.map((item) => `<a class="story-row" href="#practice/${esc(item.id)}">${icon("hand")}<div><h3>${esc(item.teacher || "Sesión")} · ${esc(item.title || "")}</h3><p>${item.confirmed_count} ficha${item.confirmed_count === 1 ? "" : "s"} confirmada${item.confirmed_count === 1 ? "" : "s"} · Compinche te guía en tu cocina</p></div>${icon("arrow")}</a>`).join("")}</section>` : "";
}
function learningLibrary() {
  if (familyDemo?.available) return familyLibrary();
  return `${modeRail("learn")}${pageIntro("YOUR TURN TO COOK", "Make it <em>their way.</em>", "Choose a story. Bring their gestures and judgement into your own kitchen.")}${practiceSessions()}<a class="recipe-feature" href="#cook"><img src="./assets/hero-croquetas.webp" alt="A plate of golden croquettes"><div class="recipe-feature-content"><span class="tag light">Explore the example</span><div><p class="eyebrow">OUR FIRST KITCHEN STORY</p><h2>Mom’s<br>croquettes.</h2><p>Six moments. Three little secrets.</p></div><span class="button button-white">Let’s cook ${icon("arrow")}</span></div></a><div class="section-heading"><h2>From your own recordings.</h2>${label(`${recordings.length} stories`)}</div>${recordings.length ? storyRows() : `<div class="empty-card"><span class="empty-icon">${icon("memory")}</span><h3>Start with someone you love learning from.</h3><p>Capture a recipe first. Its original video and confirmed discoveries become the companion for your next attempt.</p><a href="#teach" class="button button-dark">Capture a recipe ${icon("arrow")}</a></div>`}<div class="principle-row"><span>01 <strong>Watch their gesture</strong></span><span>02 <strong>Try it yourself</strong></span><span>03 <strong>Keep what you learn</strong></span></div>`;
}
function demo() {
  const s = scenes[scene];
  return `${modeRail("record")}<div class="compact-heading"><a href="#home" class="back-link">${icon("back")} Our first story</a>${label("Illustrative example", "warm")}</div><div class="experience-layout"><section class="experience-main"><div class="camera-window demo-window"><video id="demo-video" src="./assets/cooking-demo.mp4" poster="./assets/${s.image}" playsinline muted preload="auto" loop aria-label="Illustrative kitchen footage"></video><div class="video-shade"></div><div class="camera-top"><span class="glass-tag">${icon("video")} A KITCHEN STORY</span><span class="glass-tag">STOCK FOOTAGE</span></div><button class="video-big-play" data-action="play-demo" aria-label="Play illustrative video">${icon("play")}</button><div class="camera-bottom"><span class="eyebrow">${s.tag}</span><h1>${s.short}</h1></div></div><div class="video-control-bar"><button class="round-button" data-action="play-demo" aria-label="Play or pause video">${icon("play")}</button><span id="demo-time">00:00</span><input id="demo-seek" type="range" min="0" max="12" step=".1" value="${s.seek}" aria-label="Video position"><span>00:12</span></div><div class="scene-tabs" aria-label="Story chapters">${scenes.map((s, i) => `<button class="scene-tab ${i === scene ? "active" : ""}" data-action="scene" data-index="${i}" aria-pressed="${scene === i}"><span>0${i + 1}</span>${s.name}</button>`).join("")}</div></section><aside class="experience-aside"><div class="assistant-card"><div class="assistant-label"><span>${brandMark}</span><div><strong>Compinche</strong><small>Learning from the person who knows</small></div>${label("Script")}</div><p class="assistant-question">“${s.question}”</p><p class="assistant-explainer">${s.text}</p><div class="evidence-link">${icon("layers")} A moment → a question → their explanation</div>${s.lesson ? `<button class="button button-light full-width" data-action="discovery" data-id="${s.lesson}">Open this discovery ${icon("arrow")}</button>` : `<a href="#teach" class="button button-dark full-width">Capture your own story ${icon("arrow")}</a>`}</div><div class="handoff-card"><span class="eyebrow">THE OTHER HALF OF THE STORY</span><h2>Now, let it become yours.</h2><p>Bring the same knowledge to a different kitchen.</p><a href="#cook" class="text-link">Try the learning experience ${icon("arrow")}</a></div><p class="fine-print">This example follows the story script. The footage is illustrative; these questions were not generated by analysing it.</p></aside></div>`;
}
function cook() {
  const index = Math.max(0, Math.min(5, state.step || 0)),
    s = steps[index];
  return `${modeRail("learn")}<div class="cook-heading"><div><p class="eyebrow">MOM’S CROQUETTES</p><h1>Your hands.<br><em>Her way.</em></h1></div><div class="step-counter"><strong>${String(index + 1).padStart(2, "0")}</strong><span>/ 06</span></div></div><div class="step-dots" aria-label="Cooking steps">${steps.map((s, i) => `<button data-action="cook-step" data-index="${i}" class="${i <= index ? "done" : ""} ${i === index ? "active" : ""}" aria-label="Step ${i + 1}: ${s.name}" ${i === index ? 'aria-current="step"' : ""}><span></span></button>`).join("")}</div><div class="experience-layout cook-layout"><section class="experience-main"><div class="cooking-photo"><img src="./assets/${s.image}" alt="Illustrative image for ${s.name}"><span class="glass-tag cooking-photo-tag">${icon("memory")} HER LITTLE SECRETS, AT HAND</span><div class="cooking-photo-content"><span class="eyebrow">${s.name}</span><h2>${s.title}</h2><button class="button button-white" data-action="step-action">${icon(s.type === "compare" ? "scale" : s.type === "finish" ? "bookmark" : "play")}${s.action}</button></div></div></section><aside class="experience-aside"><div class="assistant-card cook-assistant"><div class="assistant-label"><span>${brandMark}</span><div><strong>Compinche</strong><small>Right here, when you need me</small></div>${label("Example")}</div><p class="cooking-message">${s.message}</p><button class="text-link" data-action="ask-note">I have a question ${icon("arrow")}</button></div>${index === 0 ? `<div class="checklist">${["Ingredients within reach", "Your pan ready", "Time to take it slowly"].map((text, i) => `<button data-action="check" data-index="${i}" aria-pressed="${state.checks.includes(i)}" class="${state.checks.includes(i) ? "checked" : ""}"><span>${state.checks.includes(i) ? icon("check") : ""}</span>${text}</button>`).join("")}</div>` : ""}<div class="cook-navigation"><button class="round-button" data-action="previous-step" aria-label="Previous step" ${index === 0 ? "disabled" : ""}>${icon("back")}</button><button class="button button-dark" data-action="${index === 5 ? "finish" : "next-step"}">${index === 5 ? "Keep my reflection" : "Got it. Let’s go on."}${icon("arrow")}</button></div><button class="text-link compare-link" data-action="compare">${icon("scale")} My pan is different</button><p class="fine-print">Example journey · your progress stays on this device.</p></aside></div>`;
}
function familyHome() {
  return `<section class="family-hero"><div><p class="eyebrow">FROM HIS HANDS TO YOURS</p><h1>Some things<br>live in <em>his hands.</em></h1><p>The recording is real. The way he cooks is his. Keep the decisions that help you make it your own.</p><div class="original-actions"><a class="button button-dark" href="#teach">Aprender mientras cocina ${icon("arrow")}</a><a class="button button-light" href="#recipe/${esc(familyDemo.session_id)}">Ver la grabación original</a><a class="button button-light" href="#learn">Learn his way ${icon("hand")}</a></div><p class="fine-print">${clock(familyDemo.duration_seconds)} · Real family recording · ${familyDemo.analysis_count ? "Recorded Meta analysis available for review" : "Original video ready"}</p></div><a class="family-cover" href="#recipe/${esc(familyDemo.session_id)}"><img src="${esc(familyDemo.poster_url)}" alt="Dad cooking in the original family recording"><span>${icon("play")} Watch the original</span></a></section>${channelSelector(state.deviceMode)}<div class="handoff-card"><h2>A moment. A question. His explanation.</h2><p>Compinche observa los gestos, escucha la explicación y pregunta cuando falta un criterio. Prueba el flujo con el vídeo de papá o con la cámara del teléfono.</p><a class="text-link" href="#teach">Capture another recipe ${icon("arrow")}</a></div><details class="example-separation"><summary>Explore the earlier illustrative example</summary><p>This scripted example uses stock footage and is separate from your family recording.</p><a href="#example" class="text-link">Open the example</a></details>`;
}
function familyLibrary() {
  return `${pageIntro("RECIPES WITH A PERSON BEHIND THEM", "Watch their way.<br><em>Learn it in yours.</em>", "The original recording and reviewed explanations stay together. Your attempts have their own learning session.")}${channelSelector(state.deviceMode)}${practiceSessions()}<a class="family-library-card" href="#recipe/${esc(familyDemo.session_id)}"><img src="${esc(familyDemo.poster_url)}" alt="Dad’s original cooking recording"><div><span class="tag sage">Real family recording</span><h2>${esc(familyDemo.title)}</h2><p>Dad · ${clock(familyDemo.duration_seconds)} · View the recipe or start learning</p><span class="text-link">Open the recipe ${icon("arrow")}</span></div></a>${sessions.filter((item) => item.mode === "learn").length ? `<section><div class="section-heading"><h2>Your learning sessions.</h2></div>${sessions.filter((item) => item.mode === "learn").map((item) => `<a class="story-row" href="#live/${esc(item.id)}">${icon("hand")}<div><h3>${esc(item.title)}</h3><p>Your own attempt, linked to the original lesson.</p></div>${icon("arrow")}</a>`).join("")}</section>` : ""}<a href="#teach" class="text-link">Capture another recipe ${icon("arrow")}</a>`;
}
async function familyRecipe(id = familyDemo?.session_id) {
  if (!id || !familyDemo?.available || id !== familyDemo.session_id) throw new Error("Choose the prepared family recipe from Learn.");
  const data = await api.session(id);
  if (!state.selectedEvidence[id]) state.selectedEvidence[id] = familyDemo.original_evidence_id;
  return { story: null, bundle: data, html: pendingSendMarkup(id) + recipeMarkup(data, familyDemo, state.deviceMode) + knowledgeCards(data, id) };
}
function currentPlayer() {
  return $("#original-video") || $(".original-video video");
}
function playbackEvidence() {
  const player = currentPlayer();
  return bundle?.evidence?.find((item) => item.id === player?.dataset.evidenceId) || getVideoEvidence();
}
function mountExperience() {
  const player = currentPlayer();
  const evidenceId = player?.dataset.evidenceId;
  const update = () => {
    const label = $("#replay-position");
    if (label && player) label.textContent = `Recorded replay · ${clock((playbackEvidence()?.timestamp_seconds || 0) + player.currentTime)}`;
  };
  const restore = () => {
    const saved = state.playback?.[evidenceId];
    if (Number.isFinite(saved) && saved >= 0 && saved < player.duration) player.currentTime = saved;
    update();
  };
  player?.addEventListener("timeupdate", update);
  if (player?.readyState >= 1) restore();
  else player?.addEventListener("loadedmetadata", restore, { once: true });
  return () => {
    if (player) {
      state.playback = state.playback || {};
      if (evidenceId && Number.isFinite(player.currentTime)) { state.playback[evidenceId] = player.currentTime; save(); }
      player.pause();
      player.removeEventListener("timeupdate", update);
      player.removeEventListener("loadedmetadata", restore);
    }
    window.speechSynthesis?.cancel();
  };
}
async function observeReplay() {
  const source = playbackEvidence(), player = currentPlayer();
  if (!player && source?.kind === "image") return observe({ source });
  const interval = elapsedReplayInterval(player?.currentTime);
  if (!source || !interval) return toast("Play the recorded view first, then pause at the moment you want to ask about.");
  observe({ source, interval });
}
function journal() {
  const entries = Object.entries(state.notes).filter(
    ([, value]) => typeof value === "string" && value.trim(),
  );
  return entries.length
    ? `<section><div class="section-heading"><h2>The things you noticed.</h2>${label("Your words")}</div><div class="journal-grid">${entries.map(([key, value]) => `<button class="journal-card" data-action="edit-note" data-id="${esc(key)}"><span class="eyebrow">${icon("edit")} ${key.startsWith("question") ? "A QUESTION TO COME BACK TO" : key.startsWith("reflection") || key.startsWith("experiencia") ? "YOUR OWN REFLECTION" : "A PERSONAL NOTE"}</span><p>${esc(value)}</p><span class="text-link">Return to this memory ${icon("arrow")}</span></button>`).join("")}</div></section>`
    : "";
}
function matchingLocalNotes(value) {
  return findPersonalNotes(state.notes, value, discoveries.map((item) => item.id));
}
function realMemoryCards(items = []) {
  if (!items.length) return '<div class="empty-card"><p>No matching recorded knowledge yet. Try another phrase, or add a note to a recipe.</p></div>';
  return items.map((item) => `<article class="knowledge-card"><div class="knowledge-top">${label(item.kind === "local_note" ? "Personal note" : item.kind === "browser_recording" ? "Browser recording" : item.kind === "session" ? "Recipe" : item.confirmed ? "Human reviewed" : item.source === "transcript" ? "Automatic transcript · needs review" : "Needs review", item.confirmed ? "sage" : "warm")}<small>${esc(item.teacher || "Your kitchen")}</small></div><h3>${esc(item.title)}</h3><p>${esc(item.text)}</p>${item.kind === "browser_recording" ? `<a class="text-link" href="#story/${esc(item.id)}">Open your recording</a>` : item.kind === "local_note" ? `<button class="text-link" data-action="edit-note" data-id="${esc(item.id)}">Open your note</button>` : item.kind === "session" ? `<a class="text-link" href="#live/${esc(item.session_id)}">Open the conversation ${icon("arrow")}</a>` : `<button class="text-link" data-action="open-memory" data-id="${esc(item.id)}" data-session="${esc(item.session_id)}">Open the original source ${icon("arrow")}</button>`}</article>`).join("");
}
async function updateRealMemories() {
  if (currentRoute !== "memory" || !health) return;
  const epoch = ++memoryEpoch, requestedQuery = query, requestedFilter = filter;
  try {
    const result = await api.search(requestedQuery, requestedFilter);
    if (epoch !== memoryEpoch || currentRoute !== "memory" || requestedQuery !== query || requestedFilter !== filter) return;
    realMemories = result.items.concat(requestedQuery && ["all", "notes"].includes(requestedFilter) ? matchingLocalNotes(requestedQuery) : []);
    if ($("#memory-grid")) $("#memory-grid").innerHTML = realMemoryCards(realMemories);
    if ($("#memory-results-note")) $("#memory-results-note").textContent = result.truncated ? "Showing the first 50 matches. Refine your search for more." : `${realMemories.length} recorded memories`;
  } catch (error) {
    if (epoch === memoryEpoch && currentRoute === "memory") toast(error.message);
  }
}
function memory() {
  return `${pageIntro("THE KNOWLEDGE YOU KEEP", "Real moments.<br><em>Ready to find again.</em>", "Search your recipes, notes and explanations, with a path back to the evidence.")}<div class="memory-toolbar"><div class="filter-tabs" id="memory-filters" role="group" aria-label="Filter real memories">${[["all", "All memories"], ["confirmed", "Human reviewed"], ["needs_review", "Needs review"], ["notes", "Notes"]].map(([id, name]) => `<button data-action="filter" data-filter="${id}" aria-pressed="${filter === id}" class="${filter === id ? "active" : ""}">${name}</button>`).join("")}</div><label class="search-field">${icon("search")}<input id="memory-query" maxlength="200" value="${esc(query)}" placeholder="A gesture, a person, a decision…" aria-label="Search real memories"></label></div><p id="memory-results-note" class="fine-print">${health ? "Stored notes and discoveries; examples are kept separately." : "Start the local server to search your recorded knowledge."}</p><div class="knowledge-list" id="memory-grid">${realMemoryCards(realMemories)}</div>${journal()}<section><div class="section-heading"><h2>Your recordings.</h2><a href="#teach" class="text-link">Capture another recipe</a></div>${recordings.length ? storyRows() : '<p class="fine-print">Browser recordings will appear here.</p>'}</section>${sessions.length ? `<section><div class="section-heading"><h2>Your conversations.</h2></div>${sessions.map((item) => `<a class="story-row" href="#live/${esc(item.id)}">${icon(item.mode === "learn" ? "hand" : "learn")}<div><h3>${esc(item.title)}</h3><p>${item.mode === "learn" ? "Your learning session" : "Original lesson"} · ${esc(item.teacher || "Your kitchen")}</p></div>${icon("arrow")}</a>`).join("")}</section>` : ""}<div class="memory-export"><p>Your recordings and explanations belong to you.</p><button class="text-link" data-action="export">Export notes & session details ${icon("download")}</button></div><details class="example-separation"><summary>Illustrative example discoveries</summary><p>These are authored examples, separate from your recorded memories.</p><div class="discovery-grid">${discoveries.map(discoveryCard).join("")}</div></details>`;
}
function search() {
  modal("Find a moment worth keeping.", '<label class="search-field modal-search"><input id="global-query" maxlength="200" placeholder="A gesture, a person, a recipe…" aria-label="Search all memories"></label><div id="search-results" aria-live="polite"></div>');
  const field = $("#global-query"), results = $("#search-results");
  let generation = 0, timer;
  const update = async () => {
    const version = ++generation, value = field.value;
    results.textContent = "Searching your recorded memories…";
    try {
      const found = health ? await api.search(value) : { items: [] };
      if (!results.isConnected || version !== generation || field.value !== value) return;
      const own = recordings.filter((item) => searchKey(`${item.title} ${item.teacher}`).includes(searchKey(value)))
        .map((item) => ({ ...item, kind: "browser_recording", text: "Your recording in this browser" }));
      const matches = found.items.concat(matchingLocalNotes(value), own);
      results.innerHTML = realMemoryCards(matches.slice(0, 50)) + (found.truncated || matches.length > 50 ? '<p class="fine-print">Showing the first 50 results. Refine your search.</p>' : "");
    } catch (error) {
      if (results.isConnected && version === generation) results.textContent = error.message;
    }
  };
  field.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(update, 180); });
  modalCleanup = () => { clearTimeout(timer); generation++; };
  field.focus();
  update();
}

function pendingSendMarkup(id) {
  try {
    const pending = pendingTurn(id);
    if (!pending) return "";
    return `<div class="pending-question pending-send" role="status"><strong>A message needs recovery.</strong><p>${esc(pending.text)}</p><button class="button button-light" data-action="retry-turn" data-session="${esc(id)}">Recover this send</button><button class="text-link" data-action="discard-turn" data-session="${esc(id)}">Stop retrying</button><small>Stopping retries does not cancel or remove a message already received. Check the conversation before sending it again.</small></div>`;
  } catch {
    return `<div class="pending-question"><p>The saved retry could not be read. Check this conversation before clearing it.</p><button class="text-link" data-action="discard-turn" data-session="${esc(id)}">Clear unreadable retry</button></div>`;
  }
}
function agentPanel(data, id) {
  const messages = data?.messages || [],
    pending = data?.pending_question;
  return `<section class="assistant-card live-assistant"><div class="assistant-label"><span>${brandMark}</span><div><strong>Compinche</strong><small>${isMetaReady() ? "Your conversation has a memory" : "Meta connection needs a key"}</small></div><a href="#system" class="agent-dot ${isMetaReady() ? "connected" : ""}" aria-label="Open agent status"></a></div><div class="message-list" id="message-list">${messages.length ? messages.map((m) => `<div class="message ${m.role === "user" ? "user" : "assistant"}"><small>${m.role === "user" ? "YOU" : "COMPINCHE"}</small><p>${esc(m.text || m.content || "")}</p>${m.question?.text && m.question.id !== pending?.id ? `<p class="asked-question">${esc(m.question.text)}</p>` : ""}${m.evidence_ids?.length ? sourceLinks(m.evidence_ids, data) : ""}</div>`).join("") : `<div class="agent-introduction"><p>${data?.session?.mode === "learn" ? "Bring the original lesson into this kitchen. Start with what you want to try." : "Tell me what you are making, or let me look at a moment from the video."}</p><small>${isMetaReady() ? "Messages are processed by Meta. Videos are only sent when you choose “Observe this moment”." : "Your recording and notes are safe here. Connect a Meta API key on the server to start the conversation."}</small></div>`}</div>${pending ? `<div class="pending-question"><span class="eyebrow">ONE QUESTION, ROOM FOR YOUR ANSWER</span><p>${esc(pending.text)}</p></div>` : ""}${pending ? `<label class="answer-choice"><input id="answer-pending" type="checkbox"> This message answers the question above</label>` : ""}${pendingSendMarkup(id)}<form id="agent-form" data-session="${esc(id)}" class="agent-composer"><label class="sr-only" for="agent-input">${pending ? "Answer Compinche’s question" : "Talk to Compinche"}</label><textarea id="agent-input" rows="2" maxlength="5000" placeholder="${pending ? "Tell me in your own words…" : "What would you like to understand?"}" ${!isMetaReady() ? "disabled" : ""}></textarea><button class="send-button" type="submit" aria-label="Send message" ${!isMetaReady() ? "disabled" : ""}>${icon("arrow")}</button></form><div class="agent-grounding">${icon("layers")} Original evidence. Remembered context. Your confirmation.</div></section>`;
}
function knowledgeCards(data, id) {
  const items = data?.knowledge || [];
  if (!items.length) return "";
  return `<section><div class="section-heading"><h2>What we are learning.</h2>${label(`${items.length} discoveries`)}</div><div class="knowledge-list">${items.map((k) => `<article class="knowledge-card"><div class="knowledge-top">${label(k.confirmed ? "Confirmed by you" : k.source || "Needs review", k.confirmed ? "sage" : "warm")}${sourceLinks(k.evidence_ids, data)}</div><p>${esc(k.text)}</p>${k.uncertainty ? `<small>${esc(k.uncertainty)}</small>` : ""}${k.session_id && k.session_id !== id ? `<a class="text-link" href="#live/${esc(k.session_id)}">Review with the original cook ${icon("arrow")}</a>` : `<button class="text-link" data-action="confirm-knowledge" data-id="${esc(k.id)}" data-session="${esc(id)}">${icon("edit")} ${k.confirmed ? "Edit explanation" : "Review & confirm"}</button>`}</article>`).join("")}</div></section>`;
}
function sourceLinks(ids = [], data = bundle) {
  const known = [...(data?.evidence || []), ...(data?.notes || []), ...(data?.messages || []), ...(data?.knowledge || [])];
  return `<span class="source-links">${ids.map((id, index) => {
    const item = known.find((entry) => entry.id === id);
    const title = item?.kind === "video" ? "Video" : item?.kind === "image" ? "Image" : item?.role ? "Words" : "Source";
    return `<button class="text-link source-link" data-action="open-source" data-id="${esc(id)}" aria-label="Open ${title.toLowerCase()} ${index + 1}">${icon("layers")} ${title} ${index + 1}</button>`;
  }).join("")}</span>`;
}
function sourceContent(kind, source) {
  const origin = Number(source.timestamp_seconds) || 0;
  const owner = source.session_id === bundle?.session?.source_session_id ? "Original cook" : bundle?.session?.mode === "learn" ? "This learning session" : "Original lesson";
  const mediaURL = /^\/api\/media\/[a-zA-Z0-9-]+$/.test(source.url || "") ? source.url : "";
  let media = "";
  if (kind === "evidence" && mediaURL) {
    media = source.kind === "image" ? `<img class="evidence-image" src="${esc(mediaURL)}" alt="Original source">`
      : source.kind === "audio" ? `<audio src="${esc(mediaURL)}" controls preload="metadata"></audio>`
        : `<video class="full-video" src="${esc(mediaURL)}" controls playsinline preload="metadata" aria-label="Original source clip"></video>`;
    if (origin) media += `<p class="fine-print">This clip begins at ${clock(origin)} in the original recording. Playback here starts at 00:00. The precise gesture has not been marked.</p>`;
  }
  return `<article class="source-detail"><p class="eyebrow">${esc(owner)} · ${esc(kind)}</p>${media}${source.text || source.note ? `<p class="source-text">${esc(source.text || source.note)}</p>` : ""}${source.automatic_description ? `<p class="field-label">Automatic visual description · needs review</p><p>${esc(source.automatic_description)}</p>` : ""}${source.quote ? `<blockquote>${esc(source.quote)}</blockquote>` : ""}${source.uncertainty ? `<p class="fine-print">${esc(source.uncertainty)}</p>` : ""}${kind === "knowledge" || kind === "note" ? `<p class="fine-print">${source.confirmed ? "Human reviewed" : "Not human confirmed"}${source.source === "transcript" ? " · Automatic transcript" : ""}</p>` : ""}${source.original_model_text ? `<details><summary>Original model wording</summary><p>${esc(source.original_model_text)}</p></details>` : ""}${source.evidence_ids?.length ? `<p class="field-label">Linked evidence</p>${sourceLinks(source.evidence_ids)}` : ""}<button class="text-link" data-action="close-source">Close source</button></article>`;
}
async function openSource(id) {
  const sid = getSessionId(), token = routeToken();
  if (!sid) return toast("Open the recipe conversation to inspect its source.");
  if (!$("#source-preview")) modal("Original source", '<div id="source-preview" aria-live="polite"></div>');
  const preview = $("#source-preview"), request = ++sourceEpoch;
  preview.textContent = "Opening the original evidence…";
  try {
    const result = await api.source(sid, id);
    if (preview.isConnected && request === sourceEpoch && stillHere(token))
      preview.innerHTML = sourceContent(result.entity_kind, result.source);
  } catch (error) {
    if (preview.isConnected && request === sourceEpoch && stillHere(token)) preview.textContent = error.message;
  }
}
function reviewKnowledge(id, sessionId) {
  const claim = bundle?.knowledge?.find((item) => item.id === id && item.session_id === sessionId);
  if (!claim) return toast("Refresh the lesson before reviewing this explanation.");
  modal("Keep their meaning, in their words.", `<p class="dialog-intro">Check the original evidence, then confirm or correct the explanation. Your correction keeps the model's original wording.</p>${sourceLinks(claim.evidence_ids)}<div id="source-preview" aria-live="polite"></div>${claim.quote ? `<blockquote>${esc(claim.quote)}</blockquote>` : ""}<label class="field-label" for="knowledge-wording">Explanation to keep</label><textarea id="knowledge-wording" class="text-area" rows="5" maxlength="2000">${esc(claim.text)}</textarea><button class="button button-dark full-width" data-action="save-knowledge" data-id="${esc(id)}" data-session="${esc(sessionId)}">Confirm this wording ${icon("check")}</button>`);
}

function jobMarkup(job, evidence) {
  const status = job.status || "queued";
  const result = job.result || {};
  const artifacts = result.artifacts || job.artifacts || {};
  const overlay = artifacts.overlay_url || artifacts.overlay;
  const url =
    typeof overlay === "string" && overlay.startsWith("/api/") ? overlay : null;
  return `<div class="sam-job" data-job="${esc(job.id)}"><div class="sam-job-top"><span>${icon("eye")} SAM · ${esc(job.target || job.prompt || "Object tracking")}</span>${label(status, ["completed", "succeeded"].includes(status) ? "sage" : "")}</div>${evidence ? `<p class="fine-print">Source: ${esc(evidence.note || evidence.original_name || "Selected video")} · starts at ${clock(job.start_seconds || 0)}</p>` : ""}${url ? `<video src="${esc(url)}" controls playsinline preload="metadata" aria-label="SAM segmentation result"></video><p class="fine-print">Actual segmentation output. Masks track the selected object; they do not establish weight, temperature or doneness.</p>` : ["failed", "error", "interrupted"].includes(status) ? `<p class="job-error">${esc(job.error?.message || job.error || job.message || "Segmentation could not finish. Your video is unchanged.")}</p>` : `<div class="job-progress"><span class="loading-dot"></span><p>${["completed", "succeeded"].includes(status) ? "Segmentation complete. Open the report for evidence." : "Tracking the selected moment in the video…"}</p></div>`}${artifacts.report?.startsWith?.("/api/") ? `<a href="${esc(artifacts.report)}" target="_blank" rel="noopener" class="text-link">View evidence report ${icon("arrowUp")}</a>` : ""}</div>`;
}
function jobsList(data) {
  const jobs = data?.jobs || [];
  return jobs.length
    ? `<section class="job-list"><div class="section-heading"><h2>Seeing the scene.</h2>${label("SAM 3.1")}</div>${jobs
        .map((job) =>
          jobMarkup(
            job,
            data.evidence?.find((e) => e.id === job.evidence_id),
          ),
        )
        .join("")}</section>`
    : "";
}
async function originalStory(id) {
  const story = await getRecording(id);
  if (!story)
    return {
      story: null,
      bundle: null,
      html: `<div class="empty-card"><h1>This recording is not on this device.</h1><a href="#teach" class="button button-dark">Capture a recipe</a></div>`,
    };
  const linked = state.links[id];
  let bundle = null;
  if (linked?.session && health) {
    try {
      bundle = await api.session(linked.session);
    } catch {}
  }
  const url = objectURL(story.blob);
  return {
    story,
    bundle,
    html: `${modeRail("record")}<div class="compact-heading"><a class="back-link" href="#memory">${icon("back")} Your kitchen story</a>${label("Original recording", "sage")}</div><div class="story-heading"><p class="eyebrow">${esc(story.teacher || "YOUR KITCHEN")}</p><h1>${esc(story.title || "A recipe worth keeping")}<span class="accent-dot">.</span></h1><p>${clock(story.duration)} · Saved in this browser</p></div><div class="experience-layout"><section class="experience-main"><div class="original-video"><video id="original-video" src="${url}" controls playsinline preload="metadata" aria-label="Your original recording"></video></div><div class="original-actions"><a class="text-link" download="compinche-recording.${story.blob.type.includes("mp4") ? "mp4" : "webm"}" href="${url}">${icon("download")} Keep a copy</a><button class="text-link" data-action="recording-note">${icon("plus")} Add a note</button></div>${bundle ? `<div class="analysis-tools"><div><p class="eyebrow">TWO DIFFERENT WAYS OF LOOKING</p><h2>See it. Understand it.</h2></div><div class="analysis-tool-grid"><button class="analysis-tool" data-action="observe" ${!isMetaReady() ? "disabled" : ""}><span>${icon("spark")}</span><strong>Observe this moment</strong><small>Send the complete selected clip to Meta for an evidence-based conversation.</small>${icon("arrowUp")}</button><button class="analysis-tool" data-action="segment" ${!isSAMReady() ? "disabled" : ""}><span>${icon("eye")}</span><strong>Track an object</strong><small>Choose a moment and follow an object for up to eight seconds.</small>${icon("arrowUp")}</button></div>${!isMetaReady() ? '<a href="#system" class="setup-inline">Connect Meta to enable observation and conversation →</a>' : ""}</div>${jobsList(bundle)}` : `<div class="connect-story"><span class="empty-icon">${brandMark}</span><h2>Give this moment<br>an attentive apprentice.</h2><p>Open a working copy on your local agent. You choose separately when a video is sent to Meta.</p><button class="button button-dark" data-action="prepare-story" ${!health ? "disabled" : ""}>Bring in Compinche ${icon("arrow")}</button>${!health ? '<a href="#system" class="text-link">Start the local agent →</a>' : ""}</div>`}${!bundle && story.notes?.length ? `<div class="section-heading"><h2>Your notes along the way.</h2></div><div class="timeline-notes">${story.notes.map((n) => `<div><time>${clock(n.time)}</time><p>${esc(n.text)}</p></div>`).join("")}</div>` : ""}${bundle?.notes?.length ? `<div class="timeline-notes">${bundle.notes.map((n) => `<div><time>${clock(n.timestamp_seconds)}</time><p>${esc(n.text)}</p></div>`).join("")}</div>` : ""}</section><aside class="experience-aside">${bundle ? agentPanel(bundle, linked.session) : `<div class="handoff-card"><span class="eyebrow">THE WAY IT WORKS</span><h2>The camera keeps the moment.<br>You keep the meaning.</h2><p>Compinche can connect the visible gesture, your explanation and the reason behind a decision.</p><div class="evidence-chain"><span>${icon("eye")} What was seen</span><span>${icon("volume")} What was said</span><span>${icon("check")} What you confirm</span></div></div>`}${bundle ? `<div class="handoff-card"><p class="eyebrow">WHEN YOU ARE READY</p><h2>Pass it to another pair of hands.</h2><button class="button button-light" data-action="start-learning">Learn from this recipe ${icon("arrow")}</button></div>` : ""}</aside></div>${bundle ? knowledgeCards(bundle, linked.session) : ""}`,
  };
}
async function liveSession(id) {
  const bundle = await api.session(id);
  const s = bundle.session || {};
  const evidence = (bundle.evidence || []).filter((e) =>
    ["video", "image"].includes(e.kind) && !["poster", "sdk_replay"].includes(e.role),
  );
  const video = selectedEvidence(bundle, id);
  return {
    story: null,
    bundle,
    html: `${channelSelector(state.deviceMode)}${modeRail(s.mode === "learn" ? "learn" : "record")}<div class="compact-heading"><a class="back-link" href="#memory">${icon("back")} Your conversations</a>${label(s.mode === "learn" ? "Learning their way" : "Capturing their way", "sage")}</div><div class="story-heading"><p class="eyebrow">${esc(s.teacher || "YOUR KITCHEN")}</p><h1>${esc(s.title || "A new kitchen story")}<span class="accent-dot">.</span></h1><p>${s.mode === "learn" ? "Original knowledge, ready for a new kitchen." : "Gestures, decisions and explanations, kept together."}</p></div><div class="experience-layout"><section class="experience-main">${video ? `<div class="evidence-picker"><label class="field-label" for="evidence-select">Choose a source to watch or analyse</label><select id="evidence-select">${evidence.map((e, i) => `<option value="${esc(e.id)}" ${e.id === video.id ? "selected" : ""}>${e.session_id === id ? "This kitchen" : "Original cook"} · ${e.kind} ${i + 1}${e.note ? " · " + esc(e.note).slice(0, 65) : ""}</option>`).join("")}</select></div><div class="original-video">${video.kind === "image" ? `<img class="evidence-image" src="${esc(video.url)}" alt="Selected kitchen evidence">` : `<video data-evidence-id="${esc(video.id)}" src="${esc(video.url)}" controls playsinline preload="metadata" aria-label="Selected session evidence video"></video>`}</div>` : `<div class="live-empty"><span>${icon("hand")}</span><h2>${s.mode === "learn" ? "Start with what<br>you want to learn." : "Every good question<br>starts with noticing."}</h2><p>${s.source_session_id ? "This conversation is connected to the original session and its evidence." : "Add an original recording to give Compinche something to observe."}</p></div>`}${audioControls(state.deviceMode)}<div class="original-actions"><button class="text-link" data-action="live-upload">${icon("plus")} Add my attempt</button><button class="text-link" data-action="compare">${icon("scale")} Compare my pan</button></div>${video ? `<div class="analysis-tools"><div class="analysis-tool-grid"><button class="analysis-tool" data-action="observe" ${!isMetaReady() ? "disabled" : ""}>${icon("spark")}<strong>Observe this moment</strong><small>Send this selected source to Meta</small>${icon("arrowUp")}</button><button class="analysis-tool" data-action="segment" ${!isSAMReady() || video.kind !== "video" ? "disabled" : ""}>${icon("eye")}<strong>Track an object</strong><small>Choose a moment to track locally</small>${icon("arrowUp")}</button></div></div>` : ""}${jobsList(bundle)}</section><aside class="experience-aside">${agentPanel(bundle, id)}</aside></div>${knowledgeCards(bundle, id)}`,
  };
}
function system() {
  const agent = health?.agent || {},
    sam = health?.sam || {};
  return `${pageIntro("THE APPRENTICE BEHIND THE EXPERIENCE", "An attentive agent.<br><em>A grounded memory.</em>", "Compinche runs as your own application. The conversation, original evidence and model tools stay connected.")}<div class="system-layout"><section><div class="system-card"><div class="system-card-top"><span class="system-icon">${icon("memory")}</span><div><h2>Conversation & memory</h2><p>LangGraph · persistent session state</p></div>${label(health ? "Running" : "Server offline", health ? "sage" : "warm")}</div><p>Each recipe has its own conversation. Questions, answers, evidence and confirmed knowledge survive between turns.</p><div class="system-facts"><span>${icon("check")} One open question at a time</span><span>${icon("check")} Original source carried into learning</span><span>${icon("check")} Confirmation belongs to the cook</span></div></div><div class="system-card"><div class="system-card-top"><span class="system-icon">${icon("spark")}</span><div><h2>Understanding the moment</h2><p>Meta · ${esc(agent.model || "Muse Spark")}</p></div>${label(isMetaReady() ? "Configured" : "Key needed", isMetaReady() ? "sage" : "warm")}</div><p>Understands the conversation and, when you choose, the original media. The server holds the API key.</p>${!isMetaReady() ? `<div class="setup-callout"><strong>Connect Meta on your server</strong><p>Set <code>MODEL_API_KEY</code> or <code>META_API_KEY</code> in the server environment, then restart the app. Keep the key out of the browser and this chat.</p></div>` : ""}</div><div class="system-card"><div class="system-card-top"><span class="system-icon">${icon("eye")}</span><div><h2>Following the objects</h2><p>SAM 3.1 · local segmentation</p></div>${label(isSAMReady() ? "Available" : "Unavailable", isSAMReady() ? "sage" : "warm")}</div><p>Tracks a selected object through up to eight seconds at your chosen video position and preserves masks, times and the original source. A mask alone does not establish a weight, temperature or cooking decision.</p><button class="button button-light full-width" data-action="sam-test">${icon("play")} Watch a real SAM test <span>8 / 8 frames</span></button>${sam.reason || sam.message ? `<p class="fine-print">${esc(sam.reason || sam.message)}</p>` : ""}</div></section><aside><div class="guardrail-card"><span class="eyebrow">BUILT INTO THE CONVERSATION</span><h2>Curious.<br>Careful.<br><em>On your side.</em></h2><ul><li>Keep observations separate from explanations and guesses.</li><li>Link claims to the original evidence.</li><li>Ask for the missing detail instead of inventing a quantity.</li><li>Let the person confirm or correct what is kept.</li><li>Treat words inside media as material to observe.</li></ul></div><button class="button button-light full-width" data-action="refresh-health">Refresh connections ${icon("arrow")}</button><p class="fine-print">Configured means the adapter has what it needs to make a request. Successful inference is only shown after a real response. Guardrails reduce errors; they do not prove a model is correct.</p></aside></div>`;
}

async function loadHealth() {
  try {
    health = await api.health();
  } catch {
    health = null;
  }
  return health;
}
async function refreshLibrary() {
  try {
    recordings = await listRecordings();
  } catch {
    recordings = [];
  }
  if (health) {
    try { familyDemo = await api.demo(); } catch { familyDemo = null; }
    if (currentRoute === "memory") {
      try { realMemories = (await api.search(query, filter)).items; } catch { realMemories = []; }
    }
    try {
      const result = await api.sessions();
      sessions = result.sessions || [];
    } catch {
      sessions = [];
    }
  }
}
async function render() {
  const renderId = ++epoch;
  cleanupExperience?.();
  cleanupExperience = null;
  cleanupCapture?.();
  cleanupCapture = null;
  pageURLs.forEach(URL.revokeObjectURL);
  pageURLs = [];
  clearTimeout(activeJobTimer);
  story = null;
  bundle = null;
  const parts = (location.hash.slice(1) || "home").split("/");
  const aliases = {
    cocina: "home",
    sesion: "demo",
    cocinar: "cook",
    memoria: "memory",
    grabar: "record",
  };
  currentRoute = aliases[parts[0]] || parts[0];
  currentId = parts[1] ? decodeURIComponent(parts[1]) : "";
  if (sourceToOpen && !(currentRoute === "live" && currentId === sourceToOpen.session)) sourceToOpen = null;
  if (
    ![
      "home",
      "record",
      "teach",
      "practice",
      "learn",
      "memory",
      "demo",
      "cook",
      "story",
      "live",
      "system",
      "recipe",
      "example",
      "example-cook",
    ].includes(currentRoute)
  )
    currentRoute = "home";
  await refreshLibrary();
  if (renderId !== epoch) return;
  if (["demo", "cook"].includes(currentRoute) && familyDemo?.available) currentId = familyDemo.session_id;
  let html;
  try {
    html =
      currentRoute === "home"
        ? home()
        : currentRoute === "teach" || currentRoute === "practice"
          ? `<div id="live-container" lang="es"></div>`
        : currentRoute === "record"
          ? `${modeRail("record")}<div id="capture-container"></div>`
          : currentRoute === "learn"
            ? learningLibrary()
            : currentRoute === "memory"
              ? memory()
              : currentRoute === "recipe"
                ? await familyRecipe(currentId)
                : currentRoute === "example"
                  ? demo()
                  : currentRoute === "example-cook"
                    ? cook()
                    : currentRoute === "demo"
                ? familyDemo?.available ? await familyRecipe() : demo()
                : currentRoute === "cook"
                  ? familyDemo?.available ? await familyRecipe() : cook()
                  : currentRoute === "story"
                    ? await originalStory(currentId)
                    : currentRoute === "live"
                      ? await liveSession(currentId)
                      : system();
  } catch (error) {
    html = `<div class="empty-card"><h1>This story needs a moment.</h1><p>${esc(error.message)}</p><button class="button button-dark" data-action="retry">Try again ${icon("arrow")}</button><a href="#home" class="text-link">Return home</a></div>`;
  }
  if (renderId !== epoch) return;
  if (typeof html === "object") {
    story = html.story;
    bundle = html.bundle;
    html = html.html;
  }
  $("#app").innerHTML = shell(html);
  if (currentRoute === "record")
    cleanupCapture = mountCapture($("#capture-container"), {
      onSaved: (record) => {
        toast("Your original recording is saved.");
        if (renderId === epoch && currentRoute === "record")
          navigate("story/" + encodeURIComponent(record.id));
      },
      onBack: () => navigate("home"),
    });
  if (currentRoute === "teach" || currentRoute === "practice") cleanupExperience = mountLive($("#live-container"), { demo: familyDemo, lessonId: currentRoute === "practice" ? currentId : null, onSession: (id) => { state.lastLiveSession = id; save(); } });
  if ($("#demo-video")) bindDemo();
  if (bundle) cleanupExperience = mountExperience();
  if (
    bundle?.jobs?.some((j) =>
      ["queued", "pending", "running"].includes(j.status),
    )
  )
    pollJobs();
  const routeTitles = {
    home: "From their hands to yours",
    teach: "Aprender mientras cocina",
    record: "Capture a recipe",
    learn: "Learn their way",
    memory: "Your memories",
    demo: familyDemo?.available ? "Dad’s recipe" : "Illustrative example",
    cook: familyDemo?.available ? "Learning Dad’s way" : "Example cooking journey",
    recipe: "Dad’s recipe",
    example: "Illustrative example",
    "example-cook": "Example cooking journey",
    story: "Your kitchen story",
    live: "Your conversation",
    system: "The agent studio",
  };
  document.title = `${routeTitles[currentRoute]} · Compinche`;
  if (currentRoute === "demo")
    $(".scene-tab.active")?.scrollIntoView({
      block: "nearest",
      inline: "center",
    });
  if (sourceToOpen && bundle && getSessionId() === sourceToOpen.session) {
    const target = sourceToOpen; sourceToOpen = null; await openSource(target.id);
  }
}
function bindDemo() {
  const video = $("#demo-video");
  if (!video) return;
  const seek = () => {
    video.currentTime = scenes[scene].seek;
  };
  if (video.readyState >= 1) seek();
  else video.addEventListener("loadedmetadata", seek, { once: true });
  video.addEventListener("timeupdate", () => {
    if (!$("#demo-seek")) return;
    $("#demo-seek").value = video.currentTime;
    $("#demo-time").textContent = clock(video.currentTime);
  });
  video.addEventListener("play", () => {
    $(".video-big-play").classList.add("playing");
    $('.video-control-bar [data-action="play-demo"]').innerHTML = icon("pause");
  });
  video.addEventListener("pause", () => {
    $(".video-big-play").classList.remove("playing");
    $('.video-control-bar [data-action="play-demo"]').innerHTML = icon("play");
  });
  $("#demo-seek").addEventListener(
    "input",
    (e) => (video.currentTime = Number(e.target.value)),
  );
}
function closeModal(restore = true) {
  sourceEpoch++;
  observationContext = null;
  modalCleanup?.();
  modalCleanup = null;
  $$("video,audio", $("#modal-root")).forEach((media) => media.pause());
  $("#modal-root").innerHTML = "";
  document.body.style.overflow = "";
  $("#app").inert = false;
  if (restore && dialogFocus?.isConnected) dialogFocus.focus();
}
function modal(title, body, cls = "") {
  closeModal(false);
  dialogFocus = document.activeElement;
  $("#app").inert = true;
  document.body.style.overflow = "hidden";
  $("#modal-root").innerHTML =
    `<div class="dialog-backdrop"><section class="dialog ${cls}" role="dialog" aria-modal="true" aria-labelledby="dialog-title"><div class="dialog-handle"></div><header class="dialog-header"><h2 id="dialog-title">${title}</h2><button class="round-button" data-action="close" aria-label="Close dialog">${icon("close")}</button></header><div class="dialog-body">${body}</div></section></div>`;
  $(".dialog-header button").focus();
  $(".dialog-backdrop").addEventListener("click", (e) => {
    if (e.target === e.currentTarget) closeModal();
  });
}
function openDiscovery(id) {
  const d = discoveries.find((x) => x.id === id);
  if (!d) return;
  modal(
    d.title,
    `<div class="discovery-modal-video"><video src="./assets/cooking-demo.mp4" poster="./assets/${d.image}" muted playsinline loop controls aria-label="Illustrative cooking footage"></video><span class="tag light">Example from the story script</span></div><p class="discovery-modal-copy">${d.text}</p><div class="knowledge-chain">${[
      ["THE GESTURE", d.observation],
      ["THE SIGNAL", d.signal],
      ["THE FEELING", d.feeling],
      ["THE DECISION", d.decision],
    ]
      .map(
        ([name, text], i) =>
          `<div><span class="chain-number">0${i + 1}</span><div><small>${name}</small><p>${text}</p></div></div>`,
      )
      .join(
        "",
      )}</div><div class="quote-question"><span class="eyebrow">THE QUESTION THAT OPENS A DOOR</span><p>“${d.question}”</p></div><label class="field-label" for="discovery-note">Something you want to remember</label><textarea id="discovery-note" class="text-area" rows="3" placeholder="A thought, a question, a detail…">${esc(state.notes[id] || "")}</textarea><div class="dialog-actions"><button class="button button-light" data-action="save-discovery-note" data-id="${id}">Save my note</button><button class="button button-dark" data-action="save-discovery" data-id="${id}" id="save-discovery-button">${icon(state.saved.includes(id) ? "check" : "bookmark")}${state.saved.includes(id) ? "Saved" : "Keep this discovery"}</button></div><p class="fine-print">${d.source}. The footage illustrates the idea; the cook’s authentic demonstration and answer will come from a real session.</p>`,
  );
  const v = $(".discovery-modal-video video");
  v.addEventListener(
    "loadedmetadata",
    () => (v.currentTime = scenes[d.scene].seek),
    { once: true },
  );
}
function toggleDiscovery(id) {
  const saved = state.saved.includes(id);
  state.saved = saved
    ? state.saved.filter((x) => x !== id)
    : [...state.saved, id];
  if (save())
    toast(
      saved
        ? "Removed from your saved discoveries."
        : "A little knowledge, kept close.",
    );
  const button = $("#save-discovery-button");
  if (button)
    button.innerHTML = `${icon(saved ? "bookmark" : "check")}${saved ? "Keep this discovery" : "Saved"}`;
  if ($("#memory-grid")) $("#memory-grid").innerHTML = memoryGrid();
}
function openNote(key, title = "A thought worth keeping.") {
  modal(
    title,
    `<p class="dialog-intro">Keep it in your own words. You can return to it in your memories.</p><label class="field-label" for="personal-note">Your note</label><textarea id="personal-note" class="text-area" rows="5" placeholder="What did you notice?">${esc(state.notes[key] || "")}</textarea><div class="dialog-actions"><button class="button button-dark" data-action="save-note" data-id="${esc(key)}">${icon("bookmark")} Keep this thought</button></div>`,
  );
}
function pan(d, color, id) {
  const r = d * 2.1;
  return `<svg viewBox="0 0 240 210" aria-hidden="true"><defs><pattern id="${id}" width="12" height="12" patternUnits="userSpaceOnUse"><path d="M12 0H0V12" fill="none" stroke="${color}" stroke-opacity=".18" stroke-width=".6"/></pattern></defs><path d="M${110 + r} 83h${100 - r}q6 0 6 6t-6 6h-${100 - r}" fill="none" stroke="${color}" stroke-width="5"/><circle cx="110" cy="89" r="${r}" fill="url(#${id})" stroke="${color}" stroke-width="2"/><circle cx="110" cy="89" r="${r - 5}" fill="none" stroke="${color}" stroke-width=".8"/><path d="M${110 - r} ${100 + r}h${r * 2}m-${r * 2}-4v8m${r * 2}-8v8" fill="none" stroke="${color}"/><text x="110" y="${r + 122}" fill="${color}" text-anchor="middle" font-size="13" font-family="DM Sans">${d} cm</text></svg>`;
}
function compare() {
  modal(
    "A different pan. The same idea.",
    `<p class="dialog-intro">Use the inside diameter of each pan. See how their surfaces compare, at the same scale.</p><div class="pan-comparison"><div class="pan-box"><span class="eyebrow">THEIR PAN</span><div id="their-pan">${pan(28, "#758665", "their-grid")}</div><label for="their-diameter"><strong id="their-size">28 cm</strong><small>Inside diameter</small></label><input type="range" id="their-diameter" min="16" max="36" value="28" step="1" aria-label="Their pan diameter in centimetres"></div><div class="pan-box yours"><span class="eyebrow">YOUR PAN</span><div id="your-pan">${pan(24, "#bd8067", "your-grid")}</div><label for="your-diameter"><strong id="your-size">24 cm</strong><small>Inside diameter</small></label><input type="range" id="your-diameter" min="16" max="36" value="24" step="1" aria-label="Your pan diameter in centimetres"></div></div><div class="pan-result" aria-live="polite"><div><strong id="pan-percent">73%</strong><small>OF THEIR SURFACE</small></div><p id="pan-reason">At the same depth, the geometric ratio is <strong>0.73×</strong> the original quantity.</p></div><p class="fine-print">Example starting sizes. Calculated from your measurements for circular pans at the same depth. This is geometry, not a camera measurement or a validated cooking recommendation.</p><button class="button button-dark full-width" data-action="close">Got it ${icon("check")}</button>`,
  );
  $$(".pan-box input").forEach((input) =>
    input.addEventListener("input", () => {
      const a = +$("#their-diameter").value,
        b = +$("#your-diameter").value,
        r = (b * b) / (a * a);
      $("#their-pan").innerHTML = pan(a, "#758665", "their-grid");
      $("#your-pan").innerHTML = pan(b, "#bd8067", "your-grid");
      $("#their-size").textContent = `${a} cm`;
      $("#your-size").textContent = `${b} cm`;
      $("#pan-percent").textContent = `${Math.round(r * 100)}%`;
      $("#pan-reason").innerHTML =
        `At the same depth, the geometric ratio is <strong>${r.toFixed(2)}×</strong> the original quantity.`;
    }),
  );
}
function split() {
  modal(
    "The gesture. Then your turn.",
    `<p class="dialog-intro">Place your attempt beside the example. Watch both and leave room for what you notice.</p><div class="gesture-split"><div><span class="eyebrow">EXAMPLE FOOTAGE</span><video id="reference-gesture" src="./assets/cooking-demo.mp4" poster="./assets/cooking-poster.webp" controls muted playsinline loop></video></div><div><span class="eyebrow">YOUR ATTEMPT</span><div class="gesture-upload" id="gesture-target"><label for="gesture-file">${icon("video")}Add your video<input id="gesture-file" type="file" accept="video/*"></label></div></div></div><div class="dialog-actions"><button id="play-gestures" class="button button-dark" data-action="play-gestures" disabled>${icon("play")} Play together</button></div><p class="fine-print">Manual playback comparison. No automatic alignment or evaluation. This file stays in your browser.</p>`,
  );
  $("#gesture-file").addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (!file) return;
    if (file.type && !file.type.startsWith("video/")) {
      toast("Choose a video file.");
      return;
    }
    const url = URL.createObjectURL(file);
    modalCleanup = () => URL.revokeObjectURL(url);
    $("#gesture-target").outerHTML =
      `<video id="attempt-gesture" src="${url}" controls muted playsinline></video>`;
    $("#play-gestures").disabled = false;
  });
}
function about() {
  modal(
    "Good things are passed on.",
    `<div class="about-mark">${brandMark}</div><h3 class="about-title">An apprentice<br>with a good memory.</h3><p class="dialog-intro">Compinche captures the gestures, decisions and little details that make a recipe someone’s own. Then it helps another person learn from that original knowledge.</p><div class="about-mode"><span>01</span><div><strong>Capture their way.</strong><p>Record the cook. Keep the demonstration, the answer and the reason together.</p></div></div><div class="about-mode"><span>02</span><div><strong>Learn their way.</strong><p>Bring those moments into your kitchen. Try the gesture. Ask about the difference.</p></div></div><p class="fine-print">This version runs locally with a LangGraph agent, a Meta adapter and a local SAM tool. The example story uses illustrative footage. Your recordings stay in your browser until you open them with the local agent; Meta only receives media when you explicitly ask it to observe.</p><a href="#system" class="button button-light full-width">See agent connections ${icon("arrow")}</a>`,
  );
}
function downloadJSON(data, name) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
async function exportAll() {
  const remote = [];
  if (health)
    for (const s of sessions) {
      try {
        remote.push(await api.session(s.id));
      } catch {
        remote.push({ id: s.id, error: "Could not retrieve this session" });
      }
    }
  downloadJSON(
    {
      app: "Compinche",
      version: 2,
      date: new Date().toISOString(),
      preferences: state,
      recordings,
      sessions: remote,
      notice:
        "Download original video files separately from their stories. The three example discoveries come from the story script.",
    },
    "compinche-memories.json",
  );
  toast("Your notes and session details have been exported.");
}
function getSessionId() {
  return ["live", "recipe", "demo", "cook"].includes(currentRoute) ? currentId : state.links[currentId]?.session;
}
function selectedEvidence(data = bundle, id = getSessionId()) {
  const items = (data?.evidence || []).filter((e) =>
    ["video", "image"].includes(e.kind) && !["poster", "sdk_replay"].includes(e.role),
  );
  return (
    items.find((e) => e.id === state.selectedEvidence[id]) ||
    items.find((e) => e.id === familyDemo?.original_evidence_id) ||
    items.find((e) => e.session_id === data?.session?.source_session_id) ||
    items[items.length - 1]
  );
}
function getVideoEvidence() {
  return currentRoute === "story"
    ? bundle?.evidence?.find((e) => e.id === state.links[currentId]?.evidence)
    : selectedEvidence();
}
async function withBusy(button, task, label = "One moment…") {
  if (busy) return;
  busy = true;
  const original = button?.innerHTML;
  if (button) {
    button.disabled = true;
    button.innerHTML = `<span class="loading-dot"></span>${label}`;
  }
  try {
    return await task();
  } catch (error) {
    toast(
      error.message ||
        "This action could not be completed. Your recording is unchanged.",
    );
  } finally {
    busy = false;
    if (button?.isConnected) {
      button.disabled = false;
      button.innerHTML = original;
    }
  }
}
async function prepareStory(button) {
  const recordingId = currentId,
    token = routeToken();
  await withBusy(
    button,
    async () => {
      const record = await getRecording(recordingId);
      if (!record?.blob)
        throw new Error("The original recording is not available.");
      if (record.blob.size > (health?.limits?.max_upload_bytes || 157286400))
        throw new Error(
          "This recording is larger than 150 MiB. Use a shorter clip for analysis.",
        );
      let link = state.links[recordingId] || {};
      if (!link.session) {
        const response = await api.create({
          title: record.title,
          teacher: record.teacher,
          mode: "record",
        });
        const session = response.session || response;
        link.session = session.id;
        state.links[recordingId] = link;
        save();
      }
      if (!link.evidence) {
        const result = await api.upload(
          link.session,
          record.blob,
          "Original recording selected by the user.",
        );
        const evidence = result.evidence || result;
        link.evidence = evidence.id;
        state.links[recordingId] = link;
        save();
      }
      link.synced_notes = link.synced_notes || [];
      for (const note of record.notes || []) {
        const nid = note.id || `${note.time}:${note.text}`;
        if (link.synced_notes.includes(nid)) continue;
        await api.note(link.session, {
          text: note.text,
          timestamp_seconds: note.time || 0,
          evidence_ids: [link.evidence],
        });
        link.synced_notes.push(nid);
        save();
      }
      toast(
        "Your local agent has the recording. Nothing has been sent to Meta.",
      );
      await refreshHere(token);
    },
    "Opening on your local agent…",
  );
}
function observe(options = {}) {
  const source = options.source || getVideoEvidence(), sid = getSessionId();
  if (!source || !sid) return toast("Choose an original source first.");
  if (source.kind !== "video" && source.size > (health?.limits?.max_meta_clip_bytes || 41943040))
    return toast("Choose an image under 40 MiB.");
  const displayed = playbackEvidence(), player = currentPlayer();
  const interval = options.interval || selectedInterval(source, displayed?.id, player?.currentTime || 0);
  const spanish = familyDemo?.available && [bundle?.session?.id, bundle?.session?.source_session_id].includes(familyDemo.session_id);
  const prompt = spanish ? "Responde en español. Observa solo este fragmento. Describe lo sustentado por la evidencia y pregunta por una única decisión que siga sin explicación. No inventes cantidades, sensaciones ni respuestas del cocinero." : "Observe only this selected source. Describe what the evidence supports and ask one useful question if a decision remains unexplained.";
  modal("Look at a selected moment.", `<div class="assistant-label"><span>${brandMark}</span><div><strong>Meta · ${esc(health?.agent?.model || "Muse Spark")}</strong><small>Your chosen evidence, in context</small></div></div>${source.kind === "video" ? `<p class="dialog-intro">Prepare a short interval locally, check its preview, then choose whether to send it to Meta. The full recording stays on this computer.</p><div class="clip-fields"><label for="clip-start">Start in this video (seconds)<input class="text-area" id="clip-start" type="number" min="0" step="0.25" value="${interval.start_seconds.toFixed(2)}"></label><label for="clip-length">Length (up to 120 seconds)<input class="text-area" id="clip-length" type="number" min="0.1" max="120" step="0.25" value="${interval.duration_seconds.toFixed(2)}"></label></div><button class="button button-light" data-action="prepare-clip">Prepare this interval</button><div id="analysis-preview" aria-live="polite"><p class="fine-print">No video has been sent to Meta.</p></div>` : `<p class="dialog-intro">This sends the selected image and this session’s context to Meta when you press Send.</p>`}<label for="observation-prompt" class="field-label">What should Compinche pay attention to?</label><textarea id="observation-prompt" class="text-area" rows="3">${esc(prompt)}</textarea>${bundle?.pending_question ? `<label class="answer-choice"><input id="observation-answer" type="checkbox"> This source answers the open question</label>` : ""}<p class="fine-print">What a gesture feels like and why it was chosen need an explanation or your review.</p><button class="button button-dark full-width" id="send-observation" data-action="run-observe" ${source.kind === "video" || !isMetaReady() ? "disabled" : ""}>Send the selected source to Meta ${icon("arrow")}</button>`);
  observationContext = { source, evidence: source.kind === "video" ? null : source,
    session: sid, token: routeToken(), revision: 0, pendingQuestion: bundle?.pending_question?.id || null };
}
async function prepareObservationClip(button) {
  const request = observationContext;
  if (!request || request.source.kind !== "video") return;
  const start = Number($("#clip-start")?.value), length = Number($("#clip-length")?.value);
  if (!Number.isFinite(start) || !Number.isFinite(length) || start < 0 || length <= 0 || length > 120)
    return toast("Choose a start and a length from 0.1 to 120 seconds.");
  const revision = request.revision;
  request.evidence = null;
  $("#send-observation").disabled = true;
  await withBusy(button, async () => {
    const result = await api.clip(request.session, { evidence_id: request.source.id, start_seconds: start, duration_seconds: length });
    if (observationContext !== request || request.revision !== revision || !stillHere(request.token)) return;
    request.evidence = result.evidence;
    $("#analysis-preview").innerHTML = `<video class="full-video" src="${esc(result.evidence.url)}" controls playsinline preload="metadata" aria-label="Prepared interval preview"></video><p class="fine-print">${clock(result.evidence.duration_seconds)} · ${(result.evidence.size / 1048576).toFixed(1)} MiB · Starts at ${clock(result.evidence.timestamp_seconds)} in its original source. Playback of this clip starts at 00:00. Prepared locally; not yet sent to Meta.</p>`;
    $("#send-observation").disabled = !isMetaReady();
  }, "Preparing the selected interval…");
}

async function sendTurn(
  id,
  text,
  evidence = [],
  answerTo = null,
  button = null,
) {
  const token = routeToken(), composerDraft = $("#agent-input")?.value || "";
  return withBusy(
    button,
    async () => {
      try {
        await deliverTurn(id, { text, evidence_ids: evidence, answer_to: answerTo }, (intent) => api.turn(id, intent));
        await loadHealth();
      } catch (error) {
        if (stillHere(token)) {
          await render();
          if ($("#agent-input")) $("#agent-input").value = composerDraft;
        }
        throw error;
      }
      if (stillHere(token)) {
        closeModal();
        await refreshHere(token);
        $("#agent-input")?.focus();
      }
    },
    "Compinche is thinking…",
  );
}
function segment() {
  const evidence = getVideoEvidence();
  if (!evidence) return toast("Add an original video first.");
  const position = Math.floor($(".original-video video")?.currentTime || 0);
  modal(
    "Follow one thing closely.",
    `<p class="dialog-intro">Track an object for up to eight seconds from the moment you choose.</p><label class="field-label" for="sam-start">Start at (seconds into this video)</label><input id="sam-start" type="number" min="0" max="86400" step="0.25" value="${position}" class="text-area"><div class="target-options">${[
      ["saucepan", "The pan", "cook"],
      ["spoon", "The spoon", "hand"],
      ["food", "The food", "leaf"],
      ["hand", "The hands", "hand"],
      ["dough", "The dough", "leaf"],
      ["croquette", "The croquettes", "cook"],
    ]
      .map(
        ([value, title, ic]) =>
          `<button class="target-option" data-action="run-segment" data-target="${value}">${icon(ic)}<strong>${title}</strong>${icon("arrow")}</button>`,
      )
      .join(
        "",
      )}</div><p class="fine-print">One job runs at a time. The output keeps the masks, timestamps and source video; it does not infer cooking readiness or physical measurements.</p>`,
  );
}
async function runSegment(target, button) {
  const id = getSessionId(),
    evidence = getVideoEvidence(),
    token = routeToken();
  const start = Number($("#sam-start")?.value || 0);
  if (!Number.isFinite(start) || start < 0 || start > 86400)
    return toast("Choose a valid start time in the selected video.");
  await withBusy(
    button,
    async () => {
      const job = await api.segment(id, evidence.id, target, start);
      state.jobs.push({ id: job.id, session: id });
      save();
      if (stillHere(token)) {
        closeModal();
        toast("SAM is tracking the object in your local video.");
        await refreshHere(token);
        pollJobs();
      }
    },
    "Starting SAM…",
  );
}
function pollJobs() {
  clearTimeout(activeJobTimer);
  const id = getSessionId();
  if (!id) return;
  activeJobTimer = setTimeout(async () => {
    try {
      const fresh = await api.session(id);
      if (getSessionId() !== id) return;
      bundle = fresh;
      const pending = (fresh.jobs || []).some((j) =>
        ["queued", "pending", "running"].includes(j.status),
      );
      const existing = $(".job-list");
      if (existing) {
        const tmp = document.createElement("div");
        tmp.innerHTML = jobsList(fresh);
        existing.replaceWith(tmp.firstElementChild);
      } else if (fresh.jobs?.length) {
        $(".experience-main")?.insertAdjacentHTML("beforeend", jobsList(fresh));
      }
      if (pending) pollJobs();
      else
        toast(
          (fresh.jobs || []).some((j) =>
            ["failed", "error", "interrupted"].includes(j.status),
          )
            ? "SAM has finished. Review the job status below."
            : "The segmentation result is ready to review.",
        );
    } catch {
      if (getSessionId() === id) pollJobs();
    }
  }, 3000);
}
async function startLearning(button) {
  const source = getSessionId(),
    token = routeToken();
  await withBusy(
    button,
    async () => {
      const created = await api.create({
        title: bundle?.session?.title || story?.title || "Learning a recipe",
        teacher: bundle?.session?.teacher || story?.teacher || "",
        mode: "learn",
        source_session_id: source,
      });
      if (stillHere(token))
        navigate("live/" + encodeURIComponent((created.session || created).id));
      else toast("Your learning session is ready in Memories.");
    },
    "Opening your learning session…",
  );
}
function uploadAttempt() {
  const sessionId = getSessionId(),
    token = routeToken();
  modal(
    "A moment from your kitchen.",
    `<p class="dialog-intro">Add your own attempt to this session. The file goes to your local agent; Meta sees it only if you choose “Observe this moment”.</p><label class="upload-area" for="attempt-upload">${icon("video")}<strong>Choose a video or image</strong><span>Up to 150 MiB</span><input id="attempt-upload" type="file" accept="video/*,image/*"></label><p id="upload-progress" class="fine-print" role="status"></p>`,
  );
  $("#attempt-upload").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    if (file.size > (health?.limits?.max_upload_bytes || 157286400)) {
      toast("Choose a file smaller than 150 MiB.");
      return;
    }
    e.target.disabled = true;
    $("#upload-progress").textContent = "Opening the file on your local agent…";
    try {
      const result = await api.upload(
        sessionId,
        file,
        "Learner’s own attempt.",
      );
      state.selectedEvidence[sessionId] = (result.evidence || result).id;
      save();
      if (stillHere(token)) {
        closeModal();
        await refreshHere(token);
        toast("Your attempt is ready to review.");
      }
    } catch (error) {
      if (stillHere(token) && $("#upload-progress")) {
        e.target.disabled = false;
        $("#upload-progress").textContent = error.message;
      } else toast(error.message);
    }
  });
}
function addRecordingNote() {
  modal(
    "A detail worth noticing.",
    `<label class="field-label" for="recording-note">Your observation or explanation</label><textarea id="recording-note" class="text-area" rows="4" placeholder="What would you like to keep about this moment?"></textarea><p class="fine-print">This is your note. It will not be presented as a model observation.</p><button class="button button-dark full-width" data-action="save-recording-note">Keep this note ${icon("bookmark")}</button>`,
  );
}

// All user actions are explicit: opening a camera, sending media and model calls.
document.addEventListener("click", async (event) => {
  if (event.target.closest(".skip-link")) {
    event.preventDefault();
    $("#main").focus();
    $("#main").scrollIntoView({ block: "start" });
    return;
  }
  const button = event.target.closest("[data-action]");
  if (!button || button.disabled) return;
  const action = button.dataset.action,
    id = button.dataset.id,
    index = Number(button.dataset.index);
  switch (action) {
    case "retry":
      await render();
      break;
    case "close":
      closeModal();
      break;
    case "about":
      about();
      break;
    case "search":
      search();
      break;
    case "discovery":
      openDiscovery(id);
      break;
    case "save-discovery":
      toggleDiscovery(id);
      break;
    case "save-discovery-note":
      state.notes[id] = $("#discovery-note").value.trim();
      if (save()) toast("Your note is saved.");
      break;
    case "filter":
      filter = button.dataset.filter;
      $$("#memory-filters button").forEach((b) => {
        b.classList.toggle("active", b === button);
        b.setAttribute("aria-pressed", String(b === button));
      });
      await updateRealMemories();
      break;
    case "reset-filter":
      filter = "all";
      query = "";
      await render();
      break;
    case "edit-note":
      openNote(id);
      break;
    case "save-note":
      state.notes[id] = $("#personal-note").value.trim();
      if (save()) {
        closeModal();
        toast("Your thought has a place in your memories.");
        if (currentRoute === "memory") await render();
      }
      break;
    case "export":
      await withBusy(button, exportAll, "Gathering your memories…");
      break;
    case "scene":
      scene = index;
      await render();
      break;
    case "play-demo": {
      const v = $("#demo-video");
      if (v.paused)
        v.play().catch(() =>
          toast("The video could not start. Please try again."),
        );
      else v.pause();
      break;
    }
    case "cook-step":
      state.step = index;
      save();
      await render();
      break;
    case "next-step":
      state.step = Math.min(5, state.step + 1);
      save();
      await render();
      break;
    case "previous-step":
      state.step = Math.max(0, state.step - 1);
      save();
      await render();
      break;
    case "step-action": {
      const s = steps[state.step || 0];
      if (s.type === "compare") compare();
      else if (s.type === "split") split();
      else if (s.type === "finish")
        openNote("reflection-" + Date.now(), "Her way. Something of you.");
      else if (s.type === "scene") {
        scene = s.scene;
        navigate("demo");
      } else openDiscovery(s.lesson);
      break;
    }
    case "finish":
      openNote("reflection-" + Date.now(), "Her way. Something of you.");
      break;
    case "ask-note":
      openNote("question-" + state.step, "A question for next time.");
      break;
    case "compare":
      compare();
      break;
    case "check":
      state.checks = state.checks.includes(index)
        ? state.checks.filter((x) => x !== index)
        : [...state.checks, index];
      save();
      button.classList.toggle("checked");
      button.setAttribute("aria-pressed", String(state.checks.includes(index)));
      $("span", button).innerHTML = state.checks.includes(index)
        ? icon("check")
        : "";
      break;
    case "play-gestures": {
      const a = $("#reference-gesture"),
        b = $("#attempt-gesture");
      a.currentTime = 8;
      b.currentTime = 0;
      Promise.all([a.play(), b.play()]).catch(() =>
        toast("Use the controls on each video to start playback."),
      );
      break;
    }
    case "prepare-story":
      await prepareStory(button);
      break;
    case "observe":
      observe();
      break;
    case "prepare-clip":
      await prepareObservationClip(button);
      break;
    case "run-observe": {
      const request = observationContext;
      if (!request?.evidence) { toast("Prepare the selected interval first."); break; }
      const text = $("#observation-prompt")?.value.trim();
      if (!text) { toast("Add what you would like Compinche to notice."); break; }
      if (!stillHere(request.token) || getSessionId() !== request.session) { closeModal(); break; }
      await sendTurn(request.session, text, [request.evidence.id], $("#observation-answer")?.checked ? request.pendingQuestion : null, button);
      break;
    }
    case "segment":
      segment();
      break;
    case "run-segment":
      await runSegment(button.dataset.target, button);
      break;
    case "start-learning":
      await startLearning(button);
      break;
    case "live-upload":
      uploadAttempt();
      break;
    case "recording-note":
      addRecordingNote();
      break;
    case "save-recording-note": {
      const text = $("#recording-note").value.trim();
      if (!text) return $("#recording-note").focus();
      if (getSessionId()) {
        await withBusy(
          button,
          async () => {
            const token = routeToken();
            await api.note(getSessionId(), {
              text,
              timestamp_seconds: $("#original-video")?.currentTime || 0,
              evidence_ids: getVideoEvidence() ? [getVideoEvidence().id] : [],
            });
            if (stillHere(token)) {
              closeModal();
              await refreshHere(token);
              toast("Your note is saved.");
            }
          },
          "Saving your note…",
        );
      } else {
        state.notes["recording-" + currentId + "-" + Date.now()] = text;
        if (save()) {
          closeModal();
          toast("Your note is saved in Memories.");
        }
      }
      break;
    }
    case "retry-turn": {
      const sid = button.dataset.session;
      const intent = pendingTurn(sid);
      if (intent) await sendTurn(sid, intent.text, intent.evidence_ids, intent.answer_to, button);
      break;
    }
    case "discard-turn":
      discardPendingTurn(button.dataset.session);
      await render();
      toast("Retry cleared. The saved conversation is unchanged.");
      break;
    case "open-memory": {
      const sid = button.dataset.session;
      closeModal();
      if (currentRoute === "live" && currentId === sid) await openSource(id);
      else { sourceToOpen = { id, session: sid }; navigate("live/" + sid); }
      break;
    }
    case "refresh-sdk-input": {
      const sid = getSessionId();
      if (!sid) { toast("Open a recipe first."); break; }
      await withBusy(button, async () => {
        const data = await api.session(sid);
        const latest = data.evidence.filter((item) => item.session_id === sid && ["meta_dat_mock", "meta_dat_device"].includes(item.capture_source)).at(-1);
        if (!latest) { toast("No SDK capture has arrived for this session. The recorded replay remains selected."); return; }
        state.selectedEvidence[sid] = latest.id; save(); navigate("live/" + sid);
        toast("SDK capture selected. Check its source before observing it.");
      });
      break;
    }
    case "device-mode":
      state.deviceMode = deviceMode(button.dataset.mode);
      save();
      await render();
      break;
    case "open-moment":
      state.selectedEvidence[button.dataset.session] = id;
      save();
      navigate("live/" + button.dataset.session);
      break;
    case "replay-play":
      try { await currentPlayer()?.play(); } catch { toast("Press play on the original recording to enable audio."); }
      break;
    case "replay-pause":
      currentPlayer()?.pause();
      break;
    case "observe-replay":
      await observeReplay();
      break;
    case "speak-knowledge": {
      const text = keptExplanation(bundle, id);
      if (!text) { toast("Review and confirm this explanation before reading it as an instruction."); break; }
      if (!("speechSynthesis" in window)) { toast("This browser cannot read aloud. Use the original recording."); break; }
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = "es-ES";
      window.speechSynthesis.speak(utterance);
      break;
    }
    case "stop-speaking":
      window.speechSynthesis?.cancel();
      break;
    case "sdk-help":
      modal("Ray-Ban Meta · recorded replay", `<p class="dialog-intro">Use the official Android MockDeviceKit with a selected video. It provides a simulated screenless camera source; the phone is the controller.</p><p>The local browser replay works without an Android device. The native SDK adapter is in <code>wearables/android/CompincheReplay.kt</code>. It streams real SDK frame buffers and uploads recorded evidence only when explicitly requested.</p>${familyDemo?.sdk_replay_url ? `<a class="button button-light" href="${esc(familyDemo.sdk_replay_url)}" download="compinche-rayban-replay.mp4">Download the HEVC replay for MockDeviceKit</a>` : ""}<p class="fine-print">No physical glasses are connected by this browser. A native Android runtime is required to run MockDeviceKit itself.</p>`);
      break;
    case "open-source":
      await openSource(id);
      break;
    case "close-source": {
      const preview = $("#source-preview");
      sourceEpoch++;
      if (preview) { preview.querySelectorAll("video,audio").forEach((media) => media.pause()); preview.replaceChildren(); }
      if (!$("#knowledge-wording")) closeModal();
      break;
    }
    case "confirm-knowledge":
      reviewKnowledge(id, button.dataset.session);
      break;
    case "save-knowledge": {
      const text = $("#knowledge-wording")?.value.trim();
      if (!text) { toast("Keep a few words for this explanation."); break; }
      sourceEpoch++;
      $$("#source-preview video,#source-preview audio").forEach((media) => media.pause());
      await withBusy(button, async () => {
        const token = routeToken();
        await api.confirm(button.dataset.session, id, text);
        if (stillHere(token)) { closeModal(); await render(); }
        toast("Your wording is confirmed and kept with its original evidence.");
      }, "Keeping your explanation…");
      break;
    }
    case "refresh-health":
      await withBusy(
        button,
        async () => {
          $("#app").innerHTML =
            '<div class="opening-loader"><strong>compinche.</strong><span>Making room for a little knowledge…</span></div>';
          await loadHealth();
          await render();
          toast(
            health
              ? "Connections refreshed."
              : "The local agent server is unavailable.",
          );
        },
        "Checking connections…",
      );
      break;
    case "sam-test":
      modal(
        "A real look at the scene.",
        `<span class="tag sage">Actual SAM 3.1 output</span><video class="full-video" style="margin-top:18px" src="./assets/sam-test-overlay.mp4" poster="./assets/sam-test-poster.jpg" controls playsinline aria-label="Actual SAM saucepan tracking test"></video><p class="dialog-intro" style="margin-top:16px">A saucepan tracked across eight frames from a two-second stock clip. The original and segmented views are shown together.</p><p class="fine-print">This is a recorded local inference test, not a live camera feed. Object continuity does not prove physical scale or cooking readiness.</p><a href="./assets/sam-test-result.json" target="_blank" rel="noopener" class="text-link">Open the evidence report ${icon("arrowUp")}</a>`,
      );
      break;
  }
});
document.addEventListener("submit", async (event) => {
  if (event.target.id !== "agent-form") return;
  event.preventDefault();
  const input = $("#agent-input");
  const text = input.value.trim();
  if (!text || !isMetaReady()) return;
  const button = $("button", event.target);
  await sendTurn(
    event.target.dataset.session,
    text,
    [],
    $("#answer-pending")?.checked ? bundle?.pending_question?.id : null,
    button,
  );
});
document.addEventListener("change", (event) => {
  if (event.target.id === "evidence-select") {
    state.selectedEvidence[getSessionId()] = event.target.value;
    save();
    render();
  }
});
document.addEventListener("input", (event) => {
  if (["clip-start", "clip-length"].includes(event.target.id) && observationContext) {
    observationContext.revision++;
    observationContext.evidence = null;
    if ($("#send-observation")) $("#send-observation").disabled = true;
    const preview = $("#analysis-preview");
    if (preview) { preview.querySelectorAll("video").forEach((video) => video.pause()); preview.textContent = "Prepare the updated interval before sending."; }
  }
  if (event.target.id === "memory-query") {
    query = event.target.value;
    clearTimeout(memoryTimer);
    memoryTimer = setTimeout(updateRealMemories, 180);
  }
});
document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "k") {
    event.preventDefault();
    search();
  }
  const dialog = $(".dialog");
  if (!dialog) return;
  if (event.key === "Escape") {
    event.preventDefault();
    closeModal();
  }
  if (event.key === "Tab") {
    const elements = $$(
      "button:not([disabled]),a[href],input:not([disabled]),textarea:not([disabled]),select,video[controls],audio[controls]",
      dialog,
    ).filter((el) => el.getClientRects().length);
    const first = elements[0],
      last = elements[elements.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  }
});
window.addEventListener("hashchange", () => {
  closeModal();
  window.scrollTo({ top: 0, behavior: "instant" });
  render();
});
window.addEventListener("pagehide", () => {
  cleanupExperience?.();
  cleanupCapture?.();
  clearTimeout(activeJobTimer);
});
$("#app").innerHTML =
  '<div class="opening-loader"><strong>compinche.</strong><span>Making room for a little knowledge…</span></div>';
await loadHealth();
await render();
