# Small Tools

A collection of small, single-purpose tools for everyday office and document work.

| Tool | What it does | Runs with |
|---|---|---|
| [`certificate-manager`](certificate-manager) | Ingest certificates (image, PDF or Word), detect and crop the certificate, and file it by name, category and year. Optional OCR pre-fill. | Python, Flask, PyMuPDF, OpenCV |
| [`pdf-colour-page-detector`](pdf-colour-page-detector) | Finds which pages of a PDF contain colour, so you can print colour and black-and-white pages separately and save on printing cost. | Python, PyMuPDF |
| [`word-to-pdf`](word-to-pdf) | Convert Word documents to PDF entirely in the browser. Nothing is uploaded. | Open the HTML file in a browser |
| [`store-manager`](store-manager) | Inventory and goods-receipt system for a small store: items, suppliers, purchase orders, GRN with QC, job-work and outward challans, printable documents. Data stays in the browser. | Open the HTML file in a browser |
| [`folder-backup-manager`](folder-backup-manager) | Back up folders with a manifest of what was copied, and skip files that have not changed. | Python (standard library, Tkinter) |

## Licence

GNU AGPL v3, because two of the tools use PyMuPDF, which is AGPL-licensed. See [LICENSE](LICENSE).
