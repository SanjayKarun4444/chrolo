# Launching the first public alpha

Candidate: **0.2.0a1**. Intended tag: **v0.2.0a1**. Distribution:
`chrolo-starter`; CLI/import: `chrolo`. The initial launch is a public source
repository and a GitHub pre-release. A package-registry upload is separate.

## Current remote facts

The 2026-11-19 authenticated check confirmed the repository is private, the
available account has admin rights, and `master` at `fe7ec7e` passed
[hosted CI](https://github.com/SanjayKarun4444/chrolo/actions/runs/44612899922).
That run predates the alpha-candidate changes. The new four-platform/version
matrix and candidate must pass hosted CI after an authorized push.

GitHub private vulnerability reporting is the selected security channel. It
is not active on the private repository. GitHub documents the feature for
[public repositories](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/report-privately).
Making the repository public and enabling the channel are coordinated launch
actions, not completed preparation steps.

## Local candidate checks

Use a clean checkout of the candidate commit. Build into a new empty directory
so stale releases cannot be mistaken for the candidate:

```bash
python -m pip install -e '.[release]'
python -m build --outdir results/release-dist
python -m twine check --strict results/release-dist/*
python benchmarks/check_distribution.py --dist-dir results/release-dist --expected-version 0.2.0a1 --require-prerelease --report results/release-dist/distribution-check.json
```

The source archive includes the examples, documentation, benchmark programs,
and tests needed to inspect the source release. It excludes raw results,
datasets, environment files, and external checkouts. The localhost test key is
intentional documented test data. The wheel contains the runtime and license.
Distribution validation follows the [Python packaging guidance](https://packaging.python.org/en/latest/tutorials/packaging-projects/).

Create a separate environment and install the built wheel, not the editable
checkout. The following commands assume that environment is activated:

```bash
python -m pip install 'results/release-dist/chrolo_starter-0.2.0a1-py3-none-any.whl[dev,langgraph,langchain,openai,anthropic]'
python -m pip check
python -m chrolo --version
python -m pytest -q
python benchmarks/package_workflow_smoke.py --output-dir results/release-core
python benchmarks/package_provider_smoke.py --output-dir results/release-providers
```

Run the full suite from the checkout; the imported runtime should come from
the installed wheel. The package smoke scripts execute outside the checkout.
With retained fixtures and Docker, also run the 28-check real-action gate and
the before/after source-repair pilot. Keep raw reports local.

The distribution report lists SHA-256 hashes. Verify those exact hashes after
copying assets for a release; do not rebuild between validation and upload.
Build from the final clean commit and record that commit alongside the hashes.
Do not reuse hashes from an earlier documentation or metadata build.

## Actions requiring publication authorization

The standing instruction is local commits without pushing. Nothing in this
runbook grants permission to override it.

1. Review the exact outgoing commits, history, third-party attribution, and
   public repository settings. Approve the public-source launch explicitly.
2. Push the candidate branch only after authorization. Require all jobs in the
   updated CI workflow to pass on that exact commit; a prior green run is not
   sufficient. CI only validates and stores artifacts; it does not publish.
3. Change repository visibility to public when approved. Enable private
   vulnerability reporting immediately, verify the endpoint reports enabled,
   and record the verified activation in the launch evidence. Verify the documentation
   and security links as a public visitor. Do not announce until this is done.
4. Create the annotated tag `v0.2.0a1` on the validated final commit and push the
   tag only with authorization. Prepare the GitHub pre-release using
   [the release notes](releases/0.2.0a1.md), the validated wheel/source archive,
   and their hash report. Confirm the tag, version, commit, and assets agree.
5. Publish the pre-release and announcement only after approval. Do not upload
   to PyPI without separately verifying package ownership and an authorized
   publishing method. No package-publishing credentials are required for the
   source-only launch.

If a check fails, keep the candidate unpublished, fix it, and rerun affected
checks. If files change after a build, rebuild and replace the candidate
checksums before approving publication. Retain failures and negative benchmark
results rather than removing them to improve the launch story.

## Prepared announcement

> Chrolo is an experimental, MIT-licensed tool for turning reproducible agent
> failures into smaller fixtures and executable pytest regressions. Record
> explicit model/tool boundaries, verify offline replay, and reduce a failure
> while keeping its original signature. The first alpha includes synchronous
> OpenAI/Anthropic integrations, runnable local examples, and selected real-action
> regression cases with documented controls and limitations. Start with the
> repository quickstart; no model key is needed for the demo. Feedback on real
> developer workflows is welcome. APIs are experimental, and unwrapped effects,
> async, and streaming remain outside current coverage.

Append the verified public repository and pre-release URLs only after launch.
