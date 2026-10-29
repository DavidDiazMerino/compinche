const paths = {
  home: "M3 10 12 3l9 7M5 9v11h5v-6h4v6h5V9",
  learn: "M12 3v5m-4-3 1 4m7-4-1 4M5 10h14v3a7 7 0 0 1-14 0v-3ZM8 20h8",
  cook: "M4 10a8 8 0 0 1 16 0M3 14h18M6 14v6h12v-6M12 3v2",
  memory:
    "M4 4h6a3 3 0 0 1 3 3v14a4 4 0 0 0-4-3H4V4Zm9 3a3 3 0 0 1 3-3h5v14h-4a4 4 0 0 0-4 3",
  search: "m21 21-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
  arrow: "M4 12h15m-5-5 5 5-5 5",
  arrowUp: "M6 18 18 6M6 6h12v12",
  chevron: "m9 5 7 7-7 7",
  back: "M20 12H5m5 5-5-5 5-5",
  plus: "M12 4v16M4 12h16",
  play: "m9 5 11 7-11 7V5Z",
  pause: "M8 5v14M16 5v14",
  hand: "M8 12V7a2 2 0 0 1 4 0v5-8a2 2 0 0 1 4 0v9-6a2 2 0 0 1 4 0v9c0 4-3 6-6 6-3 0-5-2-7-4l-4-5a2 2 0 0 1 3-2l2 2",
  spark:
    "m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3ZM20 2v4m-2-2h4",
  eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Zm13 0a3 3 0 1 1-6 0 3 3 0 0 1 6 0",
  info: "M12 11v6m0-10h.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
  close: "m6 6 12 12M6 18 18 6",
  check: "m5 12 4 4L19 6",
  bookmark: "M6 3h12v19l-6-4-6 4V3Z",
  clock: "M12 6v6l4 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
  camera: "M3 7h4l2-3h6l2 3h4v14H3V7Zm13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
  video: "M3 6h12v12H3V6Zm12 4 6-3v10l-6-3",
  scale: "M3 17 17 3l4 4L7 21l-4-4Zm4-4 3 3m1-7 3 3m1-7 3 3",
  download: "M12 3v12m-5-5 5 5 5-5M3 17v4h18v-4",
  edit: "m4 16 12-12 4 4L8 20H4v-4Zm9-9 4 4",
  volume: "M3 9h4l5-5v16l-5-5H3V9Zm13-2a7 7 0 0 1 0 10m3-13a11 11 0 0 1 0 16",
  folder: "M3 7V4h6l3 3h9v13H3V7Z",
  leaf: "M20 3C7 2 3 9 6 15c6 6 15 0 14-12ZM4 21 15 10",
  flame: "M12 3c0 5-5 6-5 10a5 5 0 0 0 10 0c0-3-1-5-3-7 0 3-2 4-2 4V3Z",
  layers: "m12 3 10 5-10 5L2 8l10-5ZM2 12l10 5 10-5M2 16l10 5 10-5",
  trash: "M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7",
  full: "M3 8V3h5m8 0h5v5M3 16v5h5m8 0h5v-5",
};
export const icon = (name, cl = "") =>
  `<svg class="icon ${cl}" viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[name] || paths.spark}"/></svg>`;
export const brandMark = `<svg class="brand-mark" viewBox="0 0 40 40" fill="none" aria-hidden="true"><path d="M23 7a13 13 0 1 0 0 26M32 12a8 8 0 1 0 0 16" stroke="currentColor" stroke-width="4.3" stroke-linecap="round"/></svg>`;
