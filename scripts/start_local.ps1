# Desktop-only backend: keep both the frontend and backend bound to loopback.
$env:ISPACE_LOCAL_LOGIN = '1'
Set-Location (Split-Path -Parent $PSScriptRoot)
python -m uvicorn main:app --host 127.0.0.1 --port 8000
