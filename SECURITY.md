# Security policy

## Supported version

Only Ver3 is supported. Ver1 and Ver2 are not published or maintained in this repository.

## Reporting a vulnerability

Do not disclose credentials, tokens, private host information, or exploitable details in a public issue. Use GitHub's private vulnerability reporting feature for this repository. If that feature is unavailable, open a public issue containing only a non-sensitive summary and request a private contact channel.

## Operational boundary

The repository contains templates, not production configuration. Operators must manage API keys, device tokens, database credentials, session secrets, external access controls, system paths, and backups outside Git.

If a credential is committed accidentally, removing it from the latest revision is insufficient. Revoke or rotate it immediately and remove it from repository history as appropriate.
