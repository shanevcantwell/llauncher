"""Microbenchmark: psutil process-table walk cost on the Windows seat (#521).

Standalone script — run directly with the venv's Python, not via pytest.
Measures the walk shapes that motivated #521's name-first filtering and
``is_port_in_use`` socket-table fix:

    a) process_iter() with no field projection (pid only, lazily fetched)
    b) process_iter(["pid", "name"]) — cheap, no cmdline() anywhere
    c) process_iter(["pid", "name", "cmdline"]) — the OLD full walk
    d) name-filter-first: walk (b), then cmdline() only on name matches
    e) per-call cmdline() cost sampled across ~20 processes spread across
       the table (isolates the per-process floor from the walk-count term)
    f) psutil.net_connections(kind="tcp") — the OLD-vs-NEW comparison for
       is_port_in_use
    g) the real llauncher functions post-fix, for an end-to-end sanity
       check against (d)/(f)

Usage::

    python docs/experiments/bench_psutil_walk.py

Origin: benchmarked ad hoc under ``%TEMP%\\walk_bench\\bench.py`` on
2026-08-27 (psutil 7.2.1, ~315 processes) to produce the before-numbers
cited in issue #521's root-cause table; this is that script, tidied and
committed as the experiment artifact.
"""

import statistics
import time

import psutil

BINARY_NAMES = ("llama-server", "llama-server.exe")


def timeit(fn, n=5):
    times = []
    result = None
    for _ in range(n):
        t0 = time.perf_counter()
        result = fn()
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1000)
    return times, result


def report(label, times, extra=""):
    print(
        f"{label}: min={min(times):.1f}ms median={statistics.median(times):.1f}ms "
        f"max={max(times):.1f}ms {extra}"
    )


def name_filter_first():
    procs = list(psutil.process_iter(["pid", "name"]))
    matches = []
    for p in procs:
        try:
            name = (p.info.get("name") or "").lower()
        except Exception:
            continue
        if any(b in name for b in BINARY_NAMES):
            try:
                p.cmdline()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
            matches.append(p)
    return procs, matches


def main():
    print("=== a) process_iter() pids only ===")
    times, res = timeit(lambda: list(psutil.process_iter()))
    report("a", times, f"count={len(res)}")

    print("=== b) process_iter(['pid','name']) ===")
    times, res = timeit(lambda: list(psutil.process_iter(["pid", "name"])))
    report("b", times, f"count={len(res)}")

    print("=== c) process_iter(['pid','name','cmdline']) full walk (OLD) ===")
    times, res = timeit(lambda: list(psutil.process_iter(["pid", "name", "cmdline"])))
    report("c", times, f"count={len(res)}")

    print("=== d) name-filter-first (NEW shape) ===")
    d_times = []
    d_matches = 0
    d_total = 0
    for _ in range(5):
        t0 = time.perf_counter()
        procs, matches = name_filter_first()
        t1 = time.perf_counter()
        d_times.append((t1 - t0) * 1000)
        d_matches = len(matches)
        d_total = len(procs)
    report("d", d_times, f"total_procs={d_total} name_matches={d_matches}")

    print("=== e) per-call cmdline() cost on ~20 individual processes ===")
    all_procs = list(psutil.process_iter(["pid", "name"]))
    # Spread selection across the table to include some likely
    # protected/system processes, not just the front of the list.
    sample = all_procs[:: max(1, len(all_procs) // 20)][:20]
    per_call = []
    denied = 0
    denied_times = []
    ok_times = []
    for p in sample:
        t0 = time.perf_counter()
        try:
            p.cmdline()
            t1 = time.perf_counter()
            dt = (t1 - t0) * 1000
            ok_times.append(dt)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            t1 = time.perf_counter()
            dt = (t1 - t0) * 1000
            denied += 1
            denied_times.append(dt)
        per_call.append(dt)

    print(
        f"e: n={len(per_call)} min={min(per_call):.2f}ms "
        f"median={statistics.median(per_call):.2f}ms max={max(per_call):.2f}ms"
    )
    print(f"e: AccessDenied count={denied}")
    if ok_times:
        print(
            f"e: ok-calls min={min(ok_times):.2f}ms "
            f"median={statistics.median(ok_times):.2f}ms max={max(ok_times):.2f}ms"
        )
    if denied_times:
        print(
            f"e: denied-calls min={min(denied_times):.2f}ms "
            f"median={statistics.median(denied_times):.2f}ms max={max(denied_times):.2f}ms"
        )

    print("=== f) net_connections(kind='tcp') (NEW is_port_in_use shape) ===")
    try:
        times, res = timeit(lambda: psutil.net_connections(kind="tcp"), n=5)
        report("f", times, f"count={len(res)}")
    except Exception as e:
        print(f"f: FAILED - {type(e).__name__}: {e}")

    print("=== real functions from llauncher (post-#521 fix) ===")
    from llauncher.core import process as llproc

    g_times, _ = timeit(lambda: llproc.find_all_llama_servers(), n=3)
    report("find_all_llama_servers", g_times)

    h_times, _ = timeit(lambda: llproc.is_port_in_use(8090), n=3)
    report("is_port_in_use(8090)", h_times)


if __name__ == "__main__":
    main()
