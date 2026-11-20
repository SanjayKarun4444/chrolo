# Public alpha readiness

Review date: 2026-11-19. This review prepares a public release; it does not
publish the repository, push commits, create a release, or upload a package.

## Assessment

Chrolo is a credible candidate for a public **experimental alpha**. The local
code, examples, license, and validation support that scope. Complete the
repository and reporting setup below before announcing a release. This review
does not establish production readiness or a stable compatibility commitment.

Making source public and publishing a package are separate decisions. The
README documents source installation and does not assume that the
`chrolo-starter` distribution has been published or that a package name is
available to the maintainers.

## Verified locally

| Area | Evidence |
| --- | --- |
| License | MIT license exists at the repository root and is declared in package metadata |
| Onboarding | README quickstart records three events/calls, reduces to one event/two calls, and produces the expected failing regression |
| Application fixes | Project walkthroughs and real-action source-repair pilots verify before/after regression behavior with recorded data unchanged |
| Standard suite | Latest runtime checkpoint: 291 passed; three opt-in Docker cases skipped |
| Real-action gates | All 28 repeated checks passed across three independent repository tasks |
| Distribution build | Fresh wheel built with declared build dependencies; version remains 0.2.0 |
| Installed core workflow | Capture, replay, preparation, and generated pytest before/after checks pass outside the source checkout |
| Installed provider matrix | All 21 SDK/LangGraph checks pass with local HTTP fixtures and no skips |
| Installed repair support | All 44 Docker-runner contract checks pass outside the checkout |
| Installed real-action repair | The PyFilesystem test fails before the source repair and passes afterward using the fresh wheel outside the checkout, with its fixture unchanged |
| Local data boundaries | Raw `results/`, datasets, and external harness checkout are ignored; none of these paths appeared in the reachable-history path review |

The wheel build used this review's revised README. Build reports and logs remain
ignored under `results/readme-release-review/`; no distribution upload occurred.

## History and sensitive-data review

A local heuristic review inspected 310 reachable Git-history blobs for common
provider-token and private-key shapes. The findings were filename fragments in
documentation, an artificial key-shaped value in the secret-scanner test, and
the documented localhost test private key. The test certificate/key must never
be used in a real deployment.

No production credential was identified by that review. This is not an
exhaustive secret audit, a dependency security assessment, or a legal opinion.
The current ignore rules also cover `.env` files and `.chrolo-local/` bundles.
Maintainers should review the exact outgoing revision and its history before
publication, including third-party provenance and any newly staged artifacts.

## Publication setup still to verify

1. **GitHub access and workflow execution.** Confirm the intended repository,
   maintainer permissions, desired public visibility, and a passing hosted
   Actions run. CI is configured for Python 3.10/3.12 and package workflows;
   local checks do not prove that the hosted workflow has run. No push or
   visibility change was performed in this review.
2. **A confidential reporting route.** Enable private vulnerability reporting
   or publish a real maintainer security contact, then update `SECURITY.md`.
   The policy currently explains the gap rather than advertising an unverified
   private channel or response-time guarantee.
3. **Release identity and announcement.** Confirm the public project/package
   names, choose the intended tag and release notes, and clearly mark the
   first release experimental/pre-release. Name ownership and registry
   publication are not verified here. A source-only alpha can use the existing
   checkout installation instructions.

These are the remaining public-launch checks, rather than a requirement to
implement every future roadmap feature before sharing the source.

## Limits to keep visible

- Offline replay covers explicitly wrapped calls; trusted application code
  can still execute unwrapped effects. Docker task replay is a separate path.
- Buffered synchronous provider interfaces are covered. Async, streaming,
  SDK response reconstruction, and arbitrary agent capture are unsupported.
- Benchmark evidence includes selected tasks and deterministic external
  fixtures. It does not establish corpus-wide reliability or an efficiency
  advantage over a naive deletion baseline.
- External developer usability, broader platform execution, and physical-phone
  QA remain open. Public availability would not resolve these validation gaps.

See the [roadmap](roadmap.md), [security policy](../SECURITY.md), and
[benchmark methods](../benchmarks/README.md) for details.
