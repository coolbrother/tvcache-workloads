# Terminal-Bench pilot on NERSC Perlmutter

GRPO training with veRL on the 51 easy Terminal-Bench tasks the TVCache paper
used (`container-manager-public/tasks_easy_network_skip`), with every bash
command going through TVCache, then `tvverify check-graph` on the cache it
built. Pilot settings: Qwen2.5-1.5B-Instruct, 4 rollouts per task, batch
4, 5 epochs (the paper: Qwen3-4B, 8 rollouts, 10 epochs).

Perlmutter has no Docker daemon, so the container manager drives
**podman-hpc** (`CONTAINER_CLI`, `COMPOSE_CMD`, `DOCKER_HOST`; see `env.sh`).

## Order

| Step | Where | Command |
|---|---|---|
| 0. Log in | Mac | `sshproxy.sh -u <user>` |
| 1. Stage the code | Mac | `REMOTE=<user>@perlmutter.nersc.gov bash stage.sh` |
| 2. Probe podman (**gate**) | login node first (free), then a short shared CPU job | `bash probe_podman.sh`; if it passes, confirm under `salloc -q shared_interactive -C cpu -c 16 -t 00:20:00 -A <project>` |
| 3. Build servers, install Python side | login node | `bash setup.sh` |
| 4. Prebuild the 51 task images | login node (NERSC allows `podman-hpc build` there) | start the manager (as in `train_pilot.sh` step 1), then `$VERL_PY prebuild.py $TASKS_ROOT` |
| 5. Smoke test | compute node | from `tvcache-workloads`: `$VERL_PY -m tvverify check --adapter tvcache_workloads.bash.verify:HelloWorldAdapter --rollouts 5`; must match the Mac run (contract 11/11, cache PASS, replay PASS) |
| 6. Sampling test | GPU node | `MODE=sample bash train_pilot.sh` (about 20 min) |
| 7. Pilot | GPU node | `MODE=train bash train_pilot.sh`, once per variant (below) |

Step 2 is the gate: if the **docker API socket** or **SDK** items fail, stop.
The manager's Docker SDK calls and `docker-compose` both need that socket.

GPU allocation for steps 6–7. The pilot uses one GPU (`N_GPUS=1`), so ask
for a shared quarter node instead of a whole node; it is charged at a
quarter of the node rate:
`salloc -q shared_interactive -C gpu -G 1 -c 32 -t 04:00:00 -A <project>`.
Use `-G 2 -c 64` if the containers run out of host RAM.

## Budget

The project has about 10 node-hours. Rough cost per step: probe and
prebuild on the login node, about 0; CPU confirm, about 0.1; sampling test,
about 0.1; one pilot variant (5 epochs, 4 rollouts: about 5 h on a quarter
node, a guess until the sampling test gives a real step time), about 1.25.
Both variants fit with room for reruns. 5 h is over the 4-hour interactive
limit, so training goes in as an `sbatch` job on the `shared` QoS.
Checkpoints are saved every 10 steps; rerun with the same `RUN=` to resume
(the cache restarts empty, so later steps begin cold). Check usage with `sshare -A <project>`
or Iris.

## veRL environment

`VERL_PY` must be a Python with veRL 0.9.1 (the version the integration was
written against), vLLM and torch. A prebuilt veRL container image, run through
podman-hpc, is the least painful option; a conda env from NERSC's
`pytorch` module plus `pip install verl[vllm]==0.9.1` also works. `setup.sh`
then installs tvclient, tvcache-workloads and tvverify into it.

## Variants

`TVCACHE_VARIANT=fixes` (default) runs the `correctness-fixes` branches of
tvclient and tvcache-rust, with a per-lookup trace (`$RUN/trace`).
`TVCACHE_VARIANT=main` runs unmodified TVCache `main` (no trace: it has no
observer hooks). Running both shows whether bugs B1–B3 hit during real
training, and whether the fixes hold.

## What a run leaves in `$RUN`

| Path | What |
|---|---|
| `report.md` | tvverify's combined report: bugs, noise, inconclusive, per task |
| `check/dump_*.json`, `.log` | check-graph results per dump (the last dump also checks snapshots, C2a) |
| `dumps/dump_*.json` | DUMP_ALL every `DUMP_INTERVAL` s (default 600) and at the end |
| `trace/` | TraceRecorder JSONL (fixes variant only) |
| `logs/` | veRL, cache server, manager, podman, sidecar; `run.txt` has the summary and leftover snapshot count |

Knobs: `MODE`, `EPOCHS`, `N_GPUS`, `ROLLOUTS`, `BATCH`, `CHECK_NODES`,
`DUMP_INTERVAL`, `MODEL`, `TASKS_ROOT`, `TVCACHE_VARIANT`.
