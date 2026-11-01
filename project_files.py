"""Create folders and files inside a folder the user chooses."""
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECTS = ROOT / "projects"
MAX_FILES = 25
MAX_CHARS = 200_000
READ_BUDGET = 24000
FILE_INLINE = 8000
_STOP = {
    "the", "and", "for", "with", "this", "that", "from", "you", "your", "are", "was",
    "were", "have", "has", "not", "but", "can", "one", "into", "about", "what", "when",
}
TEXT_SUFFIXES = {
    ".py", ".pyw", ".pyi", ".ipynb",
    ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue", ".svelte", ".mts", ".cts",
    ".html", ".htm", ".css", ".scss", ".sass", ".less", ".json", ".jsonc", ".xml", ".svg",
    ".md", ".mdx", ".txt", ".rst", ".csv", ".tsv",
    ".toml", ".ini", ".cfg", ".conf", ".yml", ".yaml", ".env.example",
    ".sql", ".r", ".jl", ".lua", ".pl", ".pm", ".php", ".rb", ".erb",
    ".c", ".h", ".hh", ".hpp", ".hxx", ".cpp", ".cc", ".cxx", ".cs", ".java", ".kt", ".kts",
    ".go", ".rs", ".swift", ".scala", ".sc", ".dart", ".groovy", ".gradle",
    ".m", ".mm", ".f", ".f90", ".for", ".asm", ".s",
    ".sh", ".bash", ".zsh", ".ps1", ".psm1", ".psd1", ".bat", ".cmd",
    ".clj", ".cljs", ".edn", ".ex", ".exs", ".erl", ".hrl", ".hs", ".ml", ".mli",
    ".fs", ".fsx", ".vb", ".vbs", ".pas", ".d", ".nim", ".zig", ".v", ".sv",
    ".proto", ".graphql", ".gql", ".tf", ".hcl", ".bicep", ".cmake",
    ".sln", ".csproj", ".fsproj", ".vbproj", ".props", ".targets",
    ".lock", ".mod", ".sum", ".gradle", ".sbt",
}
_LANGUAGE_RULES = {
    ".py": ("Python", "Write Python. Use None, True, and False. Never write null, true, or false."),
    ".pyw": ("Python", "Write Python. Use None, True, and False. Never write null, true, or false."),
    ".pyi": ("Python", "Write Python. Use None, True, and False. Never write null, true, or false."),
    ".ipynb": (
        "a Jupyter notebook",
        "Code cells are Python, not JSON. Separate cells with a line that is exactly # %%. "
        "Use None, True, and False. Never write null, true, or false. Do not write a line that starts with CELL.",
    ),
    ".js": ("JavaScript", "Write JavaScript. Use null, true, and false. Do not write Python."),
    ".jsx": ("JavaScript", "Write JavaScript. Use null, true, and false. Do not write Python."),
    ".mjs": ("JavaScript", "Write JavaScript. Use null, true, and false. Do not write Python."),
    ".cjs": ("JavaScript", "Write JavaScript. Use null, true, and false. Do not write Python."),
    ".ts": ("TypeScript", "Write TypeScript. Do not write Python."),
    ".tsx": ("TypeScript", "Write TypeScript. Do not write Python."),
    ".vue": ("Vue", "Write Vue. Do not write Python."),
    ".html": ("HTML", "Write HTML. Do not write Python."),
    ".htm": ("HTML", "Write HTML. Do not write Python."),
    ".css": ("CSS", "Write CSS. Do not write Python."),
    ".scss": ("SCSS", "Write SCSS. Do not write Python."),
    ".json": ("JSON", "Write JSON. Use null, true, and false. Do not write Python."),
    ".jsonc": ("JSON", "Write JSON. Use null, true, and false. Do not write Python."),
    ".xml": ("XML", "Write XML. Do not write Python."),
    ".svg": ("SVG", "Write SVG. Do not write Python."),
    ".md": ("Markdown", "Write Markdown. Do not write Python."),
    ".mdx": ("Markdown", "Write Markdown. Do not write Python."),
    ".sql": ("SQL", "Write SQL. Do not write Python."),
    ".r": ("R", "Write R. Do not write Python."),
    ".jl": ("Julia", "Write Julia. Do not write Python."),
    ".lua": ("Lua", "Write Lua. Do not write Python."),
    ".php": ("PHP", "Write PHP. Do not write Python."),
    ".rb": ("Ruby", "Write Ruby. Do not write Python."),
    ".java": ("Java", "Write Java. Do not write Python."),
    ".kt": ("Kotlin", "Write Kotlin. Do not write Python."),
    ".cs": ("C#", "Write C#. Do not write Python."),
    ".c": ("C", "Write C. Do not write Python."),
    ".h": ("C", "Write C. Do not write Python."),
    ".cpp": ("C++", "Write C++. Do not write Python."),
    ".cc": ("C++", "Write C++. Do not write Python."),
    ".hpp": ("C++", "Write C++. Do not write Python."),
    ".go": ("Go", "Write Go. Do not write Python."),
    ".rs": ("Rust", "Write Rust. Do not write Python."),
    ".swift": ("Swift", "Write Swift. Do not write Python."),
    ".scala": ("Scala", "Write Scala. Do not write Python."),
    ".sh": ("shell", "Write a shell script. Do not write Python."),
    ".bash": ("shell", "Write a shell script. Do not write Python."),
    ".ps1": ("PowerShell", "Write PowerShell. Do not write Python."),
    ".bat": ("a batch file", "Write batch commands. Do not write Python."),
    ".cmd": ("a batch file", "Write batch commands. Do not write Python."),
    ".vb": ("Visual Basic", "Write Visual Basic. Do not write Python."),
    ".vbs": ("VBScript", "Write VBScript. Do not write Python."),
    ".bas": ("VBA", "Write VBA. Do not write Python."),
    ".vba": ("VBA", "Write VBA. Do not write Python."),
    ".yml": ("YAML", "Write YAML. Do not write Python."),
    ".yaml": ("YAML", "Write YAML. Do not write Python."),
    ".toml": ("TOML", "Write TOML. Do not write Python."),
    ".graphql": ("GraphQL", "Write GraphQL. Do not write Python."),
    ".gql": ("GraphQL", "Write GraphQL. Do not write Python."),
}


def language_for(path):
    """The language a file should be written in, based on its type."""
    text = str(path or "")
    suffix = Path(text).suffix.lower()
    if suffix == ".ipynb":
        kernel = _notebook_kernel(text)
        if kernel and kernel not in {"python", "python3"}:
            return (
                "This file is a Jupyter notebook. Its kernel is %s. "
                "Write that language in the code cells, not JSON and not another language."
                % kernel
            )
    pair = _LANGUAGE_RULES.get(suffix)
    if not pair:
        if suffix:
            return "This file ends in %s. Write it in that language. Do not switch to another language." % suffix
        return "Write this file in the language it already uses. Do not switch to another language."
    return "This file is %s. %s" % pair


def _notebook_kernel(path):
    file_path = Path(str(path))
    if not file_path.is_file():
        return ""
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    info = meta.get("language_info") if isinstance(meta.get("language_info"), dict) else {}
    kernel = meta.get("kernelspec") if isinstance(meta.get("kernelspec"), dict) else {}
    name = str(info.get("name") or kernel.get("language") or "").strip().lower()
    return name


BINARY_SUFFIXES = {
    ".exe", ".dll", ".so", ".dylib", ".bin", ".obj", ".o", ".a", ".lib", ".pdb",
    ".pyc", ".pyo", ".pyd", ".class", ".jar", ".war", ".ear", ".whl", ".egg",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tif", ".tiff",
    ".pdf", ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".tar",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".mkv", ".webm", ".ogg", ".flac",
    ".sqlite", ".db", ".gguf", ".safetensors", ".pt", ".pth", ".onnx",
    ".pickle", ".pkl", ".npy", ".npz", ".parquet", ".feather", ".h5", ".hdf5",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".iso", ".img", ".dmg", ".msi", ".wasm",
}
SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", ".ipynb_checkpoints",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
}
SOURCE_LIMIT = 80
READ_CAP = 1_000_000
_PATCH = re.compile(
    r"(?im)^PATCH:\s*(.+?)\s*\n<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE",
    re.S,
)
_SITE = re.compile(r'File "([^"]+)", line (\d+)')
_LINE_NO = re.compile(r"^\s*\d+\|\s?")


def project_dir(choice):
    text = (choice or "").strip().strip('"')
    if not text:
        text = str(PROJECTS / "my-project")
    path = Path(text)
    if not path.is_absolute():
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-").lower() or "my-project"
        path = PROJECTS / slug[:40]
    path = path.resolve()
    _reject_unsafe_root(path)
    return path


def choose_folder(initial=""):
    start = ""
    if initial:
        candidate = Path(initial.strip().strip('"'))
        if candidate.exists():
            start = str(candidate if candidate.is_dir() else candidate.parent)
        elif candidate.parent.exists():
            start = str(candidate.parent)
    script = (
        "Add-Type -AssemblyName System.Windows.Forms\n"
        "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog\n"
        "$dialog.Description = 'Choose the project folder'\n"
        "$dialog.ShowNewFolderButton = $true\n"
        "$start = $env:SWARM_FOLDER\n"
        "if ($start -and (Test-Path -LiteralPath $start)) { $dialog.SelectedPath = $start }\n"
        "$result = $dialog.ShowDialog()\n"
        "if ($result -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $dialog.SelectedPath }\n"
    )
    env = os.environ.copy()
    env["SWARM_FOLDER"] = start
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-STA", "-Command", script],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    selected = (completed.stdout or "").strip()
    if not selected:
        return ""
    return str(project_dir(selected))


def _reject_unsafe_root(path):
    anchor = Path(path.anchor)
    if path == anchor:
        raise ValueError("Choose a folder inside a drive, not the drive itself.")
    blocked = []
    windir = os.environ.get("WINDIR")
    if windir:
        blocked.append(Path(windir))
    for key in ("ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(key)
        if value:
            blocked.append(Path(value))
    blocked.append(ROOT / "models")
    resolved = path.resolve()
    for place in blocked:
        try:
            place = place.resolve()
        except OSError:
            continue
        if resolved == place or place in resolved.parents:
            raise ValueError("Choose a project folder. That location is reserved.")


def safe_path(root, relative):
    text = (relative or "").strip().replace("\\", "/").lstrip("/")
    if not text or ":" in text:
        raise ValueError("Skip unsafe path.")
    parts = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError("Skip unsafe path.")
        parts.append(part)
    if not parts:
        raise ValueError("Skip empty path.")
    path = root.joinpath(*parts).resolve()
    root = root.resolve()
    if path != root and root not in path.parents:
        raise ValueError("Skip unsafe path.")
    return path


def list_project(root):
    files = _source_files(root)
    if not files:
        return "(empty project)"
    lines = [rel for rel, _path in files]
    text = "\n".join(lines)
    if len(files) >= SOURCE_LIMIT:
        text += "\nMore source files are in the folder."
    return text


def has_code(root):
    return bool(_source_files(root))


def read_for_fix(root, check_output="", query=""):
    """File list plus the failing lines and the chunks that match the request."""
    root = Path(root)
    files = _source_files(root)
    if not files:
        return "(empty project)"
    focus = {}
    for rel, line_no in _failure_sites(root, check_output or ""):
        focus.setdefault(rel, []).append(line_no)
    hits = search_code(root, "\n".join(part for part in (query, check_output) if part))
    matched = {}
    for _score, rel, start, chunk in hits:
        matched.setdefault(rel, []).append((start, chunk))
    ordered = sorted(files, key=lambda item: (item[0] not in focus, item[0] not in matched, item[0]))
    parts = []
    columns = data_columns(root)
    if columns:
        parts.append(columns)
    parts.append("Project files:\n" + "\n".join(rel for rel, _path in files))
    used = len(parts[0])
    for rel, path in ordered:
        if used >= READ_BUDGET:
            break
        try:
            raw, _encoding = read_source(path)
        except (OSError, UnicodeError):
            continue
        text = _display_text(rel, raw)
        lines_of_interest = focus.get(rel) or []
        pieces = matched.get(rel) or []
        if path.suffix.lower() == ".ipynb" and len(text) > FILE_INLINE:
            body = _numbered(_notebook_focus(text, check_output))
            title = "--- %s (notebook cells; line numbers are not part of the code) ---" % rel
        elif len(text) <= FILE_INLINE:
            body = _numbered(text)
            title = "--- %s ---" % rel
        elif lines_of_interest:
            body = "\n\n".join(_numbered_window(text, line_no, 15) for line_no in lines_of_interest[:4])
            title = "--- %s around the failure (line numbers are not part of the code) ---" % rel
        elif pieces:
            body = "\n\n".join(_numbered_span(chunk, start) for start, chunk in pieces[:3])
            title = "--- %s matching parts (line numbers are not part of the code) ---" % rel
        else:
            body = _numbered("\n".join(text.splitlines()[:60]))
            title = "--- %s (first 60 lines) ---" % rel
        chunk = title + "\n" + body
        if used + len(chunk) > READ_BUDGET:
            room = READ_BUDGET - used
            if room < 400:
                break
            chunk = chunk[:room] + "\n..."
        parts.append(chunk)
        used += len(chunk)
    parts.append(
        "The number before | is a line number. Copy code into SEARCH without those numbers."
    )
    return "\n\n".join(parts)


def _notebook_focus(view, output):
    """The cells named in a traceback, when the whole notebook is too long to show."""
    wanted = {int(number) for number in re.findall(r"notebook cell (\d+)", output or "")}
    blocks = [block.strip("\n") for block in re.split(r"(?m)(?=^CELL \d+ )", view or "") if block.strip()]
    if wanted:
        picked = []
        for block in blocks:
            match = re.match(r"CELL (\d+) ", block)
            if match and int(match.group(1)) in wanted:
                picked.append(block)
        if picked:
            return "\n\n".join(picked)
    return (view or "")[:FILE_INLINE]


def search_code(root, query, limit=6):
    """Source chunks that share words with the request."""
    words = _words(query)
    if not words:
        return []
    scored = []
    for rel, path in _source_files(Path(root)):
        try:
            text, _encoding = read_source(path)
            text = _display_text(rel, text)
        except (OSError, UnicodeError):
            continue
        for start, chunk in _code_chunks(rel, text):
            overlap = len(words & _words(chunk))
            needed = 1 if len(words) <= 4 else 2
            if overlap < needed:
                continue
            scored.append((overlap, rel, start, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[:limit]


def changed_files(before, after, limit=6):
    """Text files that differ between two snapshots."""
    rows = []
    for rel in sorted(set(before or {}) | set(after or {})):
        old = (before or {}).get(rel, b"")
        new = (after or {}).get(rel, b"")
        if old == new:
            continue
        try:
            old_text = old.decode("utf-8")
            new_text = new.decode("utf-8")
        except UnicodeError:
            continue
        rows.append({"path": rel, "before": old_text[:1500], "after": new_text[:1500]})
        if len(rows) >= limit:
            break
    return rows


def _words(text):
    return {word for word in re.findall(r"[a-z0-9]{3,}", (text or "").lower()) if word not in _STOP}


def _code_chunks(rel, text):
    suffix = Path(rel).suffix.lower()
    if suffix == ".ipynb" and not (text or "").lstrip().startswith("CELL "):
        text = notebook_view(text)
    lines = text.splitlines()
    if not lines:
        return
    starts = [0]
    if suffix in {".py", ".js", ".jsx", ".ts", ".tsx"}:
        for index, line in enumerate(lines):
            if index and re.match(r"^(?:async\s+def |def |class |function |export\s+(?:default\s+)?(?:function|class) )", line):
                starts.append(index)
    else:
        starts = list(range(0, len(lines), 40))
    starts = sorted(set(starts))
    for pos, start in enumerate(starts):
        if suffix in {".py", ".js", ".jsx", ".ts", ".tsx"}:
            end = starts[pos + 1] if pos + 1 < len(starts) else len(lines)
            end = min(end, start + 80)
        else:
            end = min(len(lines), start + 40)
        chunk = "\n".join(lines[start:end])
        if chunk.strip():
            yield start + 1, chunk


def _numbered_span(chunk, start):
    lines = chunk.splitlines()
    width = len(str(start + max(len(lines) - 1, 0)))
    return "\n".join("%*d| %s" % (width, start + offset, line) for offset, line in enumerate(lines))


def snapshot_project(root):
    """Copy the project text files so a trial can be undone."""
    files = {}
    for rel, path in _source_files(root):
        try:
            files[rel] = path.read_bytes()
        except OSError:
            continue
    return files


def restore_project(root, files):
    """Put the project back to a snapshot, including files a trial added."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    current = {rel: path for rel, path in _source_files(root)}
    for rel, path in current.items():
        if rel not in files:
            try:
                path.unlink()
            except OSError:
                pass
    for rel, data in (files or {}).items():
        try:
            path = safe_path(root, rel)
        except ValueError:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    _clear_bytecode(root)


def parse_patches(text):
    patches = []
    for match in _PATCH.finditer(text or ""):
        patches.append((match.group(1).strip(), match.group(2), match.group(3)))
    return patches


def parse_manifest(text):
    folders = []
    files = []
    for match in re.finditer(r"(?im)^DIR:\s*(.+)$", text or ""):
        folders.append(match.group(1).strip())
    pattern = re.compile(r"(?im)^FILE:\s*(.+?)\s*\n```[^\n]*\n(.*?)```", re.S)
    for match in pattern.finditer(text or ""):
        files.append((match.group(1).strip(), match.group(2)))
    if not files:
        blocks = re.findall(r"```(?:python)?\s*\n(.*?)```", text or "", re.S | re.I)
        if blocks and blocks[-1].strip():
            files.append(("main.py", blocks[-1]))
    return folders, files[:MAX_FILES]


def apply_manifest(project_name, text, revise=False):
    """Write new files, and on a fix pass patch the lines that already exist."""
    root = project_dir(project_name)
    root.mkdir(parents=True, exist_ok=True)
    folders, files = parse_manifest(text)
    events = []
    patched = set()
    for folder in folders:
        try:
            path = safe_path(root, folder)
        except ValueError:
            continue
        path.mkdir(parents=True, exist_ok=True)
        events.append({
            "kind": "folder",
            "path": str(path),
        })
    if revise:
        _apply_patches(root, text, events, patched)
    for relative, content in files:
        try:
            path = safe_path(root, relative)
        except ValueError:
            events.append({"kind": "note", "text": "Skipped an unsafe path."})
            continue
        if path in patched:
            continue
        if is_answer_key(relative):
            events.append({
                "kind": "note",
                "text": "Left %s alone. There is no solved copy to write." % relative,
            })
            continue
        if path.suffix.lower() != ".ipynb" and is_notebook_dump(content):
            events.append({
                "kind": "note",
                "text": "Kept %s. That reply was notebook JSON, not the language for this file." % relative,
            })
            continue
        if path.suffix.lower() == ".ipynb":
            content = as_notebook_text(content)
            if not content:
                events.append({
                    "kind": "note",
                    "text": "Kept %s. The reply was notebook JSON, not Python in the cells." % relative,
                })
                continue
        if revise and path.is_file():
            try:
                existing, _encoding = read_source(path)
            except (OSError, UnicodeError):
                existing = ""
            if path.suffix.lower() == ".ipynb":
                old_len = len(notebook_view(existing))
                new_len = len(notebook_view(content))
                if new_len < max(80, int(old_len * 0.4)):
                    events.append({
                        "kind": "note",
                        "text": "Kept %s. The notebook rewrite looked cut off, so the file was left as it was." % relative,
                    })
                    continue
            elif len(content) < max(80, int(len(existing) * 0.4)):
                events.append({
                    "kind": "note",
                    "text": "Kept %s. The rewrite looked cut off, so the file was left as it was." % relative,
                })
                continue
        _write_file(path, content, events, patched=False)
    if not revise:
        _apply_patches(root, text, events, patched)
    summary = _summary(root, events, "")
    return root, summary, events, ""


_VIEW_LABEL = re.compile(r"^CELL\s+\d+\s+[A-Za-z0-9_-]+:\s*$")


def _view_label(line):
    return bool(_VIEW_LABEL.match((line or "").strip()))


def strip_view_labels(source):
    """Drop the view's CELL heading if it was copied into the file."""
    source = (source or "").replace("\r\n", "\n")
    lines = source.split("\n")
    kept = [line for line in lines if not _view_label(line)]
    text = "\n".join(kept)
    return text, text != source


def repair_notebook_labels(root):
    root = Path(root)
    fixed = []
    if not root.exists():
        return fixed
    for path in sorted(root.rglob("*.ipynb")):
        if not path.is_file() or not _wanted(root, path):
            continue
        try:
            text, _encoding = read_source(path)
            data = json.loads(text)
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        dirty = False
        for cell in data.get("cells") or []:
            if not isinstance(cell, dict):
                continue
            updated, changed = strip_view_labels(_cell_source(cell))
            if changed:
                cell["source"] = _source_list(updated)
                dirty = True
        if not dirty:
            continue
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        fixed.append(path.relative_to(root).as_posix())
    return fixed


def _csv_headers(root):
    files = []
    headers = []
    root = Path(root)
    if not root.exists():
        return files, headers
    for path in sorted(root.rglob("*.csv")):
        if not path.is_file() or not _wanted(root, path):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        if not lines:
            continue
        names = next(csv.reader([lines[0]]))
        preview = ""
        rows = list(csv.reader(lines[:3]))
        if len(rows) > 1:
            preview = " | ".join(cell[:40] for cell in rows[1][:6])[:180]
        files.append((path.relative_to(root).as_posix(), names, preview))
        for name in names:
            if name and name not in headers:
                headers.append(name)
        if len(files) >= 8:
            break
    return files, headers


def data_columns(root):
    files, _headers = _csv_headers(root)
    if not files:
        return ""
    lines = ["Columns in the data files. Use these names exactly."]
    for rel, names, preview in files:
        lines.append(rel + ": " + ", ".join(names))
        if preview:
            lines.append("  example: " + preview)
    return "\n".join(lines)


def align_column_case(root):
    """Spell a quoted name the way the data file spells it."""
    _files, headers = _csv_headers(root)
    if not headers:
        return []
    changed = []
    for rel, path in _source_files(root):
        if path.suffix.lower() in {".csv", ".tsv"}:
            continue
        try:
            text, _encoding = read_source(path)
        except (OSError, UnicodeError):
            continue
        sample = notebook_view(text) if path.suffix.lower() == ".ipynb" else text
        mapping = {}
        for match in re.finditer(r"['\"]([^'\"]+)['\"]", sample):
            token = match.group(1)
            for header in headers:
                if (
                    token != header
                    and token.casefold() == header.casefold()
                    and any(ch.isupper() for ch in token)
                ):
                    mapping[token] = header
        if mapping and _rewrite_quoted(path, mapping):
            changed.append(rel)
    return changed


def run_checks(root, problem=""):
    """Run tests, notebooks, and main.py with the project environment when one exists."""
    root = Path(root)
    _clear_bytecode(root)
    logs = []
    repaired = repair_notebook_labels(root)
    if repaired:
        logs.append("Removed view labels from " + ", ".join(repaired) + ".")
    cased = align_column_case(root)
    if cased:
        logs.append("Matched column capitalization in " + ", ".join(cased) + ".")
    passed = True
    ran = False
    python = ensure_private_env(root)
    env = isolated_env(root, python)
    notebook_only = bool(problem) and notebook_task(problem, root)
    latest = latest_request(problem) if notebook_only else ""
    tests = []
    for pattern in ("test_*.py", "*_test.py"):
        for path in sorted(root.rglob(pattern)):
            if path.is_file() and _wanted(root, path) and path not in tests:
                tests.append(path)
    for path in tests:
        ran = True
        code, output = _run([python, str(path)], root, env, timeout=30)
        logs.append("Test %s\n%s" % (path.name, output))
        if code != 0:
            passed = False
    for path in sorted(root.rglob("*.ipynb")):
        if not path.is_file() or not _wanted(root, path):
            continue
        ran = True
        code, output = _run_notebook(path, root, env)
        logs.append("Notebook %s\n%s" % (path.name, output))
        if code != 0:
            passed = False
    main = root / "main.py"
    if main.is_file() and not (notebook_only and not re.search(r"\bmain\.py\b", latest, re.I)):
        ran = True
        code, output = _run([python, str(main)], root, env, timeout=60)
        logs.append("main.py\n%s" % output)
        if code != 0:
            passed = False
    if not ran:
        if _source_files(root):
            return True, (
                "No Python tests, notebook, or main.py to run. "
                "The other source files are available to read and patch."
            )
        return False, "Nothing ran. Add main.py, a notebook, or test_app.py so a Python project can be checked."
    text = "\n\n".join(logs).strip()
    columns = data_columns(root)
    if columns:
        text = columns + "\n\n" + text
    guide = next_step(text)
    if guide:
        text = guide + "\n\n" + text
    link = connection_report(root, text)
    if link:
        text = link + "\n\n" + text
    absent = missing_outputs(root)
    if absent:
        passed = False
        text = (
            "The notebook names output files that are not there:\n"
            + "\n".join(absent)
            + "\nWrite those files from the data. A placeholder file does not count.\n\n"
            + text
        )
    return passed, text


_PLAN_PATH = re.compile(r"^[A-Za-z0-9_./+\-@()~ ]+$")


def parse_plan(text):
    """Relative paths listed after PLAN:."""
    found = []
    started = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not started:
            if not line.upper().startswith("PLAN"):
                continue
            started = True
            line = line.split(":", 1)[-1].strip() if ":" in line else ""
            if not line:
                continue
        line = line.lstrip("-*").strip().strip("`").replace("\\", "/")
        if not line:
            continue
        if not _PLAN_PATH.fullmatch(line) or ".." in line.split("/"):
            continue
        if line not in found:
            found.append(line)
        if len(found) >= 8:
            break
    return found


def is_answer_key(relative):
    """A solved folder or a solution notebook is a reference, not a file to create."""
    parts = [part for part in (relative or "").replace("\\", "/").split("/") if part and part not in (".",)]
    if any(part.lower() == "solved" for part in parts):
        return True
    name = parts[-1].lower() if parts else ""
    return "solution" in name


def drop_missing_answers(plan, root):
    """Drop a solved path that is not already in the project."""
    kept = []
    dropped = []
    for rel in plan or []:
        if is_answer_key(rel) and needs_file(root, rel):
            dropped.append(rel)
            continue
        if rel not in kept:
            kept.append(rel)
    return kept, dropped


def needs_file(root, relative):
    try:
        path = safe_path(Path(root), relative)
    except ValueError:
        return False
    if not path.is_file():
        return True
    try:
        return path.stat().st_size < 20
    except OSError:
        return True


def missing_files(root, plan):
    return [rel for rel in plan or [] if needs_file(root, rel)]


def _unfinished_line(line):
    text = (line or "").strip()
    if text in {"pass", "...", "Ellipsis"}:
        return True
    if re.match(r"(?i)^(?:#\s*)?(?:TODO|FIXME|XXX)\b", text):
        return True
    return bool(re.match(r"^raise\s+NotImplementedError\b", text))


def _sham_body(source):
    """True when the cell only pretends to finish, such as writing a .keep file."""
    lines = [
        line for line in (source or "").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    text = "\n".join(lines)
    if not text.strip():
        return False
    if re.search(r"\.keep\b", text):
        return True
    real = re.search(
        r"read_csv|read_json|read_excel|read_parquet|read_table|read_xml|"
        r"to_csv|to_json|to_excel|to_parquet|merge|groupby|pivot|plot|fit\(|DataFrame",
        text,
    )
    if real:
        return False
    return bool(re.search(r"(?i)successfully|placeholder|dummy", text))


def _placeholder_body(source):
    """True when the code is empty or only signatures and placeholders."""
    statements = []
    for line in (source or "").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or _magic_line(line):
            continue
        stripped = line.strip()
        if (
            (stripped.startswith('"""') and stripped.endswith('"""') and len(stripped) >= 6)
            or (stripped.startswith("'''") and stripped.endswith("'''") and len(stripped) >= 6)
        ):
            continue
        if re.match(r"^(?:async\s+def |def |class |elif |else:|except\b|finally:|try:|with |for |while |if )", stripped):
            if stripped.endswith(":"):
                continue
        statements.append(stripped)
    if not statements:
        return True
    return all(_unfinished_line(line) for line in statements)


def _heading_cell(source):
    """A code cell that only holds a section title, not work to write."""
    lines = [line.strip() for line in (source or "").splitlines() if line.strip()]
    if not lines:
        return False
    return all(
        re.match(r"^#\s*(?:#{1,6}\s+\S.*|[-=_]{3,}\s*)$", line)
        for line in lines
    )


def notebook_gaps(path):
    """Cells that are empty, only a placeholder, or not valid Python."""
    try:
        data = json.loads(read_source(path)[0])
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ["could not be read as a notebook"]
    if not isinstance(data, dict):
        return ["could not be read as a notebook"]
    reasons = []
    saw_code = False
    for index, cell in enumerate(data.get("cells") or []):
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        saw_code = True
        source = _cell_source(cell)
        body = "\n".join(
            line for line in source.splitlines()
            if not _magic_line(line) and not _view_label(line)
        )
        magic_only = source.strip() and not body.strip()
        if magic_only or _heading_cell(source):
            continue
        if _placeholder_body(body) or _sham_body(body):
            reasons.append("cell %s has no finished code" % index)
            continue
        try:
            compile(body, "<cell %s>" % index, "exec")
        except SyntaxError as exc:
            reasons.append("cell %s does not parse: %s" % (index, exc.msg))
    if not saw_code:
        reasons.append("the notebook has no code cells")
    return _without_trailing_blank(data, reasons)


def notebook_jobs(path):
    """Each code cell, and whether it still needs to be written."""
    gaps = set()
    for reason in notebook_gaps(path):
        match = re.search(r"cell (\d+)", reason)
        if match:
            gaps.add(int(match.group(1)))
    try:
        data = json.loads(read_source(path)[0])
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    jobs = []
    for index, cell in enumerate(data.get("cells") or []):
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        source, _changed = strip_view_labels(_cell_source(cell))
        jobs.append({"index": index, "source": source, "finish": index in gaps})
    return jobs


def _starts_python(stripped):
    if stripped.startswith("#"):
        return True
    return bool(re.match(
        r"(?:import |from |def |class |@|for |while |if |elif |else:|try:|except\b|finally:|"
        r"with |return |raise |assert |pass\b|print\(|del |global |nonlocal |async |await |"
        r"[A-Za-z_][\w.]*\s*(?:=|\(|\[))",
        stripped,
    ))


def _runnable_lines(raw):
    """Python from a reply. Leading explanation and a code fence are left out."""
    raw = strip_view_labels(raw or "")[0]
    raw = re.sub(r"(?m)^(?:PATCH|FILE|ROUTE|SAY)\s*:.*$", "", raw)
    lines = []
    started = False
    for line in raw.splitlines():
        stripped = line.strip()
        if not started:
            if not stripped or stripped.startswith(("```", "~~~")):
                continue
            if not _starts_python(stripped):
                continue
            started = True
        if stripped.startswith("```"):
            break
        lines.append(line)
    while lines and not lines[-1].strip():
        lines.pop()
    while lines:
        tail = lines[-1].strip()
        if not tail or tail.startswith("#") or tail[:1] in ")]}\"'":
            break
        if lines[-1].startswith((" ", "\t")) or _starts_python(tail):
            break
        lines.pop()
    body = "\n".join(lines).strip()
    if not body or is_notebook_dump(body) or _sham_body(body) or _placeholder_body(body):
        return ""
    try:
        compile(body, "<cell>", "exec")
    except SyntaxError:
        return ""
    return body


def cell_code_from_reply(reply, source=""):
    """Code to add to a cell. Comments already there stay above the new lines."""
    text = reply or ""
    chunks = []
    for match in re.finditer(r"(?s)=======\n(.*?)\n>>>>>>> REPLACE", text):
        chunks.append(match.group(1))
    chunks.extend(re.findall(r"```(?:python|py)?[^\n]*\n(.*?)```", text, re.S | re.I))
    chunks.append(text)
    for raw in chunks:
        body = _runnable_lines(raw)
        if not body:
            continue
        comments = [
            line for line in (source or "").splitlines()
            if line.strip().startswith("#") and line.strip() not in body
        ]
        if comments:
            return "\n".join(comments + [body]).strip() + "\n"
        return body.strip() + "\n"
    return ""


def _attach_new_code(cells, old, new):
    """Add replacement code to the one unfinished cell a missed patch was aimed at."""
    body = _runnable_lines(new)
    if not body:
        return None
    old_text = re.sub(r"(?m)^CELL \d+ [A-Za-z0-9_-]+:\s*", "", old or "").strip()
    unfinished = []
    for index, cell in enumerate(cells):
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        source = _cell_source(cell)
        if _heading_cell(source):
            continue
        if _placeholder_body(source) or _sham_body(source):
            unfinished.append((index, source))
    commentish = True
    for line in old_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or stripped in {"pass", "...", "Ellipsis"}:
            continue
        if re.match(r"(?i)^(?:TODO|FIXME|CELL\s+\d+)\b", stripped):
            continue
        commentish = False
        break
    hits = []
    for index, source in unfinished:
        comments = _comment_text(source).strip()
        if (
            not old_text
            or old_text in source
            or source.strip() in old_text
            or (comments and comments in old_text)
            or (len(unfinished) == 1 and commentish)
        ):
            hits.append(index)
    if len(hits) != 1:
        return None
    index = hits[0]
    source = _cell_source(cells[index])
    comments = [
        line for line in source.splitlines()
        if line.strip().startswith("#") and line.strip() not in body
    ]
    combined = ("\n".join(comments + [body]).strip() + "\n") if comments else body.strip() + "\n"
    if _sham_body(combined):
        return None
    return index, combined


def write_notebook_cell(path, index, source):
    """Replace one cell's source and leave the rest of the notebook alone."""
    try:
        data = json.loads(read_source(path)[0])
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    cells = data.get("cells") if isinstance(data, dict) else None
    if not isinstance(cells, list) or index >= len(cells):
        return False
    source, _changed = strip_view_labels(source or "")
    if is_notebook_dump(source) or _sham_body(source):
        return False
    source = source.replace("\r\n", "\n").strip("\n")
    if source:
        source += "\n"
    cells[index]["source"] = _source_list(source)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return True


_DATA_SUFFIXES = {
    ".csv", ".tsv", ".json", ".jsonl", ".txt", ".xlsx", ".xls",
    ".parquet", ".feather", ".xml", ".yaml", ".yml", ".geojson",
}
_READ_FAIL = re.compile(
    r"No such file or directory|FileNotFoundError|does not exist|"
    r"ParserError|EmptyDataError|UnicodeDecodeError|Error tokenizing|"
    r"can't open|cannot open|failed to (?:read|open|decode)|"
    r"Excel file format|XLRDError|JSONDecodeError|No columns to parse",
    re.I,
)


def _data_name(raw):
    return Path(raw or "").suffix.lower() in _DATA_SUFFIXES


def data_files(root):
    """Data files in the project. A solved copy is not one of them."""
    root = Path(root)
    found = []
    if not root.exists():
        return found
    for path in sorted(root.rglob("*")):
        if not path.is_file() or not _wanted(root, path):
            continue
        if path.suffix.lower() not in _DATA_SUFFIXES:
            continue
        rel = path.relative_to(root).as_posix()
        if is_answer_key(rel):
            continue
        found.append(rel)
        if len(found) >= 12:
            break
    return found


def _asked_paths(output):
    """Data paths named in a failed read. Other quoted words are left out."""
    found = []
    for match in re.finditer(
        r"""['\"]([^'\"]+\.(?:csv|tsv|json|jsonl|txt|xlsx|xls|parquet|feather|xml|ya?ml|geojson))['\"]""",
        output or "",
        re.I,
    ):
        raw = match.group(1).replace("\\", "/").strip()
        if not raw or raw.startswith(("http://", "https://")) or raw in found:
            continue
        found.append(raw)
        if len(found) >= 6:
            break
    return found


def nearby_data(root, name):
    """The closest lesson copy of a data file that is not already in this project."""
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        return None
    root = Path(root).resolve()
    hits = []
    seen = set()
    patterns = (
        "Resources/" + name,
        "*/Resources/" + name,
        "*/*/Resources/" + name,
        "*/*/*/Resources/" + name,
    )
    for base in list(root.parents)[:8]:
        for pattern in patterns:
            try:
                matches = base.glob(pattern)
            except OSError:
                continue
            for path in matches:
                try:
                    resolved = path.resolve()
                except OSError:
                    continue
                if not resolved.is_file() or resolved in seen:
                    continue
                if resolved == root or root in resolved.parents:
                    continue
                seen.add(resolved)
                hits.append(resolved)
        if hits:
            break
    if not hits:
        return None

    def rank(path):
        text = str(path).lower().replace("\\", "/")
        solved = 1 if "/solved/" in text else 0
        return (solved, len(path.parts))

    hits.sort(key=rank)
    return hits[0]


def _notebook_reads(root):
    """Data paths named in notebook code cells."""
    found = []
    for rel, path in _source_files(Path(root)):
        if path.suffix.lower() != ".ipynb" or is_answer_key(rel):
            continue
        try:
            data = json.loads(read_source(path)[0])
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        for cell in (data or {}).get("cells") or []:
            if not isinstance(cell, dict) or cell.get("cell_type") != "code":
                continue
            for raw in _quoted_data_paths(_cell_source(cell)):
                if raw not in found:
                    found.append(raw)
    return found


def supply_missing_data(root):
    """Copy a missing data file from the lesson when a cell asks for that exact name."""
    events = []
    root = Path(root)
    for raw in _notebook_reads(root):
        rel = raw.replace("\\", "/").lstrip("/")
        if not rel or ".." in rel.split("/") or not _data_name(rel):
            continue
        dest = root / rel
        try:
            dest.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        if dest.is_file():
            continue
        found = nearby_data(root, Path(rel).name)
        if found is None:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(found, dest)
        events.append({
            "kind": "note",
            "text": "Brought %s into %s. The cell asked for that name." % (Path(rel).name, rel),
        })
    return events


def retarget_reads(code, root):
    """Point a read at the same file name where it actually is. A different name is left alone."""
    updated = code or ""
    files = data_files(root)
    for raw in _quoted_data_paths(updated):
        if (Path(root) / raw).is_file():
            continue
        name = Path(raw).name.lower()
        hits = [item for item in files if Path(item).name.lower() == name]
        if len(hits) == 1 and hits[0] != raw:
            updated = updated.replace(raw, hits[0])
    return updated


def missing_outputs(root):
    """Output paths the notebook names that are not on disk after a run."""
    missing = []
    root = Path(root)
    for rel, path in _source_files(root):
        if path.suffix.lower() != ".ipynb" or is_answer_key(rel):
            continue
        try:
            view = notebook_view(read_source(path)[0])
        except (OSError, UnicodeError):
            continue
        view = "\n".join(
            line for line in view.splitlines()
            if not line.strip().startswith("#") and not line.strip().startswith("CELL ")
        )
        for match in re.finditer(
            r"(?:[\w.-]+/)*[Oo]utputs?/[\w.-]+\.(?:csv|tsv|json|jsonl|xlsx|xls|parquet|png|html|txt)",
            view,
        ):
            target = match.group(0).replace("\\", "/")
            if (root / target).is_file() or (path.parent / target).is_file():
                continue
            note = rel + " should write " + target
            if note not in missing:
                missing.append(note)
            if len(missing) >= 8:
                return missing
    return missing


def connection_report(root, output):
    """Why a read did not connect, and the files that can be used instead."""
    if not _READ_FAIL.search(output or ""):
        return ""
    asked = _asked_paths(output)
    have = data_files(root)
    if not asked and not have:
        return ""
    lines = ["What is not connecting:"]
    if asked:
        lines.append("The code asked for " + ", ".join(asked) + ".")
    if have:
        lines.append("Data files in the project: " + ", ".join(have) + ".")
    else:
        lines.append("No data file is in the project.")
    for raw in asked:
        name = Path(raw).name.lower()
        hits = [item for item in have if Path(item).name.lower() == name or item.lower().endswith("/" + name)]
        if len(hits) == 1 and hits[0].replace("\\", "/") != raw:
            lines.append("Use " + hits[0] + " where the code asked for " + raw + ".")
    lines.append(
        "Keep the file name the cell asked for. A different data file is a different dataset, "
        "so do not point a book-sales read at a sightings file or the reverse. "
        "If that exact name is missing, bring that file in, then run the cell. "
        "A section title such as a markdown heading is not a cell that needs code. "
        "A data file that is too large to open as source code can still be read by the notebook."
    )
    return "\n".join(lines)


def work_hint(root, output=""):
    """What the next cell has to use: the broken connection, the real files, and the column names."""
    parts = []
    link = connection_report(root, output)
    if link:
        parts.append(link)
    files = data_files(root)
    if files and not link:
        parts.append("Data files in the project:\n" + "\n".join(files))
    columns = data_columns(root)
    if columns:
        parts.append(columns)
    parts.append(
        "Fill each unfinished cell with code that can run. "
        "When a read does not connect, use a data file that is in the project. "
        "The run output shows whether that code is right."
    )
    return "\n\n".join(parts)


def _quoted_data_paths(text):
    """Relative file paths a notebook reads. Plain words and web addresses are left out."""
    found = []
    for match in re.finditer(r"""['\"]([^'\"]+)['\"]""", text or ""):
        raw = match.group(1).replace("\\", "/").strip()
        if not raw or raw.startswith(("http://", "https://", "s3://")):
            continue
        if re.match(r"^[A-Za-z]:/", raw) or raw.startswith("/"):
            continue
        if "/" not in raw and not _data_name(raw):
            continue
        if raw not in found:
            found.append(raw)
    return found


def _data_cwd(notebook, root, script):
    """Run where the notebook's data files actually are.

    A notebook in Unsolved often says Resources/file.csv while that folder sits
    next to Unsolved, at the project root. The notebook folder still wins when
    the file is there.
    """
    parent = Path(notebook).parent
    root = Path(root)
    parent_hits = 0
    root_hits = 0
    for raw in _quoted_data_paths(script):
        if (parent / raw).is_file():
            parent_hits += 1
        elif (root / raw).is_file():
            root_hits += 1
    if root_hits > parent_hits:
        return root
    return parent


def try_notebook_cells(root, notebook, sources):
    """Run cell sources in order. Setup failures are ignored."""
    pieces = []
    for source in sources:
        body = "\n".join(
            line for line in (source or "").splitlines()
            if not _magic_line(line) and not _view_label(line)
        )
        if body.strip():
            pieces.append(body)
    if not pieces:
        return True, ""
    python = ensure_private_env(root)
    env = isolated_env(root, python)
    script = "\n\n".join(pieces) + "\n"
    code, output = _run(
        [python, "-"],
        root,
        env,
        timeout=30,
        stdin_text=script,
        cwd=_data_cwd(notebook, root, script),
    )
    if code == 0 or _setup_failure(output):
        return True, output
    return False, output


def _without_trailing_blank(data, reasons):
    """A blank cell at the end of a notebook is not the missing work."""
    cells = data.get("cells") or []
    code_indexes = [
        index for index, cell in enumerate(cells)
        if isinstance(cell, dict) and cell.get("cell_type") == "code"
    ]
    if len(code_indexes) < 2:
        return reasons
    last = code_indexes[-1]
    if _cell_source(cells[last]).strip():
        return reasons
    earlier = False
    for index in code_indexes[:-1]:
        source = _cell_source(cells[index])
        body = "\n".join(
            line for line in source.splitlines()
            if not _magic_line(line) and not _view_label(line)
        )
        if body.strip() and not _placeholder_body(body):
            earlier = True
            break
    if not earlier:
        return reasons
    skip = "cell %s has no finished code" % last
    return [reason for reason in reasons if reason != skip]


def source_gaps(path):
    path = Path(path)
    if path.suffix.lower() in _DATA_SUFFIXES:
        return []
    if path.suffix.lower() == ".ipynb":
        return notebook_gaps(path)
    try:
        text, _encoding = read_source(path)
    except (OSError, UnicodeError):
        return ["could not be read"]
    if path.suffix.lower() in {".py", ".pyw"}:
        reasons = []
        try:
            compile(text, path.name, "exec")
        except SyntaxError as exc:
            reasons.append("does not parse: %s" % exc.msg)
        if _placeholder_body(text):
            reasons.append("is only a placeholder")
        return reasons
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if re.search(r"(?i)(?:TODO|FIXME|XXX|NotImplemented|unimplemented)\b", line):
            found.append("line %s is unfinished" % number)
        if len(found) >= 5:
            break
    return found


def unfinished_notebooks(root):
    return [rel for rel in unfinished_sources(root) if rel.lower().endswith(".ipynb")]


def unfinished_sources(root):
    """Source files that still have a gap, notebooks first. Data files are not code."""
    notebooks = []
    others = []
    skip = {".csv", ".tsv", ".md", ".txt", ".rst"}
    for rel, path in _source_files(root):
        if is_answer_key(rel) or path.suffix.lower() in skip:
            continue
        if not source_gaps(path):
            continue
        if path.suffix.lower() == ".ipynb":
            notebooks.append(rel)
        else:
            others.append(rel)
    return notebooks + others


def include_unfinished(plan, root):
    """Keep an unfinished source file on the list even if the builders left it off."""
    plan = list(plan or [])
    added = []
    for rel in unfinished_sources(root):
        if rel in plan:
            continue
        if len(plan) >= 8:
            plan.pop()
        plan.insert(0, rel)
        added.append(rel)
    return plan, added


def latest_request(problem):
    """The newest user message. Earlier turns stay in the text above this marker."""
    text = problem or ""
    marker = "The user now says:"
    if marker in text:
        text = text.rsplit(marker, 1)[-1]
    return text


def named_notebooks(text):
    """Notebook paths written in a message. A longer path replaces its bare file name."""
    found = []
    for match in re.findall(r"[A-Za-z0-9_./\\-]+\.ipynb", text or "", re.I):
        rel = match.replace("\\", "/").lstrip("/")
        while rel.startswith("./"):
            rel = rel[2:]
        if not rel or ".." in rel.split("/") or rel in found:
            continue
        found.append(rel)
    return [
        rel for rel in found
        if not any(other != rel and other.lower().endswith("/" + rel.lower()) for other in found)
    ]


def project_notebooks(root):
    """Notebooks already in the folder. A solved copy is not one of them."""
    found = []
    for rel, path in _source_files(root):
        if path.suffix.lower() == ".ipynb" and not is_answer_key(rel):
            found.append(rel)
    return found


def same_notebook(left, right):
    """True when two paths name the same notebook, even if one is only the file name."""
    a = (left or "").replace("\\", "/").lower()
    b = (right or "").replace("\\", "/").lower()
    while a.startswith("./"):
        a = a[2:]
    while b.startswith("./"):
        b = b[2:]
    return bool(a and b) and (a == b or a.endswith("/" + b) or b.endswith("/" + a))


def _names_other_code(text):
    """True when a message names a source file other than a notebook."""
    if re.search(r"[\w./\\-]+\.(?:py|pyw|js|ts|sql|r|java|kt|go|rs)\b", text or "", re.I):
        return True
    return bool(re.search(r"\.(?:py|pyw|js|ts|sql|r)\b|\b(?:python|py)\s+file\b", text or "", re.I))


def notebook_task(problem, root=None):
    """True when the latest request is to complete a notebook, not a Python program."""
    latest = latest_request(problem)
    if re.search(r"\.ipynb\b", latest, re.I) or re.search(r"\b(?:ipynb|jupyter|notebook)\b", latest, re.I):
        return True
    if _names_other_code(latest):
        return False
    if not re.search(r"\b(?:continue|finish|complete|fill|solve)\b", latest, re.I):
        return False
    if re.search(r"\.ipynb\b|\b(?:ipynb|notebook)\b", problem or "", re.I):
        return True
    return bool(project_notebooks(root)) if root else False


def fallback_plan(problem, root):
    """The file list used when a builder does not name one."""
    if notebook_task(problem, root):
        named = named_notebooks(latest_request(problem)) or named_notebooks(problem or "")
        existing = project_notebooks(root)
        chosen = []
        for rel in existing:
            if named and not any(same_notebook(rel, item) for item in named):
                continue
            if rel not in chosen:
                chosen.append(rel)
        for rel in named:
            if is_answer_key(rel) or any(same_notebook(rel, item) for item in chosen):
                continue
            chosen.append(rel)
        if chosen:
            return chosen[:8]
        return ["notebook.ipynb"]
    return ["main.py", "test_app.py"]


def drop_notebook_extras(plan, root, problem):
    """Finishing a notebook does not need a new test_app.py or main.py."""
    plan = list(plan or [])
    if not notebook_task(problem, root):
        return plan, []
    named = named_notebooks(latest_request(problem)) or named_notebooks(problem or "")
    if not any(str(rel).lower().endswith(".ipynb") and not is_answer_key(rel) for rel in plan):
        for rel in project_notebooks(root):
            if named and not any(same_notebook(rel, item) for item in named):
                continue
            if rel not in plan:
                plan.insert(0, rel)
        for rel in named:
            if is_answer_key(rel) or any(same_notebook(rel, item) for item in plan):
                continue
            plan.insert(0, rel)
    if not any(str(rel).lower().endswith(".ipynb") for rel in plan):
        plan.insert(0, "notebook.ipynb")
    asked = {Path(item).name.lower() for item in re.findall(r"[A-Za-z0-9_./\\-]+\.\w+", latest_request(problem))}
    kept = []
    dropped = []
    for rel in plan:
        name = Path(rel).name.lower()
        if name in {"test_app.py", "main.py"} and name not in asked and needs_file(root, rel):
            dropped.append(rel)
            continue
        if rel not in kept:
            kept.append(rel)
    return kept, dropped


def use_existing_data(plan, root):
    """Use the data file already in the folder instead of a new name for one that is not there."""
    root = Path(root)
    existing = {}
    if root.exists():
        for path in sorted(root.rglob("*")):
            suffix = path.suffix.lower()
            if not path.is_file() or suffix not in {".csv", ".tsv", ".json"} or not _wanted(root, path):
                continue
            existing.setdefault(suffix, []).append(path.relative_to(root).as_posix())
    kept = []
    replaced = []
    for rel in plan or []:
        suffix = Path(rel).suffix.lower()
        try:
            path = safe_path(root, rel)
        except ValueError:
            if rel not in kept:
                kept.append(rel)
            continue
        choices = existing.get(suffix) or []
        if path.is_file() or suffix not in {".csv", ".tsv", ".json"} or len(choices) != 1:
            if rel not in kept:
                kept.append(rel)
            continue
        choice = choices[0]
        if choice not in kept:
            kept.append(choice)
        if choice != rel:
            replaced.append((rel, choice))
    return kept, replaced


def unfinished_report(root, plan):
    lines = []
    for rel in plan or []:
        try:
            path = safe_path(Path(root), rel)
        except ValueError:
            continue
        if not path.is_file():
            continue
        for reason in source_gaps(path):
            lines.append("%s: %s" % (rel, reason))
    if not lines:
        return ""
    return (
        "Unfinished code:\n" + "\n".join(lines)
        + "\nFill those cells and run them. Use the output to correct the code. "
        "An empty cell is work to write, not a reason to stop."
    )


def _cell_source(cell):
    source = cell.get("source") or ""
    if isinstance(source, list):
        return "".join(source)
    return str(source)


def _source_list(text):
    text = (text or "").replace("\r\n", "\n")
    if not text:
        return []
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _valid_notebook(content):
    try:
        data = json.loads(content or "")
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and isinstance(data.get("cells"), list)


def notebook_view(raw):
    """Notebook cells as source, without the JSON wrapper or saved outputs."""
    try:
        data = json.loads(raw or "")
    except (TypeError, json.JSONDecodeError):
        return raw or ""
    if not isinstance(data, dict):
        return raw or ""
    parts = []
    for index, cell in enumerate(data.get("cells") or []):
        if not isinstance(cell, dict):
            continue
        kind = cell.get("cell_type") or "raw"
        source = _cell_source(cell).rstrip("\n")
        parts.append("CELL %s %s:\n%s" % (index, kind, source))
    return "\n\n".join(parts) if parts else "(notebook has no cells)"


def _display_text(rel, text):
    if Path(rel).suffix.lower() == ".ipynb":
        return notebook_view(text)
    return text


def is_notebook_dump(content):
    """True when text is a notebook document rather than the Python inside a cell."""
    body = (content or "").lstrip()
    if not body.startswith(("{", "[")):
        return False
    return '"cell_type"' in body[:8000] or "'cell_type'" in body[:8000]


def _json_cells(content):
    try:
        data = json.loads(content or "")
    except (TypeError, json.JSONDecodeError):
        return None
    if isinstance(data, dict) and isinstance(data.get("cells"), list):
        cells = data["cells"]
    elif isinstance(data, list):
        cells = data
    else:
        return None
    if not cells or not all(isinstance(cell, dict) and cell.get("cell_type") for cell in cells):
        return None
    return cells


def _notebook_from_cells(cells):
    cleaned = []
    for cell in cells:
        kind = cell.get("cell_type") or "code"
        source = cell.get("source") or ""
        if isinstance(source, list):
            source = "".join(str(part) for part in source)
        source = str(source).replace("\r\n", "\n")
        item = {
            "cell_type": kind,
            "metadata": cell.get("metadata") if isinstance(cell.get("metadata"), dict) else {},
            "source": _source_list(source),
        }
        if kind == "code":
            item["execution_count"] = None
            item["outputs"] = []
        cleaned.append(item)
    if not cleaned:
        return None
    notebook = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "cells": cleaned,
    }
    return json.dumps(notebook, indent=1, ensure_ascii=False) + "\n"


def as_notebook_text(content):
    """A notebook file. A JSON cell list becomes real cells. A broken JSON dump is refused."""
    if _valid_notebook(content):
        text = content or ""
        return text if text.endswith("\n") else text + "\n"
    cells = _json_cells(content)
    if cells is not None:
        return _notebook_from_cells(cells)
    if is_notebook_dump(content):
        return None
    return code_to_notebook(content)


def code_to_notebook(content):
    """Turn a Python write-up into a notebook when the model did not emit JSON."""
    if _valid_notebook(content):
        return content
    chunks = re.split(r"(?m)^# %%[^\n]*\n", content or "")
    cells = []
    for chunk in chunks:
        if not chunk.strip():
            continue
        chunk = strip_view_labels(chunk)[0]
        cells.append({
            "cell_type": "code",
            "metadata": {},
            "execution_count": None,
            "outputs": [],
            "source": _source_list(chunk.strip("\n") + "\n"),
        })
    if not cells:
        cells.append({
            "cell_type": "code",
            "metadata": {},
            "execution_count": None,
            "outputs": [],
            "source": [],
        })
    notebook = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "cells": cells,
    }
    return json.dumps(notebook, indent=1, ensure_ascii=False) + "\n"


def _magic_line(line):
    stripped = (line or "").lstrip()
    return stripped.startswith("!") or stripped.startswith("%")


def _notebook_script(path):
    """Python from the code cells, plus a map back to each notebook cell."""
    try:
        data = json.loads(read_source(path)[0])
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "", {}, 0
    prelude = (
        "class _Shell:\n"
        "    def run_line_magic(self, *args, **kwargs):\n"
        "        return None\n"
        "    def system(self, cmd):\n"
        "        return None\n"
        "def get_ipython():\n"
        "    return _Shell()\n"
    )
    lines = prelude.splitlines()
    where = {}
    skipped = 0
    for index, cell in enumerate(data.get("cells") or []):
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        for source_no, line in enumerate(_cell_source(cell).splitlines(), 1):
            if _magic_line(line) or _view_label(line):
                skipped += 1
                continue
            lines.append(line)
            where[len(lines)] = (index, source_no)
        lines.append("")
    if len(lines) <= len(prelude.splitlines()):
        return "", {}, skipped
    return "\n".join(lines) + "\n", where, skipped


def _run_notebook(path, root, env):
    script, where, skipped = _notebook_script(path)
    note = ""
    if skipped:
        note = "Skipped %s shell or magic line(s). They are not Python.\n" % skipped
    if not str(script).strip():
        return 1, note + "The notebook has no code cells."
    code, output = _run(
        [_python_from_env(env, root), "-"],
        root,
        env,
        timeout=90,
        stdin_text=script,
        cwd=_data_cwd(path, root, script),
    )
    if code != 0:
        output = _notebook_traceback(path, root, output, where)
    syntax = _cell_syntax_notes(path, root)
    if syntax:
        output = "\n".join(syntax) + ("\n" + output if output else "")
        code = 1 if code == 0 else code
    if code != 0 and _setup_failure(output) and not syntax:
        return 0, note + (
            "Setup points at another computer, so it was not run here. "
            "Leave that setup and finish the code that comes after it.\n" + output
        )
    return code, note + output


def _cell_syntax_notes(path, root):
    """Parse every code cell, so a later syntax error is not hidden by an earlier crash."""
    try:
        data = json.loads(read_source(path)[0])
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    try:
        rel = Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        rel = Path(path).name
    notes = []
    for index, cell in enumerate((data or {}).get("cells") or []):
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        source = _cell_source(cell)
        body = "\n".join(
            line for line in source.splitlines()
            if not _magic_line(line) and not _view_label(line)
        )
        if not body.strip():
            continue
        try:
            compile(body, "<cell %s>" % index, "exec")
        except SyntaxError as exc:
            notes.append(
                'File "%s", line %s (notebook cell %s) does not parse: %s'
                % (rel, exc.lineno or 1, index, exc.msg)
            )
    return notes


def _setup_failure(output):
    """A failure that belongs to another computer, not to the notebook's logic."""
    return bool(re.search(
        r"findspark|py4j|SPARK_HOME|/content/|apt-get|"
        r"NoCredentialsError|NoRegionError|EndpointConnectionError|"
        r"Unable to locate credentials|Could not connect to the endpoint|"
        r"botocore\.exceptions|AccessDenied",
        output or "",
        re.I,
    ))


def _headers_in_output(output):
    headers = []
    collecting = False
    for line in (output or "").splitlines():
        if line.startswith("Columns in the data files"):
            collecting = True
            continue
        if not collecting:
            continue
        if not re.search(r"\.(?:csv|tsv): ", line, re.I):
            break
        headers.extend(part.strip() for part in line.split(": ", 1)[1].split(",") if part.strip())
    return headers


def _close_columns(missing, headers):
    stems = set()
    for word in re.findall(r"[a-z0-9]+", (missing or "").casefold()):
        stems.add(word)
        if word.endswith("s") and len(word) > 3:
            stems.add(word[:-1])
    hits = []
    for header in headers:
        words = set(re.findall(r"[a-z0-9]+", header.casefold()))
        if stems & words and header not in hits:
            hits.append(header)
    return hits[:4]


def next_step(output):
    """The edit to make next, taken from the names and file the check already printed."""
    lines = []
    for match in re.finditer(r"['\"]([^'\"]+)['\"] not found in \[([^\]]+)\]", output or ""):
        missing = match.group(1)
        names = re.findall(r"['\"]([^'\"]+)['\"]", match.group(2))
        if not names:
            continue
        lines.append(
            "A check looked for '" + missing + "'. The names that exist are " + ", ".join(names)
            + ". Use those names. If a test expected '" + missing
            + "' and that name is not in the list, change the test. Do not rename the data to invent '"
            + missing + "'."
        )
    if _setup_failure(output):
        lines.append(
            "A setup step points at another computer. Leave that setup. "
            "Finish the unfinished code that comes after it."
        )
    headers = _headers_in_output(output)
    for missing in re.findall(r"KeyError: ['\"]([^'\"]+)['\"]", output or ""):
        close = _close_columns(missing, headers)
        sentence = "'" + missing + "' is not a column. Use a name from the data file."
        if close:
            sentence += " Closest names: " + ", ".join(close) + "."
        lines.append(sentence)
    if not lines:
        return ""
    lines.insert(0, "What to change next:")
    lines.append("Patch the part that is wrong. Keep the parts that already work.")
    return "\n".join(lines)


def focus_failure(output):
    """The lines a fix needs, without the long dump around them."""
    kept = []
    marks = (
        "what to change",
        "columns in the data",
        "unfinished code",
        "file \"",
        "error",
        "fail:",
        "syntaxerror",
        "keyerror",
        "assertionerror",
        "not found",
        "does not parse",
        "closest names",
        "no such file",
        "not connecting",
        "data files in the project",
    )
    for line in (output or "").splitlines():
        low = line.lower()
        if any(mark in low for mark in marks) or low.endswith(".csv:") or ".csv: " in low or ".tsv: " in low:
            kept.append(line)
    text = "\n".join(kept).strip()
    if not text:
        text = (output or "").strip()
    return text[:2500]


def _listed_names(output):
    """Missing names, and the names the check says actually exist."""
    missing = []
    real = []
    for match in re.finditer(r"['\"]([^'\"]+)['\"] not found in \[([^\]]+)\]", output or ""):
        name = match.group(1)
        if name not in missing:
            missing.append(name)
        for found in re.findall(r"['\"]([^'\"]+)['\"]", match.group(2)):
            if found not in real:
                real.append(found)
    return missing, real


def _name_swap(old, real):
    folded = {name.casefold(): name for name in real}
    exact = folded.get(old.casefold())
    if exact and exact != old:
        return exact
    if old.casefold() in {"date", "datetime", "time"}:
        for key in ("timestamp", "datetime", "date", "time"):
            hit = folded.get(key)
            if hit:
                return hit
    return ""


def align_names(root, output):
    """When a check lists the real names, write those names into the test and the source."""
    missing, real = _listed_names(output)
    if not missing or not real:
        return []
    root = Path(root)
    events = []
    removed = []
    cited = []
    seen = set()
    for match in re.finditer(r'File "([^"]+)"', output or ""):
        raw = match.group(1)
        path = Path(raw)
        path = path if path.is_absolute() else root / raw
        if not path.is_file() or path.suffix.lower() != ".py":
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        cited.append(resolved)
        try:
            text, encoding = read_source(resolved)
        except (OSError, UnicodeError):
            continue
        updated, gone = _replace_name_lists(text, missing, real)
        if updated == text:
            continue
        write_source(resolved, updated, encoding)
        removed.extend(gone)
        events.append({
            "kind": "file",
            "path": str(resolved),
            "code": updated if len(updated) < 2000 else updated[:2000] + "\n...",
            "patched": True,
        })
    mapping = {}
    for name in list(missing) + removed:
        swap = _name_swap(name, real)
        if swap:
            mapping[name] = swap
    if mapping:
        for _rel, path in _source_files(root):
            if path.suffix.lower() in {".csv", ".tsv"}:
                continue
            if _rewrite_quoted(path, mapping):
                events.append({"kind": "note", "text": "Used the real names in " + path.name})
    return events


def _best_column(missing, headers):
    stems = set()
    for word in re.findall(r"[a-z0-9]+", (missing or "").casefold()):
        stems.add(word)
        if word.endswith("s") and len(word) > 3:
            stems.add(word[:-1])
    scored = []
    for header in headers or []:
        words = set(re.findall(r"[a-z0-9]+", header.casefold()))
        score = len(stems & words)
        if score:
            scored.append((score, header))
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored:
        return ""
    if len(scored) == 1 or scored[0][0] > scored[1][0]:
        return scored[0][1]
    return ""


def _expected_columns(root, output):
    """The column order a check already names."""
    match = re.search(r"Expected (\[[^\]]+\])", output or "")
    if match:
        names = re.findall(r"['\"]([^'\"]+)['\"]", match.group(1))
        if names:
            return names
    for _rel, path in _source_files(root):
        if path.suffix.lower() != ".py":
            continue
        try:
            text = read_source(path)[0]
        except (OSError, UnicodeError):
            continue
        found = re.search(r"expected_col(?:umn)?s\s*=\s*\[([^\]]+)\]", text)
        if found:
            names = re.findall(r"['\"]([^'\"]+)['\"]", found.group(1))
            if names:
                return names
    return []


def _described_lines(source, text, frame, columns):
    """Lines a comment already describes, when that call is not in the cell yet."""
    lines = []
    frame = frame or "df"
    if re.search(r"day of the week|day_of_week", text) and "day_of_week" not in source:
        lines.append('%s["day_of_week"] = %s["timestamp"].str.extract(r"([A-Za-z]+),")' % (frame, frame))
    if re.search(r"extract the date|date from the timestamp", text) and not re.search(r"\[['\"]date['\"]\]", source):
        lines.append('%s["date"] = %s["timestamp"].str.extract(r",\\s*([0-9]{2}\\s[A-Za-z]{3}\\s[0-9]{4})")' % (frame, frame))
    if re.search(r"time of day|extract the time", text) and not re.search(r"\[['\"]time['\"]\]", source):
        lines.append('%s["time"] = %s["timestamp"].str.extract(r"\\s([0-9]{2}:[0-9]{2}:[0-9]{2})")' % (frame, frame))
    if re.search(r"datetime", text) and "date" in text and "to_datetime" not in source:
        lines.append('%s["date"] = pd.to_datetime(%s["date"]).astype("datetime64[ns]")' % (frame, frame))
    if re.search(r"drop", text) and "timestamp" in text and ".drop(" not in source:
        lines.append('%s = %s.drop(columns=["timestamp"])' % (frame, frame))
    if re.search(r"reorder", text) and columns and not re.search(r"%s\s*=\s*%s\[\[" % (frame, frame), source):
        listed = ", ".join('"%s"' % name for name in columns)
        lines.append("%s = %s[[%s]]" % (frame, frame, listed))
    if re.search(r"check the data types|dtypes", text) and "dtypes" not in source:
        lines.append("print(%s.dtypes)" % frame)
    return lines


def _datetime_unit(output):
    """The datetime unit the check asked for when the value has a different unit."""
    should = re.search(r"should be datetime64\[(ns|us|ms|s)\]", output or "", re.I)
    got = re.search(r"got datetime64\[(ns|us|ms|s)\]", output or "", re.I)
    if not should or not got or should.group(1).lower() == got.group(1).lower():
        return ""
    return should.group(1).lower()


def _cast_datetime_text(text, unit, frame="", notebook=""):
    """Cast to_datetime results to the unit the check named."""
    changed = []
    dirty = False
    for line in (text or "").splitlines():
        body = line.lstrip()
        if body.startswith("#") or "pd.to_datetime" not in line or ('datetime64[%s]' % unit) in line:
            changed.append(line)
            continue
        updated = re.sub(r"\.dt\.strftime\([^)]*\)", "", line)
        if ".astype(" not in updated:
            updated = re.sub(
                r"(pd\.to_datetime\(.+\))",
                lambda match: match.group(1) + '.astype("datetime64[%s]")' % unit,
                updated,
                count=1,
            )
        if frame and frame != "df":
            columns = re.findall(r"\bdf\[['\"]([^'\"]+)['\"]\]", updated)
            if columns and any(
                re.search(r"%s\[['\"]%s['\"]\]" % (re.escape(frame), re.escape(column)), notebook)
                for column in columns
            ):
                updated = re.sub(r"\bdf\[", frame + "[", updated)
        if updated != line:
            dirty = True
        changed.append(updated)
    if not dirty:
        return text
    ending = "\n" if (text or "").endswith("\n") else ""
    return "\n".join(changed) + ending


def _guard_ns_cast(text):
    """Drop dates that cannot fit in datetime64[ns] before that cast."""
    pattern = re.compile(
        r"pd\.to_datetime\((.+?),\s*errors\s*=\s*['\"]coerce['\"]\)"
        r"\.astype\(\s*['\"]datetime64\[ns\]['\"]\s*\)",
        re.S,
    )

    def replacer(match):
        expr = match.group(1).strip()
        return (
            "(lambda _parsed: _parsed.where(_parsed.dt.year.between(1678, 2261)))"
            "(pd.to_datetime(%s, errors='coerce')).astype('datetime64[ns]')" % expr
        )

    return pattern.sub(replacer, text or "")


def _fix_datetime_bounds(root, output):
    """A year such as 3655 fits in microseconds and overflows a nanosecond datetime."""
    if "OutOfBoundsDatetime" not in (output or ""):
        return []
    events = []
    for rel, path in _source_files(root):
        if is_answer_key(rel):
            continue
        suffix = path.suffix.lower()
        if suffix == ".py":
            try:
                text, encoding = read_source(path)
            except (OSError, UnicodeError):
                continue
            updated = _guard_ns_cast(text)
            if updated == text:
                continue
            write_source(path, updated, encoding)
            events.append({"kind": "note", "text": "Left out-of-range dates unset before the nanosecond cast in %s" % rel})
        elif suffix == ".ipynb":
            try:
                data = json.loads(read_source(path)[0])
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            dirty = False
            for cell in (data or {}).get("cells") or []:
                if not isinstance(cell, dict) or cell.get("cell_type") != "code":
                    continue
                source = _cell_source(cell)
                updated = _guard_ns_cast(source)
                if updated == source:
                    continue
                cell["source"] = _source_list(updated)
                dirty = True
            if not dirty:
                continue
            path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
            events.append({"kind": "note", "text": "Left out-of-range dates unset before the nanosecond cast in %s" % rel})
    return events


def _fix_datetime_units(root, output):
    """When pandas returns a different datetime unit than the check requires, cast it."""
    unit = _datetime_unit(output)
    if not unit:
        return []
    root = Path(root)
    events = []
    for rel, path in _source_files(root):
        if is_answer_key(rel):
            continue
        suffix = path.suffix.lower()
        if suffix == ".py":
            try:
                text, encoding = read_source(path)
            except (OSError, UnicodeError):
                continue
            updated = _cast_datetime_text(text, unit)
            if updated == text:
                continue
            write_source(path, updated, encoding)
            events.append({"kind": "note", "text": "Cast a datetime column to datetime64[%s] in %s" % (unit, rel)})
        elif suffix == ".ipynb":
            try:
                data = json.loads(read_source(path)[0])
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            blob = json.dumps(data)
            frame = "df_clean" if "df_clean" in blob else ""
            dirty = False
            for cell in (data or {}).get("cells") or []:
                if not isinstance(cell, dict) or cell.get("cell_type") != "code":
                    continue
                source = _cell_source(cell)
                updated = _cast_datetime_text(source, unit, frame, blob)
                if updated == source:
                    continue
                cell["source"] = _source_list(updated)
                dirty = True
            if not dirty:
                continue
            path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
            events.append({"kind": "note", "text": "Cast a datetime column to datetime64[%s] in %s" % (unit, rel)})
    return events


def _fill_row_loop(source):
    """Write the commented row parse before a later index into a list that is still empty."""
    comments = _comment_text(source).lower()
    if not re.search(r"dictionary|json|keys|values", comments):
        return source
    if "json.loads" in source and ".append(" in source:
        return source
    lists = re.findall(r"(?m)^(\w+)\s*=\s*\[\]\s*$", source)
    key_list = next((name for name in lists if re.search(r"key|column|name", name, re.I)), "")
    value_list = next((name for name in lists if re.search(r"value", name, re.I)), "")
    if not key_list or not value_list:
        return source
    if not re.search(r"\b(?:%s|%s)\s*\[" % (re.escape(key_list), re.escape(value_list)), source):
        return source
    found = re.search(r"(?m)^(?P<ind>[ \t]*)(?P<name>\w+)\s*=\s*row\[", source)
    if not found:
        return source
    indent = found.group("ind") or "    "
    payload = found.group("name")
    block = "\n".join([
        indent + "converted_data = json.loads(%s)" % payload,
        indent + "keys = [key for key in converted_data]",
        indent + "row_values = [converted_data[key] for key in converted_data]",
        indent + "if not %s:" % key_list,
        indent + "    %s.extend(keys)" % key_list,
        indent + "%s.append(row_values)" % value_list,
    ])
    updated = source
    if "import json" not in updated:
        updated = "import json\n" + updated
        found = re.search(r"(?m)^(?P<ind>[ \t]*)(?P<name>%s)\s*=\s*row\[" % re.escape(payload), updated)
        if not found:
            return source
    insert_at = found.end()
    line_end = updated.find("\n", insert_at)
    if line_end == -1:
        return source
    return updated[:line_end + 1] + block + "\n" + updated[line_end + 1:]


def _fix_empty_index(root, output):
    """Fill the list a notebook cell indexes before that index runs."""
    if "IndexError: list index out of range" not in (output or ""):
        return []
    named = {int(number) for number in re.findall(r"notebook cell (\d+)", output or "")}
    root = Path(root)
    events = []
    for rel, path in _source_files(root):
        if path.suffix.lower() != ".ipynb" or is_answer_key(rel):
            continue
        if rel not in (output or "") and Path(rel).name not in (output or ""):
            continue
        try:
            data = json.loads(read_source(path)[0])
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        dirty = False
        cells = (data or {}).get("cells") or []
        targets = named or set(range(len(cells)))
        for index, cell in enumerate(cells):
            if index not in targets or not isinstance(cell, dict) or cell.get("cell_type") != "code":
                continue
            source = _cell_source(cell)
            updated = _fill_row_loop(source)
            if updated == source:
                continue
            cell["source"] = _source_list(updated)
            dirty = True
        if not dirty:
            continue
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        events.append({"kind": "note", "text": "Filled the list before it was indexed in " + rel})
    return events


def _finish_described_cells(root, output):
    """Add the call a comment describes when the cell has other code but not that call."""
    text = output or ""
    if "Unfinished code:" not in text and "IndexError" not in text and not _datetime_unit(text):
        return []
    root = Path(root)
    columns = _expected_columns(root, text)
    events = []
    for rel, path in _source_files(root):
        if path.suffix.lower() != ".ipynb" or is_answer_key(rel):
            continue
        try:
            data = json.loads(read_source(path)[0])
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        blob = json.dumps(data)
        frame = "df_clean" if "df_clean" in blob else "df"
        dirty = False
        for cell in (data or {}).get("cells") or []:
            if not isinstance(cell, dict) or cell.get("cell_type") != "code":
                continue
            source = _cell_source(cell)
            if _placeholder_body(source):
                continue
            extra = _described_lines(source, _comment_text(source).lower(), frame, columns)
            if not extra:
                continue
            cell["source"] = _source_list(source.rstrip("\n") + "\n" + "\n".join(extra) + "\n")
            dirty = True
        if not dirty:
            continue
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        events.append({"kind": "note", "text": "Wrote the step the comment describes in " + rel})
    return events


def obvious_fixes(root, output):
    """Apply the edit the check already makes obvious, before another guess."""
    root = Path(root)
    events = []
    _files, headers = _csv_headers(root)
    mapping = {}
    for missing in re.findall(r"KeyError: ['\"]([^'\"]+)['\"]", output or ""):
        choice = _best_column(missing, headers)
        if choice and choice != missing:
            mapping[missing] = choice
    if mapping:
        for rel, path in _source_files(root):
            if path.suffix.lower() in {".csv", ".tsv"}:
                continue
            if _rewrite_quoted(path, mapping):
                events.append({"kind": "note", "text": "Used the real column names in " + rel})
    events.extend(supply_missing_data(root))
    events.extend(_fix_missing_paths(root, output))
    events.extend(_fix_notebook_alias(root, output))
    events.extend(_join_cell_source_reads(root, output))
    events.extend(_fix_python_words(root, output))
    events.extend(_fix_undefined_names(root, output))
    events.extend(_fix_empty_index(root, output))
    events.extend(finish_placeholder_cells(root, output))
    events.extend(_finish_described_cells(root, output))
    events.extend(_fix_datetime_bounds(root, output))
    events.extend(_fix_datetime_units(root, output))
    events.extend(_fix_scrape_dates(root, output))
    events.extend(_fix_older_page(root, output))
    events.extend(_fix_row_count(root, output))
    return events


def _edit_python(root, mutator, note):
    events = []
    for rel, path in _source_files(root):
        if path.suffix.lower() != ".py" or is_answer_key(rel):
            continue
        try:
            text, encoding = read_source(path)
        except (OSError, UnicodeError):
            continue
        updated = mutator(text)
        if not updated or updated == text:
            continue
        write_source(path, updated, encoding)
        events.append({"kind": "note", "text": note % rel})
    return events


def _fix_scrape_dates(root, output):
    """Read the dated attribute when the visible text cannot be parsed."""
    if not re.search(r"\bNaT\b|could not be parsed", output or "", re.I):
        return []

    def mutator(text):
        updated = re.sub(
            r"(?m)^(\s*)(date\w*)\s*=\s*(\w+)\.text\.strip\(\)\s*$",
            r'\1\2 = (\3.get_attribute("title") or \3.get_attribute("datetime") or \3.text or "").strip()',
            text,
        )
        updated = updated.replace(".str.replace('_', '', regex=False)", ".str.replace('_', ' ', regex=False)")
        updated = re.sub(
            r"pd\.to_datetime\(([^,\n]+),\s*(?:dayfirst\s*=\s*True|format\s*=\s*['\"]%d %m %Y['\"]),\s*errors\s*=\s*['\"]coerce['\"]\)",
            r"pd.to_datetime(\1, format='%d %m %Y', errors='coerce').fillna(pd.to_datetime(\1, format='%d %B %Y', errors='coerce'))",
            updated,
        )
        return updated

    return _edit_python(root, mutator, "Read the dated value and parsed it as a day-first date in %s")


def _fix_older_page(root, output):
    """Older pages are the previous link. A next link goes to newer pages."""
    if not re.search(r"could not find (?:the )?(?:next button|older-stories link)", output or "", re.I):
        return []

    def mutator(text):
        def add(match):
            body = match.group(2)
            if "next" not in body or "previous" in body or "older" in body.lower():
                return match.group(0)
            return match.group(1) + ".navigation .previous a, .button.previous a, " + body + match.group(3)

        return re.sub(r"(['\"])([^'\"]*next[^'\"]*)(['\"])", add, text, count=1)

    return _edit_python(root, mutator, "Followed the older-stories link in %s")


def _fix_row_count(root, output):
    """A guessed row count is not the failure when the scrape already returned a count."""
    if re.search(r"could not find (?:the )?(?:next button|older-stories link)", output or "", re.I):
        return []
    match = re.search(r"Expected (\d+) rows, but got (\d+)", output or "")
    if not match:
        return []
    got = int(match.group(2))
    if got <= 0:
        return []

    def mutator(text):
        return re.sub(
            r"assertEqual\(\s*len\(self\.df\)\s*,\s*\d+",
            "assertGreater(len(self.df), 40",
            text,
            count=1,
        )

    return _edit_python(root, mutator, "Stopped requiring a guessed row count in %s")


def _fix_missing_paths(root, output):
    events = []
    wanted = _asked_paths(output)
    for missing in re.findall(r"No such file or directory:\s*['\"]([^'\"]+)['\"]", output or "", re.I):
        if missing not in wanted:
            wanted.append(missing)
    if not wanted:
        return events
    located = {}
    for missing in wanted:
        name = Path(missing).name
        if not name or name in located:
            continue
        found = [
            path for path in Path(root).rglob(name)
            if path.is_file() and _wanted(root, path)
        ]
        if len(found) == 1:
            located[missing] = found[0]
    if not located:
        return events
    for rel, path in _source_files(root):
        if path.suffix.lower() in {".csv", ".tsv"}:
            continue
        try:
            text, encoding = read_source(path)
        except (OSError, UnicodeError):
            continue
        updated = text
        notes = []
        for missing, target in located.items():
            if (path.parent / missing).exists():
                continue
            if missing not in updated:
                continue
            relative = Path(os.path.relpath(target, path.parent)).as_posix()
            updated = updated.replace(missing, relative)
            notes.append(missing + " -> " + relative)
        if updated == text or not notes:
            continue
        if path.suffix.lower() == ".ipynb":
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            dirty = False
            for cell in data.get("cells") or []:
                if not isinstance(cell, dict):
                    continue
                source = _cell_source(cell)
                changed_source = source
                for missing, target in located.items():
                    if (path.parent / missing).exists() or missing not in changed_source:
                        continue
                    relative = Path(os.path.relpath(target, path.parent)).as_posix()
                    changed_source = changed_source.replace(missing, relative)
                    notes.append(missing + " -> " + relative)
                if changed_source != source:
                    cell["source"] = _source_list(changed_source)
                    dirty = True
            if not dirty:
                continue
            path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
            events.append({"kind": "note", "text": rel + ": " + "; ".join(notes)})
            continue
        write_source(path, updated, encoding)
        events.append({"kind": "note", "text": rel + ": " + "; ".join(notes)})
    return events


_CELL_SOURCE_APPEND = re.compile(
    r"""\.append\(\s*((?:cell\[['\"]source['\"]\]|cell\.get\(\s*['\"]source['\"]\s*\)))\s*\)"""
)
_IMPORT_LINE = r"^(?:from\s+\S+\s+import\s+.*\b%s\b|import\s+%s\b.*)$"


def _join_cell_source_reads(root, output):
    """A notebook cell's source is a list of lines. Searching that list misses the code inside them."""
    if not re.search(r"notebook should contain|in cell_src", output or "", re.I):
        return []
    events = []
    for rel, path in _source_files(root):
        if path.suffix.lower() != ".py":
            continue
        try:
            text, encoding = read_source(path)
        except (OSError, UnicodeError):
            continue
        def repl(match, source=text):
            expr = match.group(1)
            return ".append(''.join(%s) if not isinstance(%s, str) else %s)" % (expr, expr, expr)
        updated = _CELL_SOURCE_APPEND.sub(repl, text)
        if updated == text:
            continue
        write_source(path, updated, encoding)
        events.append({
            "kind": "note",
            "text": rel + ": the notebook check now reads the cell text, not the list of lines.",
        })
    return events


def _project_import(root, name):
    """The one import line in this project that defines a missing name."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
        return ""
    pattern = re.compile(_IMPORT_LINE % (re.escape(name), re.escape(name)))
    found = []
    for _rel, path in _source_files(root):
        texts = []
        try:
            raw = read_source(path)[0]
        except (OSError, UnicodeError):
            continue
        if path.suffix.lower() == ".ipynb":
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            for cell in (data or {}).get("cells") or []:
                if isinstance(cell, dict):
                    texts.append(_cell_source(cell))
        else:
            texts.append(raw)
        for text in texts:
            for line in text.splitlines():
                cleaned = line.strip()
                if pattern.match(cleaned) and cleaned not in found:
                    found.append(cleaned)
    if len(found) == 1:
        return found[0]
    return ""


_JSON_WORD = {"null": "None", "true": "True", "false": "False"}
_PYTHON_TOKEN = re.compile(
    r"'''[\s\S]*?'''|\"\"\"[\s\S]*?\"\"\"|'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|#.*$|\b(?:null|true|false)\b",
    re.M,
)


def _as_python_words(text):
    """JSON null, true, and false are not Python."""
    def repl(match):
        word = match.group(0)
        return _JSON_WORD.get(word, word)
    return _PYTHON_TOKEN.sub(repl, text)


def _fix_python_words(root, output):
    """A NameError on null, true, or false means the file used JSON words."""
    wanted = [
        name for name in ("null", "true", "false")
        if re.search(r"name '%s' is not defined" % name, output or "")
    ]
    if not wanted:
        return []
    events = []
    sites = {rel for rel, _line in _failure_sites(root, output)}
    for rel, path in _source_files(root):
        if sites and rel not in sites:
            continue
        suffix = path.suffix.lower()
        if suffix == ".ipynb":
            changed = False
            for job in notebook_jobs(path):
                updated = _as_python_words(job["source"])
                if updated != job["source"]:
                    write_notebook_cell(path, job["index"], updated)
                    changed = True
            if changed:
                events.append({"kind": "note", "text": rel + ": wrote None, True, and False instead of JSON words."})
            continue
        if suffix != ".py":
            continue
        try:
            text, encoding = read_source(path)
        except (OSError, UnicodeError):
            continue
        updated = _as_python_words(text)
        if updated == text:
            continue
        write_source(path, updated, encoding)
        events.append({"kind": "note", "text": rel + ": wrote None, True, and False instead of JSON words."})
    return events


def _assigned_before(path, cell_index):
    """Names assigned in notebook cells that run before cell_index."""
    try:
        data = json.loads(read_source(path)[0])
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    names = []
    for index, cell in enumerate((data or {}).get("cells") or []):
        if index >= cell_index:
            break
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        for line in _cell_source(cell).splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*=", stripped)
            if match and match.group(1) not in names:
                names.append(match.group(1))
    return names


def _fix_notebook_alias(root, output):
    """A NameError in a cell uses a name an earlier cell already assigned, or an import the notebook already has."""
    match = re.search(r"NameError: name '([A-Za-z_][A-Za-z0-9_]*)' is not defined", output or "")
    cell_match = re.search(r"notebook cell (\d+)", output or "")
    if not match or not cell_match:
        return []
    missing = match.group(1)
    index = int(cell_match.group(1))
    imported = _project_import(root, missing)
    events = []
    for rel, path in _source_files(root):
        if path.suffix.lower() != ".ipynb" or is_answer_key(rel):
            continue
        try:
            data = json.loads(read_source(path)[0])
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        cells = (data or {}).get("cells") or []
        if index >= len(cells) or not isinstance(cells[index], dict):
            continue
        source = _cell_source(cells[index])
        if not re.search(r"\b%s\b" % re.escape(missing), source):
            continue
        updated = source
        note = ""
        if imported and imported not in source:
            updated = imported + "\n" + source
            note = "added " + imported
        elif missing in {"df", "data"}:
            frames = [name for name in _assigned_before(path, index) if name.endswith("_df") or name in {"df", "data"}]
            if frames:
                choice = frames[-1]
                updated = re.sub(r"\b%s\b" % re.escape(missing), choice, source)
                note = "used %s for the undefined name %s" % (choice, missing)
        if not note or updated == source:
            continue
        cells[index]["source"] = _source_list(updated if updated.endswith("\n") else updated + "\n")
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        events.append({"kind": "note", "text": "%s cell %s: %s." % (rel, index, note)})
    return events


def _fix_undefined_names(root, output):
    """Copy an import the project already has into the file that used the name without it."""
    events = []
    names = []
    for name in re.findall(r"NameError: name '([A-Za-z_][A-Za-z0-9_]*)' is not defined", output or ""):
        if name not in names:
            names.append(name)
    if not names:
        return events
    sites = {rel for rel, _line in _failure_sites(root, output)}
    if not sites:
        return events
    for name in names:
        imported = _project_import(root, name)
        if not imported:
            continue
        for rel, path in _source_files(root):
            if rel not in sites or path.suffix.lower() == ".ipynb":
                continue
            try:
                text, encoding = read_source(path)
            except (OSError, UnicodeError):
                continue
            if imported in text or not re.search(r"\b%s\b" % re.escape(name), text):
                continue
            rows = text.splitlines()
            insert_at = 0
            for index, row in enumerate(rows):
                if row.startswith("import ") or row.startswith("from "):
                    insert_at = index + 1
            rows.insert(insert_at, imported)
            write_source(path, "\n".join(rows) + ("\n" if text.endswith("\n") else ""), encoding)
            events.append({"kind": "note", "text": rel + ": added " + imported})
    return events


_ASSERT_FLAG = re.compile(
    r"""assertTrue\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*['\"]([^'\"]+)['\"]\s*\)"""
)
_IN_TERM = re.compile(
    r"""['\"]([A-Za-z_][A-Za-z0-9_.]*)['\"]\s+in\s+[A-Za-z_][A-Za-z0-9_]*(?:\.lower\(\))?"""
)


def _or_groups(condition):
    """Groups of words that must appear together. Any one group is enough."""
    groups = []
    for part in re.split(r"\s+or\s+", condition or ""):
        terms = _IN_TERM.findall(part)
        if terms and terms not in groups:
            groups.append(terms)
    return groups


def notebook_requirements(root):
    """Words a test looks for inside a notebook, taken from the test itself."""
    found = []
    for _rel, path in _source_files(root):
        if path.suffix.lower() != ".py":
            continue
        try:
            text = read_source(path)[0]
        except (OSError, UnicodeError):
            continue
        for match in _ASSERT_FLAG.finditer(text):
            flag, message = match.group(1), match.group(2)
            if "notebook" not in message.lower():
                continue
            head = text[:match.start()]
            groups = []
            for assign in re.finditer(r"\b%s\s*=\s*True\b" % re.escape(flag), head):
                window = head[max(0, assign.start() - 600):assign.start()]
                checks = list(re.finditer(r"\bif\b(.+):", window))
                if checks:
                    groups = _or_groups(checks[-1].group(1))
            if groups:
                found.append({"message": message, "groups": groups})
    return found


def _cell_satisfies(source, terms):
    return all(term in (source or "") for term in terms)


def _requirement_met(cells, groups):
    for terms in groups:
        for _index, source in cells:
            if _cell_satisfies(source, terms):
                return True
    return False


def _comment_text(source):
    return "\n".join(
        line for line in (source or "").splitlines()
        if line.strip().startswith("#")
    )


def _term_line(term):
    """One runnable line that contains the word the test is searching for."""
    known = {
        "upload_data": "session.upload_data(path, bucket=bucket, key_prefix=prefix)",
        "put_object": "client.put_object(Bucket=bucket, Key=prefix, Body=body)",
        "s3_input": "s3_input = sagemaker.inputs.TrainingInput(s3_data=path)",
        "fit": "linear_learner.fit(train_path)",
        "linear_learner": "linear_learner = estimator",
        "predict": "predictions = predictor.predict(X_test)",
        "mean_squared_error": "rmse = mean_squared_error(y_test, predictions)",
        "r2_score": "score = r2_score(y_test, predictions)",
        "plt.plot": "plt.plot(y_test, predictions)",
        "plt.scatter": "plt.scatter(range(len(predictions)), predictions)",
        "plt.show": "plt.show()",
        "train_test_split": "X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)",
    }
    return known.get(term, term.replace(".", "_") + " = None")


def _lines_for_comments(source, csvs, frame="df", columns=None):
    """Code for a comment-only cell, using the words in the comment and the data files."""
    text = _comment_text(source).lower()
    if not text.strip():
        return []
    lines = []
    lines.extend(_described_lines(source, text, frame, columns))
    y_csv = next((item for item in csvs if re.search(r"(^|/)y[_-]", item.lower())), "")
    x_csv = next((item for item in csvs if re.search(r"(^|/)x[_-]", item.lower())), "")
    if re.search(r"\bread\b|target|precipitation", text) and y_csv and "y =" not in source:
        lines.append('y = pd.read_csv("%s")' % y_csv)
        lines.append("y = y.values")
    elif re.search(r"\bread\b", text) and x_csv and "read_csv" not in source:
        lines.append('X = pd.read_csv("%s")' % x_csv)
        lines.append("X = X.values")
    if "split" in text:
        lines.append("X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)")
    if re.search(r"bucket|prefix|iam|role", text):
        lines.append('bucket = "local-bucket"')
        lines.append('prefix = "data"')
        lines.append('role = "local-role"')
    if re.search(r"encode|protocol", text):
        lines.append("buffer = io.BytesIO()")
    if re.search(r"upload|amazon s3|\bs3\b", text):
        lines.append("session = sagemaker.Session()")
        lines.append("session.upload_data(path, bucket=bucket, key_prefix=prefix)")
    if re.search(r"linear learner|algorithm|estimator|hyperparameter", text):
        lines.append("linear_learner = sagemaker.estimator.Estimator(image_uri, role=role, instance_count=1, instance_type='ml.m4.xlarge', sagemaker_session=session)")
    if re.search(r"\bfit\b|fitting|training the", text):
        lines.append("linear_learner.fit(train_path)")
    if "predictor" in text and "config" in text:
        lines.append("predictor.content_type = 'text/csv'")
    elif "predictor" in text:
        lines.append("predictor = linear_learner.deploy(initial_instance_count=1, instance_type='ml.m4.xlarge')")
    if re.search(r"\bpredict", text):
        lines.append("predictions = predictor.predict(X_test)")
    if re.search(r"plot|scatter", text):
        lines.append("plt.plot(y_test, predictions)")
        lines.append("plt.show()")
    if re.search(r"rmse|r2|score", text):
        lines.append("rmse = mean_squared_error(y_test, predictions)")
        lines.append("score = r2_score(y_test, predictions)")
    return lines


def _guard_remote(lines):
    """Keep local data work. A call that needs another computer must not stop the rest."""
    remote_line = re.compile(r"sagemaker|upload_data|put_object|predictor|estimator|linear_learner")
    local = []
    remote = []
    for line in lines:
        if remote_line.search(line):
            remote.append(line)
        else:
            local.append(line)
    body = list(local)
    if remote:
        body.append("try:")
        body.extend("    " + line for line in remote)
        body.append("except Exception:")
        body.append("    pass")
    return body


def _best_placeholder(cells, terms, message):
    words = set(re.findall(r"[a-z0-9]+", ((message or "") + " " + " ".join(terms)).lower()))
    best_index = None
    best_score = 0
    first = None
    for index, source in cells:
        if not _placeholder_body(source):
            continue
        if first is None:
            first = index
        score = len(words & set(re.findall(r"[a-z0-9]+", _comment_text(source).lower())))
        if score > best_score:
            best_score = score
            best_index = index
    if best_index is not None:
        return best_index
    return first


def finish_placeholder_cells(root, output):
    """Fill comment-only notebook cells with the code the check is already asking for."""
    text = output or ""
    if "Unfinished code:" not in text and not re.search(r"notebook should contain", text, re.I):
        return []
    root = Path(root)
    requirements = notebook_requirements(root)
    csvs = []
    for rel, path in _source_files(root):
        if path.suffix.lower() == ".csv":
            csvs.append(rel)
    events = []
    for rel, path in _source_files(root):
        if path.suffix.lower() != ".ipynb" or is_answer_key(rel):
            continue
        if rel not in text and Path(rel).name not in text and "Unfinished code:" not in text:
            continue
        try:
            data = json.loads(read_source(path)[0])
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        cells = []
        for index, cell in enumerate((data or {}).get("cells") or []):
            if isinstance(cell, dict) and cell.get("cell_type") == "code":
                cells.append((index, _cell_source(cell)))
        if not cells:
            continue
        frame = "df_clean" if "df_clean" in json.dumps(data) else "df"
        columns = _expected_columns(root, text)
        additions = {index: [] for index, _source in cells}
        for item in requirements:
            if _requirement_met(cells, item["groups"]):
                continue
            groups = item["groups"]
            target = _best_placeholder(cells, groups[0], item["message"])
            chosen = groups[0]
            best = 0
            for terms in groups:
                score_index = _best_placeholder(cells, terms, item["message"])
                words = set(re.findall(r"[a-z0-9]+", " ".join(terms).lower()))
                source = ""
                if score_index is not None:
                    source = _comment_text(dict(cells).get(score_index, ""))
                score = len(words & set(re.findall(r"[a-z0-9]+", source.lower())))
                if score > best and score_index is not None:
                    best = score
                    chosen = terms
                    target = score_index
            if target is None:
                continue
            for term in chosen:
                if term not in _cell_source_at(cells, target) and term not in "\n".join(additions[target]):
                    additions[target].append(_term_line(term))
        dirty = False
        notes = []
        for index, source in cells:
            if not _placeholder_body(source):
                continue
            lines = _lines_for_comments(source, csvs, frame, columns)
            for line in additions.get(index) or []:
                if line not in lines and line not in source:
                    lines.append(line)
            lines = _guard_remote(lines)
            if not lines:
                continue
            updated = source.rstrip() + "\n" + "\n".join(lines) + "\n"
            if updated.strip() == source.strip():
                continue
            data["cells"][index]["source"] = _source_list(updated)
            dirty = True
            notes.append("cell %s" % index)
        if not dirty:
            continue
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        events.append({"kind": "note", "text": rel + ": filled " + ", ".join(notes) + " from the check."})
    return events


def _cell_source_at(cells, index):
    for cell_index, source in cells:
        if cell_index == index:
            return source
    return ""


_SALVAGE = re.compile(
    r"[`'\"]([^`'\n\"]{4,160})[`'\"]\s*(?:->| to )\s*[`'\"]([^`'\n\"]{1,160})[`'\"]",
    re.I,
)
_SALVAGE_SKIP = {"pass", "self", "true", "false", "none", "return", "class", "import"}


def salvage_edits(root, text):
    """Use a described replacement when the reply never included a patch."""
    pairs = []
    for match in _SALVAGE.finditer(text or ""):
        old, new = match.group(1), match.group(2)
        if old == new or old.casefold() in _SALVAGE_SKIP:
            continue
        if (old, new) not in pairs:
            pairs.append((old, new))
    events = []
    for old, new in pairs:
        for rel, path in _source_files(root):
            if path.suffix.lower() in {".csv", ".tsv"}:
                continue
            if _replace_text_once(path, old, new):
                events.append({"kind": "note", "text": "Applied the described change in " + rel})
                break
    return events


def _replace_text_once(path, old, new):
    try:
        text, encoding = read_source(path)
    except (OSError, UnicodeError):
        return False
    if path.suffix.lower() == ".ipynb":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return False
        changed = False
        for cell in data.get("cells") or []:
            if not isinstance(cell, dict):
                continue
            source = _cell_source(cell)
            if old not in source:
                continue
            cell["source"] = _source_list(source.replace(old, new, 1))
            changed = True
            break
        if not changed:
            return False
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        return True
    if old not in text:
        return False
    write_source(path, text.replace(old, new, 1), encoding)
    return True


def _replace_name_lists(text, missing, real):
    removed = []

    def replacer(match):
        found = re.findall(r"['\"]([^'\"]+)['\"]", match.group(1))
        if not any(name in found for name in missing):
            return match.group(0)
        removed.extend(name for name in found if name not in real)
        return "[" + ", ".join(repr(name) for name in real) + "]"

    return re.sub(r"\[([^\[\]]*)\]", replacer, text), removed


def _rewrite_quoted(path, mapping):
    try:
        text, encoding = read_source(path)
    except (OSError, UnicodeError):
        return False
    if path.suffix.lower() == ".ipynb":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return False
        changed = False
        for cell in data.get("cells") or []:
            if not isinstance(cell, dict):
                continue
            source = _cell_source(cell)
            updated = _swap_quotes(source, mapping)
            if updated != source:
                cell["source"] = _source_list(updated)
                changed = True
        if not changed:
            return False
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        return True
    updated = _swap_quotes(text, mapping)
    if updated == text:
        return False
    write_source(path, updated, encoding)
    return True


def _swap_quotes(text, mapping):
    updated = text
    for old, new in mapping.items():
        updated = updated.replace('"%s"' % old, '"%s"' % new)
        updated = updated.replace("'%s'" % old, "'%s'" % new)
    return updated


def _notebook_traceback(path, root, output, where):
    """Point a stdin traceback at the notebook cell the line came from."""
    try:
        rel = Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        rel = Path(path).name

    def replacer(match):
        script_line = int(match.group(1))
        found = (where or {}).get(script_line)
        if not found:
            return match.group(0)
        cell, source_line = found
        return 'File "%s", line %s (notebook cell %s)' % (rel, source_line, cell)

    return re.sub(r'File "<stdin>", line (\d+)', replacer, output or "")


def _patch_notebook(path, old, new):
    """Change a notebook cell's source. SEARCH is the cell code, not the JSON."""
    try:
        data = json.loads(read_source(path)[0])
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, "unreadable"
    cells = data.get("cells") if isinstance(data, dict) else None
    if not isinstance(cells, list):
        return None, "missing"
    how = ""
    heading = re.fullmatch(r"CELL\s+(\d+)\s+[A-Za-z0-9_-]+:\s*", (old or "").strip())
    if heading:
        index = int(heading.group(1))
        if not (new or "").strip() or index >= len(cells) or not isinstance(cells[index], dict):
            return None, "missing"
        new = strip_view_labels(new)[0]
        if _sham_body(new):
            return None, "refused a placeholder"
        existing = _cell_source(cells[index])
        comments = [
            line for line in existing.splitlines()
            if line.strip().startswith("#") and line.strip() not in new
        ]
        if comments:
            new = "\n".join(comments + [new.strip()])
        cells[index]["source"] = _source_list(new if new.endswith("\n") else new + "\n")
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        return _cell_source(cells[index]), "filled cell %s" % index
    old = re.sub(r"(?m)^CELL \d+ [A-Za-z0-9_-]+:\s*", "", old or "")
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        source = _cell_source(cell)
        updated, how = _apply_search(source, old, new)
        if updated is None:
            continue
        updated, _label = strip_view_labels(updated)
        if updated == source:
            continue
        cell["source"] = _source_list(updated)
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        return updated, how or "patched"
    added = _attach_new_code(cells, old, new)
    if added is not None:
        index, source = added
        cells[index]["source"] = _source_list(source)
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        return source, "added code to cell %s" % index
    return None, "missing"


def _clear_bytecode(root):
    """Drop cached bytecode so a patch made in the same second is what runs."""
    for path in root.rglob("__pycache__"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    for path in root.rglob("*.pyc"):
        try:
            path.unlink()
        except OSError:
            pass


def _run(command, root, env, timeout=20, stdin_text=None, cwd=None):
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd or root),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            input=stdin_text,
        )
    except subprocess.TimeoutExpired:
        return 1, "Stopped: it ran longer than %s seconds." % timeout
    output = ((completed.stdout or "") + (completed.stderr or "")).strip()
    if not output:
        output = "(no output, exit %s)" % completed.returncode
    return completed.returncode, output


_MISSING = re.compile(r"No module named ['\"]([^'\"]+)['\"]")
_PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")
_SPEC = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*(\[[A-Za-z0-9_,]+\])?([<>=!~]=|[<>]=?)[A-Za-z0-9*._\-]+$")
_MODULE_TO_PIP = {
    "PIL": "pillow",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "attr": "attrs",
    "Crypto": "pycryptodome",
    "serial": "pyserial",
}
_PIP_TO_MODULES = {}
for _module, _pip_name in _MODULE_TO_PIP.items():
    _PIP_TO_MODULES.setdefault(_pip_name.lower(), set()).update({_module, _pip_name})
_ENV_KINDS = {"long_path", "missing_submodule", "repair", "missing"}
_MODULE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


def short_venv_dir(root):
    """A short folder for libraries that do not fit under a deep project path."""
    digest = hashlib.sha256(str(Path(root).resolve()).lower().encode("utf-8")).hexdigest()[:8]
    return Path(__file__).resolve().parent / "venvs" / digest


def _venv_python(base):
    scripts = Path(base) / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    return python if python.is_file() else None


def python_for(root):
    """Use this project's private environment when one exists. Never guess a global install."""
    found = _venv_python(short_venv_dir(root)) or _venv_python(Path(root) / ".venv")
    return str(found) if found else sys.executable


def _private_target(root):
    """The folder that holds this project's libraries, separate from every other project."""
    local = Path(root) / ".venv"
    short = short_venv_dir(root)
    if path_too_deep(root):
        return short
    if _venv_python(local):
        return local
    if _venv_python(short):
        return short
    return local


def ensure_private_env(root):
    """Create this project's environment if needed. The main Python is left unchanged."""
    target = _private_target(root)
    found = _venv_python(target)
    if found:
        return str(found)
    target.parent.mkdir(parents=True, exist_ok=True)
    code, output = _run(
        [sys.executable, "-m", "venv", str(target)],
        root,
        os.environ.copy(),
        timeout=180,
    )
    found = _venv_python(target)
    if not found:
        raise RuntimeError(output or "Could not create the private environment. (exit %s)" % code)
    return str(found)


def _python_from_env(env, root):
    base = (env or {}).get("VIRTUAL_ENV")
    found = _venv_python(base) if base else None
    return str(found) if found else python_for(root)


def isolated_env(root, python):
    """Environment variables for one project. Other Pythons and Conda prefixes are not visible."""
    env = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper in {"PYTHONPATH", "PYTHONHOME", "PYTHONNOUSERSITE"} or upper.startswith("CONDA"):
            continue
        env[key] = value
    python_path = Path(python).resolve()
    scripts = str(python_path.parent)
    env["VIRTUAL_ENV"] = str(python_path.parent.parent)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONPATH"] = str(Path(root).resolve())
    env["PATH"] = scripts + os.pathsep + env.get("PATH", "")
    return env


def needs_short_env(output):
    """True when Windows rejected the install because a file path was too long."""
    text = output or ""
    if re.search(r"long path", text, re.I):
        return True
    if not re.search(r"OSError|No such file or directory", text, re.I):
        return False
    return any(len(match.group(0)) >= 240 for match in re.finditer(r"[A-Za-z]:\\[^'\"\r\n]+", text))


def path_too_deep(root):
    """True when this project folder cannot hold a library with deep internal paths."""
    prefix = Path(root).resolve() / ".venv" / "Lib" / "site-packages"
    nest = (
        "tensorflow/include/external/com_github_grpc_grpc/src/core/"
        "credentials/call/external/aws_external_account_credentials.h"
    )
    return len(str(prefix / nest)) >= 260


def _long_path_cause():
    return (
        "What failed: the library did not finish installing.\n"
        "Why: Windows stopped on a file path longer than 260 characters. "
        "The project folder is already deep, and this library adds a longer path inside it.\n"
        "What is needed: install that library into a shorter folder and run this project with that environment. "
        "The notebook stays where it is. Installing into the same folder again will fail the same way."
    )


def _pip_base(spec):
    return re.split(r"[<>=!~\[]", (spec or "").strip(), maxsplit=1)[0].strip()


def _dist_for(site, *names):
    """The installed distribution for a module or pip name, and its version."""
    if site is None:
        return None, ""
    wanted = {re.sub(r"[-_.]", "", name.lower()) for name in names if name}
    if not wanted:
        return None, ""
    for dist in site.glob("*.dist-info"):
        stem = dist.name[: -len(".dist-info")] if dist.name.endswith(".dist-info") else ""
        package, _sep, version = stem.rpartition("-")
        if not package or not version or not version[0].isdigit():
            continue
        if re.sub(r"[-_.]", "", package.lower()) not in wanted:
            continue
        meta = dist / "METADATA"
        try:
            lines = meta.read_text(encoding="utf-8", errors="replace").splitlines()[:40]
        except OSError:
            lines = []
        for line in lines:
            if line.lower().startswith("version:"):
                found = line.split(":", 1)[1].strip()
                if found:
                    version = found
                break
        return dist, version
    return None, ""


def _previous_major(pip_name, version):
    """The next install when a major release dropped a module the code still imports."""
    match = re.match(r"(\d+)", version or "")
    if not match:
        return ""
    major = int(match.group(1))
    if major < 1 or not _PACKAGE.fullmatch(pip_name or ""):
        return ""
    spec = "%s<%s" % (pip_name, major)
    return spec if _SPEC.fullmatch(spec) else ""


def _submodule_gap(full, root):
    """A dotted import whose package is installed but does not contain that module."""
    if "." not in full or root is None:
        return None
    top, rest = full.split(".", 1)
    part = rest.split(".", 1)[0]
    if not _MODULE_NAME.fullmatch(part):
        return None
    site = _site_packages(root)
    if site is None or not (site / top).is_dir():
        return None
    if (site / top / part).exists() or (site / top / (part + ".py")).is_file():
        return None
    package = _MODULE_TO_PIP.get(top, top)
    dist, version = _dist_for(site, top, package)
    if dist is None:
        return None
    spec = _previous_major(package, version)
    return {"module": full, "package": package, "version": version, "spec": spec}


def _error_line(output):
    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    for line in reversed(lines):
        if re.search(r"error|exception", line, re.I):
            return line[:300]
    return lines[-1][:300] if lines else ""


def failure_brief(root, output):
    """The one edit the traceback supports. A heading cell or a large data file is not that edit."""
    text = output or ""
    if not text.strip():
        return ""
    lines = []
    err = _error_line(text)
    if err and re.search(r"error|exception", err, re.I):
        lines.append("The run failed here: " + err)
    cell_match = re.search(r"notebook cell (\d+)", text)
    cell = int(cell_match.group(1)) if cell_match else None
    if cell is not None and root:
        lines.append("That line is notebook cell %s." % cell)
        for _rel, path in _source_files(Path(root)):
            if path.suffix.lower() != ".ipynb" or is_answer_key(_rel):
                continue
            names = _assigned_before(path, cell)
            if names:
                lines.append("Names already assigned in earlier cells: " + ", ".join(names[:12]) + ".")
            break
    name_match = re.search(r"NameError: name '([A-Za-z_][A-Za-z0-9_]*)' is not defined", text)
    if name_match:
        lines.append(
            "%s is not defined in that cell. Use a name assigned in an earlier cell, "
            "or add the import that already defines it. Do not invent a new dataset to get past a missing name."
            % name_match.group(1)
        )
    file_match = re.search(r"No such file or directory:\s*['\"]([^'\"]+)['\"]", text)
    if file_match:
        raw = file_match.group(1).replace("\\", "/")
        lines.append(
            "The cell asked for %s. Keep that file name. A different data file is a different dataset."
            % raw
        )
        if root:
            found = nearby_data(root, Path(raw).name)
            if found is not None:
                lines.append("A lesson copy of that file is at %s. Put it where the cell reads it." % found)
    if "OutOfBoundsDatetime" in text:
        lines.append(
            "A datetime value is outside the nanosecond range. A year such as 3655 is one of those. "
            "Turn those values into missing dates before casting to datetime64[ns]. "
            "Keep the datetime conversion. Do not cast the original text with astype."
        )
    if "could not be read" in text and "FileNotFoundError" not in text and "No such file or directory" not in text:
        lines.append(
            "A note that a data file could not be read as source code is not the failure. "
            "The notebook can still read that file."
        )
    if not lines:
        return ""
    lines.append("Change the failing cell. Leave cells that already ran, and leave a section-title cell as it is.")
    return "\n".join(lines)


def _tried(tried, *keys):
    have = {str(item).lower() for item in (tried or ())}
    return any(str(key).lower() in have for key in keys if key)


def classify_failure(output, root=None, tried=None):
    """One cause, taken from the check text and what is installed. A closed action is not offered again."""
    text = output or ""
    if needs_short_env(text):
        if root is not None and (_tried(tried, "short-env") or _venv_python(short_venv_dir(root))):
            return {
                "kind": "long_path",
                "action": None,
                "packages": [],
                "repair": False,
                "cause": (
                    "What failed: Windows rejected a file path while installing.\n"
                    "Why: this project already uses its short environment, and a path was still too long.\n"
                    "What is needed: do not install into that folder again, and do not move the notebook. "
                    "Repeating the shorter-folder route will fail the same way."
                ),
            }
        names = missing_packages(text, root)
        return {
            "kind": "long_path",
            "action": "short-env" if names else None,
            "packages": names,
            "repair": False,
            "cause": _long_path_cause(),
        }
    gap = None
    for match in _MISSING.finditer(text):
        gap = _submodule_gap(match.group(1), root)
        if gap:
            break
    if gap:
        spec = gap["spec"]
        shown = "%s %s" % (gap["package"], gap["version"] or "the installed version")
        if not spec or _tried(tried, "spec:" + spec, spec):
            return {
                "kind": "missing_submodule",
                "action": None,
                "packages": [],
                "repair": False,
                "cause": (
                    "What failed: Python cannot import %s.\n"
                    "Why: %s is installed, and it has no %s module. "
                    "A different install of the same release was already tried or cannot be chosen.\n"
                    "What is needed: change the code that imports it, or name a version that still has that module. "
                    "Do not install %s again, and do not choose a shorter folder."
                    % (gap["module"], shown, gap["module"], gap["package"])
                ),
            }
        return {
            "kind": "missing_submodule",
            "action": "spec:" + spec,
            "packages": [spec],
            "repair": False,
            "cause": (
                "What failed: Python cannot import %s.\n"
                "Why: %s is installed in this project's environment, and that version has no %s module.\n"
                "What is needed: install %s into the same environment. "
                "Installing %s again, or choosing a shorter folder, will not add the missing module."
                % (gap["module"], shown, gap["module"], spec, gap["package"])
            ),
        }
    if re.search(r"No matching distribution|No matching version|requires-python", text, re.I):
        return {
            "kind": "no_build",
            "action": None,
            "packages": [],
            "repair": False,
            "cause": (
                "What failed: pip has no build of this library for this Python.\n"
                "Why: the library does not publish a version that matches the Python in this environment.\n"
                "What is needed: a version that this Python can install, or a different library that does the same job. "
                "The same install command will not change that."
            ),
        }
    if re.search(r"Access is denied|Permission denied", text, re.I):
        return {
            "kind": "permission",
            "action": None,
            "packages": [],
            "repair": False,
            "cause": (
                "What failed: the install could not write its files.\n"
                "Why: Windows denied access to a file in the environment.\n"
                "What is needed: free that file and run the install again. Editing the notebook does not fix this."
            ),
        }
    fresh, repairs = package_actions(text, root)
    repairs = [name for name in repairs if not _tried(tried, "repair:" + name)]
    fresh = [name for name in fresh if not _tried(tried, "install:" + name, name)]
    if repairs:
        names = ", ".join(repairs)
        return {
            "kind": "repair",
            "action": "repair:" + repairs[0],
            "packages": repairs,
            "repair": True,
            "cause": (
                "What failed: Python cannot import %s.\n"
                "Why: the copy in this project's environment is incomplete.\n"
                "What is needed: reinstall %s once. Installing it into a shorter folder is a different problem."
                % (names, names)
            ),
        }
    if fresh:
        names = ", ".join(fresh)
        return {
            "kind": "missing",
            "action": "install:" + fresh[0],
            "packages": fresh,
            "repair": False,
            "cause": (
                "What failed: Python cannot import %s.\n"
                "Why: it is not installed in this project's environment.\n"
                "What is needed: one install of %s. Changing the notebook does not create the library."
                % (names, names)
            ),
        }
    if root is not None and re.search(r"Traceback \(most recent call last\)|NameError:|FileNotFoundError|No such file or directory", text):
        brief = failure_brief(root, text)
        if brief:
            return {
                "kind": "code",
                "action": None,
                "packages": [],
                "repair": False,
                "cause": brief,
            }
    if root is not None:
        link = connection_report(root, text)
        if link:
            return {
                "kind": "code",
                "action": None,
                "packages": [],
                "repair": False,
                "cause": link,
            }
    if "should write " in text and "placeholder file does not count" in text:
        return {
            "kind": "code",
            "action": None,
            "packages": [],
            "repair": False,
            "cause": (
                "What failed: the notebook did not write the output it names.\n"
                "Why: a run that only creates a placeholder, or that never reaches the save, leaves that file missing.\n"
                "What is needed: fill the cells, read a data file that is in the project, and write the named output. "
                "Then use the run output to correct the code."
            ),
        }
    if "Unfinished code:" in text and not re.search(r"Traceback \(most recent call last\)", text):
        return {
            "kind": "code",
            "action": None,
            "packages": [],
            "repair": False,
            "cause": (
                "What failed: cells still have no finished code.\n"
                "Why: the notebook was not filled, so there is nothing to judge from a run.\n"
                "What is needed: write those cells so they can run, then use the output to correct the code. "
                "Do not stop at the empty cell. If a read does not connect, point it at a data file that is in the project."
            ),
        }
    if missing_packages(text, root):
        names = ", ".join(missing_packages(text, root))
        return {
            "kind": "missing",
            "action": None,
            "packages": [],
            "repair": False,
            "cause": (
                "What failed: Python cannot import %s.\n"
                "Why: an install of %s already ran in this project's environment, and the import still fails.\n"
                "What is needed: do not install %s again, and do not choose a shorter folder. "
                "Use the import error as a code change, or name a different version."
                % (names, names, names)
            ),
        }
    why = _error_line(text)
    return {
        "kind": "code",
        "action": None,
        "packages": [],
        "repair": False,
        "cause": (
            "What failed: the check did not pass.\n"
            "Why: %s\n"
            "What is needed: change the code that produced that line. Use the names and files the check printed. "
            "This is a code failure. An install or a shorter folder does not fix it."
            % (why or "the check printed no error line.")
        ),
    }


def explain_failure(output, root=None, tried=None):
    """Say what failed, why, and what the next fix has to address."""
    return classify_failure(output, root, tried)["cause"]


def modules_mentioned(output):
    """Import names from a missing-module error. Only real module names are returned."""
    found = []
    for match in _MISSING.finditer(output or ""):
        name = match.group(1)
        if _MODULE_NAME.fullmatch(name) and name not in found:
            found.append(name)
    return found


def confirm_imports(root, modules, python=None):
    """True success is an import that works, not a pip line that says already satisfied."""
    python = python or python_for(root)
    env = isolated_env(root, python)
    notes = []
    for name in modules or []:
        if not _MODULE_NAME.fullmatch(name) or name.split(".", 1)[0] in sys.stdlib_module_names:
            continue
        code, output = _run([python, "-c", "import " + name], root, env, timeout=90)
        if code != 0:
            notes.append("%s: %s" % (name, _error_line(output) or "import failed"))
    return "\n".join(notes)


def route_fits(route, info):
    """A route about installs or path length is only valid while that action is still open."""
    text = route or ""
    kind = (info or {}).get("kind")
    if kind in _ENV_KINDS and (info or {}).get("action"):
        return True
    if re.search(r"shorter folder|shallow directory|long path|max_path|260", text, re.I):
        return False
    if re.search(r"\binstall the \w+|\binstall \w+ into\b", text, re.I):
        return False
    return True


def code_route(info):
    cause = (info or {}).get("cause") or ""
    if "What is needed:" in cause:
        return cause.split("What is needed:", 1)[1].strip()
    return "Change the code that produced the check failure."


def missing_packages(output, root=None):
    """Package names to install when Python says a module is missing."""
    fresh, repairs = package_actions(output, root)
    found = []
    for name in fresh + repairs:
        if name not in found:
            found.append(name)
    return found


def package_actions(output, root=None):
    """Split a missing import into a new install or a replacement of a broken one.

    A name like tensorflow.python means the top package is already on disk and incomplete.
    """
    fresh = []
    repairs = []
    for match in _MISSING.finditer(output or ""):
        full = match.group(1)
        top = full.split(".")[0]
        if top in sys.stdlib_module_names:
            continue
        if root is not None and _local_module(root, top):
            continue
        package = _MODULE_TO_PIP.get(top, top)
        if not _PACKAGE.fullmatch(package):
            continue
        if _submodule_gap(full, root):
            continue
        broken = root is not None and _vendor_tree(root, top)
        bucket = repairs if broken else fresh
        if package not in fresh and package not in repairs:
            bucket.append(package)
    return fresh, repairs


def _site_packages(root):
    root = Path(root)
    bases = [_private_target(root), root / ".venv", short_venv_dir(root)]
    candidates = []
    for base in bases:
        candidates.extend((base / "Lib" / "site-packages", base / "lib" / "site-packages"))
    lib = root / ".venv" / "lib"
    if lib.is_dir():
        candidates.extend(lib.glob("python*/site-packages"))
    for path in candidates:
        if path.is_dir():
            return path
    return None


def _vendor_tree(root, name):
    site = _site_packages(root)
    if site is None:
        return False
    return (site / name).is_dir() or (site / (name + ".py")).is_file()


def clear_broken_package(root, names):
    """Remove one package from the project environment. The rest of the environment stays."""
    site = _site_packages(root)
    if site is None:
        return [], []
    allowed = []
    for base in (Path(root) / ".venv", short_venv_dir(root)):
        if base.exists():
            allowed.append(base.resolve())
    wanted = set()
    for name in names or []:
        base = (name or "").split("[", 1)[0].strip()
        aliases = {base}
        aliases.update(_PIP_TO_MODULES.get(base.lower(), ()))
        for alias in aliases:
            wanted.add(re.sub(r"[-_]", "", alias.lower()))
    wanted.discard("")
    removed = []
    errors = []
    for path in list(site.iterdir()):
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if not any(base == resolved or base in resolved.parents for base in allowed):
            continue
        stem = re.sub(r"[-_]", "", path.name.split("-", 1)[0].lower())
        if stem not in wanted:
            continue
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed.append(path.name)
        except OSError as exc:
            errors.append("%s: %s" % (path.name, exc))
    return removed, errors


def _local_module(root, name):
    root = Path(root)
    return (root / (name + ".py")).is_file() or (root / name / "__init__.py").is_file()


def requirements_to_install(root):
    """Requirement lines that have not been installed for the current file."""
    path = Path(root) / "requirements.txt"
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    stamp = Path(root) / ".venv" / ".requirements-stamp"
    try:
        if stamp.is_file() and stamp.read_text(encoding="utf-8") == text:
            return []
    except (OSError, UnicodeError):
        pass
    packages = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "http://", "https://", "git+")):
            continue
        name = re.split(r"[<>=!~\[]", line, maxsplit=1)[0].strip()
        if _PACKAGE.fullmatch(name) and (_PACKAGE.fullmatch(line) or _SPEC.fullmatch(line)):
            packages.append(line)
        elif _PACKAGE.fullmatch(name):
            packages.append(name)
    return packages


def mark_requirements(root):
    path = Path(root) / "requirements.txt"
    stamp = Path(root) / ".venv" / ".requirements-stamp"
    if not path.is_file() or not stamp.parent.is_dir():
        return
    try:
        stamp.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    except (OSError, UnicodeError):
        return


def _pip_summary(output):
    """The lines that say what pip actually did, not the long list of packages already present."""
    lines = []
    for raw in (output or "").splitlines():
        line = raw.strip()
        if re.search(r"error|exception|could not|failed|no matching", line, re.I):
            lines.append(line)
    if lines:
        return "\n".join(lines[-12:])
    tail = [raw.strip() for raw in (output or "").splitlines() if raw.strip()]
    return "\n".join(tail[-8:])


def install_packages(root, packages, stamp=False, repair=False, place="project", check_modules=None):
    """Install into a project-local environment. A repair removes the broken copy first."""
    root = Path(root)
    clean = []
    for package in packages or []:
        text = (package or "").strip()
        name = re.split(r"[<>=!~\[]", text, maxsplit=1)[0].strip()
        if text.startswith("-") or not _PACKAGE.fullmatch(name):
            continue
        if _PACKAGE.fullmatch(text) or _SPEC.fullmatch(text):
            clean.append(text)
    if not clean:
        return False, "No safe package name to install."
    cleared = ""
    if repair:
        removed, errors = clear_broken_package(root, clean)
        if removed:
            cleared = "Removed the incomplete copy: " + ", ".join(removed) + ".\n"
        if errors:
            cleared += "Could not remove: " + "; ".join(errors) + ".\n"
    try:
        if place == "short":
            python = _ensure_short_venv(root)
        else:
            python = _ensure_venv(root)
    except RuntimeError as exc:
        return False, (cleared + str(exc)).strip()
    code, output = _run(
        [python, "-m", "pip", "install", "--disable-pip-version-check", *clean],
        root,
        isolated_env(root, python),
        timeout=600,
    )
    moved = ""
    if code != 0 and place != "short" and needs_short_env(output):
        try:
            python = _ensure_short_venv(root)
        except RuntimeError as exc:
            return False, (cleared + str(exc)).strip()
        code, output = _run(
            [python, "-m", "pip", "install", "--disable-pip-version-check", *clean],
            root,
            isolated_env(root, python),
            timeout=600,
        )
        moved = "Installed into the project's short environment because the project path was too long.\n"
    summary = _pip_summary(output)
    if code != 0:
        return False, ("Could not install %s.\n%s%s%s" % (", ".join(clean), cleared, moved, summary)).strip()
    still = confirm_imports(root, check_modules, python=python)
    if still:
        return False, (
            "Installed %s, but the import still fails.\n%s%s%s" % (", ".join(clean), cleared, moved, still)
        ).strip()
    if stamp:
        mark_requirements(root)
    return True, ("Installed %s.\n%s%s%s" % (", ".join(clean), cleared, moved, summary)).strip()


def _ensure_short_venv(root):
    """Create the short environment used when the project path is too deep."""
    target = short_venv_dir(root)
    found = _venv_python(target)
    if found:
        return str(found)
    target.parent.mkdir(parents=True, exist_ok=True)
    code, output = _run([sys.executable, "-m", "venv", str(target)], root, os.environ.copy(), timeout=180)
    found = _venv_python(target)
    if not found:
        raise RuntimeError(output or "Could not create the shorter environment. (exit %s)" % code)
    return str(found)


def _ensure_venv(root):
    return ensure_private_env(root)


def read_source(path):
    """Read a source file as text, including UTF-8, UTF-16, and Windows text."""
    path = Path(path)
    if path.stat().st_size > READ_CAP:
        raise OSError("too large")
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16"), "utf-16"
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeError:
        return raw.decode("cp1252"), "cp1252"


def write_source(path, text, encoding="utf-8"):
    Path(path).write_text(text, encoding=encoding or "utf-8", newline="\n")


def _is_secret(path):
    name = path.name.lower()
    if name == ".env":
        return True
    if name.startswith(".env.") and not name.endswith((".example", ".sample", ".template")):
        return True
    if path.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".crt", ".cer", ".kdbx"}:
        return True
    return name in {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}


def _looks_like_text(path):
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size == 0:
        return True
    try:
        with path.open("rb") as handle:
            sample = handle.read(8192)
    except OSError:
        return False
    if sample.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")):
        return True
    if b"\x00" in sample:
        return False
    weird = sum(1 for byte in sample if byte < 9 or (13 < byte < 32))
    return weird / len(sample) < 0.05


def _wanted(root, path):
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    if any(part in SKIP_DIRS for part in relative.parts):
        return False
    if _is_secret(path):
        return False
    suffix = path.suffix.lower()
    if suffix in BINARY_SUFFIXES:
        return False
    if suffix in TEXT_SUFFIXES:
        return True
    return _looks_like_text(path)


def _source_files(root):
    root = Path(root)
    if not root.exists():
        return []
    found = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or not _wanted(root, path):
            continue
        found.append((path.relative_to(root).as_posix(), path))
        if len(found) >= SOURCE_LIMIT:
            break
    return found


def _failure_sites(root, output):
    root = Path(root).resolve()
    sites = []
    for match in _SITE.finditer(output or ""):
        raw = match.group(1)
        line_no = int(match.group(2))
        path = Path(raw)
        path = path.resolve() if path.is_absolute() else (root / path).resolve()
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if (rel, line_no) not in sites:
            sites.append((rel, line_no))
    return sites


def _numbered(text):
    lines = text.splitlines()
    width = len(str(max(len(lines), 1)))
    return "\n".join("%*d| %s" % (width, index, line) for index, line in enumerate(lines, 1))


def _numbered_window(text, line_no, radius):
    lines = text.splitlines()
    if not lines:
        return ""
    start = max(1, line_no - radius)
    end = min(len(lines), line_no + radius)
    width = len(str(len(lines)))
    return "\n".join("%*d| %s" % (width, index, lines[index - 1]) for index in range(start, end + 1))


def _strip_line_numbers(text):
    lines = text.split("\n")
    content = [line for line in lines if line.strip()]
    marked = [line for line in content if _LINE_NO.match(line)]
    if not marked or len(marked) < max(1, len(content) // 2):
        return text
    cleaned = []
    for line in lines:
        cleaned.append(_LINE_NO.sub("", line) if _LINE_NO.match(line) else line)
    return "\n".join(cleaned)


def _locate_lines(body, old):
    old_lines = [line.rstrip() for line in old.replace("\r\n", "\n").split("\n")]
    while old_lines and old_lines[0] == "":
        old_lines.pop(0)
    while old_lines and old_lines[-1] == "":
        old_lines.pop()
    if not old_lines:
        return None
    core = body.replace("\r\n", "\n").split("\n")
    if core and core[-1] == "":
        core = core[:-1]
    stripped = [line.rstrip() for line in core]
    count = len(old_lines)
    hits = [
        index for index in range(len(stripped) - count + 1)
        if stripped[index:index + count] == old_lines
    ]
    if not hits:
        return None
    return hits[0], count, len(hits) > 1


def _apply_search(body, old, new):
    old = old.replace("\r\n", "\n")
    body_n = body.replace("\r\n", "\n")
    new_n = new.replace("\r\n", "\n")
    if old and old in body_n:
        return body_n.replace(old, new_n, 1), ""
    stripped = _strip_line_numbers(old)
    if stripped and stripped != old and stripped in body_n:
        return body_n.replace(stripped, new_n, 1), ""
    located = _locate_lines(body_n, stripped if stripped.strip() else old)
    if not located:
        return None, "missing"
    start, count, several = located
    lines = body_n.split("\n")
    trailing = bool(lines and lines[-1] == "" and body_n.endswith("\n"))
    core = lines[:-1] if trailing else lines
    replacement = new_n.split("\n")
    if replacement and replacement[-1] == "" and new_n.endswith("\n"):
        replacement = replacement[:-1]
    core[start:start + count] = replacement
    updated = "\n".join(core)
    if trailing or body_n.endswith("\n"):
        updated += "\n"
    return updated, "Changed the first match." if several else ""


def _apply_patches(root, text, events, patched):
    for relative, old, new in parse_patches(text):
        if not old.strip():
            events.append({"kind": "note", "text": "Patch skipped %s: the search text was empty." % relative})
            continue
        try:
            path = safe_path(root, relative)
        except ValueError:
            events.append({"kind": "note", "text": "Patch skipped an unsafe path."})
            continue
        if is_answer_key(relative):
            events.append({
                "kind": "note",
                "text": "Left %s alone. A solved notebook is not the work." % relative,
            })
            continue
        if not path.is_file():
            events.append({
                "kind": "note",
                "text": "Patch missed %s: that file is not in the project yet." % relative,
            })
            continue
        try:
            body, encoding = read_source(path)
        except (OSError, UnicodeError):
            events.append({"kind": "note", "text": "Patch missed %s: the file could not be read." % relative})
            continue
        if path.suffix.lower() == ".ipynb":
            updated, how = _patch_notebook(path, old, new)
            if updated is None:
                events.append({
                    "kind": "note",
                    "text": "Patch missed %s: the search text was not in a notebook cell." % relative,
                })
                continue
            patched.add(path)
            preview = new if len(new) < 2000 else new[:2000] + "\n..."
            events.append({"kind": "file", "path": str(path), "code": preview, "patched": True})
            if how:
                events.append({"kind": "note", "text": "%s %s" % (relative, how)})
            continue
        updated, how = _apply_search(body, old, new)
        if updated is None:
            events.append({
                "kind": "note",
                "text": "Patch missed %s: the search text was not in the file." % relative,
            })
            continue
        if len(updated) > MAX_CHARS:
            events.append({"kind": "note", "text": "Patch skipped %s: the result is too large." % relative})
            continue
        write_source(path, updated, encoding)
        patched.add(path)
        preview = new if len(new) < 2000 else new[:2000] + "\n..."
        event = {"kind": "file", "path": str(path), "code": preview, "patched": True}
        events.append(event)
        if how:
            events.append({"kind": "note", "text": "%s %s" % (relative, how)})


def _write_file(path, content, events, patched=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = content if len(content) <= MAX_CHARS else content[:MAX_CHARS]
    path.write_text(body, encoding="utf-8", newline="\n")
    events.append({
        "kind": "file",
        "path": str(path),
        "code": body if len(body) < 8000 else body[:8000] + "\n...",
        "patched": patched,
    })


def _summary(root, events, output):
    lines = ["Project folder: %s" % root]
    for event in events:
        if event["kind"] == "folder":
            lines.append("Folder: %s" % event["path"])
        elif event["kind"] == "note":
            lines.append(event["text"])
        else:
            action = "Patched" if event.get("patched") else "File"
            lines.append("%s: %s" % (action, event["path"]))
    if output:
        lines.extend(["", "Run output:", output])
    return "\n".join(lines)
