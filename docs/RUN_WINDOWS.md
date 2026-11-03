# Running HLS Key Control Utility on Windows

## Before you start

The application requires:

- Windows 10 or Windows 11;
- access to the SQL Server hosting the HLS registry;
- Microsoft ODBC Driver 17 or 18 for SQL Server;
- a connected optical probe and its COM port;
- a `.env` file containing the connection settings and HLS keys supplied by an authorized administrator.

## First launch

1. Extract the archive to a permanent location, for example `C:\HLS-Key-Control-Utility`.
2. Copy `.env.example` and rename the copy to `.env`.
3. Fill in `.env` with the active SQL Server settings and HLS values.
4. Start `HLS Key Control Utility.exe`.
5. Optionally run `create_desktop_shortcut.cmd` to place an application shortcut on the desktop.

## Important notes

- Never share the `.env` file: it contains access settings and cryptographic keys.
- Do not move the `.exe` file separately from the `_internal` folder.
- Operation logs are stored locally in daily files. Excel exports are written to the selected folder.
- If a SQL Server connection fails, check server availability, the `.env` values, and that the ODBC driver is installed.

## Support

When contacting support, attach the error text, the daily log file, and the application version.