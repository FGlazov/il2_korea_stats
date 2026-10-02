# 11 — Open Questions

Only **unanswered** questions live here, ordered by how much each one blocks or shapes the design.
When a question is answered, write the answer into the relevant doc (requirement, decision, or format doc) and
**delete it from this file**. If it's partly answered, cut it down to the part that's still open.
IDs are never reused or renumbered, so gaps are expected.

## High impact

**OQ-19 Review bailout rule v2** (owner: maintainer)
Claude validated the rule on the sample data (2026-10-02). It replaced the first draft, which mostly flagged players who had landed. Rule and results:
[02, FR-ING-14](02_functional_requirements.md#bailout-rule-v2-fr-ing-14--validated-on-210-sample-missions-2026-10-02). Evidence:
[12](12_korea_log_format.md#pilot-bailout-detection-validated-2026-10-02). Still open:
- Does the maintainer accept rule v2 and its thresholds (100 m, 0.5 s, 30 s, 60 s)?
- Compare with the other developer's indirect-signal approach.
- Why do F-86s show roughly 3–9× more undamaged bailouts per aircraft lost than other types: players, or a game quirk?

## Lower impact

**OQ-1 Config keys that enable text logs in Korea's DServer `startup.cfg`** (owner: maintainer, will ask server operators)
`il2ks doctor` needs them to detect and explain a missing setting. In BoS it was `mission_text_log = 1` and `text_log_folder`.
