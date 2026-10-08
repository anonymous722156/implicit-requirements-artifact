# Implicit Requirements in Repository-Level Coding

This artifact accompanies our FSE 2027 submission on implicit requirements (IRs) in coding-agent tasks and contains the study's data and experiment scripts.

## Contents

| Location | Contents |
| --- | --- |
| `case-mapping.md`, `case-mapping.csv` | Fixed mapping of the 100 sampled cases to SWE-bench Pro instance IDs and GitHub commits. |
| `rq1/` | The 217 IRs and per-case counts. |
| `rq2/` | Final IR classifications, taxonomy tables, category counts, and agreement results. |
| `rq3/` | Three-run IR/case outcomes and failure-stage results. |
| `rq4/` | Three-run outcomes under original and IR-explicit task specifications on each model's target set. |
| `experiment/` | Model settings, Docker image references, and dependencies. |
| `scripts/` | Experiment runner and container preparation. |
| `STATISTICS.json` | Summary statistics. |

## Run coding-agent experiments

The runner uses [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent), version 2.4.5, and the [SWE-bench Pro](https://github.com/scaleapi/SWE-bench_Pro) case images listed in `experiment/case-environments.jsonl`. It requires Python 3.10+ and Docker with Linux/AMD64 support.

| Model key | Model |
| --- | --- |
| `deepseek` | DeepSeek V4.1-Flash |
| `gpt6_sol` | GPT-6 Sol |
| `minimax` | MiniMax M3 |

Install the Python dependencies and configure an OpenAI-compatible model endpoint:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r experiment/requirements.txt
export IR_API_KEY='YOUR_API_KEY'
export IR_API_BASE='YOUR_ENDPOINT/v1'
```

Use the original 731-task SWE-bench Pro public-split parquet. The runner reads tasks from it and adds the selected IR statements for RQ4:

```bash
python3 scripts/run_experiment.py --dataset /path/to/SWE-bench_Pro.parquet --condition rq3 --model deepseek --case C007 --output ../experiment-runs --execute --pull-images
python3 scripts/run_experiment.py --dataset /path/to/SWE-bench_Pro.parquet --condition rq4 --model deepseek --case C007 --output ../experiment-runs --execute --pull-images
```

Runs default to three repeats per case. Omit `--case` to run all cases for the selected model and condition. Set `--request-model` if your endpoint uses a different model request name.
