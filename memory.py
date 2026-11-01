"""Lessons kept on this PC. No downloads, no weight updates."""
import json
import re
import threading
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OLLAMA = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3.6:27b"
ROOT = Path(__file__).resolve().parent
STORE = ROOT / "memory" / "lessons.jsonl"
CODE_NOTES = ROOT / "memory" / "code_notes.jsonl"
CODE_EDITS = ROOT / "memory" / "code_edits.jsonl"
# Fits beside the 17GB model on a 24GB card, and holds the code the builder reads.
NUM_CTX = 12288
_LOCK = threading.Lock()
_WORDS = re.compile(r"[a-z0-9]{3,}")


def retrieve(query, project="", failure="", limit=4):
    """Methods that match this kind of failure, plus notes for this folder only."""
    signals = _signals((query or "") + "\n" + (failure or ""))
    methods = []
    for item in _load():
        if item.get("kind") == "project" or not item.get("lesson"):
            continue
        tags = set(item.get("tags") or [])
        score = len(tags & signals)
        if score <= 0:
            continue
        if tags & signals & {"dates", "pages", "rows"}:
            score += 2
        methods.append((score, item))
    methods.sort(key=lambda pair: pair[0], reverse=True)
    picked = []
    for _score, item in _project_notes(project, limit=2):
        picked.append(item)
    room = max(0, limit - len(picked))
    for _score, item in methods[:room]:
        picked.append(item)
    return picked[:limit]


def prompt_block(lessons):
    if not lessons:
        return ""
    methods = []
    local = []
    for item in lessons:
        line = item.get("lesson") or ""
        if not line:
            continue
        if item.get("kind") == "project":
            local.append(line)
        else:
            methods.append(line)
    parts = []
    if methods:
        parts.append(
            "Methods for this kind of failure. Use one when it fits.\n"
            + "\n".join("- " + line for line in methods)
        )
    if local:
        parts.append(
            "Notes for this folder only. They do not apply to a different project.\n"
            + "\n".join("- " + line for line in local)
        )
    return "\n\n".join(parts)


def show_lesson(item):
    text = (item or {}).get("lesson") or ""
    if (item or {}).get("kind") == "project" and text:
        return "This folder: " + text
    return text


def list_lessons():
    """Methods only. A note about one folder is not a lesson for every project."""
    with _LOCK:
        return [item for item in _load() if item.get("kind") != "project" and item.get("lesson")]


def remember(problem, outcome, source, project=""):
    problem = (problem or "").strip()
    outcome = (outcome or "").strip()
    if len(problem) < 12 or len(outcome) < 40:
        return None
    raw = _complete(
        "You keep methods on this computer. Write one reusable method from the work below. "
        "One sentence, starting with When. Name the kind of failure and the kind of fix. "
        "Do not name a file, a folder, a notebook, or a library that only this project used. "
        "Reply SKIP if this run only picked a route, or the lesson would only be true for one project.",
        "Problem:\n%s\n\nOutcome:\n%s" % (problem[:2000], outcome[:3000]),
        120,
    )
    lesson = _as_method(_clean_lesson(raw))
    if lesson:
        _store_method(lesson, source)
    fact = _project_fact(outcome)
    if fact and _real_project(project):
        remember_code(project, problem, [], fact)
    return lesson


def _store_method(lesson, source):
    with _LOCK:
        items = [item for item in _load() if item.get("kind") != "project"]
        best_index = -1
        best_score = 0.0
        lesson_words = _tokens(lesson)
        for index, item in enumerate(items):
            known = _tokens(item.get("lesson", ""))
            if not known or not lesson_words:
                continue
            overlap = len(lesson_words & known) / float(len(lesson_words))
            if overlap > best_score:
                best_score = overlap
                best_index = index
        stamp = datetime.now(timezone.utc).isoformat()
        record = {
            "ts": stamp,
            "source": source,
            "kind": "method",
            "tags": _tags_for(lesson),
            "lesson": lesson,
        }
        if best_score >= 0.55 and best_index >= 0:
            items[best_index] = record
        else:
            items.append(record)
        if len(items) > 20:
            merged = _consolidate(items)
            if merged:
                items = merged
        _save(items)


def _clean_lesson(raw):
    text = (raw or "").strip().strip('"')
    if not text or text.upper().startswith("SKIP"):
        return None
    line = text.splitlines()[0].strip()
    if len(line) < 20:
        return None
    return line[:400]


_TOO_SPECIFIC = re.compile(
    r"[A-Za-z]:\\|[/\\]|\b[\w.-]+\.(?:ipynb|py|csv|tsv)\b",
    re.I,
)


def _as_method(lesson):
    """A method names a kind of fix. A file, a folder, or one project's route is not a method."""
    text = (lesson or "").strip()
    if len(text) < 20 or _TOO_SPECIFIC.search(text):
        return None
    if "route or answer was chosen" in text.lower():
        return None
    return text[:400]


def _tags_for(text):
    low = (text or "").lower()
    tags = []
    if re.search(r"column|names that exist|expected a name", low):
        tags.append("names")
    if re.search(r"notebook|cell", low):
        tags.append("notebook")
    if re.search(r"must contain|contain a word", low):
        tags.append("phrase")
    if re.search(r"cannot find a file|missing file|already in the project", low):
        tags.append("missing-file")
    if re.search(r"install|library|module", low):
        tags.append("install")
    if re.search(r"another computer|setup", low):
        tags.append("setup")
    if re.search(r"solved|solution notebook", low):
        tags.append("solved")
    if re.search(r"check fails|test assertion|the code", low):
        tags.append("code")
    if re.search(r"relative phrase|title or datetime|month name", low):
        tags.append("dates")
    if re.search(r"older pages|older or previous", low):
        tags.append("pages")
    if re.search(r"row count|invent rows", low):
        tags.append("rows")
    return tags or ["code"]


def _signals(text):
    low = (text or "").lower()
    found = set()
    if re.search(r"column|keyerror|not found in|header", low):
        found.add("names")
    if re.search(r"notebook|\.ipynb|\bcell\b", low):
        found.add("notebook")
    if re.search(r"should contain|must contain", low):
        found.add("phrase")
    if re.search(r"no such file|filenotfound|cannot find", low):
        found.add("missing-file")
    if re.search(r"no module named|modulenotfound|install", low):
        found.add("install")
    if re.search(r"findspark|spark_home|credential|another computer|endpoint", low):
        found.add("setup")
    if re.search(r"\bsolved\b|solution", low):
        found.add("solved")
    if re.search(r"assertionerror|syntaxerror|nameerror|indexerror|datetime64|unfinished code", low):
        found.add("code")
    if re.search(r"\bnat\b|could not be parsed|weeks ago|datetime attribute", low):
        found.add("dates")
    if re.search(r"could not find (?:the )?(?:next|older)|older stories|pagination", low):
        found.add("pages")
    if re.search(r"expected \d+ rows|but got \d+", low):
        found.add("rows")
    return found


def _norm_project(value):
    text = (value or "").strip().strip('"').replace("/", "\\").lower()
    if text in {"", "my-project", "any project", "this pc"}:
        return ""
    return text[:240]


def _real_project(value):
    return bool(_norm_project(value))


def _same_project(saved, current):
    left = _norm_project(saved)
    right = _norm_project(current)
    if not left or not right:
        return False
    return left == right or left.startswith(right) or right.startswith(left)


def _project_fact(outcome):
    """Facts that are only true in this folder: columns, missing words, and the files named."""
    lines = []
    for raw in (outcome or "").splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        if re.search(
            r"\.csv:|should contain|AssertionError|No module named|No such file|Unfinished code:",
            line,
            re.I,
        ):
            lines.append(line[:180])
        if len(lines) >= 4:
            break
    if not lines:
        return None
    return " ".join(lines)[:500]


def _project_notes(project, limit=2):
    if not _real_project(project):
        return []
    found = []
    for item in _load_jsonl(CODE_NOTES):
        if not _same_project(item.get("project"), project):
            continue
        text = item.get("text") or ""
        if not text:
            continue
        found.append((1, {
            "kind": "project",
            "project": item.get("project") or "",
            "lesson": text[:500],
        }))
        if len(found) >= limit:
            break
    return found


def _consolidate(items):
    notebook = "\n".join("- " + item.get("lesson", "") for item in items)
    raw = _complete(
        "Merge these methods into at most 10 stronger methods. Drop repeats and anything that names one file or one project. "
        "Each method is one line starting with LESSON:",
        notebook[:8000],
        500,
    )
    merged = []
    stamp = datetime.now(timezone.utc).isoformat()
    for line in (raw or "").splitlines():
        match = re.match(r"(?i)^LESSON:\s*(.+)$", line.strip())
        if not match:
            continue
        lesson = _as_method(match.group(1).strip()[:400])
        if lesson:
            merged.append({
                "ts": stamp,
                "source": "review",
                "kind": "method",
                "tags": _tags_for(lesson),
                "lesson": lesson,
            })
    if len(merged) < 3:
        return None
    return merged[:10]


def _complete(system, user, limit):
    payload = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "think": False,
        "options": {"temperature": 0.2, "num_predict": limit, "num_ctx": NUM_CTX},
    }).encode("utf-8")
    request = urllib.request.Request(
        OLLAMA,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        data = json.loads(response.read().decode("utf-8"))
    if data.get("error"):
        raise RuntimeError(data["error"])
    return ((data.get("message") or {}).get("content")) or ""


def _tokens(text):
    return set(_WORDS.findall((text or "").lower()))


def _load():
    if not STORE.exists():
        return []
    items = []
    for line in STORE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return items


def _save(items):
    STORE.parent.mkdir(exist_ok=True)
    temporary = STORE.with_suffix(".tmp")
    body = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items)
    temporary.write_text(body, encoding="utf-8")
    temporary.replace(STORE)


def remember_code(project, problem, files, approach):
    """Keep a note for this folder. A different folder does not see it."""
    if not _real_project(project):
        return None
    problem = " ".join((problem or "").split())[:300]
    approach = " ".join((approach or "").split())[:500]
    names = []
    for name in files or []:
        text = str(name).strip()
        if text and text not in names:
            names.append(text)
        if len(names) >= 8:
            break
    if len(problem) < 12 and not names and not approach:
        return None
    if approach and not names:
        text = approach[:800]
    else:
        text = "Files: %s. What worked: %s." % (
            ", ".join(names) or "the project",
            approach or "the checks passed",
        )
    note = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "project": _norm_project(project),
        "files": names,
        "text": text[:800],
    }
    with _LOCK:
        items = _load_jsonl(CODE_NOTES)
        words = _tokens(text)
        replaced = False
        for item in items:
            if not _same_project(item.get("project"), project):
                continue
            known = _tokens(item.get("text", ""))
            if words and known and len(words & known) / float(len(words)) >= 0.55:
                item.update(note)
                replaced = True
                break
        if not replaced:
            items.append(note)
        _save_jsonl(CODE_NOTES, items[-40:])
    return note["text"]


def code_block(query, project="", limit=3):
    """Notes from this folder only. Another project's notes stay out."""
    if not _real_project(project):
        return ""
    lines = []
    for item in _load_jsonl(CODE_NOTES):
        if not _same_project(item.get("project"), project):
            continue
        text = item.get("text") or ""
        if text and text not in lines:
            lines.append(text)
        if len(lines) >= limit:
            break
    if not lines:
        return ""
    return (
        "Notes for this folder only. They do not apply to a different project.\n"
        + "\n".join("- " + line for line in lines)
    )


def remember_edit(problem, project, changes):
    """Save one successful before/after edit for a later local training run."""
    rows = []
    for change in changes or []:
        if not isinstance(change, dict):
            continue
        path = str(change.get("path") or "").strip()
        if not path:
            continue
        rows.append({
            "path": path[:180],
            "before": (change.get("before") or "")[:1500],
            "after": (change.get("after") or "")[:1500],
        })
        if len(rows) >= 6:
            break
    if not rows:
        return None
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "project": (project or "")[:120],
        "problem": " ".join((problem or "").split())[:500],
        "changes": rows,
    }
    with _LOCK:
        items = _load_jsonl(CODE_EDITS)
        items.append(record)
        _save_jsonl(CODE_EDITS, items[-200:])
    return record


def edit_count():
    return len(_load_jsonl(CODE_EDITS))


def _load_jsonl(path):
    if not path.exists():
        return []
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return items


def _save_jsonl(path, items):
    path.parent.mkdir(exist_ok=True)
    temporary = path.with_suffix(".tmp")
    body = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items)
    temporary.write_text(body, encoding="utf-8")
    temporary.replace(path)
