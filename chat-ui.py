"""Serve the local chat and swarm pages on this PC only."""
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import memory
import project_files
from swarm import MODEL, swarm

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
APP_DIR = os.path.dirname(os.path.abspath(__file__))
HOST = "127.0.0.1"
PORT = 8787
IDLE_SECONDS = 10 * 60
CHATS = Path(APP_DIR) / "chats"
_CHAT_ID = re.compile(r"^[a-f0-9]{32}$")
_NO_WINDOW = 0x08000000
_activity_lock = threading.Lock()
_busy = 0
_activity = time.monotonic()


def note_busy(active):
    """Count a chat or startup so an idle shutdown waits until it finishes."""
    global _busy, _activity
    with _activity_lock:
        if active:
            _busy += 1
        else:
            _busy = max(0, _busy - 1)
        _activity = time.monotonic()


def model_up():
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=1) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def model_ready():
    """True when this project's model is actually listed, not merely when the server answers."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return False
    names = [item.get("name") or "" for item in (data.get("models") or []) if isinstance(item, dict)]
    return MODEL in names


def _powershell(command, timeout):
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-WindowStyle", "Hidden",
            "-Command", command,
        ],
        cwd=APP_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=_NO_WINDOW,
    )


def ensure_model():
    """Start Ollama with this project's model folder. A tray server with no model is restarted."""
    if model_ready():
        return
    completed = _powershell(". .\\env.ps1; Start-LocalOllama -Restart", 70)
    if completed.returncode != 0 or not model_ready():
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(
            detail[:300] or "Ollama is up, but %s is not in this project's model folder." % MODEL
        )


def stop_model():
    """Stop the Ollama process. The chat page stays up."""
    if swarm.running:
        swarm.request_stop()
    _powershell(
        "Get-Process -Name 'ollama','ollama app' -ErrorAction SilentlyContinue | "
        "Stop-Process -Force -ErrorAction SilentlyContinue",
        20,
    )


def _code_stamp():
    stamp = 0.0
    for name in ("chat-ui.py", "swarm.py", "project_files.py", "memory.py"):
        try:
            stamp = max(stamp, (Path(APP_DIR) / name).stat().st_mtime)
        except OSError:
            continue
    return stamp


_CODE_STAMP = _code_stamp()
_server = None
_restarting = False


def _watch_code():
    """Restart when the swarm code on disk is newer than the copy this process loaded."""
    global _restarting
    while True:
        time.sleep(2)
        try:
            if _restarting or _server is None:
                continue
            with _activity_lock:
                busy = _busy
            if busy or swarm.running:
                continue
            if _code_stamp() <= _CODE_STAMP:
                continue
            time.sleep(0.5)
            with _activity_lock:
                busy = _busy
            if busy or swarm.running or _code_stamp() <= _CODE_STAMP:
                continue
            _restarting = True
            _server.shutdown()
            _server.server_close()
            time.sleep(0.3)
            subprocess.Popen(
                [sys.executable, str(Path(APP_DIR) / "chat-ui.py")],
                cwd=APP_DIR,
                creationflags=_NO_WINDOW,
            )
            os._exit(0)
        except Exception:
            _restarting = False
            continue


def _watch_idle():
    while True:
        time.sleep(30)
        with _activity_lock:
            busy = _busy
            idle = time.monotonic() - _activity
        if busy or swarm.running or idle < IDLE_SECONDS:
            continue
        if model_up():
            stop_model()


def _chat_file(chat_id):
    if not _CHAT_ID.fullmatch(chat_id or ""):
        raise ValueError("Unknown chat.")
    return CHATS / (chat_id + ".json")


def _clip(value, limit):
    text = value if isinstance(value, str) else ""
    return text[:limit]


def list_chats(kind):
    """Newest saved conversations of one kind, without the full transcript."""
    if kind not in ("chat", "swarm"):
        kind = "chat"
    CHATS.mkdir(exist_ok=True)
    items = []
    for path in CHATS.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if data.get("kind") != kind or not _CHAT_ID.fullmatch(data.get("id") or ""):
            continue
        items.append({
            "id": data["id"],
            "title": data.get("title") or "Untitled",
            "updated": data.get("updated") or 0,
        })
    items.sort(key=lambda item: item["updated"], reverse=True)
    return items[:50]


def load_chat(chat_id):
    path = _chat_file(chat_id)
    if not path.is_file():
        raise ValueError("Unknown chat.")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("kind") == "swarm":
        return {
            "id": data.get("id"),
            "kind": "swarm",
            "title": data.get("title") or "Untitled",
            "updated": data.get("updated") or 0,
            "turns": data.get("turns") if isinstance(data.get("turns"), list) else [],
            "final": data.get("final") or "",
            "seats": data.get("seats") if isinstance(data.get("seats"), dict) else {},
            "refs": _clean_refs(data.get("refs"), data.get("id")),
        }
    return {
        "id": data.get("id"),
        "kind": "chat",
        "title": data.get("title") or "Untitled",
        "updated": data.get("updated") or 0,
        "messages": data.get("messages") if isinstance(data.get("messages"), list) else [],
        "refs": _clean_refs(data.get("refs"), data.get("id")),
    }


def save_chat(payload):
    """Write one conversation. An empty one is not stored."""
    payload = payload if isinstance(payload, dict) else {}
    kind = payload.get("kind") if payload.get("kind") in ("chat", "swarm") else "chat"
    chat_id = payload.get("id") or uuid.uuid4().hex
    if not _CHAT_ID.fullmatch(chat_id):
        chat_id = uuid.uuid4().hex
    if kind == "swarm":
        turns = []
        for turn in payload.get("turns") or []:
            if not isinstance(turn, dict):
                continue
            ask = _clip(turn.get("ask"), 4000).strip()
            answer = _clip(turn.get("answer"), 12000)
            if ask or answer:
                turns.append({"ask": ask, "answer": answer})
            if len(turns) >= 16:
                break
        if not turns:
            return {"id": chat_id, "saved": False}
        seats = {}
        raw_seats = payload.get("seats") if isinstance(payload.get("seats"), dict) else {}
        for seat_id, seat in list(raw_seats.items())[:16]:
            if not isinstance(seat, dict):
                continue
            key = "".join(ch for ch in str(seat_id) if ch.isalnum() or ch in "-_")[:32].strip("-_")
            if not key:
                continue
            seats[key] = {
                "name": _clip(seat.get("name"), 40),
                "text": _clip(seat.get("text"), 8000),
            }
        title = " ".join(turns[0]["ask"].split())[:72] or "Swarm"
        record = {
            "id": chat_id,
            "kind": "swarm",
            "title": title,
            "updated": time.time(),
            "turns": turns,
            "final": _clip(payload.get("final"), 20000),
            "seats": seats,
            "refs": _clean_refs(payload.get("refs"), chat_id),
        }
    else:
        messages = []
        total = 0
        for message in payload.get("messages") or []:
            if not isinstance(message, dict) or message.get("role") not in ("user", "assistant"):
                continue
            content = _clip(message.get("content"), 20000)
            total += len(content)
            if total > 400000:
                break
            messages.append({"role": message["role"], "content": content})
            if len(messages) >= 80:
                break
        if not any(item["role"] == "user" and item["content"].strip() for item in messages):
            return {"id": chat_id, "saved": False}
        title = next(item["content"] for item in messages if item["role"] == "user" and item["content"].strip())
        record = {
            "id": chat_id,
            "kind": "chat",
            "title": " ".join(title.split())[:72] or "Chat",
            "updated": time.time(),
            "messages": messages,
            "refs": _clean_refs(payload.get("refs"), chat_id),
        }
    CHATS.mkdir(exist_ok=True)
    path = CHATS / (chat_id + ".json")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    return {"id": chat_id, "title": record["title"], "updated": record["updated"], "saved": True}


def delete_chat(chat_id):
    path = _chat_file(chat_id)
    if path.is_file():
        path.unlink()


_WORDS = re.compile(r"[a-z0-9]{3,}")
_STOP = {
    "the", "and", "for", "with", "this", "that", "from", "you", "your", "are", "was",
    "were", "have", "has", "not", "but", "can", "one", "into", "about", "what", "when",
    "how", "its", "all", "any", "just", "like", "then", "than", "them", "they",
}


def _tokens(text):
    return {word for word in _WORDS.findall((text or "").lower()) if word not in _STOP}


def _named_files(text):
    """File names a message actually names. Shared words like notebook are not enough."""
    return {name.lower() for name in re.findall(r"[\w.-]+\.(?:ipynb|py|csv|tsv|txt|md)", text or "")}


def _clean_refs(values, skip_id=""):
    found = []
    for value in values or []:
        text = value if isinstance(value, str) else ""
        if _CHAT_ID.fullmatch(text) and text != skip_id and text not in found:
            found.append(text)
        if len(found) >= 6:
            break
    return found


def _saved_chats():
    if not CHATS.is_dir():
        return
    for path in CHATS.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if _CHAT_ID.fullmatch(data.get("id") or ""):
            yield data


def _search_text(data):
    parts = [data.get("title") or ""]
    if data.get("kind") == "swarm":
        for turn in data.get("turns") or []:
            if isinstance(turn, dict):
                parts.append(turn.get("ask") or "")
                parts.append((turn.get("answer") or "")[:500])
    else:
        for message in data.get("messages") or []:
            if not isinstance(message, dict):
                continue
            content = message.get("content") or ""
            parts.append(content if message.get("role") == "user" else content[:500])
    return "\n".join(parts)


def _excerpt(data):
    pairs = []
    if data.get("kind") == "swarm":
        for turn in data.get("turns") or []:
            if isinstance(turn, dict):
                pairs.append(((turn.get("ask") or "").strip(), (turn.get("answer") or "").strip()))
    else:
        pending = None
        for message in data.get("messages") or []:
            if not isinstance(message, dict):
                continue
            content = (message.get("content") or "").strip()
            if message.get("role") == "user":
                if pending:
                    pairs.append(pending)
                pending = (content, "")
            elif message.get("role") == "assistant" and pending:
                pairs.append((pending[0], content))
                pending = None
        if pending:
            pairs.append(pending)
    pairs = [pair for pair in pairs if pair[0] or pair[1]]
    if not pairs:
        return ""
    chosen = [pairs[0]]
    if len(pairs) > 1:
        chosen.append(pairs[-1])
    lines = []
    for ask, answer in chosen:
        if ask:
            lines.append("Prompt: " + " ".join(ask.split())[:280])
        if answer:
            lines.append("Result: " + " ".join(answer.split())[:280])
    return "\n".join(lines)


def related_chats(query, skip_id="", pinned=None, limit=3):
    """Other saved chats that were pinned, or that share the words in this prompt."""
    query_words = _tokens(query)
    pinned_ids = _clean_refs(pinned, skip_id)
    ranked = []
    forced = []
    for data in _saved_chats():
        chat_id = data["id"]
        if chat_id == skip_id:
            continue
        chat_files = _named_files((data.get("title") or "") + "\n" + _excerpt(data))
        if chat_files and not (chat_files & _named_files(query)) and chat_id not in pinned_ids:
            continue
        shared = len(query_words & _tokens(_search_text(data))) if query_words else 0
        overlap = shared / float(len(query_words)) if query_words else 0.0
        hit = {
            "id": chat_id,
            "title": data.get("title") or "Untitled",
            "kind": "swarm" if data.get("kind") == "swarm" else "chat",
            "excerpt": _excerpt(data),
            "pinned": chat_id in pinned_ids,
        }
        if chat_id in pinned_ids:
            forced.append(hit)
        elif shared >= 2 and overlap >= 0.15:
            ranked.append((overlap, hit))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    chosen = []
    seen = set()
    for hit in forced + [item for _, item in ranked]:
        if hit["id"] in seen:
            continue
        seen.add(hit["id"])
        chosen.append({key: hit[key] for key in ("id", "title", "kind", "excerpt", "pinned")})
        if len(chosen) >= limit:
            break
    return chosen


def reference_block(items):
    if not items:
        return ""
    lines = [
        "Earlier chats saved on this PC. The current chat is its own series of prompts. "
        "Use an earlier chat only when it has a fact this prompt needs. "
        "Do not treat those chats as this project."
    ]
    for item in items:
        label = "Pinned" if item.get("pinned") else "Related"
        kind = "swarm chat" if item.get("kind") == "swarm" else "chat"
        lines.append("%s earlier %s: %s\n%s" % (label, kind, item.get("title") or "Untitled", item.get("excerpt") or ""))
    return "\n\n".join(lines)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def log_message(self, fmt, *args):
        return

    def handle(self):
        try:
            super().handle()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, TimeoutError):
            return
        except OSError as exc:
            # The browser closed the connection before the request finished.
            if getattr(exc, "winerror", None) in (10053, 10054, 10038):
                return
            raise

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/api/swarm/events":
            self._events()
            return
        if path == "/api/lessons":
            self._json(200, {"lessons": memory.list_lessons()})
            return
        if path == "/api/model":
            self._json(200, {"running": model_ready()})
            return
        if path == "/api/chats":
            kind = (urllib.parse.parse_qs(parsed.query).get("kind") or ["chat"])[0]
            self._json(200, {"chats": list_chats(kind)})
            return
        if path.startswith("/api/chats/"):
            try:
                self._json(200, load_chat(path.rsplit("/", 1)[-1]))
            except (ValueError, OSError, UnicodeError, json.JSONDecodeError):
                self._json(404, {"error": "That chat is not on this PC."})
            return
        super().do_GET()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_error(400)
            return
        if path == "/api/chat":
            self._chat(data)
            return
        if path == "/api/browse-folder":
            self._browse_folder(data)
            return
        if path == "/api/model/stop":
            stop_model()
            self._json(200, {"running": model_up()})
            return
        if path == "/api/chats":
            self._json(200, save_chat(data))
            return
        if path == "/api/chats/delete":
            try:
                delete_chat((data or {}).get("id") or "")
            except ValueError:
                self._json(400, {"ok": False, "error": "Unknown chat."})
                return
            self._json(200, {"ok": True})
            return
        if path == "/api/swarm/guide":
            swarm.guide(data.get("guidance") or "")
            self._json(200, {"ok": True})
            return
        if path == "/api/swarm/stop":
            swarm.request_stop()
            self._json(200, {"ok": True})
            return
        if path != "/api/swarm":
            self.send_error(404)
            return
        raw_problem = (data.get("problem") or "").strip()
        if not raw_problem:
            self._json(400, {"ok": False, "error": "Describe a problem first."})
            return
        skip = data.get("chatId") or ""
        if not _CHAT_ID.fullmatch(skip or ""):
            skip = ""
        related = related_chats(raw_problem, skip_id=skip, pinned=data.get("refs"))
        problem = _with_history(raw_problem, data.get("history"))
        background = reference_block(related)
        if background:
            problem = background + "\n\n" + problem
        note_busy(True)
        try:
            try:
                ensure_model()
            except Exception as exc:
                self._json(503, {"ok": False, "error": "Could not start the local model. %s" % exc})
                return
        finally:
            note_busy(False)
        members = data.get("members") if isinstance(data.get("members"), list) else []
        if not swarm.start(
            problem,
            data.get("team") or "",
            members,
            bool(data.get("projectMode")),
            data.get("projectName") or "",
            (data.get("runMode") or "safe") == "yolo",
        ):
            self._json(409, {"ok": False, "error": "A swarm is already working."})
            return
        self._json(202, {
            "ok": True,
            "references": [
                {"id": item["id"], "title": item["title"], "pinned": item["pinned"]}
                for item in related
            ],
        })

    def _browse_folder(self, data):
        try:
            selected = project_files.choose_folder((data or {}).get("path") or "")
        except ValueError as exc:
            self._json(400, {"path": "", "error": str(exc)})
            return
        except Exception as exc:
            self._json(500, {"path": "", "error": "Could not open the folder picker. %s" % exc})
            return
        self._json(200, {"path": selected})

    def _chat(self, data):
        messages = data.get("messages") if isinstance(data.get("messages"), list) else []
        user_text = ""
        for message in messages:
            if isinstance(message, dict) and message.get("role") == "user":
                user_text = message.get("content") or ""
        lessons = memory.retrieve(user_text)
        skip = data.get("chatId") or ""
        if not _CHAT_ID.fullmatch(skip or ""):
            skip = ""
        related = related_chats(user_text, skip_id=skip, pinned=data.get("refs"))
        note = "\n\n".join(
            part for part in (
                memory.prompt_block(lessons),
                memory.code_block(user_text),
                reference_block(related),
            ) if part
        )
        if note:
            if messages and isinstance(messages[0], dict) and messages[0].get("role") == "system":
                messages[0]["content"] = note + "\n\n" + (messages[0].get("content") or "")
            else:
                messages.insert(0, {"role": "system", "content": note})
        note_busy(True)
        try:
            try:
                ensure_model()
            except Exception as exc:
                self._json(503, {"error": "Could not start the local model. %s" % exc})
                return
            payload = json.dumps({
                "model": data.get("model") or "qwen3.6:27b",
                "messages": messages,
                "stream": True,
                "think": False,
                "options": {"num_ctx": memory.NUM_CTX},
            }).encode("utf-8")
            request = urllib.request.Request(
                "http://127.0.0.1:11434/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            try:
                response = urllib.request.urlopen(request, timeout=120)
            except urllib.error.URLError as exc:
                self._json(502, {"error": "The local model is not running. %s" % exc})
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Lessons", str(len(lessons)))
            self.end_headers()
            if related:
                notice = json.dumps({
                    "references": [
                        {"id": item["id"], "title": item["title"], "pinned": item["pinned"]}
                        for item in related
                    ]
                })
                self.wfile.write((notice + "\n").encode("utf-8"))
                self.wfile.flush()
            assistant = []
            try:
                while True:
                    line = response.readline()
                    if not line:
                        break
                    self.wfile.write(line)
                    self.wfile.flush()
                    try:
                        event = json.loads(line.decode("utf-8"))
                    except json.JSONDecodeError:
                        continue
                    piece = ((event.get("message") or {}).get("content")) or ""
                    if piece:
                        assistant.append(piece)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                pass
            finally:
                response.close()
            answer = "".join(assistant).strip()
            if user_text and answer:
                threading.Thread(
                    target=_remember_chat,
                    args=(user_text, answer),
                    daemon=True,
                ).start()
        finally:
            note_busy(False)

    def _json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _events(self):
        q, backlog = swarm.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            for event in backlog:
                self._write_event(event)
            while True:
                self._write_event(q.get())
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass
        finally:
            swarm.unsubscribe(q)

    def _write_event(self, event):
        payload = json.dumps(event, ensure_ascii=False)
        self.wfile.write(("data: %s\n\n" % payload).encode("utf-8"))
        self.wfile.flush()


class Server(ThreadingHTTPServer):
    allow_reuse_address = True


def _with_history(problem, history):
    """Carry the last few exchanges so a follow-up stays the same conversation."""
    if not isinstance(history, list) or not history:
        return "The user now says:\n%s" % problem
    current_files = _named_files(problem)
    lines = [
        "This continues the same conversation. Follow the latest message. "
        "If an earlier answer names a file the latest message does not name, that file is a different project. Do not use it."
    ]
    for turn in history[-8:]:
        if not isinstance(turn, dict):
            continue
        ask = (turn.get("ask") or "").strip()
        answer = (turn.get("answer") or "").strip()
        if not ask and not answer:
            continue
        earlier_files = _named_files(ask + "\n" + answer)
        if earlier_files and not (earlier_files & current_files):
            answer = "That answer was about a different project. Do not reuse its files."
        lines.append("Earlier you asked:\n%s\n\nThe team answered:\n%s" % (ask[:1200], answer[:1800]))
    lines.append("The user now says:\n%s" % problem)
    return "\n\n".join(lines)


def _remember_chat(user_text, answer):
    try:
        memory.remember(user_text, answer, "chat")
        if _looks_like_code(user_text) or _looks_like_code(answer):
            memory.remember_code("chat", user_text, [], answer)
    except Exception:
        return


def _looks_like_code(text):
    sample = text or ""
    return "```" in sample or "\ndef " in sample or sample.startswith("def ") or "\nclass " in sample or sample.startswith("class ")


if __name__ == "__main__":
    threading.Thread(target=_watch_idle, daemon=True).start()
    threading.Thread(target=_watch_code, daemon=True).start()
    server = Server((HOST, PORT), Handler)
    _server = server
    print("Local chat at http://%s:%s/" % (HOST, PORT), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
