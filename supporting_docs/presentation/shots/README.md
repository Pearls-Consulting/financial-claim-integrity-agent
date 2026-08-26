# Screenshots for the progress deck

Save screenshots here named by slide number, e.g. `03-claims-queue.png`,
`07-gate1-fraud.png`, `13-ar.png` + `13-en.png` (two files with the same
number go side by side). Then from `backend/`:

    .venv/Scripts/python scripts/build_screenshots_deck.py

The deck `../SDB-Invoicing-Solution-Progress.pptx` is regenerated; slides
with no screenshot keep a grey placeholder saying what to capture.
