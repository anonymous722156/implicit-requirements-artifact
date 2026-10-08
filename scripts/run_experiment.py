#!/usr/bin/env python3
"""Run fresh RQ3/RQ4 tasks with mini-swe-agent. Default: print plan only."""
import argparse
import csv
import importlib.metadata
import importlib.resources
import json
import os
import random
import re
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ("deepseek", "gpt6_sol", "minimax")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def build_task(case):
    sections = []
    for key, title in (("problem_statement", "Problem Statement"),
                       ("requirements", "Requirements"), ("interface", "Interface")):
        value = str(case.get(key) or "").strip()
        if value:
            sections.append(f"# {title}\n\n{value}")
    if not sections:
        raise ValueError("Empty task specification")
    return "\n\n".join(sections).strip() + "\n"


def load_official_tasks(path):
    import pyarrow.parquet as pq

    rows = pq.read_table(path, columns=["instance_id", "problem_statement", "requirements", "interface"]).to_pylist()
    tasks = {r["instance_id"]: r for r in rows}
    if len(rows) != len(tasks) or len(tasks) != 731:
        raise ValueError("Official task inventory differs")
    return tasks


def task_for_case(case_id, original, target_ids, inventory):
    task = dict(original)
    if target_ids:
        ordered = sorted(target_ids)
        random.Random(f"2026091405:C2:A1-{case_id}").shuffle(ordered)
        bullets = [f"Additional behavioral requirement: {inventory[i]['canonical_obligation'].strip()} "
                   "The implementation approach is not prescribed." for i in ordered]
        addition = "Additional Behavioral Requirements\n\n" + "\n".join("- " + s for s in bullets)
        base = str(task.get("requirements") or "").rstrip()
        task["requirements"] = f"{base}\n\n{addition}" if base else addition
    return build_task(task)


def plan(args):
    configs = read_json(ROOT / "experiment/model-configurations.json")
    environments = {r["case_id"]: r for r in
                    (json.loads(s) for s in (ROOT / "experiment/case-environments.jsonl").read_text().splitlines())}
    with (ROOT / "case-mapping.csv").open(encoding="utf-8-sig", newline="") as handle:
        mapping = {r["case_id"]: r for r in csv.DictReader(handle)}
    inventory = {r["ir_id"]: r for r in
                 (json.loads(s) for s in (ROOT / "rq1/implicit-requirements.jsonl").read_text().splitlines())}
    targets = {}
    if args.condition == "rq4":
        with (ROOT / "rq4/target-ir-three-runs.csv").open(newline="") as handle:
            for row in csv.DictReader(handle):
                if row["model"] == args.model:
                    targets.setdefault(row["case_id"], []).append(row["ir_id"])
        available = set(targets)
    else:
        available = {r["case_id"] for r in inventory.values()}
    selected = set(args.case) if args.case else available
    if not selected <= available:
        raise ValueError(f"Cases not in this condition: {sorted(selected - available)}")
    tasks = load_official_tasks(args.dataset)
    result = []
    for case_id in sorted(selected):
        case = tasks[mapping[case_id]["instance_id"]]
        task = task_for_case(case_id, case, targets.get(case_id, []), inventory)
        for repeat in range(1, args.repeats + 1):
            result.append({"condition": args.condition, "model": args.model,
                           "request_model": args.request_model or configs[args.model]["model"],
                           "case_id": case_id, "repeat": repeat,
                           "task": task,
                           "image": environments[case_id]["image"],
                           "expected_image_digest": environments[case_id]["expected_image_digest"],
                           "platform": environments[case_id]["expected_platform"],
                           "base_commit": mapping[case_id]["base_commit"],
                           "step_limit": args.steps or configs[args.model]["step_limit"],
                           "output": str(args.output.resolve() / args.condition / args.model / f"{case_id}-r{repeat}")})
    return result, configs[args.model]


def execute(job, settings, args):
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    os.environ["MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT"] = str(settings["max_retries"] + 1)
    import litellm
    import yaml
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.environments.docker import DockerEnvironment
    from minisweagent.models.litellm_model import LitellmModel

    class CredentialSafeModel(LitellmModel):
        abort_exceptions = LitellmModel.abort_exceptions + [litellm.exceptions.BadRequestError]

        def serialize(self):
            value = super().serialize()
            def redact(x):
                if isinstance(x, dict):
                    return {k: "[REDACTED]" if k.lower() in {"api_key", "authorization", "token"}
                            else redact(v) for k, v in x.items()}
                return [redact(v) for v in x] if isinstance(x, list) else x
            return redact(value)

    key, base = os.getenv(args.api_key_env), os.getenv(args.api_base_env)
    if not key or not base:
        raise ValueError(f"Set {args.api_key_env} and {args.api_base_env}")
    head = job["base_commit"]
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("Invalid pre-change commit")
    image_ref = job["image"].rsplit(":", 1)[0] + "@" + job["expected_image_digest"]
    if args.pull_images:
        subprocess.run(["docker", "pull", "--platform", job["platform"], image_ref], check=True)
    info = json.loads(subprocess.check_output(["docker", "image", "inspect", image_ref], text=True))[0]
    if f"{info['Os']}/{info['Architecture']}" != job["platform"]:
        raise ValueError("Docker image platform differs")
    output = Path(job["output"])
    if output.exists():
        raise ValueError("Output exists; choose a new output directory")
    output.mkdir(parents=True)
    config = yaml.safe_load(importlib.resources.files("minisweagent.config").joinpath("benchmarks/swebench.yaml").read_text())
    sanitizer = ROOT / "scripts/prepare_container.sh"
    environment = DockerEnvironment(
        image=info["Id"], cwd="/app", timeout=120, interpreter=["bash", "-c"],
        env=config["environment"]["env"] | {"PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_RETRIES": "0"},
        run_args=["--rm", "--entrypoint=", "--network=none", "--cap-drop=ALL",
                  "--security-opt=no-new-privileges", f"--platform={job['platform']}",
                  f"--volume={sanitizer}:/opt/ir/prepare_container.sh:ro"])
    try:
        setup = environment.execute({"command": f"bash /opt/ir/prepare_container.sh {shlex.quote(head)}"}, timeout=900)
        if setup.get("returncode") != 0:
            raise RuntimeError("Pre-change container preparation failed")
        model = CredentialSafeModel(
            model_name=f"openai/{job['request_model']}", cost_tracking="ignore_errors",
            model_kwargs={"api_key": key, "api_base": base, "custom_llm_provider": "openai",
                          "temperature": settings["temperature"], "max_tokens": settings["max_output_tokens"],
                          "timeout": 120, "max_retries": 0, "drop_params": True, "parallel_tool_calls": True},
            observation_template=config["model"]["observation_template"],
            format_error_template=config["model"]["format_error_template"])
        agent = DefaultAgent(model, environment, system_template=config["agent"]["system_template"],
                             instance_template=config["agent"]["instance_template"],
                             step_limit=job["step_limit"], cost_limit=0,
                             wall_time_limit_seconds=args.wall_seconds,
                             max_consecutive_format_errors=3, output_path=output / "trajectory.json")
        result = agent.run(task=job["task"])
        # Use the final Submitted output, not an incidental workspace diff.
        (output / "submission.patch").write_text(str(result.get("submission") or ""), encoding="utf-8")
        (output / "status.json").write_text(json.dumps({"exit_status": result.get("exit_status"),
                                                       "case_id": job["case_id"], "repeat": job["repeat"]}) + "\n")
    finally:
        environment.cleanup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--condition", choices=("rq3", "rq4"), required=True)
    parser.add_argument("--model", choices=MODELS, required=True)
    parser.add_argument("--dataset", type=Path, required=True, help="Original SWE-bench Pro public-split parquet")
    parser.add_argument("--case", action="append", default=[], help="Cxxx; repeat option to select several cases; omit for all")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--steps", type=int, help="Override the initial step limit")
    parser.add_argument("--wall-seconds", type=int, default=3600)
    parser.add_argument("--request-model", help="Provider's current model request name")
    parser.add_argument("--api-key-env", default="IR_API_KEY")
    parser.add_argument("--api-base-env", default="IR_API_BASE")
    parser.add_argument("--output", type=Path, required=True, help="Run output directory outside the artifact directory")
    parser.add_argument("--pull-images", action="store_true")
    parser.add_argument("--execute", action="store_true", help="Create containers and make paid API calls")
    args = parser.parse_args()
    if args.repeats < 1 or args.wall_seconds < 1 or (args.steps is not None and args.steps < 1):
        parser.error("Budgets and repeat count must be positive")
    if args.output.resolve().is_relative_to(ROOT.resolve()):
        parser.error("Output must be outside the public package")
    jobs, settings = plan(args)
    if not args.execute:
        print(json.dumps([{k: v for k, v in job.items() if k != "task"} for job in jobs], indent=2))
        return
    if importlib.metadata.version("mini-swe-agent") != "2.4.5":
        raise RuntimeError("Install mini-swe-agent==2.4.5")
    for job in jobs:
        execute(job, settings, args)


if __name__ == "__main__":
    main()
