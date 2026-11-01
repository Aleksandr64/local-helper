<#
.SYNOPSIS
    Наскрізна перевірка lh з «незручними» шляхами: тули .py, .ps1 і .sh, add, -help, запуск, workdir.

.DESCRIPTION
    Створює тимчасову папку (теж з пробілом), а в ній підпапки з пробілами, кирилицею, [дужками],
    апострофом, &, %, !, $, ^, ;, комою й емодзі в назвах. У кожну кладе три тули (py, ps1, sh), підключає
    їх через `lh add` під іменами smoke_* із закріпленою робочою папкою і перевіряє:
    опис у -help, запуск з аргументом-шляхом, що тул стартує саме в закріпленій папці.
    Потім перевіряє `lh workdir`: --clear, `.`, відносний шлях, шлях з кінцевим \, неіснуючу папку.
    Наприкінці відключає всі smoke_* тули й видаляє тимчасову папку.

    За замовчуванням lh викликається через обгортку lh.cmd — так, як із терміналу.
    Потрібен PowerShell 7 (pwsh). Тули .sh перевіряються, лише якщо знайдено Git Bash.

.PARAMETER Python
    викликати local_helper.py Python'ом з .venv напряму, без обгортки lh.cmd

.PARAMETER Keep
    не видаляти тимчасову папку (щоб подивитись на файли)

.EXAMPLE
    pwsh -File tests\smoke\smoke.ps1

.EXAMPLE
    pwsh -File tests\smoke\smoke.ps1 -Python -Keep
#>
param([switch]$Python, [switch]$Keep)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8

$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).ProviderPath
$venvPy = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPy)) { $venvPy = "python" }
$lhCmd = Join-Path $repo "lh.cmd"
$lhPy = Join-Path $repo "local_helper.py"

$script:passed = 0
$script:failed = 0
$script:added = [Collections.Generic.List[string]]::new()

function Invoke-Lh {
    # lh з довільними аргументами; повертає код виходу і весь вивід (stdout + stderr)
    $ErrorActionPreference = "Continue"
    if ($Python) { $out = & $venvPy $lhPy @args 2>&1 } else { $out = & $lhCmd @args 2>&1 }
    [pscustomobject]@{ Code = $LASTEXITCODE; Text = (@($out) | ForEach-Object { "$_" }) -join "`n" }
}

function Test-That([bool]$Ok, [string]$Name, [string]$Detail = "") {
    if ($Ok) {
        $script:passed++
        Write-Host "  ok    $Name" -ForegroundColor Green
    } else {
        $script:failed++
        Write-Host "  FAIL  $Name" -ForegroundColor Red
        if ($Detail) { Write-Host ($Detail -replace "(?m)^", "        ") -ForegroundColor DarkGray }
    }
}

function Get-Field([string]$Text, [string]$Key) {
    # значення рядка KEY=... з виводу тулу
    $line = $Text -split "`n" | Where-Object { $_.StartsWith("$Key=") } | Select-Object -First 1
    if ($null -eq $line) { return $null }
    $line.Substring($Key.Length + 1).TrimEnd("`r")
}

function Format-Dir([string]$Path) {
    # для порівняння: C:/x/ і c:\X — одна папка
    if ($null -eq $Path) { return "" }
    $Path.Replace("/", "\").TrimEnd("\").ToLowerInvariant()
}

function Write-Utf8([string]$Path, [string]$Text, [bool]$Bom = $false, [bool]$Lf = $false) {
    if ($Lf) { $Text = $Text.Replace("`r`n", "`n") } else { $Text = $Text.Replace("`r`n", "`n").Replace("`n", "`r`n") }
    [IO.File]::WriteAllText($Path, $Text, [Text.UTF8Encoding]::new($Bom))
}

$probePy = @'
"""Smoke-тул на Python: друкує папку запуску й аргумент."""
import argparse
from pathlib import Path

parser = argparse.ArgumentParser(prog="local_helper probe", description=__doc__)
parser.add_argument("text", nargs="?", default="", help="будь-який текст")
parser.add_argument("--flag", action="store_true", help="прапорець")
args = parser.parse_args()
print(f"CWD={Path.cwd()}")
print(f"ARG={args.text}")
print(f"FLAG={args.flag}")
'@

$probePs1 = @'
<#
.SYNOPSIS
    Smoke-тул на PowerShell: друкує папку запуску й аргумент.
.PARAMETER Text
    будь-який текст
.PARAMETER Flag
    прапорець
#>
param([string]$Text = "", [switch]$Flag)
[Console]::OutputEncoding = [Text.Encoding]::UTF8
"CWD=$((Get-Location).ProviderPath)"
"ARG=$Text"
"FLAG=$Flag"
'@

$probeSh = @'
#!/usr/bin/env bash
# Smoke-тул на Bash: друкує папку запуску й аргумент.
#
# Параметри: [текст] [прапорець]
echo "CWD=$(pwd -W)"
echo "ARG=${1-}"
echo "FLAG=${2-}"
'@

$hasBash = (& $venvPy -c "import sys; sys.path.insert(0, sys.argv[1]); import local_helper as lh; print(lh.find_bash() or '')" $repo).Trim()
$kinds = @("py", "ps1")
if ($hasBash) { $kinds += "sh" } else { Write-Host "Git Bash не знайдено — .sh-тули пропускаю" -ForegroundColor Yellow }

$names = @(
    "з пробілом",
    "Кирилиця ґєії",
    "brackets [1]",
    "apos'trophe",
    "amp & ersand",
    'pct 100%',
    "bang!",
    'dollar $HOME',
    "caret ^ up",
    "semi;colon, comma (1)",
    "emoji 📁"
)
# cmd.exe розгортає %ЗМІННА% в аргументах .cmd-файлів, тож через lh.cmd такий шлях не дійде
# (обмеження Windows, див. GUIDE.md, розділ 10); напряму через Python — має працювати.
if ($Python) { $names += 'env %PATH%' }

$suffix = [guid]::NewGuid().ToString("N").Substring(0, 6)
$base = Join-Path ([IO.Path]::GetTempPath()) "lh smoke $suffix"
[void][IO.Directory]::CreateDirectory($base)
Push-Location -LiteralPath $base
Write-Host "lh: $(if ($Python) { "$venvPy local_helper.py" } else { $lhCmd })"
Write-Host "Тимчасова папка: $base`n"

try {
    for ($i = 0; $i -lt $names.Count; $i++) {
        $dir = Join-Path $base $names[$i]
        [void][IO.Directory]::CreateDirectory($dir)
        Write-Utf8 (Join-Path $dir "probe.py") $probePy
        Write-Utf8 (Join-Path $dir "probe.ps1") $probePs1 -Bom $true
        Write-Utf8 (Join-Path $dir "probe.sh") $probeSh -Lf $true
        $argPath = Join-Path $dir "файл з пробілом.txt"
        Write-Host "[$($names[$i])]"

        foreach ($kind in $kinds) {
            $tool = "smoke_${kind}_${i}_$suffix"
            $r = Invoke-Lh add (Join-Path $dir "probe.$kind") --name $tool --workdir $dir
            Test-That ($r.Code -eq 0) "$kind add" $r.Text
            if ($r.Code -ne 0) { continue }
            $script:added.Add($tool)

            $r = Invoke-Lh $tool -help
            $helpOk = $r.Code -eq 0 -and $r.Text.Contains("Smoke-тул") -and $r.Text.Contains("прапорець")
            Test-That $helpOk "$kind -help" $r.Text

            $runArgs = switch ($kind) {
                "py" { @($argPath, "--flag") }
                "ps1" { @("-Text", $argPath, "-Flag") }
                "sh" { @($argPath, "--flag") }
            }
            $r = Invoke-Lh $tool @runArgs
            $cwd = Get-Field $r.Text "CWD"
            $got = Get-Field $r.Text "ARG"
            Test-That ($r.Code -eq 0 -and (Format-Dir $cwd) -eq (Format-Dir $dir)) "$kind запуск у закріпленій папці" `
                "очікував: $dir`nотримав:  $cwd`nкод: $($r.Code)`n$($r.Text)"
            Test-That ($got -eq $argPath) "$kind аргумент-шлях дійшов без змін" "очікував: $argPath`nотримав:  $got"
            Test-That ((Get-Field $r.Text "FLAG") -in @("True", "--flag")) "$kind прапорець" $r.Text
        }
    }

    Write-Host "`n[lh workdir]"
    $dir = Join-Path $base $names[0]
    $tool = "smoke_py_0_$suffix"
    $run = { Format-Dir (Get-Field (Invoke-Lh $tool).Text "CWD") }

    $r = Invoke-Lh list
    Test-That ($r.Text.Contains($tool) -and $r.Text.Contains("[папка]")) "list показує тул з [папка]" $r.Text

    $r = Invoke-Lh workdir $tool --clear
    Test-That ($r.Code -eq 0 -and (& $run) -eq (Format-Dir $base)) "--clear: тул запускається в поточній папці"

    Push-Location -LiteralPath $dir
    $r = Invoke-Lh workdir $tool .
    Pop-Location
    Test-That ($r.Code -eq 0 -and (& $run) -eq (Format-Dir $dir)) "workdir . — поточна папка" $r.Text

    $r = Invoke-Lh workdir $tool $names[2]
    Test-That ($r.Code -eq 0 -and (& $run) -eq (Format-Dir (Join-Path $base $names[2]))) "відносний шлях (з [дужками])" $r.Text

    $r = Invoke-Lh workdir $tool "$(Join-Path $base $names[1])\"
    Test-That ($r.Code -eq 0 -and (& $run) -eq (Format-Dir (Join-Path $base $names[1]))) "шлях з кінцевим \" $r.Text

    $r = Invoke-Lh workdir $tool ((Join-Path $base $names[3]).Replace("\", "/"))
    Test-That ($r.Code -eq 0 -and (& $run) -eq (Format-Dir (Join-Path $base $names[3]))) "шлях з / замість \" $r.Text

    $r = Invoke-Lh workdir $tool
    Test-That ($r.Code -eq 0 -and $r.Text.Contains($names[3])) "workdir без папки показує поточну" $r.Text

    $r = Invoke-Lh workdir $tool (Join-Path $base "нема такої")
    Test-That ($r.Code -eq 1 -and $r.Text.Contains("Папку не знайдено")) "неіснуюча папка — помилка, код 1" $r.Text

    $gone = Join-Path $base "зникне"
    [void][IO.Directory]::CreateDirectory($gone)
    [void](Invoke-Lh workdir $tool $gone)
    [IO.Directory]::Delete($gone)
    $r = Invoke-Lh $tool
    Test-That ($r.Code -eq 1 -and $r.Text.Contains("--clear")) "зникла закріплена папка — зрозуміла помилка" $r.Text
}
finally {
    Pop-Location
    foreach ($tool in $script:added) { [void](Invoke-Lh remove $tool) }
    $left = (Invoke-Lh list).Text -split "`n" | Where-Object { $_ -match "smoke_\w+_$suffix" }
    if ($left) { Write-Host "Не вдалося відключити: $left" -ForegroundColor Red; $script:failed++ }
    if ($Keep) { Write-Host "`nПапку лишено: $base" } else { Remove-Item -LiteralPath $base -Recurse -Force }
}

Write-Host "`nПройдено: $script:passed, провалено: $script:failed" -ForegroundColor $(if ($script:failed) { "Red" } else { "Green" })
exit [int]($script:failed -gt 0)
