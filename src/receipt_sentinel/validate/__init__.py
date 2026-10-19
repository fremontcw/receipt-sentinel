"""validate

Deterministic checks on an extracted receipt (spec §4.4). Model output is never trusted on
its own: each rule passes or fails with a reason, and failures go to review, not the bin.

Status:
    None
Issues:
    None
Todo:
    - [ ] Duplicate detection (vendor+total+date) lands with the receipts store in step 8
"""
