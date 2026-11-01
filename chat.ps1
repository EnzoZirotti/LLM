# Start a local chat. Models live in this folder. The API stays on this PC only.
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "env.ps1")
Start-LocalOllama
& $script:OllamaExe run "qwen3.6:27b"
