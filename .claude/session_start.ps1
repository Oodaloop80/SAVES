# SessionStart hook — print the current ROADMAP phase and its open checklist items.
#
# WHY THIS IS A FILE AND NOT AN INLINE COMMAND (Bora, 2026-08-07):
#   This logic used to live as a 603-character one-liner inside .claude/settings.json. It
#   carried 6 backslash-escaped quotes, a backtick (`n), and a literal em-dash. Passed through
#   a shell it lost its quoting and failed with `exit 255 / "The system cannot find the path
#   specified"` - reproduced from the correct working directory, so this was NOT a cwd problem.
#   The em-dash also arrived as mojibake.
#
#   The failure surfaces only as an opaque "SessionStart hook error" at launch, which says
#   nothing about the cause. A script file has no escaping layer at all and can be run
#   directly to test:  powershell -NoProfile -ExecutionPolicy Bypass -File .claude\session_start.ps1
#
#   Measured, same invocation, same directory:  old = exit 255, stderr set
#                                               new = exit 0, phase printed
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
