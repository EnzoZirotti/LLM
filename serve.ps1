# Background server for this folder. Listens on localhost only.
$log = Join-Path $PSScriptRoot "ollama-serve.log"
. (Join-Path $PSScriptRoot "env.ps1")
# Ollama logs on stderr. Leave that stream alone or PowerShell will stop the server.
$ErrorActionPreference = "Continue"
"starting $($script:OllamaExe) models=$($script:ModelsDir) host=$($script:OllamaHost)" | Out-File -FilePath $log -Encoding utf8
& cmd.exe /c "`"$($script:OllamaExe)`" serve >> `"$log`" 2>&1"
