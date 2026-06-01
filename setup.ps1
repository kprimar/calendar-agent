# Windows setup script for calendar-agent
# Run once from the project root: .\setup.ps1

Write-Host "=== Calendar Agent Setup ===" -ForegroundColor Cyan

# 1. Create virtual environment if it doesn't exist
if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Yellow
    python -m venv .venv
} else {
    Write-Host "Virtual environment already exists." -ForegroundColor Green
}

# 2. Activate and install dependencies
Write-Host "Installing dependencies..." -ForegroundColor Yellow
.\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
.\.venv\Scripts\pip.exe install -r requirements.txt --quiet

Write-Host ""
Write-Host "Setup complete!" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Place your credentials.json in the credentials\ folder"
Write-Host "  2. Activate the venv:  .\.venv\Scripts\Activate.ps1"
Write-Host "  3. Test authentication: python auth\google_auth.py"
