param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 2147483647)]
    [int]$ProjectId,
    [string]$Questions = "C:\Users\imada\Desktop\Q.md",
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [string]$Account,
    [string]$CredentialsFile = "$PSScriptRoot\..\test_files\qa_credentials.local.json",
    [switch]$DryRun
)

$batchArguments = @(
    "--questions", $Questions,
    "--skip-upload",
    "--project-id", "$ProjectId",
    "--base-url", $BaseUrl
)
if ($Account) { $batchArguments += @("--account", $Account) }
if (Test-Path -LiteralPath $CredentialsFile) {
    $batchArguments += @("--credentials-file", $CredentialsFile)
}
if ($DryRun) { $batchArguments += "--dry-run" }

& "$PSScriptRoot\..\agent\Scripts\python.exe" -X utf8 -B "$PSScriptRoot\batch_qa.py" @batchArguments
exit $LASTEXITCODE
