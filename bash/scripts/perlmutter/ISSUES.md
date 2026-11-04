# Perlmutter bring-up: what went wrong and how it was fixed

Problems hit while bringing the Terminal-Bench pilot up on Perlmutter
(2026-11-02/03), in the order they came up. "Where" is the file that holds
the fix.

| # | Symptom | Cause | Fix | Where |
|---|---|---|---|---|
| 1 | `sshproxy.sh: command not found` | NERSC's current client is a binary named `sshproxy`, not the old script | Use `sshproxy -u <user>` | README |
| 2 | `sshproxy`: `Device not configured (os error 6)` | Run through Claude Code's `!`, which has no TTY for the password/OTP prompt | Run it in a separate terminal | (none) |
| 3 | Probe would report "podman-hpc runs" as FAIL | `podman-hpc` has no `--version` flag | Check with `podman-hpc version` | `probe_podman.sh` |
| 4 | `docker-compose build`: `crun: create /sys/fs/cgroup/docker: Permission denied` | Compose v2 builds through a BuildKit container, which rootless podman-hpc cannot run | `DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0` (classic builder, goes through the podman API) | `env.sh` |
| 5 | Every `/start` returned 500 with `UnboundLocalError: ... 'e'` | Manager bug: Python unbinds `e` after an `except ... as e` block, so the final `return` crashed and hid the real error | Keep the error in `last_error` | `container-manager-public/rlhf_server/server.py` |
| 6 | The real `/start` error never reached the manager log | `print()` output is block-buffered when piped, and was lost when the manager was killed | `PYTHONUNBUFFERED=1` | `bash/scripts/start_manager.sh` |
| 7 | Every `/start` failed: `copier: put: error setting ownership ... lchown ... invalid argument` | The Docker SDK's `put_archive` tar carried the host uid (115448), which is not mapped inside a rootless podman container. (The probe's `podman cp` check passed because the CLI handles ownership itself.) | Tar entries written as root (uid/gid 0) | `container-manager-public/terminal_bench/terminal/docker_compose_manager.py` |
| 8 | A failed `/start` left containers running (57 after the first prebuild) | The manager's retries do not clean up the containers they started | Cleaned up by hand (`podman-hpc rm -af`); not fixed in the manager | (open) |
| 9 | `fix-git` does not build | Its setup clones `github.com/TheMikeMerrill/personal-site`, which is now 404. Fails everywhere, not just on Perlmutter | Left out of the dataset | `train_pilot.sh` (`EXCLUDE_TASKS`), `verl/dataset.py` (`--exclude`) |
| 10 | `accelerate-maximal-square` does not build: `insufficient UIDs or GIDs ... lchown /llvm-10.0.0.src` | Its base image (`taichidev/taichi`) has files owned by uid 107418, beyond rootless podman's subuid range | Left out of the dataset | same as 9 |
| 11 | `tvverify`: `RuntimeError: tvcache-rust not found` | tvverify looks for `tvcache-rust` next to `tvcache-workloads`; on Perlmutter it lives under `variants/<variant>/` | `TVCACHE_RUST_DIR="$(variant_dir)/tvcache-rust"`, and load `rust/stable` for cargo | `env.sh` |
| 12 | Contract check failed: "a restored snapshot equals the source at fork time" (restored cwd `/app` instead of `/app/work`) | `stage.sh` excluded `tmux-res`, which is the tmux-resurrect plugin (tracked source), so cwd/exported variables were never saved or replayed. The manager logs the copy error but `/start` still succeeds | Stop excluding `tmux-res` | `stage.sh` |
| 13 | First sampling job (60728653) failed after 10 min: "container manager did not open port 9901"; manager log: `Could not acquire lock for ~/.cache/uv ... os error 524` | `$HOME` on compute nodes does not support `flock` (scratch and `/tmp` do). uv and cargo both lock their caches. Login nodes are fine, so only a batch job shows it | `UV_CACHE_DIR` and `CARGO_HOME` on scratch, rebuilt once on the login node to fill them. Checked on a compute node with `probe_locks.sh` | `env.sh` |
| 14 | Second sampling job (60729400) failed after 2 min in veRL's dataset load: `fcntl.flock ... OSError: [Errno 524]` | Same as 13: HF `datasets` locks its cache under `~/.cache/huggingface` | Every cache on scratch: `XDG_CACHE_HOME`, `HF_HOME`, `VLLM_CACHE_ROOT`, `TRITON_CACHE_DIR`, `TORCHINDUCTOR_CACHE_DIR`, `MPLCONFIGDIR`, `CUDA_CACHE_PATH` | `env.sh` |
| 15 | Third sampling job (60729933) failed after 3 min while building the actor model: `FlashAttention2 has been toggled on, but ... the package for FlashAttention2 doesn't seem to be installed` | veRL turns on `flash_attention_2` by default; `flash-attn` is not in `verl-env` (no wheel for torch 2.11 + CUDA 13; building it takes a long time) | **Not fixed yet.** Plan: `+actor_rollout_ref.model.override_config.attn_implementation=sdpa` in `train_pilot.sh` (PyTorch's built-in attention; fine for a 1.5B model), or install `flash-attn` | `train_pilot.sh` (open) |

## Still worth knowing

- **Images are node-local.** podman's storage is `/images` (NVMe) on login
  nodes and `/tmp` (RAM) on compute nodes. The manager rebuilds each task
  image on every `/start`, so a GPU job starts with an empty build cache and
  pays the first build of each task (median 51 s, max 287 s for
  `png-generation`). `podman-hpc migrate` makes a login-built image visible on
  compute nodes.
- **Silent failure in #12.** A missing tmux-resurrect breaks state capture
  without failing `/start`. Only tvverify's contract check caught it. Worth
  making the manager fail loudly.
- **#8 is still open** in the manager.
- On the Mac, the manager's full `pytest tests/unit` hangs without Docker
  running; `tests/unit/terminal` and `tests/unit/snapshots` pass (19 tests).

## Status after the fixes

Smoke test on login03 (2026-11-03), `fixes` variant: contract 11/11 (1 skip),
cache PASS, replay PASS (13/13 nodes, 0 noise), no leftover containers or
snapshot images. Same verdicts as the Mac run.
