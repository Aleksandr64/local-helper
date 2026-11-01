#!/usr/bin/env bash
# Наскрізна перевірка lh з Git Bash: незручні шляхи, тули .py/.ps1/.sh, add, -help, запуск, workdir.
#
# Те саме, що tests/smoke/smoke.ps1, але з боку bash: lh викликається bash-обгорткою `lh` з кореня
# репозиторію. Створює тимчасову папку з підпапками, у назвах яких пробіли, кирилиця, [дужки],
# апостроф, &, %, %PATH%, !, $, ^, `, ;, кома й емодзі; у кожну кладе три тули (py, ps1, sh),
# підключає їх як smoke_* із закріпленою робочою папкою і перевіряє -help і запуск. Шляхи для
# `lh add` чергуються: POSIX (/tmp/...) і Windows (C:\...). Потім перевіряє `lh workdir`.
# Наприкінці відключає всі smoke_* тули й видаляє тимчасову папку.
#
# Параметри:
#   --keep   не видаляти тимчасову папку
#
# Приклад:
#   bash tests/smoke/smoke.sh
set -uo pipefail

keep=0
[[ "${1-}" == "--keep" ]] && keep=1

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LH="$repo/lh"
py="$repo/.venv/Scripts/python.exe"
[[ -x "$py" ]] || py=python

passed=0
failed=0
added=()
out=""
code=0

run_lh() {  # результат — у $out і $code
    out="$("$LH" "$@" 2>&1)"
    code=$?
}

ok() { passed=$((passed + 1)); printf '  \e[32mok\e[0m    %s\n' "$1"; }
fail() {
    failed=$((failed + 1))
    printf '  \e[31mFAIL\e[0m  %s\n' "$1"
    [[ -n "${2-}" ]] && printf '%s\n' "$2" | sed 's/^/        /'
    return 0
}
expect() {  # expect <назва> <команда-умова...>
    local name=$1
    shift
    if "$@"; then ok "$name"; else fail "$name" "$out"; fi
}

field() { printf '%s\n' "$out" | sed -n "s/^$1=//p" | head -n 1 | tr -d '\r'; }

norm() {  # C:/x/, /c/x і C:\X — одна папка
    local p
    p="$(cygpath -w "$1" 2>/dev/null || printf '%s' "$1")"
    p="${p//\//\\}"
    p="${p%\\}"
    printf '%s' "${p,,}"
}

same_dir() { [[ "$(norm "$1")" == "$(norm "$2")" ]]; }
contains() { [[ "$out" == *"$1"* ]]; }

base="$(mktemp -d "/tmp/lh smoke sh XXXXXX")"
suffix="$(printf '%s' "${base##* }" | tr 'A-Z' 'a-z')"

cleanup() {
    cd /
    for t in "${added[@]}"; do "$LH" remove "$t" >/dev/null 2>&1; done
    if "$LH" list 2>/dev/null | grep -q "smoke_.*_$suffix"; then
        echo "Не вдалося відключити smoke-тули з суфіксом $suffix" >&2
        failed=$((failed + 1))
    fi
    if [[ $keep == 1 ]]; then echo "Папку лишено: $base"; else rm -rf "$base"; fi
    if [[ $failed -gt 0 ]]; then c=31; else c=32; fi
    printf '\n\e[%smПройдено: %s, провалено: %s\e[0m\n' "$c" "$passed" "$failed"
    [[ $failed -eq 0 ]]
}
trap cleanup EXIT
cd "$base" || exit 1
echo "lh: $LH"
echo "Тимчасова папка: $base ($(cygpath -w "$base"))"
echo

has_pwsh="$("$py" -c "import sys; sys.path.insert(0, sys.argv[1]); import local_helper as lh; print(lh.find_pwsh() or '')" "$(cygpath -w "$repo")")"
kinds=(py sh)
if [[ -n "$has_pwsh" ]]; then kinds=(py ps1 sh); else echo "PowerShell не знайдено — .ps1-тули пропускаю"; fi

write_probes() {
    cat > "$1/probe.py" <<'EOF'
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
EOF
    { printf '\xEF\xBB\xBF'; cat <<'EOF'
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
EOF
    } > "$1/probe.ps1"
    cat > "$1/probe.sh" <<'EOF'
#!/usr/bin/env bash
# Smoke-тул на Bash: друкує папку запуску й аргумент.
#
# Параметри: [текст] [прапорець]
echo "CWD=$(pwd -W)"
echo "ARG=${1-}"
echo "FLAG=${2-}"
EOF
}

names=("з пробілом" "Кирилиця ґєії" "brackets [1]" "apos'trophe" "amp & ersand" "pct 100%" "env %PATH%"
       "bang!" 'dollar $HOME' "caret ^ up" 'back`tick' "semi;colon, comma (1)" "emoji 📁")

for i in "${!names[@]}"; do
    dir="$base/${names[$i]}"
    mkdir -p "$dir"
    write_probes "$dir"
    wdir="$(cygpath -w "$dir")"
    arg="$wdir\\файл з пробілом.txt"
    # шлях для add/--workdir: парні — POSIX (/tmp/...), непарні — Windows (C:\...)
    if (( i % 2 == 0 )); then form="$dir"; else form="$wdir"; fi
    echo "[${names[$i]}]"

    for kind in "${kinds[@]}"; do
        tool="smoke_${kind}_${i}_$suffix"
        run_lh add "$form/probe.$kind" --name "$tool" --workdir "$form"
        expect "$kind add" test "$code" -eq 0
        [[ $code -eq 0 ]] || continue
        added+=("$tool")

        run_lh "$tool" -help
        expect "$kind -help" eval '[[ $code -eq 0 ]] && contains "Smoke-тул" && contains "прапорець"'

        case $kind in
            py | sh) run_lh "$tool" "$arg" --flag ;;
            ps1) run_lh "$tool" -Text "$arg" -Flag ;;
        esac
        cwd="$(field CWD)"
        got="$(field ARG)"
        flag="$(field FLAG)"
        expect "$kind запуск у закріпленій папці" eval '[[ $code -eq 0 ]] && same_dir "$cwd" "$dir"'
        if [[ "$got" == "$arg" ]]; then ok "$kind аргумент-шлях дійшов без змін"
        else fail "$kind аргумент-шлях дійшов без змін" "очікував: $arg"$'\n'"отримав:  $got"; fi
        expect "$kind прапорець" eval '[[ "$flag" == True || "$flag" == --flag ]]'
    done
done

echo
echo "[lh workdir]"
tool="smoke_py_0_$suffix"
cwd_of_run() { run_lh "$tool"; field CWD; }

run_lh list
expect "list показує тул з [папка]" eval 'contains "$tool" && contains "[папка]"'

run_lh workdir "$tool" --clear
expect "--clear: тул запускається в поточній папці" same_dir "$(cwd_of_run)" "$base"

cd "$base/${names[0]}" && run_lh workdir "$tool" . && cd "$base"
expect "workdir . — поточна папка" same_dir "$(cwd_of_run)" "$base/${names[0]}"

run_lh workdir "$tool" "${names[2]}"
expect "відносний шлях (з [дужками])" same_dir "$(cwd_of_run)" "$base/${names[2]}"

run_lh workdir "$tool" "$base/${names[1]}/"
expect "POSIX-шлях з кінцевим /" same_dir "$(cwd_of_run)" "$base/${names[1]}"

run_lh workdir "$tool" "$(cygpath -w "$base/${names[3]}")\\"
expect "Windows-шлях з кінцевим \\" same_dir "$(cwd_of_run)" "$base/${names[3]}"

run_lh workdir "$tool" "$base/${names[11]}"
expect "шлях з ; (MSYS не плутає його зі списком шляхів)" same_dir "$(cwd_of_run)" "$base/${names[11]}"

run_lh workdir "$tool" "$base/нема такої"
expect "неіснуюча папка — помилка, код 1" eval '[[ $code -eq 1 ]] && contains "Папку не знайдено"'

mkdir "$base/зникне"
run_lh workdir "$tool" "$base/зникне"
rmdir "$base/зникне"
run_lh "$tool"
expect "зникла закріплена папка — зрозуміла помилка" eval '[[ $code -eq 1 ]] && contains "--clear"'
