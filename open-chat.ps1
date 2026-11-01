# Open the local chat page. Starts the model server if it is not already running.
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "env.ps1")
Start-LocalOllama

$ui = "http://127.0.0.1:8787/"
$python = "C:\Users\zirot\anaconda3\python.exe"
if (-not (Test-Path $python)) {
    $python = (Get-Command python -ErrorAction Stop).Source
}

$up = $false
& curl.exe -s -o NUL --max-time 1 $ui 2>$null
if ($LASTEXITCODE -eq 0) { $up = $true }

if (-not $up) {
    $scriptPath = Join-Path $PSScriptRoot "chat-ui.py"
    $commandLine = "`"$python`" `"$scriptPath`""
    $created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $commandLine }
    if ($created.ReturnValue -ne 0) {
        throw "Could not start the chat page (code $($created.ReturnValue))"
    }
    $ready = $false
    for ($i = 0; $i -lt 20; $i++) {
        & curl.exe -s -o NUL --max-time 1 $ui 2>$null
        if ($LASTEXITCODE -eq 0) {
            $ready = $true
            break
        }
        Start-Sleep -Milliseconds 300
    }
    if (-not $ready) {
        throw "The chat page did not start at $ui"
    }
}

Start-Process $ui
Write-Host "Chat is open at $ui"
