# Replay terminal privacy review

Reviewed 2026-11-18. This checkpoint covers cassette identity validation and the
CLI's captured-exception replay output. It does not establish a general privacy
guarantee for every field of a user-supplied trace or arbitrary graph code.

## Findings and changes

1. Duplicate cassette interaction IDs were interpolated into validation errors.
   A trace-controlled ID could therefore appear in `chrolo replay` diagnostics.
   Validation now reports the zero-based position of the duplicate without the
   ID value. Rejection behavior and the exception class remain unchanged.
2. Replayed recorded errors can become structured captured-exception outcomes.
   This route bypassed the CLI's existing recorded-error diagnostic redaction,
   causing the recorded exception label to appear in terminal output. The CLI
   now confirms the captured exception with details omitted. The recorded label
   remains available in replay result data for the caller's failure oracle;
   captures, replay data, and failure-signature matching are not rewritten.

## Verification

`tests/test_replay_privacy.py` covers duplicate IDs in both supported cassette
versions, subprocess replay/minimization diagnostics, absence of a minimized
artifact after invalid input, and a secret-like recorded exception label that
remains in replay data while being absent from CLI stdout/stderr. The existing
exception capture/replay/minimization test now expects the privacy-safe replay
message and still checks exact exception data and oracle behavior.

The focused privacy/exception-capture checks passed before the final full-suite
run. The initial broader run found only the old terminal-message assertion;
that assertion was updated and its complete end-to-end test passed.

Final verification: the full working-tree suite passed 270 tests, with two
Docker checks skipped by default. This count includes the pre-existing,
uncommitted benchmark tests. The focused privacy and exception-capture suite
passed all ten checks. Whitespace checks passed for this review's changes.

## Scope retained

- Explicitly requested local trace inspection still exposes selected metadata,
  including exception type labels. This change targets replay diagnostics.
- Live capture still reports its executed exception class.
- The Python replay API retains failure-signature data for caller-owned oracles.
- Replay safety applies to wrapped boundaries; trusted graph code can still
  perform unwrapped effects, as documented by the existing replay contract.
- Additional benchmark/package work was already uncommitted in the checkout.
  This review preserves that work and commits only its own source, tests, and
  this review note. No push or publication is performed.
