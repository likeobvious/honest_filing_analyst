# Synthetic PDF viewer fixtures

These PDFs are generated test data, not company filings and not valid financial evidence.

Generate the fixtures with:

```powershell
python .\src\ui\test-fixtures\generate_fixtures.py
```

Then serve `src/ui/` from the repository root and open:

```text
http://localhost:8765/test-fixtures/viewer_test.html
```

The test page checks citation click handling, `#page=N` selection, previous/next navigation, PDF switching, quote display, and text-layer highlighting.
Append `?run=1` to run the automated browser checks and display a PASS/FAIL report in the page.
