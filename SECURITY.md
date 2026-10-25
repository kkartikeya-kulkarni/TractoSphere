# Security policy

## Supported version

v0.2.0-alpha receives best-effort security fixes during active development.
It is not approved for clinical use or handling identifiable clinical data without
an independently appropriate institutional environment and safeguards.

## Report a vulnerability

Use GitHub's private vulnerability reporting on this repository when enabled:
https://github.com/kkartikeya-kulkarni/TractoSphere/security/advisories/new

If that option is unavailable, open a public issue **only requesting a private
reporting channel**, without exploit details, secrets or research data. Do not
publish passwords, tokens, identifiable MRI images or restricted subject records.
There is no guaranteed response-time SLA for this alpha.

A useful report describes the affected version, OS, prerequisites, impact and
minimal synthetic/public reproducer. Sanitized logs must remove local user paths,
account identifiers, private acquisition IDs and sensitive metadata.

## Research-data handling

All published examples use permitted public or synthetic data. Keep raw data and
processing outputs out of Git. .gitignore is a safeguard, not proof of permission
or a backup. Do not attach restricted datasets to Issues/Discussions. The active
application has no AI authentication/inference service. Scientific processing
runs through configured local tools and requires explicit plan approval.
