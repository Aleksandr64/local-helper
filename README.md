# local_helper

Особисті консольні тули на Python і лаунчер `local_helper` (коротко `lh`). Він запускає тули
з командного рядка або в повноекранному інтерфейсі в терміналі, а форму параметрів будує сам
з argparse кожного тулу.

**Повний посібник — [GUIDE.md](GUIDE.md):** встановлення, користування, як створювати тули
(вручну і через Claude), контракт тулу, підключення тулів з інших папок, вирішення проблем.

## Швидкий старт

```powershell
python "D:\Dev\Diferrent Tools\local_helper.py" setup                          # .venv + залежності
powershell -ExecutionPolicy Bypass -File "D:\Dev\Diferrent Tools\install.ps1"  # додати в PATH
```
Перевідкрий термінал, далі:
```powershell
lh                          # графічний інтерфейс
lh list                     # список тулів
lh photos_to_pdf -help      # що робить тул і які параметри приймає
lh photos_to_pdf "D:\Scans\Паспорт" --dry-run
lh new rename_photos        # створити новий тул
lh add "D:\Scripts\my_tool" # підключити тул з іншої папки
```

## Тули

| Тул | Що робить |
|---|---|
| `photos_to_pdf` | збирає фото з папки в один PDF — за нумерацією в назвах або за датою зйомки |
| `count_lines` | рахує файли й рядки в папці за розширеннями |

Актуальний список завжди показує `lh list`.
