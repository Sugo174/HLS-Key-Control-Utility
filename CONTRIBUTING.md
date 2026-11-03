# Contributing

Thank you for improving HLS Key Control Utility.

## Before opening an issue or pull request

- Never include `.env`, credentials, encryption keys, HLS values, meter dumps, exported workbooks, production logs, or customer information.
- Describe the expected and actual behaviour without disclosing protected operational data.
- For a communication issue, provide a sanitized error message and the application version.

## Development setup

Follow the source setup instructions in [README.md](README.md). Use a local `.env` created from `.env.example`; do not add it to Git.

## Code changes

- Keep changes focused and preserve the existing HLS safety checks.
- Do not weaken confirmation, recovery, or state-transition safeguards just to make a device operation retry differently.
- Add or update SQL migrations for every database schema change.
- Run a syntax check before committing:

```powershell
python -m compileall -q src
```

## Pull requests

Use a clear Russian or English commit message. Explain the affected workflow, validation performed, and any migration or deployment requirement.
