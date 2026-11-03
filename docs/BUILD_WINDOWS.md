# Building a Windows package

## Prerequisites

- Windows 10/11
- Python 3.11 or newer
- the project's dependencies installed in `.venv`
- PyInstaller installed in the same environment

## Build

Run the following from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install PyInstaller

.\.venv\Scripts\python.exe -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --noconsole `
    --name "HLS Key Control Utility" `
    --icon "src\hls_key_control\assets\hls-key-control-icon.ico" `
    --paths src `
    --add-data "src\hls_key_control\assets;assets" `
    --collect-all gurux_dlms `
    --collect-all gurux_serial `
    --hidden-import pyodbc `
    src\hls_key_control\main.py
```

The executable is created in:

```text
dist/HLS Key Control Utility/
```

For a diagnostic build, keep `--console`. Replace it with `--noconsole` only after the target environment has been validated.

## Configuration

The build intentionally does not include a production `.env`. Create a local file next to the executable:

```powershell
Copy-Item .env.example '.\dist\HLS Key Control Utility\.env'
```

Fill in approved SQL Server and HLS security settings. Do not include this file in a release archive intended for public distribution.

## Packaging

Archive the complete `HLS Key Control Utility` directory. The executable requires the adjacent `_internal` directory.
