[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $DiscloudArgs
)

$ErrorActionPreference = "Stop"

$nodeDir = "C:\Program Files\nodejs"
$npmDir = Join-Path $env:APPDATA "npm"
$discloudCmd = Join-Path $npmDir "discloud.cmd"

if (Test-Path $nodeDir) {
    $env:Path = "$nodeDir;$npmDir;$env:Path"
}

if (-not (Test-Path $discloudCmd)) {
    $discloudCmd = "discloud"
}

# Login must run through the real CLI so it can create/update ~/.discloud/.cli.
if ($DiscloudArgs.Count -gt 0 -and $DiscloudArgs[0] -eq "login") {
    & $discloudCmd @DiscloudArgs
    exit $LASTEXITCODE
}

$configPath = Join-Path $env:USERPROFILE ".discloud\.cli"
if (-not (Test-Path $configPath)) {
    Write-Error "Discloud is not logged in. Run: .\discloud.ps1 login"
    exit 1
}

$rawConfig = (Get-Content -Raw -LiteralPath $configPath).Trim()
if (-not $rawConfig) {
    Write-Error "Discloud config is empty. Run: .\discloud.ps1 login"
    exit 1
}

$base64 = $rawConfig.Replace("-", "+").Replace("_", "/")
switch ($base64.Length % 4) {
    0 { }
    2 { $base64 += "==" }
    3 { $base64 += "=" }
    default {
        Write-Error "Discloud config format is not valid base64."
        exit 1
    }
}

try {
    $decoded = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($base64))
    $config = $decoded | ConvertFrom-Json
} catch {
    Write-Error "Could not decode Discloud config. Run: .\discloud.ps1 login"
    exit 1
}

$token = [string] $config.token
if (-not $token -or (($token -split "\.").Count -ne 3)) {
    Write-Error "Discloud config does not contain a valid token. Run: .\discloud.ps1 login"
    exit 1
}

$hadToken = Test-Path Env:\DISCLOUD_TOKEN
$previousToken = $env:DISCLOUD_TOKEN

try {
    $env:DISCLOUD_TOKEN = $token
    & $discloudCmd @DiscloudArgs
    $exitCode = $LASTEXITCODE
} finally {
    if ($hadToken) {
        $env:DISCLOUD_TOKEN = $previousToken
    } else {
        Remove-Item Env:\DISCLOUD_TOKEN -ErrorAction SilentlyContinue
    }
}

exit $exitCode
