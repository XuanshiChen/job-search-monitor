$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VirtualPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$PythonCommand = if (Test-Path -LiteralPath $VirtualPython) { $VirtualPython } else { "python" }

Push-Location $ProjectRoot
try {
    & $PythonCommand "main.py" "scan"
    if ($LASTEXITCODE -ne 0) {
        throw "Job scan failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
