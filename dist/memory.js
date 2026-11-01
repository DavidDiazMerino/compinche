export function searchKey(text) {
  return String(text ?? "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/ß/g, "ss");
}

export function findPersonalNotes(notes, query, exampleIds = []) {
  if (!query.trim()) return [];
  return Object.entries(notes).filter(([, text]) => typeof text === "string" && text.trim() && searchKey(text).includes(searchKey(query)))
    .map(([id, text]) => ({ id, kind: "local_note", title: exampleIds.includes(id) ? "Your note on an illustrative example" : "Your personal note", teacher: "You", text }));
}
