# TractoSphere v0.2.1-alpha — Dataset QC document

This prerelease adds one QC document for the processing steps actually saved for
a dataset. Choose **Quality Control → Create dataset QC document…**, select one
participant's processing folder and save a new HTML file. The toolbox verifies
the saved evidence before exporting and opens the document in your browser.

The document shows each performed stage's recorded status and findings, selected
images and graphs, native before/after previews when a verified comparison is
available, measurements, processing history, short logs, exact-bound local
review declarations and source hashes.
Pending, skipped or unknown plan stages are identified separately. Complete
all-slice reports and large logs stay in their original attempt folders.

See [the illustrated QC procedure](user-guide/qc-runs.md#create-one-qc-document-for-the-steps-you-ran)
for each click, folder layout and review guidance. The command-line equivalent is
`dmri-studio combined-qc --folder PATH --output NEW-FILE.html`.

This is a review aid. It does not rerun processing, resolve an automated QC
failure or grant scientific approval. The earlier v0.2.0-alpha package remains
available on GitHub for reproducibility.
