# Packages the project into documind-bundle.tgz for upload to the server.
#
#   powershell -ExecutionPolicy Bypass -File infrastructure\lightsail\make-bundle.ps1
#
# Includes everything git would track or see as new (so uncommitted work is included), and nothing git
# ignores: no .env, no node_modules, no virtual environments, no downloaded models. Text files are
# converted to Unix line endings, because the server runs Linux and shell scripts break with Windows ones.
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $root

$files = git ls-files -co --exclude-standard |
    Where-Object { $_ -and ($_ -notmatch '(^|/)\.env$') -and ($_ -notmatch '\.(pem|key|pfx)$') -and ($_ -notmatch '\.pdf$') }
if (-not $files) { throw "git listed no files. Run this inside the DocuMind repository." }

$binary = '\.(png|jpe?g|gif|ico|webp|woff2?|ttf|otf|docx|xlsx|xls|zip|gz|tgz|onnx|bin)$'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$stage = Join-Path ([IO.Path]::GetTempPath()) ("documind-bundle-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $stage | Out-Null
try {
    foreach ($f in $files) {
        $src = Join-Path $root $f
        if (-not (Test-Path -LiteralPath $src)) { continue }   # tracked but deleted in the working tree
        $dst = Join-Path $stage $f
        New-Item -ItemType Directory -Force -Path (Split-Path $dst) | Out-Null
        if ($f -match $binary) {
            Copy-Item -LiteralPath $src -Destination $dst
        } else {
            $text = [IO.File]::ReadAllText($src)
            [IO.File]::WriteAllText($dst, $text.Replace("`r`n", "`n"), $utf8)
        }
    }
    $out = Join-Path $root "documind-bundle.tgz"
    if (Test-Path $out) { Remove-Item $out }
    tar -czf $out -C $stage .
    if ($LASTEXITCODE -ne 0) { throw "tar failed" }
    $mb = [math]::Round((Get-Item $out).Length / 1MB, 1)
    Write-Host "Created $out ($mb MB, $(@($files).Count) files)"
    Write-Host ""
    Write-Host "Upload it (replace the key file and the server address):"
    Write-Host '  scp -i "$HOME\Downloads\LightsailDefaultKey-eu-west-1.pem" documind-bundle.tgz ubuntu@<SERVER-IP>:~/'
    Write-Host "Then on the server:  mkdir -p documind && tar -xzf documind-bundle.tgz -C documind"
} finally {
    Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
}
