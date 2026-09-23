# HLS Key Control Utility

Внутреннее приложение для персонализации HLS-паролей счётчиков и ведения их реестра.

## Первый запуск

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m hls_key_control.main
```

На первом этапе приложение запускает окно. Подключение SQL и оптопорта будет добавляться по отдельным шагам.
