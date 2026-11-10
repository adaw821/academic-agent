param([string]$KeyPath = (Join-Path $env:LOCALAPPDATA 'AcademicAgentSecrets/school.key'))
$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$target = [IO.Path]::GetFullPath($KeyPath)
if ($target.StartsWith($repository + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Encryption key must be outside the repository.'
}
$folder = [IO.Path]::GetDirectoryName($target)
New-Item -ItemType Directory -Path $folder -Force | Out-Null
if (-not (Test-Path -LiteralPath $target)) {
    # Instance GetBytes works on Windows PowerShell 5.1 (.NET Framework) and PowerShell 7.
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $stream = [IO.File]::Open($target, [IO.FileMode]::CreateNew)
    try {
        $encoded = [Text.Encoding]::ASCII.GetBytes([Convert]::ToBase64String($bytes))
        $stream.Write($encoded, 0, $encoded.Length)
    } finally { $stream.Dispose() }
}
if ([Convert]::FromBase64String([IO.File]::ReadAllText($target).Trim()).Length -ne 32) {
    throw 'Existing key is invalid; not overwriting.'
}
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$acl = [Security.AccessControl.FileSecurity]::new()
$acl.SetAccessRuleProtection($true, $false)
$acl.SetOwner($sid)
$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', 'Allow'))
Set-Acl -LiteralPath $target -AclObject $acl
$envFile = Join-Path $repository '.env'
$existing = if (Test-Path -LiteralPath $envFile) { [IO.File]::ReadAllText($envFile) } else { '' }
if ($existing -match '(?m)^\s*SCHOOL_KEY_FILE\s*=\s*\S+') {
    Write-Output 'A key setting already exists; it was left unchanged.'
} else {
    $existing = [regex]::Replace($existing, '(?m)^\s*SCHOOL_KEY_FILE\s*=\s*$', '')
    [IO.File]::WriteAllText($envFile, $existing.TrimEnd() + "`nSCHOOL_KEY_FILE=" + $target.Replace('\', '/') + "`n", [Text.UTF8Encoding]::new($false))
    Write-Output 'Configured a private encryption key outside the repository. Restart the backend.'
}
