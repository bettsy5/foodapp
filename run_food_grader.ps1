$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$LocalPython = "C:\Users\jcbse\AppData\Local\Programs\Python\Python312\python.exe"
$python = if (Test-Path $LocalPython) { $LocalPython } else { (Get-Command python -ErrorAction SilentlyContinue).Source }

if (-not $python -or $python -like "*WindowsApps*") {
    Write-Host "Python is not installed or only the Microsoft Store alias is available."
    Write-Host "Install Python 3.11+ first, then rerun this script."
    exit 1
}

& $python -m pip install -r requirements.txt
& $python -m streamlit run streamlit_app.py
