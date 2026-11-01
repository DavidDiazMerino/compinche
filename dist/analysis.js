export function selectedInterval(evidence, displayedId, currentTime = 0) {
  const start = evidence.id === displayedId && Number.isFinite(currentTime) ? Math.max(0, currentTime - 6) : 0;
  const available = Number(evidence.duration_seconds);
  const length = available > 0 ? Math.min(15, available - start) : 15;
  return { start_seconds: start, duration_seconds: Math.max(.1, length) };
}

export function elapsedReplayInterval(currentTime) {
  if (!Number.isFinite(currentTime) || currentTime <= 0) return null;
  const start = Math.max(0, currentTime - 12);
  return { start_seconds: start, duration_seconds: currentTime - start };
}
