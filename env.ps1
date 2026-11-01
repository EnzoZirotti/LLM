# Shared settings for the local model environment in this folder.
$ErrorActionPreference = "Stop"

$script:Root = $PSScriptRoot
$script:ModelsDir = Join-Path $script:Root "models"
$script:OllamaHost = "127.0.0.1:11434"
$script:ModelName = "qwen3.6:27b"

$ollamaExe = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
if (-not (Test-Path $ollamaExe)) {
    $found = Get-Command ollama -ErrorAction SilentlyContinue
    if ($found) { $ollamaExe = $found.Source }
}
if (-not (Test-Path $ollamaExe)) {
    throw "Ollama is not installed. Expected $ollamaExe"
}
$script:OllamaExe = $ollamaExe

New-Item -ItemType Directory -Force -Path $script:ModelsDir | Out-Null

# Persist for later logons. Write the registry directly so this does not
# wait on a Windows settings broadcast.
Set-ItemProperty -Path "HKCU:\Environment" -Name "OLLAMA_MODELS" -Value $script:ModelsDir
Set-ItemProperty -Path "HKCU:\Environment" -Name "OLLAMA_HOST" -Value $script:OllamaHost
Set-ItemProperty -Path "HKCU:\Environment" -Name "OLLAMA_NO_CLOUD" -Value "1"

$env:OLLAMA_MODELS = $script:ModelsDir
$env:OLLAMA_HOST = $script:OllamaHost
$env:OLLAMA_NO_CLOUD = "1"
$env:Path = "$(Split-Path $script:OllamaExe);$env:Path"

function Test-OllamaPort {
    & curl.exe -s -o NUL --max-time 1 "http://127.0.0.1:11434/api/version" 2>$null
    return $LASTEXITCODE -eq 0
}

function Start-LocalOllama {
    param([switch]$Restart)

    if (-not $Restart -and (Test-OllamaPort)) {
        return
    }

    # Stop a server that may have started with the default model folder.
    Get-Process -Name "ollama", "ollama app" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1

    # Start outside this shell so the server keeps running after the script exits.
    $serveScript = Join-Path $script:Root "serve.ps1"
    $powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $commandLine = "`"$powershell`" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$serveScript`""
    $created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $commandLine }
    if ($created.ReturnValue -ne 0) {
        throw "Failed to start Ollama (code $($created.ReturnValue))"
    }

    $ready = $false
    for ($i = 0; $i -lt 40; $i++) {
        if (Test-OllamaPort) {
            $ready = $true
            break
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) {
        throw "Ollama did not start on http://$script:OllamaHost"
    }
}
