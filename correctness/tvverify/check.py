"""`tvverify check`: run the correctness checks against your own workload.

    python -m tvverify check --adapter my_pkg.my_workload:MyAdapter

Suites:
  contract  your Sandbox behaves as the cache assumes (needs no server)
  cache     rollouts through the cache return what direct execution returns
  replay    every cached node matches a fresh-root replay (C1, C2a, C3)
"""

import asyncio
import importlib
import random
from typing import List, Tuple

from tvverify.adapters.base import WorkloadAdapter
from tvverify.model.graph import GraphExport, export_task_graph
from tvverify.replay.engine import ReplayEngine
from tvverify.testing.exact_vs_nocache import run_rollouts_vs_nocache
from tvverify.testing.sandbox_contract import run_contract
from tvverify.testing.server import RustServer
from tvverify.validators.findings import Finding
from tvverify.validators.offline import OfflineValidator


def load_adapter(spec: str) -> WorkloadAdapter:
    """`package.module:ClassName` -> an adapter instance."""
    module_name, _, class_name = spec.partition(":")
    if not class_name:
        raise SystemExit(f"--adapter must look like package.module:ClassName, got {spec!r}")
    module = importlib.import_module(module_name)
    return getattr(module, class_name)()


async def _contract(adapter: WorkloadAdapter, seed: int) -> int:
    results = await run_contract(adapter, seed)
    for result in results:
        print(result)
    failed = [r for r in results if not r.ok]
    print(f"contract: {len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


async def _classify(adapter, engine: ReplayEngine, findings: List[Finding], replays: int) -> Tuple[List[Finding], List[Finding]]:
    """Splits findings into (bugs, noise). A C1/C3 output mismatch is noise
    when fresh replays of the same path disagree with each other: the tool is
    nondeterministic, so the cache cannot be blamed. The same reasoning applies
    to state and snapshot differences along a path that replays inconsistently.
    Anything else is a bug."""
    bugs: List[Finding] = []
    noise: List[Finding] = []

    async def disagrees(path: List[str], key: str) -> bool:
        outputs = [await engine.leaf_output(path, key) for _ in range(replays)]
        compare = adapter.comparator_for(key).compare
        return not all(compare(outputs[0], o, key=key).equal for o in outputs[1:])

    for finding in findings:
        if not finding.path or replays < 2 or finding.criterion not in ("C1", "C2a", "C3"):
            bugs.append(finding)
            continue
        path = list(finding.path)
        if finding.code == "output_mismatch":
            # the call whose cached value was wrong
            is_noise = await disagrees(path[:-1], path[-1])
        else:
            # a state or snapshot difference: noise if any call on the path
            # gives different outputs on different fresh replays
            is_noise = False
            for i in range(len(path)):
                if await disagrees(path[:i], path[i]):
                    is_noise = True
                    break
        (noise if is_noise else bugs).append(finding)
    return bugs, noise


async def _delete_snapshots(adapter: WorkloadAdapter, task_id: str, graph: GraphExport) -> int:
    """Stops every snapshot the cache still references. The test server goes
    away after the run, so for real sandboxes these would otherwise leak
    (committed container images, for example). Returns how many failed."""
    handles = {n.sandbox_id for n in graph.nodes.values() if n.sandbox_id} | set(graph.reclaimable)
    failures = 0
    for handle in sorted(handles):
        try:
            await adapter.sandbox_class(task_name=task_id, env_id=handle).stop()
        except Exception:
            failures += 1
    return failures


async def _cache_and_replay(adapter: WorkloadAdapter, args) -> int:
    status = 0
    rng = random.Random(args.seed)
    with RustServer() as server:
        for task in adapter.tasks():
            # The replay suite checks the trie these rollouts build, so they
            # run for either suite.
            rollouts = adapter.sample_rollouts(task.task_id, rng, args.rollouts)
            mismatches = await run_rollouts_vs_nocache(server.port, adapter, task.task_id, rollouts)
            graph = await export_task_graph(server.port, task.task_id, adapter, task.root_image_id)
            try:
                if args.suite in ("cache", "all"):
                    print(f"cache  {task.task_id}: {'PASS' if not mismatches else f'FAIL ({len(mismatches)} mismatches)'}")
                    for m in mismatches[:10]:
                        print("   ", str(m)[:300])
                    status |= bool(mismatches)
                if args.suite in ("replay", "all"):
                    # memoize=False: real sandboxes are not assumed deterministic.
                    engine = ReplayEngine(adapter, task.task_id, memoize=False)
                    report = await OfflineValidator(adapter, engine).run(graph)
                    bugs, noise = await _classify(adapter, engine, report.findings, args.replays)
                    print(f"replay {task.task_id}: {'PASS' if not bugs else f'FAIL ({len(bugs)} bugs)'}"
                          f", {len(noise)} noise, {report.checked.get('nodes_replayed', 0)} nodes replayed")
                    for f in bugs[:10]:
                        print("   BUG  ", str(f)[:300])
                    for f in noise[:5]:
                        print("   noise", str(f)[:300])
                    status |= bool(bugs)
            finally:
                failed = await _delete_snapshots(adapter, task.task_id, graph)
                if failed:
                    print(f"warning: {failed} cached snapshots of {task.task_id} could not be deleted")
    return status


def main(args) -> int:
    adapter = load_adapter(args.adapter)

    async def go() -> int:
        status = 0
        if args.suite in ("contract", "all"):
            status |= await _contract(adapter, args.seed)
            if status and args.suite == "all":
                print("skipping cache and replay suites: fix the contract failures first")
                return status
        if args.suite in ("cache", "replay", "all"):
            status |= await _cache_and_replay(adapter, args)
        return status

    return asyncio.run(go())
