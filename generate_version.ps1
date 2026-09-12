param (
    [string]$OutputFile = "src/version.py",
    [string]$MetadataFile = "src/build-info.json"
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# The checked-in version is the next stable release; CI adds a sortable build number.
$content = Get-Content -LiteralPath $OutputFile -Raw
if ($content -notmatch 'VERSION\s*=\s*"(v\d+\.\d+\.\d+)') {
    throw "Cannot read the base version from $OutputFile."
}
$baseVersion = $Matches[1]
$commitHash = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) {
    throw 'Cannot determine the build commit.'
}
$shortHash = $commitHash.Substring(0, 8)
$version = "$baseVersion+local.$shortHash"
$channel = 'local'
$branch = [string](git branch --show-current)
$branch = $branch.Trim()
$repository = 'ccvrc/DG-LAB-VRCOSC'
$runId = 0L
$runNumber = 0L
$runAttempt = 0L

if ($env:GITHUB_ACTIONS -eq 'true') {
    $repository = $env:GITHUB_REPOSITORY
    $branch = $env:GITHUB_REF_NAME
    $runId = [long]$env:GITHUB_RUN_ID
    $runNumber = [long]$env:GITHUB_RUN_NUMBER
    $runAttempt = [long]$env:GITHUB_RUN_ATTEMPT
    $version = "$baseVersion.dev$runNumber"
    if ($env:GITHUB_EVENT_NAME -eq 'push' -and $env:GITHUB_REF_TYPE -eq 'tag') {
        if ($env:GITHUB_REF_NAME -notmatch '^v\d+\.\d+\.\d+$') {
            throw 'Stable release tags must have the form vMAJOR.MINOR.PATCH.'
        }
        if ($env:GITHUB_REF_NAME -ne $baseVersion) {
            throw "Release tag $($env:GITHUB_REF_NAME) does not match source version $baseVersion."
        }
        $version = $env:GITHUB_REF_NAME
        $channel = 'stable'
    } elseif ($env:GITHUB_EVENT_NAME -eq 'push' -and $env:GITHUB_REF -eq 'refs/heads/master') {
        $channel = 'actions'
    }
}

$metadata = [ordered]@{
    schema_version = 1
    channel = $channel
    version = $version
    commit = $commitHash
    run_id = $runId
    run_number = $runNumber
    run_attempt = $runAttempt
    repository = $repository
    workflow = 'build-python-app.yml'
    branch = $branch
}

# Use UTF-8 without a BOM for both Python source and JSON (also under Windows PowerShell).
$utf8 = [System.Text.UTF8Encoding]::new($false)
$outputPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputFile)
$metadataPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($MetadataFile)
[System.IO.File]::WriteAllText($outputPath, "VERSION = `"$version`"`n", $utf8)
[System.IO.File]::WriteAllText($metadataPath, ($metadata | ConvertTo-Json) + "`n", $utf8)
Write-Host "Generated $version ($channel), commit $shortHash."
