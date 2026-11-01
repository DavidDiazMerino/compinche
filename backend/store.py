"""Small durable local store. Media remains on this computer until explicitly analysed."""
import json
import sqlite3
import threading
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return str(uuid4())


class Store:
    def __init__(self, data_root: Path):
        self.root = data_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "media").mkdir(exist_ok=True)
        (self.root / "artifacts").mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self._transaction_depth = 0
        self.conn = sqlite3.connect(self.root / "compinche.sqlite3", check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS entities (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL, data TEXT NOT NULL)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS entity_session ON entities(session_id, kind, created_at)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS turns (session_id TEXT NOT NULL, request_key TEXT NOT NULL, request_json TEXT NOT NULL, result_json TEXT NOT NULL, PRIMARY KEY(session_id, request_key))")
        self.conn.commit()
        # An interrupted job cannot remain 'running' after a process restart.
        for job in self.all_kind("job"):
            if job["status"] in ("queued", "running"):
                job.update(status="interrupted", error="The local server stopped before this job completed.")
                self.put(job["session_id"], "job", job)

    def create_session(self, values):
        session = {"id": uid(), **values, "created_at": now(), "updated_at": now(), "pending_question": None}
        with self.lock:
            self.conn.execute("INSERT INTO sessions VALUES (?, ?)", (session["id"], json.dumps(session)))
            self._commit()
        return session

    def get_session(self, sid):
        with self.lock:
            row = self.conn.execute("SELECT data FROM sessions WHERE id=?", (sid,)).fetchone()
        return json.loads(row[0]) if row else None

    def update_session(self, sid, **updates):
        with self.lock:
            session = self.get_session(sid)
            if session is None:
                raise KeyError(sid)
            session.update(updates, updated_at=now())
            self.conn.execute("UPDATE sessions SET data=? WHERE id=?", (json.dumps(session), sid))
            self._commit()
        return session

    def list_sessions(self):
        with self.lock:
            rows = self.conn.execute("SELECT data FROM sessions").fetchall()
        return sorted((json.loads(r[0]) for r in rows), key=lambda s: s["updated_at"], reverse=True)

    def put(self, sid, kind, entity):
        entity = {"id": uid(), "created_at": now(), **entity, "session_id": sid}
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO entities VALUES (?, ?, ?, ?, ?)", (entity["id"], sid, kind, entity["created_at"], json.dumps(entity)))
            self._commit()
        return entity

    def get(self, eid, kind=None):
        with self.lock:
            row = self.conn.execute("SELECT kind, data FROM entities WHERE id=?", (eid,)).fetchone()
        return json.loads(row[1]) if row and (not kind or row[0] == kind) else None

    def list(self, sid, kind):
        with self.lock:
            rows = self.conn.execute("SELECT data FROM entities WHERE session_id=? AND kind=? ORDER BY created_at, rowid", (sid, kind)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def all_kind(self, kind):
        with self.lock:
            rows = self.conn.execute("SELECT data FROM entities WHERE kind=?", (kind,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def live_events(self, sid, after=0, limit=200):
        with self.lock:
            rows = self.conn.execute("SELECT data FROM entities WHERE session_id=? AND kind='live_event' AND CAST(json_extract(data, '$.seq') AS INTEGER)>? ORDER BY CAST(json_extract(data, '$.seq') AS INTEGER) LIMIT ?", (sid, after, min(limit, 201))).fetchall()
        return [json.loads(row[0]) for row in rows]

    def related_ids(self, sid):
        session = self.get_session(sid)
        return [sid] + ([session["source_session_id"]] if session and session.get("source_session_id") else [])

    def conversation(self, sid):
        """Recall the exact question with its assistant turn, including old data.

        Earlier records copied the active question ID onto later assistant turns.
        Resolve that legacy reference only at its first occurrence so a historical
        question is neither lost after an answer nor shown repeatedly.
        """
        questions = {q["id"]: q for q in self.list(sid, "question")}
        seen = set()
        messages = self.list(sid, "message")
        for message in messages:
            if message["role"] != "assistant":
                continue
            question_id = message.get("question_id")
            stored = message.get("question")
            question_id = question_id or (stored or {}).get("id")
            if question_id and question_id not in seen:
                message["question"] = questions.get(question_id, stored)
                seen.add(question_id)
            else:
                message["question"] = None
        return messages

    def context(self, sid):
        session = self.get_session(sid)
        if session is None:
            raise KeyError(sid)
        related = self.related_ids(sid)
        return {
            "session": session,
            "messages": self.conversation(sid),
            "questions": self.list(sid, "question"),
            "evidence": [e for x in related for e in self.list(x, "evidence")],
            "knowledge": [e for x in related for e in self.list(x, "knowledge")],
            "notes": [e for x in related for e in self.list(x, "note")],
            "jobs": [e for x in related for e in self.list(x, "job")],
            "pending_question": session.get("pending_question"),
        }

    def cached_turn(self, sid, key, request_json):
        if not key:
            return None
        with self.lock:
            row = self.conn.execute("SELECT request_json,result_json FROM turns WHERE session_id=? AND request_key=?", (sid, key)).fetchone()
        if row and row[0] != request_json:
            raise ValueError("This request key was already used for a different turn.")
        return json.loads(row[1]) if row else None

    def search(self, query="", limit=30, category="all"):
        def key(text):
            return " ".join("".join(char for char in unicodedata.normalize("NFKD", str(text)).casefold()
                                    if not unicodedata.combining(char)).split())
        needle = key(query)
        sessions = {item["id"]: item for item in self.list_sessions()}
        with self.lock:
            rows = self.conn.execute("SELECT kind,data FROM entities WHERE kind IN ('knowledge','note','evidence')").fetchall()
        matches = []
        for kind, raw in rows:
            item = json.loads(raw)
            session = sessions.get(item["session_id"])
            if not session or (kind == "evidence" and (not needle or category != "all" or item.get("role") in ("analysis_clip", "sdk_replay", "poster"))):
                continue
            if category == "confirmed" and (kind != "knowledge" or not item.get("confirmed")):
                continue
            if category == "needs_review" and (item.get("confirmed") or kind not in ("knowledge", "note")):
                continue
            if category == "notes" and kind != "note":
                continue
            text = item.get("text") or item.get("automatic_description") or item.get("note") or ""
            if not text or (needle and needle not in key(" ".join([text, session.get("title", ""), session.get("teacher", "")]))):
                continue
            matches.append({"id": item["id"], "kind": kind, "session_id": item["session_id"],
                "title": session.get("title", ""), "teacher": session.get("teacher", ""), "text": text[:600],
                "confirmed": bool(item.get("confirmed")), "source": item.get("source"),
                "evidence_ids": item.get("evidence_ids", []), "updated_at": item.get("confirmed_at") or item.get("created_at", "")})
        if needle and category == "all":
            for sid, session in sessions.items():
                if needle in key(session.get("title", "") + " " + session.get("teacher", "")):
                    matches.append({"id": sid, "kind": "session", "session_id": sid, "title": session.get("title", ""),
                        "teacher": session.get("teacher", ""), "text": "Recipe conversation", "updated_at": session["updated_at"]})
        matches.sort(key=lambda item: (item["updated_at"], item["id"]), reverse=True)
        return {"items": matches[:limit], "truncated": len(matches) > limit, "query": query}

    def remember_turn(self, sid, key, request_json, result):
        if key:
            with self.lock:
                self.conn.execute("INSERT OR REPLACE INTO turns VALUES (?, ?, ?, ?)", (sid, key, request_json, json.dumps(result)))
                self._commit()

    def _commit(self):
        if not self._transaction_depth:
            self.conn.commit()

    @contextmanager
    def atomic(self):
        """Save messages, questions, claims and the request receipt together."""
        with self.lock:
            outer = self._transaction_depth == 0
            if outer:
                self.conn.execute("BEGIN IMMEDIATE")
            self._transaction_depth += 1
            try:
                yield
            except BaseException:
                if outer:
                    self.conn.rollback()
                raise
            else:
                if outer:
                    self.conn.commit()
            finally:
                self._transaction_depth -= 1

    def close(self):
        self.conn.close()
