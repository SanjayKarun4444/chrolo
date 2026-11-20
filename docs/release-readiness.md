# Alpha candidate readiness

Candidate: **0.2.0a1**. Review date: **2026-11-19**. This is an experimental alpha, not a production release or stable compatibility commitment.

## Prepared locally

The release metadata, changelog, publishable release notes, issue forms, pull-request template, security-reporting policy, and launch runbook are prepared. The package uses an explicit prerelease version, SPDX MIT metadata, license files, and project URLs. The CLI exposes `--version`.

The source-distribution manifest includes examples, documentation, tests, and benchmark code while excluding raw results, datasets, environment files, and external checkouts. A distribution checker rejects unsafe paths, unexpected local data, version disagreement, missing metadata, and missing release files. CI builds source and wheel archives, checks them with Twine and the content checker, exercises installed packages, and stores artifacts without publishing.

| Check | Result |
| --- | --- |
| Fresh installed alpha wheel | Imports from its isolated environment and reports `chrolo 0.2.0a1` |
| Dependency requirements | `pip check` passes after normal installation of declared extras |
| Full suite against installed wheel | 306 passed; three opt-in Docker cases skipped |
| Installed core workflow | Capture, replay, preparation, and generated pytest before/after checks pass outside the checkout |
| Installed SDK/LangGraph matrix | All 21 checks pass, no skips, using local HTTP fixtures |
| Real-action regression gate | All 28 repeated checks pass across PyHOCON, Vermouth, and PyFilesystem |
| Installed real-action source repair | PyFilesystem regression fails before the fix and passes afterward outside the checkout, with fixture bytes unchanged |
| Distribution metadata | Wheel and source archive pass strict Twine checks and content/version/license validation |
| License | Root MIT license is included in the distributions |

Fresh dependency resolution included LangGraph 1.2.24, LangChain Core 1.7.7, OpenAI 2.54.0, Anthropic 0.125.0, and HTTPX 0.28.1. These are the locally tested versions, not pinned support guarantees. Reports and build environments remain ignored under `results/launch-candidate/`.

The final launch kit contains the wheel, source archive, SHA-256 hashes, validation summary, release notes, and publication runbook. The kit is prepared for review, not uploaded. Its validation summary identifies the exact source commit. Use the final kit hashes rather than hashes from earlier review builds.

## GitHub facts and remaining launch gates

An authenticated preflight confirmed admin access to `SanjayKarun4444/chrolo`. The repository is currently private. [Hosted CI passed](https://github.com/SanjayKarun4444/chrolo/actions/runs/44612899922) on `fe7ec7e`; that is evidence for the preceding runtime milestone, not the new candidate.

The updated candidate workflow covers Python 3.10/3.12 on Linux and Windows with read-only repository permissions. It has not run on GitHub yet because the standing instruction is local commits without pushing.

Private vulnerability reporting is the selected launch channel. GitHub documents it for [public repositories](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/report-privately). It is not active on the private repository. Making the source public and enabling/verifying that channel must be coordinated before announcement.

The remaining external gates are:

1. Authorize pushing the exact candidate and require the updated hosted CI to pass on that commit.
2. Authorize public visibility, enable private vulnerability reporting, and verify public documentation/security links.
3. Authorize the annotated tag and GitHub pre-release, using the validated artifacts and prepared notes.

No push, visibility change, tag publication, GitHub release, announcement, or package upload is performed by preparation. A PyPI upload is a separate decision requiring verified package ownership and publishing configuration. Source installation is sufficient for the first public alpha.

Follow the [launch runbook](launch-runbook.md) for the concrete steps and prepared announcement.

## History and data review

The preceding review inspected 310 reachable history blobs for common token/private-key shapes. Findings were documentation filename fragments, an artificial key-shaped scanner fixture, and the documented localhost test key. Raw results, datasets, and external harness paths were absent from the reachable-history path review. Current ignore rules cover `.env` files and `.chrolo-local/` bundles.

No production credential was identified by that heuristic review. It is not an exhaustive security, dependency, or legal audit. Review the exact outgoing commits and third-party provenance before publication. The test key must never be used in a deployment.

## Limits that remain public

Replay covers explicit boundaries; trusted graph code can still execute unwrapped effects. Async, streaming, arbitrary agent capture, and SDK response reconstruction are unsupported. Selected real-action cases use documented deterministic external fixtures and independent oracles; they do not establish corpus-wide reliability, developer time savings, or an efficiency advantage over a naive baseline. An external developer trial and physical-phone QA remain open.
