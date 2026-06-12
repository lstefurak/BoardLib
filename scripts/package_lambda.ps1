param(
    [string]$OutputPath = "build/boardlog-lambda.zip",
    [string]$PythonRuntime = "3.13"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$buildRoot = Join-Path $repoRoot "build/lambda-package"
$zipPath = if ([System.IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath
} else {
    Join-Path $repoRoot $OutputPath
}
$zipDir = Split-Path $zipPath -Parent

if (Test-Path $buildRoot) {
    Remove-Item -LiteralPath $buildRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $buildRoot | Out-Null
New-Item -ItemType Directory -Path $zipDir -Force | Out-Null

python -m pip install `
    --requirement (Join-Path $repoRoot "backend/requirements.txt") `
    --target $buildRoot `
    --platform manylinux2014_x86_64 `
    --implementation cp `
    --python-version $PythonRuntime `
    --only-binary=:all: `
    --upgrade

# $ErrorActionPreference does not apply to native executables; without this
# check a failed pip install would still produce (and report) a broken zip.
if ($LASTEXITCODE -ne 0) {
    throw "pip install failed with exit code $LASTEXITCODE; not building the package."
}

Copy-Item -Path (Join-Path $repoRoot "src/boardlib") -Destination (Join-Path $buildRoot "boardlib") -Recurse
New-Item -ItemType Directory -Path (Join-Path $buildRoot "backend") | Out-Null
Copy-Item -Path (Join-Path $repoRoot "backend/boardlog_lambda") -Destination (Join-Path $buildRoot "backend/boardlog_lambda") -Recurse
New-Item -ItemType File -Path (Join-Path $buildRoot "backend/__init__.py") | Out-Null

if (Test-Path $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}

Compress-Archive -Path (Join-Path $buildRoot "*") -DestinationPath $zipPath -Force
Write-Host "Wrote $zipPath"
