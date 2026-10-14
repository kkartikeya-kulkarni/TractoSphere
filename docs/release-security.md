# Public distribution security gate

TractoSphere is a research-only public-alpha candidate. A clean working tree is
not proof that an existing GitHub repository is safe to make public.

Before publication, inspect every local/remote branch, tag, archived experiment,
accessible pull-request reference, release asset and CI artifact. Scan all Git
blobs (including unreachable local objects), commit metadata and current files
with a redacting secret scanner. Review screenshots and third-party provenance.
Do not print credentials or include private audit evidence in public source.

This public repository is a separately audited source snapshot with fresh Git
history. Private development history, research arrays, participant-level outputs
and archived experimental branches are excluded. The owner authorized publication
after artifact and installed-wheel verification. Future releases must repeat the
applicable checks; a clean working tree alone is insufficient.

## Repeatable content check

Run from a Git checkout before distributing source or archives:

```sh
python scripts/audit_release_contents.py --source . --artifacts dist
```

The standard-library checker reads archives without extracting them. It rejects
scientific arrays, private runtime state, unsafe archive paths, links, bundled
executables, common credential patterns and unapproved binary assets. Archive
size, nesting and file-count limits fail closed. Approved images, vector artwork
and fonts are recorded by exact SHA-256, source and license in
`release-assets.json`. Updating that registry requires provenance and visual
review; it is not a way to bypass a failed check.

The guard complements full-history Gitleaks and manual review. It cannot establish
ownership, identify every possible secret, guarantee screenshot deidentification,
or discover inaccessible server-side objects. CI runs the guard on built wheel
and source distributions; private CI remains disabled to avoid paid execution.

## Distribution boundaries

Do not distribute research acquisitions, participant-level validation outputs,
private configuration, environments, FSL executables or unreviewed archived AI
branches. External scientific software is installed separately. See
`THIRD_PARTY_NOTICES.md` for source, asset and dependency licensing.

Preserve original research files and a verified private Git backup before any
approved history remediation. Collaborators must avoid reintroducing old history
by merging or pushing private branches into a sanitized repository. Never use
`git push --all` or `--mirror` for public release preparation.

Independent physical-Mac installation remains pending and is disclosed in the alpha validation record.
