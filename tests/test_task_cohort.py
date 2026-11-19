import copy
import json
import hashlib

import pytest

from benchmarks.run_task_cohort import freeze, main, run_cohort


def source(repo="owner/repo", mode="compatibility"):
    return {"row": {"repo": repo, "instance_id": repo.replace("/", "__") + "-1",
                    "trajectory": [{"tool_calls": [{"function": {"name": "execute_bash",
                                     "arguments": json.dumps({"command": "true"})}}]}]},
            "task": {"image": "task@sha256:" + "a" * 64, "test_command": "pytest",
                     "fail_to_pass": ["target"], "pass_to_pass": ["control"]},
            "evaluation": {"kind": mode}}


def prepare(tmp_path, sources):
    paths = []
    for index, item in enumerate(sources):
        path = tmp_path / f"input-{index}.json"
        path.write_text(json.dumps(item))
        paths.append(path)
    return paths, freeze(paths, root=tmp_path, reason="All prespecified fixture cases")


def runner_factory(outcomes, calls):
    iterator = iter(outcomes)

    class Runner:
        def __init__(self, **options):
            self.outcome = next(iterator)
            self.calls = []
            calls.append(self.calls)

        def __call__(self, events):
            self.calls.append(len(events))
            if self.outcome == "exception":
                raise RuntimeError("PRIVATE_PAYLOAD")
            target = "PASSED"
            if self.outcome == "empty_control_rejected" or (events and self.outcome == "action_dependent_failure"):
                target = "FAILED"
            if events and self.outcome == "inconclusive":
                target = "ERROR"
            return {"statuses": {"target": target, "control": "PASSED"}}

    return Runner


def test_multi_repository_report_retains_every_selected_case_and_all_repeats(tmp_path):
    _, manifest = prepare(tmp_path, [source(), source("other/repo", "packaging"), source("owner/repo", "other-mode")])
    calls = []
    report = run_cohort(manifest, root=tmp_path, output=tmp_path / "report.json", repeat=2,
                        runner_factory=runner_factory(["action_dependent_failure"] * 2 + ["no_target_failure"] * 2 + ["inconclusive"] * 2, calls))
    assert report["complete"] and report["selected"] == report["evaluated"] == 3
    assert report["attempts_completed"] == report["attempts_planned"] == 6
    assert report["counts"]["action_dependent_failure"] == report["counts"]["no_target_failure"] == report["counts"]["inconclusive"] == 1
    assert report["groups"]["repository"]["owner/repo"]["selected"] == 2
    assert len(report["groups"]["instance_id"]) == 2  # Three traces are not three tasks.
    assert len(calls) == 6 and all(attempt == [0, 1] for attempt in calls)
    assert json.loads((tmp_path / "report.json").read_text()) == report


def test_unstable_reproduction_is_never_a_confirmed_failure(tmp_path):
    _, manifest = prepare(tmp_path, [source()])
    report = run_cohort(manifest, root=tmp_path, output=tmp_path / "report.json", repeat=2,
                        runner_factory=runner_factory(["action_dependent_failure", "no_target_failure"], []))
    assert report["counts"]["unstable"] == 1
    assert report["counts"]["action_dependent_failure"] == 0


def test_empty_controls_block_actions_and_remain_in_denominator(tmp_path):
    _, manifest = prepare(tmp_path, [source()])
    calls = []
    report = run_cohort(manifest, root=tmp_path, output=tmp_path / "report.json", repeat=2,
                        runner_factory=runner_factory(["empty_control_rejected"] * 2, calls))
    assert report["counts"]["empty_control_rejected"] == 1
    assert calls == [[0], [0]] and report["attempts_completed"] == 2


@pytest.mark.parametrize("setup_error", [False, True])
def test_setup_and_execution_errors_are_payload_free_inconclusive_attempts(tmp_path, setup_error):
    _, manifest = prepare(tmp_path, [source()])

    def broken(**options):
        raise RuntimeError("PRIVATE_PAYLOAD")

    report = run_cohort(manifest, root=tmp_path, output=tmp_path / "report.json", repeat=2,
                        runner_factory=broken if setup_error else runner_factory(["exception"] * 2, []))
    assert report["complete"] and report["counts"]["inconclusive"] == 1
    assert report["attempts_completed"] == 2
    assert "PRIVATE_PAYLOAD" not in json.dumps(report)


@pytest.mark.parametrize("mutation", ["content", "missing", "identity", "duplicate", "escape"])
def test_all_inputs_are_validated_before_any_runner_and_stale_success_is_invalidated(tmp_path, mutation):
    paths, manifest = prepare(tmp_path, [source(), source("second/repo")])
    output = tmp_path / "report.json"
    output.write_text('{"complete": true, "passed": true}')
    if mutation == "content":
        paths[1].write_text(paths[1].read_text() + " ")
    elif mutation == "missing":
        paths[1].unlink()
    elif mutation == "identity":
        manifest["cases"][1]["repository"] = "false/repo"
    elif mutation == "duplicate":
        manifest["cases"][1] = copy.deepcopy(manifest["cases"][0])
    else:
        manifest["cases"][1]["input"] = "../outside.json"

    def forbidden(**options):
        pytest.fail("No runner should be constructed before preflight completes")

    report = run_cohort(manifest, root=tmp_path, output=output, runner_factory=forbidden)
    assert report["error"] == "fixtures_missing_or_invalid" and not report["complete"]
    assert "passed" not in json.loads(output.read_text())


@pytest.mark.parametrize("mutation", ["tag", "targets", "controls", "overlap", "duplicate_test", "no_actions", "identity"])
def test_freeze_rejects_ambiguous_or_unpinned_task_definitions(tmp_path, mutation):
    item = source()
    if mutation == "tag":
        item["task"]["image"] = "task:latest"
    elif mutation == "targets":
        item["task"]["fail_to_pass"] = []
    elif mutation == "controls":
        item["task"]["pass_to_pass"] = []
    elif mutation == "overlap":
        item["task"]["pass_to_pass"] = ["target"]
    elif mutation == "duplicate_test":
        item["task"]["fail_to_pass"] *= 2
    elif mutation == "no_actions":
        item["row"]["trajectory"] = []
    else:
        item["row"]["repo"] = "wrong/repo"
    with pytest.raises(ValueError):
        prepare(tmp_path, [item])


def test_interrupt_keeps_selection_and_completed_attempts(tmp_path):
    _, manifest = prepare(tmp_path, [source(), source("second/repo")])
    output = tmp_path / "report.json"
    count = 0
    good = runner_factory(["no_target_failure"], [])

    def interrupt(**options):
        nonlocal count
        count += 1
        if count > 1:
            raise KeyboardInterrupt
        return good(**options)

    with pytest.raises(KeyboardInterrupt):
        run_cohort(manifest, root=tmp_path, output=output, repeat=2, runner_factory=interrupt)
    report = json.loads(output.read_text())
    assert not report["complete"] and report["selected"] == 2
    assert report["attempts_completed"] == 1 and report["counts"]["pending"] == 2


def test_report_cannot_overwrite_an_input(tmp_path):
    paths, manifest = prepare(tmp_path, [source()])
    before = paths[0].read_bytes()
    with pytest.raises(ValueError, match="differ"):
        run_cohort(manifest, root=tmp_path, output=paths[0])
    assert paths[0].read_bytes() == before


def test_same_content_cannot_be_counted_twice(tmp_path):
    with pytest.raises(ValueError, match="Duplicate"):
        prepare(tmp_path, [source(), source()])


def test_reformatting_json_cannot_manufacture_a_second_selected_case(tmp_path):
    item = source()
    first, second = tmp_path / "pretty.json", tmp_path / "compact.json"
    first.write_text(json.dumps(item, indent=2))
    second.write_text(json.dumps(item, sort_keys=True, separators=(",", ":")))
    with pytest.raises(ValueError, match="Duplicate input content"):
        freeze([first, second], root=tmp_path, reason="Same source serialized twice")


def test_reformatted_duplicate_in_manifest_is_rejected_before_execution(tmp_path):
    paths, manifest = prepare(tmp_path, [source()])
    second = tmp_path / "reformatted.json"
    second.write_text(json.dumps(source(), indent=4, sort_keys=True))
    duplicate = dict(manifest["cases"][0], id="second-case", input=second.name,
                     sha256=hashlib.sha256(second.read_bytes()).hexdigest())
    manifest["cases"].append(duplicate)
    def forbidden(**kwargs):
        pytest.fail("Duplicate cases must be rejected before any runner")
    report = run_cohort(manifest, root=tmp_path, output=tmp_path / "report.json", runner_factory=forbidden)
    assert report["error"] == "fixtures_missing_or_invalid"
    assert report["complete"] is False


@pytest.mark.parametrize("contents", ["{broken", "[]", '{"format": "unknown"}'])
def test_invalid_cli_manifest_clears_old_success_without_dumping_input(tmp_path, monkeypatch, contents):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(contents)
    output = tmp_path / "report.json"
    output.write_text('{"complete": true, "passed": true}')
    monkeypatch.setattr("sys.argv", ["cohort", "--root", str(tmp_path), "run", str(manifest), "--output", str(output)])
    assert main() == 2
    report = json.loads(output.read_text())
    assert not report["complete"] and "passed" not in report


def test_freeze_cli_does_not_overwrite_existing_selection(tmp_path, monkeypatch):
    paths, _ = prepare(tmp_path, [source()])
    output = tmp_path / "selection.json"
    output.write_text("existing selection")
    monkeypatch.setattr("sys.argv", ["cohort", "--root", str(tmp_path), "freeze", str(paths[0]),
                                   "--reason", "all cases", "--output", str(output)])
    assert main() == 2 and output.read_text() == "existing selection"


def test_invalid_manifest_extra_payload_is_not_copied_into_report(tmp_path):
    _, manifest = prepare(tmp_path, [source()])
    manifest["private"] = "PRIVATE_PAYLOAD"
    report = run_cohort(manifest, root=tmp_path, output=tmp_path / "report.json")
    assert report["error"] == "fixtures_missing_or_invalid"
    assert "PRIVATE_PAYLOAD" not in json.dumps(report)


@pytest.mark.parametrize("outcome,expected", [("action_dependent_failure", 0), ("no_target_failure", 0),
                                             ("empty_control_rejected", 1), ("inconclusive", 1)])
def test_cli_exit_code_distinguishes_valid_screening_from_invalid_execution(tmp_path, monkeypatch, outcome, expected):
    from benchmarks import run_task_cohort as module

    _, manifest = prepare(tmp_path, [source()])
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    original = module.run_cohort
    monkeypatch.setattr(module, "run_cohort", lambda *args, **kwargs:
                        original(*args, **kwargs, runner_factory=runner_factory([outcome], [])))
    monkeypatch.setattr("sys.argv", ["cohort", "--root", str(tmp_path), "run", str(manifest_path),
                                   "--repeat", "1", "--output", str(tmp_path / "report.json")])
    assert main() == expected
