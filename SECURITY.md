# Security policy

## Sensitive data

Do not publish or commit any of the following:

- `.env` files;
- `HLS_ENCRYPTION_KEY` or `HLS_FINGERPRINT_SECRET` values;
- SQL Server credentials, host names, or internal network addresses;
- real HLS passwords;
- production exports, daily logs, DLMS packet captures, or customer data.

The repository contains `.env.example` only. It is a template and must not contain real values.

## Reporting a vulnerability

Do not publish a security issue with working credentials, secrets, HLS values, or an exploit path.

Use a private communication channel with the maintainer and provide a minimal, sanitized reproduction. The issue will be assessed before any public disclosure.

## Deployment guidance

- Store `.env` outside source control and share it only through an approved secure channel.
- Use Windows integrated authentication or a dedicated least-privilege SQL account.
- Back up the SQL Server registry and protect the HLS encryption and fingerprint secrets.
- Do not reuse sample, test, or retired HLS values as production configuration.
