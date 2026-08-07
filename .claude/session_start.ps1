# SessionStart hook — print the current ROADMAP phase and its open checklist items.
#
# WHY THIS IS A FILE AND NOT AN INLINE COMMAND (Bora, 2026-08-07):
#   This logic used to live as a one-liner inside .claude/settings.json. Embedding PowerShell
#   in a JSON string means every backslash and quote is escaped twice, and a single wrong
#   escape breaks the hook in a way that only shows up as an opaque "SessionStart hook error"
#   at launch. That happened during the C:\APPS\AI\SAVES move. A script file has no escaping
#   layer at all, and can be run directly to test it.
#
# Test with:  powershell -NoProfile -ExecutionPolicy Bypass -File .claude\session_start.ps1

$ErrorActionPreference = 'SilentlyContinue'

$roadmap = Join-Path $PSScriptRoot '..\docs\ROADMAP.md'

# Launching Claude Code outside the repo is not an error — just say nothing. Before this
# guard, Get-Content on a missing file wrote to stderr and the harness reported a hook
# failure, which is alarming and means nothing.
if (-not (Test-Path $roadmap)) { exit 0 }

$content = Get-Content $roadmap -Raw
if (-not $content) { exit 0 }

if ($content -notmatch '(?s)\*\*Current phase:\*\*\s*Phase\s+(\d+)') { exit 0 }
$phase = [int]$matches[1]

Write-Host ''
Write-Host "=== SAVES - Phase $phase ===" -ForegroundColor Cyan

# Pull the checklist items out of this phase's section, up to the next phase heading.
$section = "(?s)## Phase $phase.*?(?=## Phase $($phase + 1)|$)"
if ($content -match $section) {
    $items = $matches[0] -split "`n" | Where-Object { $_ -match '^\s*-\s*\[' }
    if ($items) {
        $items | Select-Object -First 10 | ForEach-Object {
            Write-Host ($_ -replace '^\s+', '  ')
        }
    }
}
Write-Host ''
exit 0
