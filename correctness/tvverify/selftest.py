"""`tvverify selftest`: run the framework's own checks against a fresh server."""

import asyncio
import sys
from typing import Callable, List

from tvverify.adapters.mock_fs import ToyFsSandbox
from tvverify.testing.exact_vs_nocache import run_exact_vs_nocache, run_value_snapshot_coherence
from tvverify.testing.ref_vs_server import run_ref_vs_server
from tvverify.testing.server import RustServer


def run_exact_suite(seeds: int, verbose: bool = True) -> int:
    failures: List[str] = []
    with RustServer() as server:
        port = server.port

        def stage(name: str, fn: Callable[[], List[str]]) -> None:
            found = [str(f) for f in fn()]
            failures.extend(f"[{name}] {f}" for f in found)
            if verbose:
                print(f"{name:<34} {'PASS' if not found else f'FAIL ({len(found)} problems)'}")

        stage("P1 reference model vs server",
              lambda: [p for s in range(seeds) for p in asyncio.run(run_ref_vs_server(port, s))])
        ToyFsSandbox.reset()
        stage("P2 exact cache vs no cache",
              lambda: [m for s in range(seeds) for m in asyncio.run(run_exact_vs_nocache(port, s))])
        stage("P2 ... concurrent rollouts",
              lambda: [m for s in range(seeds) for m in asyncio.run(run_exact_vs_nocache(port, s + 10_000, concurrent=True))])
        stage("P2b value/snapshot coherence",
              lambda: asyncio.run(run_value_snapshot_coherence(port, trials=max(5, seeds // 10))))

    for failure in failures[:20]:
        print("  ", failure[:300])
    if verbose:
        print("selftest", "PASSED" if not failures else f"FAILED ({len(failures)} problems)")
    return 1 if failures else 0


def main(argv: List[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="tvverify")
    sub = parser.add_subparsers(dest="command", required=True)
    selftest = sub.add_parser("selftest", help="check the cache and the framework on mock environments")
    selftest.add_argument("--seeds", type=int, default=100)
    check = sub.add_parser("check", help="run the correctness checks against your own workload")
    check.add_argument("--adapter", required=True, help="package.module:ClassName of your WorkloadAdapter")
    check.add_argument("--suite", choices=["contract", "cache", "replay", "all"], default="all")
    check.add_argument("--rollouts", type=int, default=6)
    check.add_argument("--seed", type=int, default=0)
    check.add_argument("--replays", type=int, default=2, help="fresh replays used to tell noise from bugs (1 disables)")
    args = parser.parse_args(argv)
    if args.command == "check":
        from tvverify.check import main as check_main

        return check_main(args)
    if args.command == "selftest":
        return run_exact_suite(args.seeds)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
