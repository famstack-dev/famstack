# family-docs

A generator for a demo set of family documents, set in the Simpsons
world. It renders realistic PDFs and images (pay slips, invoices,
contracts, receipts, birth certificates, a kid's crayon drawing) that
you can feed into a famstack instance to populate Paperless and the
memory wiki for screenshots, screencasts, and end-to-end testing.

The **specs are the source of truth** (plain YAML, one file per
document). The rendered files are disposable artifacts you regenerate.

## Quick start

```sh
# render every English document into out/en/
uv run --extra demo python tools/family-docs/generate.py

# list the set without rendering
uv run --extra demo python tools/family-docs/generate.py --list

# render a subset (substring match on the spec name)
uv run --extra demo python tools/family-docs/generate.py --only receipt
uv run --extra demo python tools/family-docs/generate.py --only car-insurance
```

Output lands in `out/<locale>/` (gitignored). Filing those documents
into a running stack is a separate step (they are uploaded into the
Matrix `#documents` room so the archivist files them the same way a
family member would).

## How it works

```
specs/en/*.yaml   →   generate.py   →   renderers.py   →   out/en/*.{pdf,png}
   (you edit)          (discovery)       (reportlab /        (disposable)
                                          Pillow)
```

`generate.py` reads each spec, dispatches it to a renderer based on the
spec's `render` field, and writes the result. `renderers.py` is the only
module that touches reportlab and Pillow, so adding a new output format
is a new function there, not a change to every spec.

Dependencies live in the `demo` extra in `pyproject.toml`
(`reportlab`, `Pillow`, `pyyaml`). They are host-side tooling only and
are **not** needed to run the stack.

## Adding a document

Drop a new `specs/en/<name>.yaml`. Re-run the generator. That's it.

Every spec has a `render` field that selects the look, an optional
`filename` (defaults to `<name>.pdf`), and an optional `expected` block
that documents the classification you'd expect the archivist to produce
(it is also handy as future eval ground truth; it is not rendered).

### `render: pdf` — clean native-text business document

One block-based layout expresses letters, invoices, pay slips,
statements, report cards, contracts, prescriptions, and recipes. The
text layer is real, so the classifier reads actual text.

```yaml
render: pdf
filename: example.pdf
accent: "#1b5e20"           # brand colour for the letterhead + table header
letterhead:
  name: Issuer Name
  lines: [Street, "City, ST 00000"]
recipient:                  # optional
  name: Homer J. Simpson
  lines: ["742 Evergreen Terrace", "Springfield, OR 97401"]
title: Document Title
meta:                       # optional key/value box, top right
  Reference: ABC-123
  Date: 01 Jan 2026
blocks:                     # the body, rendered in order
  - p: "A paragraph of text."
  - heading: A Section Heading
  - bullets: ["first point", "second point"]
  - table:
      columns: [Description, Amount]
      align: [L, R]         # optional; default first col L, rest R
      rows:
        - ["Line item", "10.00"]
      total: ["Total", "10.00"]   # optional bold total row
  - signature: {name: Jane Doe, role: Registrar}
  - spacer: 12              # optional vertical gap in points
footer: "Small print at the bottom."
```

The PDF layout paginates automatically, so a long contract (many
`heading` + `p` blocks) flows onto multiple pages.

### `render: receipt` — narrow thermal receipt (image)

```yaml
render: receipt
filename: shop-receipt.png
store: STORE NAME
lines: ["Address line", "Cashier: ..."]
date: "02 Apr 2026  18:42"
items:
  - ["Item name", "1.49"]   # price is the line total
total: "1.49"
paid: "Cash"                # "$" is added only to numeric values
footer: "Thank you!"
```

### `render: certificate` — aged, scanned-looking certificate (image)

For the older generation's documents (sepia paper, serif title,
typewriter form fields, inked seal, cursive signature).

```yaml
render: certificate
filename: birth-certificate.png
authority: "Springfield County, State of Oregon, Office of Vital Records"
title: "Certificate of Birth"
register_no: "1956-00417"
seal_text: "VITAL RECORDS"
fields:                     # rendered as labelled, underlined form rows
  Name of Child: "Homer Jay Simpson"
  Date of Birth: "May 12, 1956"
registrar: "C. Montgomery"  # rendered in a cursive hand
```

### `render: drawing` — a child's crayon drawing (image)

Mostly visual (exercises the vision path); only the title and caption
carry text.

```yaml
render: drawing
filename: kid-drawing.png
title: MY FAMLY
caption: "by Bart Simpson, age 10"
```

## Adding a language

The German-locale set would live in `specs/de/`. Create the folder,
translate the spec content (the renderers are language-agnostic), and
render with `--locale de`. Keep the same filenames across locales so the
two sets stay easy to compare.

## Notes

- Fonts are loaded from stock macOS faces with a fallback to Pillow's
  default, so the image renderers degrade to "plain" rather than
  crashing if a face is missing.
- Amounts are checked for arithmetic: line items should sum to their
  stated total. Keep them consistent when editing.
- Keep written content free of em dashes, to match the project's style.
