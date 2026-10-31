# Додає цю папку в PATH користувача, щоб команди `local_helper` і `lh` працювали з будь-якого місця.
# Запуск (один раз):  powershell -ExecutionPolicy Bypass -File .\install.ps1

$dir = $PSScriptRoot
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$entries = @($userPath -split ';' | Where-Object { $_ -ne '' })

if ($entries | Where-Object { $_.TrimEnd('\') -ieq $dir.TrimEnd('\') }) {
    Write-Host "Папка вже в PATH: $dir"
} else {
    $newPath = (@($entries) + $dir) -join ';'
    [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
    Write-Host "Додано в PATH користувача: $dir"
    Write-Host "Перевідкрий термінал і запусти: local_helper list"
}
