# Installs the Python packages for fixture_gen.py and pdf_inspect.py into the user's Python.
# Usage: powershell -ExecutionPolicy Bypass -File .\install_requirements.ps1 [-Python <path to python.exe>]
param(
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$requirements = Join-Path $PSScriptRoot "requirements.txt"

function Find-Python {
    foreach ($candidate in @("python", "py")) {
        if (Get-Command $candidate -ErrorAction SilentlyContinue) {
            return $candidate
        }
    }
    throw "Python not found. Install Python 3.10 or newer, or pass -Python <path to python.exe>."
}

if ($Python -eq "") {
    $Python = Find-Python
}

$version = & $Python -W ignore -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$version -lt [version]"3.10") {
    throw "Python $version found, 3.10 or newer is required."
}
Write-Host "Using Python $version ($Python)"

# pip writes notices to stderr. Windows PowerShell 5.1 turns those into errors under "Stop",
# so native calls run under "Continue" and are checked by their exit code.
$ErrorActionPreference = "Continue"
& $Python -m pip install --user --disable-pip-version-check -r $requirements
if ($LASTEXITCODE -ne 0) {
    throw "pip install failed with exit code $LASTEXITCODE."
}

& $Python -c "import numpy, cv2, pymupdf; print('OK: numpy', numpy.__version__, '| opencv', cv2.__version__, '| pymupdf', pymupdf.VersionBind)"
if ($LASTEXITCODE -ne 0) {
    throw "Import check failed."
}
