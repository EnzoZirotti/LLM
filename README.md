# Local chat

A chat page and a small agent team that run on this PC. Both talk to [Ollama](https://ollama.com/) at `127.0.0.1:11434`. The pages are served at `http://127.0.0.1:8787/` and are not reachable from other machines.

The model is `qwen3.6:27b`. Its weights stay in the `models` folder next to these scripts.

## Requirements

- Windows
- [Ollama](https://ollama.com/) installed
- Python 3 on PATH (or the Anaconda install named in `open-chat.ps1`)
- The model pulled once into this folder:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\env.ps1
ollama pull qwen3.6:27b
```

`env.ps1` points `OLLAMA_MODELS` at `models\` and keeps the Ollama server on localhost.

## Open the chat page

Double-click **Open Chat.cmd**, or run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\open-chat.ps1
```

That starts Ollama if it is not already running, starts `chat-ui.py` if the page is down, and opens the browser. **Local Chat.exe** does the same thing. Rebuild it from `LocalChat.cs` if the exe is missing; the exe is not stored in this repo.

The model process stops after 10 minutes with no chat or swarm work. The page stays up. **Stop model** turns it off sooner.

For a terminal session instead of the page:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\chat.ps1
```

## Chat

The home page is one conversation with the model. **New chat** starts another. Saved chats stay in `chats\` on this PC. A later message can draw on an earlier chat when that chat was pinned or shares the words in the prompt.

## Swarm

Open **Swarm** from the chat page, or go to `http://127.0.0.1:8787/swarm.html`.

One model plays several roles. A council (Mira, Sol, and Nia by default) talks through a route. You can rename the team. When **Build files in a project folder** is on, builders write files in the folder you choose and testers run checks against them.

- **Safe** waits for you to pick a file list, or to write your own, before the build continues.
- **YOLO** keeps going and only stops to ask when the same failure repeats.

Lessons from work on this PC are stored in `memory\` and shown on the swarm page. They are not model weights.

## Train an adapter

`train_code.py` can train a small adapter from code edits that already passed checks. Running it with no flags only reports whether training can start. It does not download a model.

```powershell
python train_code.py
python train_code.py --run
```

A run needs at least 8 saved edits in `memory\code_edits.jsonl`, an original model folder with `config.json` at `models\base` (or `CODE_BASE_MODEL`), and `torch`, `transformers`, `peft`, and `bitsandbytes`. The Ollama GGUF is left as it is.

## What stays on this machine

These paths are listed in `.gitignore` and are not in the repository:

| Path | What it holds |
| --- | --- |
| `models\` | Model weights |
| `chats\` | Saved conversations |
| `memory\` | Lessons, code notes, and adapters |
| `projects\` | Folders the swarm builds |
| `workspace\` | Swarm working files |
| `venvs\` | Local Python environments |
