"""Prompts for the contract-read experiment.

The target fields are exactly what the claim gates consume (contract header,
value, dates, kind, payment schedule, penalties, BoQ lines). Evidence is a
verbatim quote in the document's language plus the page it was read from.
"""

FIELDS = """\
Target fields (use these exact keys):
- contract_no, contract_title, contract_kind (goods | services | works | consultancy | framework | other)
- first_party (the client/owner), second_party (the contractor/vendor), vendor_vat_number, vendor_cr_number
- contract_value_base, vat_amount, contract_value_total, currency
- signature_date, start_date, end_date, duration (as written, e.g. "24 شهر")
- payment_terms (how and when payments are released), payment_schedule (milestones / percentages / periodic)
- advance_payment (percentage/amount and guarantee), retention (percentage and release terms)
- performance_bond (percentage and validity)
- delay_penalty (rate, basis, cap), other_penalties
- acceptance_procedure (what document proves delivery/completion: receipt, COC, handover report...)
- boq_lines: list of {item_no, description, unit, quantity, unit_price, total}
- price_schedule_notes (lump sum vs unit rates, escalation, provisional sums)
- amendments (variation orders, addenda, change in value/duration)
"""

BATCH_SYSTEM = """\
You are a contracts analyst reading pages of a Saudi public-sector / corporate contract
(Arabic, English or mixed). You will receive a batch of pages, each preceded by a
"PAGE <n>" marker. Pages may be scanned images or extracted text.

Extract every target field you can SEE on these pages. Do not infer values that are not
on these pages; other batches cover the rest of the document. Numbers: keep the original
digits AND add a normalized numeric value when the field is an amount, percentage or
quantity. Dates: keep as written and add ISO (YYYY-MM-DD) when unambiguous; note Hijri dates.

Return ONLY JSON:
{
  "findings": [
    {
      "field": "<one of the target keys, or boq_lines>",
      "value": <string | number | object | list>,
      "evidence": "<verbatim quote from the page, original language, <= 300 chars>",
      "page": <page number from the PAGE marker>,
      "confidence": <0.0-1.0>,
      "note": "<optional: ambiguity, handwriting, stamp, partial table...>"
    }
  ],
  "page_summaries": [ {"page": <n>, "kind": "<cover|toc|general_conditions|special_conditions|boq|price_schedule|signature|annex|blank|other>", "summary": "<= 20 words"} ]
}

For boq_lines return ONE finding per table page with value = list of line objects and
evidence = the table heading or first row. Include pages with no findings only in page_summaries.
""" + FIELDS

MERGE_SYSTEM = """\
You are consolidating per-batch findings extracted from one contract into a single record.
Each input finding carries its page and verbatim evidence. Rules:
- One value per scalar field. When batches disagree, pick the value with the strongest
  evidence (signature page / special conditions beat general boilerplate; amended value
  beats original but record both in "conflicts").
- Keep the supporting evidence and page(s) for every field you keep.
- boq_lines: concatenate all lines in page order, de-duplicate exact repeats, keep page per line.
- Fill "missing" with target keys that never appeared, and "conflicts" with disagreements.

Return ONLY JSON:
{
  "contract": { "<field>": {"value": ..., "evidence": "...", "pages": [..], "confidence": 0.0} , ... },
  "boq_lines": [ {"item_no":..., "description":..., "unit":..., "quantity":..., "unit_price":..., "total":..., "page": n} ],
  "missing": ["..."],
  "conflicts": [ {"field": "...", "candidates": [ {"value": ..., "page": n, "evidence": "..."} ], "resolution": "..."} ],
  "document_map": [ {"pages": "a-b", "kind": "...", "summary": "..."} ]
}
""" + FIELDS
