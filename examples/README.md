# Example Reports

Published DeltaTrack reports, rendered from bills in the committed corpus. Open any
`.html` file in a browser, or start at `index.html`. No install required.

These are also the project's public demo, deployed to GitHub Pages from this directory:
<https://civictechdc.github.io/DeltaTrack/>.

## Files

Everything here is generated, by one script:

```bash
uv run python scripts/render_examples.py
```

| File | What it shows |
| --- | --- |
| `index.html` | Landing page. Generated from `EXAMPLES_TO_RENDER`, so it can't list a report that isn't published, or omit one that is. |
| `hr8752_xml_diff.html` | HR 8752 committee vs. floor, via the bill-XML pipeline. |
| `hr8752_pdf_diff.html` | The same pair via the PDF pipeline — the two together show the pipelines agree. |
| `hr4366_committee_vs_floor_xml_diff.html` | HR 4366 (MilCon/VA, FY2024) committee-reported vs. floor-passed. |
| `hr4366_house_vs_senate_xml_diff.html` | HR 4366 after the Senate rewrite. The large-diff case. |

Don't edit these by hand, and don't hand-write a report into this directory. Every
report goes through the same pipeline the web app and CLI use, so what a reader sees
here is what DeltaTrack would produce for them.
`tests/test_committed_examples.py` enforces both halves of that: it fails if a committed
report is stale, and if a committed report is one the script doesn't produce.

## Adding an example

Append an `ExampleSpec` to `EXAMPLES_TO_RENDER` in
[`render_examples.py`](../scripts/render_examples.py) — the bill directory, the two version
stems, which pipelines to render it through, and its landing-page blurb. Re-run the
script and commit the result; the landing page updates itself.

## Generating your own

```bash
uv run python diff_bill.py compare \
  tests/corpus/118-hr-4366/1_reported-in-house.xml \
  tests/corpus/118-hr-4366/2_engrossed-in-house.xml \
  --format html -o reports/my_report.html
```

See the [main README](../README.md) for full usage instructions.
