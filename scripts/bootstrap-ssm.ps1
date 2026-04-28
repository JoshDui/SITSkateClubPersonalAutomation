# Bootstrap SSM parameters for the skate bot.
#
# Prompts securely for the bot token (SecureString — no echo, no history),
# generates a fresh webhook secret, and populates all 5 /skatebot/prod/*
# SSM parameters in one shot.
#
# Usage:
#   $env:AWS_PROFILE = "skatebot"
#   .\scripts\bootstrap-ssm.ps1
#
# Override defaults:
#   .\scripts\bootstrap-ssm.ps1 -AdminIds "111,222" -GroupChatId "-1001234567890"

param(
    [string]$AdminIds = "844816317",
    [string]$GroupChatId = "-1001593198353",
    [string]$PowerAutomateUrl = "https://example.com/placeholder-power-automate-url",
    [string]$SsmPrefix = "/skatebot/prod"
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "== SSM Bootstrap for skate bot ==" -ForegroundColor Cyan
Write-Host "  AWS_PROFILE : $($env:AWS_PROFILE)"
Write-Host "  SSM prefix  : $SsmPrefix"
Write-Host "  Admin IDs   : $AdminIds"
Write-Host "  Group chat  : $GroupChatId"
Write-Host ""

# Sanity check — is AWS CLI reachable?
$identity = aws sts get-caller-identity --output text --query Arn 2>$null
if (-not $identity) {
    Write-Error "AWS CLI not authenticated. Run: `$env:AWS_PROFILE = 'skatebot' then retry."
    exit 1
}
Write-Host "  AWS identity: $identity" -ForegroundColor DarkGray
Write-Host ""

# ── Prompt for bot token (SecureString — doesn't echo, doesn't hit history) ─
Write-Host "Paste the bot token from BotFather (input will be hidden):" -ForegroundColor Yellow
$tokenSecure = Read-Host -AsSecureString
$bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($tokenSecure)
try {
    $botToken = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
}
finally {
    [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}

if ([string]::IsNullOrWhiteSpace($botToken)) {
    Write-Error "Empty token. Aborting."
    exit 1
}
if ($botToken -notmatch '^\d+:[A-Za-z0-9_-]+$') {
    Write-Warning "Token doesn't match the expected '123456789:ABC...' format. Continuing anyway."
}

# ── Generate a fresh webhook secret ─────────────────────────────────────────
Write-Host ""
Write-Host "Generating webhook secret..." -NoNewline
$webhookSecret = python -c "import secrets; print(secrets.token_urlsafe(32))"
if (-not $webhookSecret) {
    Write-Error "Failed to generate webhook secret. Is Python on PATH?"
    exit 1
}
Write-Host " done" -ForegroundColor Green

# ── Push parameters ─────────────────────────────────────────────────────────
function Put-Ssm {
    param(
        [string]$Name,
        [string]$Value,
        [string]$Type
    )
    $fullName = "$SsmPrefix/$Name"
    Write-Host ("  {0,-40} ..." -f $fullName) -NoNewline
    aws ssm put-parameter --name $fullName --type $Type --value $Value --overwrite | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host " ok" -ForegroundColor Green
    } else {
        Write-Host " FAILED" -ForegroundColor Red
        exit 1
    }
}

Write-Host ""
Write-Host "Pushing to SSM:" -ForegroundColor Cyan
Put-Ssm -Name "bot_token"          -Value $botToken         -Type "SecureString"
Put-Ssm -Name "webhook_secret"     -Value $webhookSecret    -Type "SecureString"
Put-Ssm -Name "admin_ids"          -Value $AdminIds         -Type "String"
Put-Ssm -Name "group_chat_id"      -Value $GroupChatId      -Type "String"
Put-Ssm -Name "power_automate_url" -Value $PowerAutomateUrl -Type "SecureString"

Write-Host ""
Write-Host "All parameters set." -ForegroundColor Green
Write-Host "Next step: python scripts\setwebhook.py" -ForegroundColor Yellow
