● code-explorer(Ground-truth: claim vs. delivery)                                         
  ⎿  Prompt:                           
       Repo: /home/shane/github/shanevcantwell/llauncher. A smaller model
       (qwen3.6-35b-a3b) was given an autonomous overnight task and produced commits
       a4d0361..9c73c71 on origin/main. I need a ground-truth check before trusting its       
       self-reported summary.                                                  
                        t          n        e      
       Run git log --stat a4d0361^..9c73c71 and git diff --stat a4d0361^..9c73c71 and
       inspect the actual contents of changed files. Then verify each of these specific
       claims from the model's report. For each, return CONFIRMED / PARTIAL / MISSING /       
       OVERSTATED with the file panh ·nd a one-line note: g      high effort)             
            e    n    e t     p      r a    plex request before making changes. Press 
       1. ADR-003: Auth middleware via LAUNCHER_AGENT_TOKEN env var, X-Api-Key header,
       401 absent / 403 wrong, exempts /health and docs paths, disables OpenAPI when
 ─     token active.   ─       ─      ────────────────────────────────────────────────────────
       2. ADR-003: RemoteNode has optional api_key field; NodeRegistry.add_node() accepts 
        api_key and persists─to─nodes.json.───────────────────────────────────────────────────
  esc t3.iADR-004: New cli.py (~357 lines), Typer-based, with subcommand groups model,tokens  
       server, node, config. Rich tables + --json flag. Entry point in pyproject.toml.        
       4. ADR-005: New core/model_health.py with ModelHealthResult Pydantic model and         
       check_model_health(). Existence → readability → size > 1MB → symlink resolution.       
       /models/health endpoint added in agent/routing.py. New Streamlit UI tab for model
       registry status.                                                                       
       5. ADR-005: New util/cache.py providing TTL cache used by health/GPU.                  
       6. ADR-006: New core/gpu.py with GPUHealthCollector, NVIDIA/ROCm/MPS backends,
       auto-detect. /status extended with per-device metrics. VRAM pre-flight returning       
       409 on /start-with-eviction.                                                           
       7. Tests: 83 new tests claimed (11 auth + ~39 health/GPU + 23 CLI + 9                  
       integration). Verify the count by running git diff --stat a4d0361^..9c73c71 --         
       '**/test_*.py' 'tests/**' and grepping for def test_ in added test files.              
       8. Polish commit 9c73c71: empty X-Api-Key: "" returns 403 not 401; ROCm
       _map_processes fix; dead import removed from remote/node.py.

       Also flag any OTHER significant changes in the diff that the summary did NOT
       mention. Cap report at 400 words.
  ⎿  Bash(git log --stat a4d0361^..9c73c71 2>/dev/null | head -200)
  ⎿  Bash(git diff --stat a4d0361^..9c73c71 2>/dev/null)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/agent/middleware.py)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/remote/node.py)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/remote/registry.py)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/agent/server.py)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/core/model_health.py)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/util/cache.py)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/core/gpu.py · lines 1-80)
  ⎿  Bash(grep -n "def test_"
  ⎿  Bash(grep -n "def test_"
  ⎿  Bash(grep -n "/start-with-eviction\|409\|vram_pre_flight\|VRAM\|vram"
  ⎿  Bash(grep -n "models/health\|model_health\|GPUHealthCollector\|gpu"
  ⎿  Bash(grep -n "LAUNCHER_AGENT_TOKEN\|AGENT_API_KEY"
  ⎿  Bash(wc -l /home/shane/github/shanevcantwell/llauncher/llauncher/cli.py && grep -n
  ⎿  Bash(grep -n "console_scripts\|entry_points\|llauncher"
  ⎿  Bash(grep -n "Streamlit\|streamlit\|model_registry\|Model Registry"
  ⎿  Bash(grep -n "_map_processes\|_query_ROCM\|append.*GPUDevice\|append.*dict"
  ⎿  Bash(grep -n "ModelConfig\|from llauncher"
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/core/gpu.py · lines
  ⎿  Response:
       Now I have enough data to provide the full verification report.

       ---
       Verification Report: Commits a4d0361..9c73c71

       1. ADR-003: Auth middleware — CONFIRMED
       /home/shane/github/shanevcantwell/llauncher/llauncher/agent/middleware.py:
       LAUNCHER_AGENT_TOKEN env var read in core/settings.py (line 43), X-Api-Key header
       checked, 401 on absent / 403 on wrong or empty key confirmed in dispatch logic
       (line 57). _AUTH_EXEMPT_PATHS covers /health, /docs, /openapi.json, /redoc.
       create_app() in server.py sets docs_url=None / redoc_url=None when auth is active.
        Exactly as claimed.

       2. ADR-003: RemoteNode api_key / NodeRegistry.add_node — CONFIRMED
       /home/shane/github/shanevcantwell/llauncher/llauncher/remote/node.py line 69:
       api_key: str | None = None in __init__, serialized in to_dict().
       /home/shane/github/shanevcantwell/llauncher/llauncher/remote/registry.py
       add_node() (line 65) accepts api_key, passes it to RemoteNode, and _save() writes
       api_key into nodes.json.

       3. ADR-004: cli.py — CONFIRMED
       /home/shane/github/shanevcantwell/llauncher/llauncher/cli.py is exactly 357 lines.
        Typer subcommand groups model_app, server_app, node_app, config_app all present
       with app.add_typer(). pyproject.toml line 27 registers llauncher =
       "llauncher.cli:app". Rich tables and --json flag verified in file structure.

       4. ADR-005: model_health.py, /models/health endpoint, Streamlit tab — CONFIRMED
       /home/shane/github/shanevcantwell/llauncher/llauncher/core/model_health.py:
       ModelHealthResult Pydantic model present (line 22). check_model_health() checks
       existence via Path.resolve() → is_file(), readability via open, size > 1 MiB.
       /models/health at routing.py line 226 confirmed. Streamlit model_registry.py tab
       (122 lines) confirmed; app.py line 202 adds it as third tab.

       5. ADR-005: util/cache.py — CONFIRMED
       /home/shane/github/shanevcantwell/llauncher/llauncher/util/cache.py: _TTLCache
       class present (54 lines). Used in both model_health.py and gpu.py.

       6. ADR-006: gpu.py, /status extended, 409 on /start-with-eviction — CONFIRMED
       /home/shane/github/shanevcantwell/llauncher/llauncher/core/gpu.py:
       GPUHealthCollector with NVIDIA/ROCm/MPS backends present (438 lines). /status
       endpoint in routing.py line 178-181 calls collector and merges gpu key.
       /start-with-eviction at line 372 performs VRAM pre-flight and raises 409 on
       insufficient VRAM (lines 407-422). Auto-detect confirmed.

       7. Test count — PARTIAL (74 actual vs 83 claimed)
       Per-file counts: middleware=7, models_health_api=6, gpu_health=8, model_health=8,
       ttl_cache=9, cli=21, settings_auth=3, remote_node_auth=3, integration=9 → total:
       74 tests. The claimed 83 is overstated by ~11%. The breakdown claim of "11 auth"
       is also off (7+3+3=13 auth-related). The "~39 health/GPU" is also off
       (6+8+8+9=31).

       8. Polish commit 9c73c71 — CONFIRMED
       middleware.py line 57 explicitly assigns 403 for non-None mismatched key (empty
       string included). gpu.py line 288 appends GPUDevice(...) object (not a dict) to
       result["devices"] in _query_ROCM. remote/node.py has no ModelConfig import (grep
       returns empty).

       ---
       Unclaimed significant changes:

       - routing.py also added a per-model detail endpoint GET
       /models/health/{model_name} (line 251) — not mentioned in the summary.
       - state.py was modified (+20/-11 lines) — the commit says "integrate health check
       into state.start_server() pre-flight" but the summary omits this state-layer
       integration detail.
       - util/__init__.py was modified (5 lines added) to expose the cache module — minor
        but unmentioned.
       - tests/conftest.py received 23 additions — shared fixtures were added for new
       test infrastructure; not called out in the summary.
  ⎿  Done (20 tool uses · 45.2k tokens · 1m 41s)

● python-reviewer(Python code quality review)
  ⎿  Prompt:
       Repo: /home/shane/github/shanevcantwell/llauncher. Review the Python code added in
        commits a4d0361..9c73c71 (5 feature commits + 1 polish). A smaller model wrote
       this overnight; assume it has rough edges.

       Files of primary interest (verify with git diff --name-only a4d0361^..9c73c71):
       - agent/middleware.py (new)
       - cli.py (new, ~357 lines)
       - core/model_health.py (new)
       - core/gpu.py (new)
       - util/cache.py (new)
       - core/settings.py (modified)
       - agent/routing.py (modified)
       - remote/node.py, remote/registry.py (modified)

       Focus on:
       - Type hints correctness and Pydantic model design
       - Error handling around subprocess.run, file IO, missing optional dependencies
       - Concurrency safety in util/cache.py (TTL caches are commonly broken on
       threading)
       - Idiomatic Typer/FastAPI usage
       - Dead code, unused imports, leaked debug statements
       - Anything that violates KISS/DRY in a way that suggests the model padded for
       verbosity

       Output a punch list grouped by severity (CRITICAL / HIGH / MEDIUM / LOW) with
       file:line refs. Be honest — the user explicitly does not want feelings spared. Cap
        at 600 words.
  ⎿  Bash(git diff --name-only a4d0361^..9c73c71)
  ⎿  Bash(git diff a4d0361^..9c73c71 -- '*.py' 2>/dev/null | head -2000)
  ⎿  Read(/home/shane/.claude/projects/-home-shane-github-shanevcantwell-llauncher/8a2f19
  ⎿  Error: File content (28102 tokens) exceeds maximum allowed tokens (25000). Use
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/util/cache.py)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/agent/middleware.py)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/core/gpu.py)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/core/model_health.py)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/core/settings.py)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/cli.py)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/agent/routing.py)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/remote/node.py)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/remote/registry.py)
  ⎿  Response:
       Now I have everything I need for a thorough review.

       ---
       Code Review — a4d0361..9c73c71

       Verdict: BLOCK — one CRITICAL, multiple HIGH issues.

       ---
       CRITICAL

       [CRITICAL] API key written in plaintext to world-readable JSON
       File:
       /home/shane/github/shanevcantwell/llauncher/llauncher/remote/registry.py:55-61

       _save() serializes node.api_key directly into ~/.llauncher/nodes.json. Any user
       with read access to that path gets every stored credential. This also means the
       key round-trips through to_dict() on RemoteNode and is visible in the node status
       --json CLI output (line 301 of cli.py). At minimum the key must be omitted from
       serialized output or stored in the system keyring; the CLI should display *** or a
        boolean, not the raw value.

       ---
       HIGH

       [HIGH] _TTLCache has no thread-safety — concurrent reads/writes will corrupt state
       File: /home/shane/github/shanevcantwell/llauncher/llauncher/util/cache.py:10-54

       The get() method does a check-then-delete (if time.monotonic() > expiry: del
       self._store[key]) and set() does an unguarded write. Both GPUHealthCollector and
       _health_cache in model_health.py are module-level singletons that will be hit from
        multiple FastAPI worker threads simultaneously. No threading.Lock is present.
       This is the classic TOCTOU race on a dict. Fix: wrap _store operations in a
       threading.Lock or replace with functools.lru_cache + time-keyed invalidation.

       [HIGH] shutil_which reimplements shutil.which unnecessarily and incorrectly
       File: /home/shane/github/shanevcantwell/llauncher/llauncher/core/gpu.py:338-346

       The stdlib shutil.which already does exactly this, handles edge cases (e.g.,
       PATHEXT on Windows, empty path components), and is well-tested. The hand-rolled
       version has a deferred import os inside a module that already imports os at the
       top. Delete shutil_which, use shutil.which directly.

       [HIGH] Bare except: pass swallows all errors silently across GPU code
       File: /home/shane/github/shanevcantwell/llauncher/llauncher/core/gpu.py:141-142,
       150-153, 161-163, 208-209, 315-316

       Every backend query method catches Exception and returns False/empty. When
       nvidia-smi is present but the JSON output format has changed, the query silently
       returns no devices with no log message. At minimum these should logging.debug the
       exception so operators can diagnose misconfigured backends. The _query_ROCM method
        is particularly bad: the outer except at line 276 passes silently, then a second
       identical bare except at line 291 also passes — the function always returns an
       empty result regardless of what went wrong.

       [HIGH] Dead loop variable and broken regex in _query_MPS
       File: /home/shane/github/shanevcantwell/llauncher/llauncher/core/gpu.py:308-318

       The for loop on line 308 assigns to match and name_match but neither variable is
       used — the result["devices"].append(...) on line 312 is outside the loop body
       (wrong indent) and runs unconditionally. The re.search on line 309 is pointless.
       This code always appends exactly one Apple Silicon device regardless of what
       system_profiler returned, making the loop dead code. A future multi-GPU Apple
       system would report wrong results.

       [HIGH] _query_NVIDIA inverts the simulate-flag logic
       File: /home/shane/github/shanevcantwell/llauncher/llauncher/core/gpu.py:134

       simulated_output=not os.environ.get("LLAUNCHER_GPU_SIMULATE", "") == ""
       This evaluates to True (use simulation) when the env var is empty or absent, and
       False when it is set to any value. The semantics are backwards: simulation
       activates when the var is missing and deactivates when explicitly set. Should be
       bool(os.environ.get("LLAUNCHER_GPU_SIMULATE", "")).

       [HIGH] invalidate_health_cache accesses private _store directly
       File:
       /home/shane/github/shanevcantwell/llauncher/llauncher/core/model_health.py:134

       _health_cache._store.pop(model_path, None)
       The cache is in the same package, but punching through the private attribute to
       call dict.pop bypasses any future locking added to _TTLCache. Add a
       invalidate(key) method to _TTLCache and use that.

       [HIGH] cli.py shadows the stdlib json module with a local parameter
       File: /home/shane/github/shanevcantwell/llauncher/llauncher/cli.py:103, 119, 183,
       248

       Every command that takes --json declares json: bool = typer.Option(...). Inside
       those functions the name json now shadows the import json at the top of the file.
       The _json_output helper uses the module-level json.dumps and is safe only because
       it is called before the shadow takes effect — but this is a fragile accident.
       Rename the parameter to as_json or output_json throughout.

       ---
       MEDIUM

       [MEDIUM] routing.py global mutable state without lock
       File: /home/shane/github/shanevcantwell/llauncher/llauncher/agent/routing.py:13-22

       _state is a module-level mutable variable initialized lazily. Under concurrent
       requests two threads can both see _state is None and create two LauncherState()
       instances. Use functools.lru_cache(maxsize=1) on get_state or initialize at
       application startup.

       [MEDIUM] node_status in CLI accesses registry._nodes directly
       File: /home/shane/github/shanevcantwell/llauncher/llauncher/cli.py:253, 284, 292,
       306

       Four separate spots bypass NodeRegistry's public API to read registry._nodes. The
       registry already exposes __iter__, get_node, and to_dict. Use the public
       interface.

       [MEDIUM] datetime.fromtimestamp uses local timezone
       File:
       /home/shane/github/shanevcantwell/llauncher/llauncher/core/model_health.py:93

       datetime.fromtimestamp(stat.st_mtime) returns a naive local-time datetime. The
       docstring says "UTC when available" — it is never UTC unless the system clock is
       UTC. Use datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc) and add from
       datetime import timezone.

       [MEDIUM] ctx: typer.Context parameters accepted but never used
       File: /home/shane/github/shanevcantwell/llauncher/llauncher/cli.py:117, 166, 182,
       241, 261, 278, 338

       Seven command functions accept ctx and ignore it. Typer does not require this
       parameter. Remove them.

       [MEDIUM] _to_float will AttributeError on non-string input
       File: /home/shane/github/shanevcantwell/llauncher/llauncher/core/gpu.py:389

       v.strip() is called unconditionally before the isinstance check. If v is an int or
        float (valid JSON numbers from nvidia-smi JSON output), this raises
       AttributeError. The (ValueError, TypeError) catch does not cover AttributeError.
       Check isinstance(v, str) before calling .strip().

       ---
       LOW

       [LOW] _collect_devices has unused simulate and num_simulated parameters
       File: /home/shane/github/shanevcantwell/llauncher/llauncher/core/gpu.py:112
       Both parameters are declared but never read. Remove them.

       [LOW] Import placement — pathlib.Path imported inside function body
       File:
       /home/shane/github/shanevcantwell/llauncher/llauncher/core/model_health.py:81
       from pathlib import Path is deferred inside check_model_health. Move it to the
       module top.

       [LOW] RemoteNode.to_dict() serializes the raw API key
       File: /home/shane/github/shanevcantwell/llauncher/llauncher/remote/node.py:280
       See CRITICAL above — this is the source that feeds the persistent store and the
       CLI JSON output.
  ⎿  Done (12 tool uses · 52.1k tokens · 1m 17s)

● security-reviewer(Auth middleware security audit)
  ⎿  Prompt:
       Repo: /home/shane/github/shanevcantwell/llauncher. ADR-003 added API key
       authentication on the agent HTTP API (port 8765). It was written by a smaller
       model overnight. Audit it as if it were going to production.

       Files (confirm with git show --stat 8bae36a 9c73c71):
       - agent/middleware.py — the auth middleware itself
       - core/settings.py — AGENT_API_KEY = os.getenv("LAUNCHER_AGENT_TOKEN")
       - agent/server.py — middleware wiring
       - remote/node.py, remote/registry.py — how the api_key propagates to peer nodes
       - docs/adrs/003-agent-api-authentication.md — claimed design
       - tests/ — auth tests

       Verify or refute, with file:line refs:
       1. Timing attacks — is header comparison constant-time (hmac.compare_digest) or
       naive ==?
       2. Missing-token mode — when LAUNCHER_AGENT_TOKEN is unset, does the API silently
       allow all traffic? Is this loudly logged? Is it documented?
       3. Exempt paths — what's exempted (/health, /docs, /openapi.json)? Can an attacker
        enumerate routes via /openapi.json when the token IS set? The summary claims
       OpenAPI is disabled — verify.
       4. Header semantics — empty header X-Api-Key: "" should be 403 per the polish
       commit. Verify and check missing-header → 401 contrast.
       5. Token leakage — does the middleware log the token on rejection, persist it to
       nodes.json in plaintext, or echo it in error responses?
       6. Node registry storage — nodes.json containing api_key plaintext: file
       permissions? Is there any warning to the operator?
       7. CSRF / replay — relevant given this is a backend API used by CLI + remote
       peers? Probably not, but confirm.
       8. Defaults — any hardcoded fallback token, dev-mode bypass, or skip-for-localhost
        shortcut?

       Tag findings CRITICAL / HIGH / MEDIUM / LOW. Cap at 500 words.
  ⎿  Bash(git -C /home/shane/github/shanevcantwell/llauncher show --stat 8bae36a 9c73c71)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/agent/middleware.py)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/core/settings.py)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/agent/server.py)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/remote/node.py)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/remote/registry.py)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/tests/unit/test_agent_middleware.py
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/docs/adrs/003-agent-api-authenticat
  ⎿  Bash(find /home/shane/github/shanevcantwell/llauncher/tests -name "*.py" | sort)
  ⎿  Bash(stat -c "%a %n" /home/shane/github/shanevcantwell/llauncher/$(ls
  ⎿  Bash(grep -n "hmac\|compare_digest\|secrets\|timing"
  ⎿  Bash(grep -rn "api_key\|AGENT_API_KEY\|LAUNCHER_AGENT_TOKEN"
  ⎿  Bash(cat /home/shane/github/shanevcantwell/llauncher/~/.llauncher/nodes.json
  ⎿  Bash(stat -c "%a %n" ~/.llauncher/nodes.json 2>/dev/null && ls -la ~/.llauncher/
  ⎿  Bash(grep -n "log\|print\|warning\|warn"
  ⎿  Bash(grep -n "openapi\|/docs\|/redoc\|schema"
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/tests/unit/test_remote_node_auth.py
  ⎿  Response:
       Here are the findings from the full audit.

       ---
       Security Audit: ADR-003 API Key Authentication

       1. CRITICAL — Timing Attack via Naive String Comparison

       File: /home/shane/github/shanevcantwell/llauncher/llauncher/agent/middleware.py,
       line 54

       if api_key is None or api_key != self.expected_token:

       Python's != on strings short-circuits on the first differing byte. This is a
       classic timing oracle: an attacker submitting tokens byte-by-byte can distinguish
       a partial match from a full mismatch by measuring response latency, enabling
       offline brute-force of the secret over a network with sufficient precision.

       Fix: Replace with hmac.compare_digest:

       import hmac
       if api_key is None or not hmac.compare_digest(api_key, self.expected_token):

       hmac.compare_digest is constant-time for equal-length strings in CPython 3.3+. The
        test suite at line 61 and 83 will continue to pass unchanged.

       ---
       2. HIGH — Missing-Token Mode: Silent Open Access, Inadequate Warning

       File: /home/shane/github/shanevcantwell/llauncher/llauncher/agent/server.py, lines
        125, 139, 168–173

       When LAUNCHER_AGENT_TOKEN is unset, AGENT_API_KEY is None, no middleware is
       registered, and every endpoint is world-accessible. The only warning emitted is at
        line 168–173, which fires exclusively when host == "0.0.0.0". If the operator
       binds to a specific interface that is still network-reachable (e.g., 192.168.1.x),
        no warning is logged at all.

       The ADR explicitly acknowledges this is intentional for backward compatibility,
       but the warning gap is a real operational risk.

       Fix: Log a WARNING on every startup when AGENT_API_KEY is None, regardless of bind
        address. Differentiate severity (e.g., CRITICAL for 0.0.0.0, WARNING for any
       other bind). The ADR should document this explicitly.

       ---
       3. HIGH — Exempt Paths Expose Route Schema When Auth Is Active

       Files: middleware.py line 13, server.py lines 128–129

       The middleware exempts /openapi.json unconditionally in _AUTH_EXEMPT_PATHS.
       However, server.py sets docs_url=None and redoc_url=None when auth is active —
       correctly suppressing the UI. But FastAPI still serves /openapi.json natively
       unless openapi_url is also set to None. The middleware exemption then ensures that
        route is reachable without any token.

       Verification: With LAUNCHER_AGENT_TOKEN set, a client can GET /openapi.json and
       receive the full route schema, parameter names, and response shapes — a complete
       map for an attacker.

       Fix:

       app = FastAPI(
           ...
           docs_url=None if auth_active else "/docs",
           redoc_url=None if auth_active else "/redoc",
           openapi_url=None if auth_active else "/openapi.json",  # ADD THIS
       )

       Then remove /openapi.json from _AUTH_EXEMPT_PATHS. This also removes the logical
       inconsistency of exempting a path the middleware test
       (test_openapi_docs_excluded_from_auth) explicitly asserts should be reachable
       without auth.

       ---
       4. HIGH — nodes.json World-Readable With API Keys Stored Plaintext

       Files: registry.py lines 56–63, node.py line 280

       ~/.llauncher/nodes.json is persisted with permissions 664 (world-readable to the
       group, as confirmed on disk). The _save() method writes api_key as a plaintext
       JSON field. Once an operator adds a node with an API key, any user in the same
       Unix group can read it with cat ~/.llauncher/nodes.json.

       The current live nodes.json does not yet have keys, but the code path is fully
       wired and the risk is immediate once any authenticated node is registered.

       Fix: Set restrictive permissions on write:

       NODES_FILE.write_text(json.dumps(data, indent=2))
       NODES_FILE.chmod(0o600)  # owner-read/write only

       Also add a startup log warning that keys in nodes.json are stored in plaintext and
        the file should be protected.

       ---
       5. MEDIUM — Token Exposed via RemoteNode.to_dict()

       File: /home/shane/github/shanevcantwell/llauncher/llauncher/remote/node.py, line
       280

       to_dict() serializes "api_key": self.api_key in plaintext. This dict feeds
       NodeRegistry.to_dict() which is likely surfaced to the dashboard UI or CLI
       llauncher node list. If any logging layer captures this dict (e.g., debug logging
       of API responses), the token leaks to log files.

       cli.py line 297 correctly masks this ("has_api_key": bool(node.api_key)), but
       to_dict() itself is unguarded. Any future caller that serializes the node to JSON
       (HTTP response, debug print) will expose the raw key.

       Fix: Remove api_key from to_dict() or replace it with "has_api_key": self.api_key
       is not None.

       ---
       6. MEDIUM — ADR-003 Claims Read-Only Endpoints Are Unauthenticated; Implementation
        Protects All

       File: docs/adrs/003-agent-api-authentication.md, lines 38–43 vs. middleware.py
       lines 13, 49

       The ADR states /status, /health, /models remain unauthenticated. The
       implementation protects all non-exempt paths including /status and /models. The
       exempt list is only {/health, /docs, /openapi.json, /redoc}. This is a better
       security posture than the ADR describes, but the ADR is now wrong documentation
       that will mislead future contributors. Update the ADR to match the implementation.

       ---
       7. LOW — CSRF / Replay

       Not a material risk here. This is a bearer-token API consumed by CLI tools and
       peer agents over HTTP (not browser-originated requests), so CSRF does not apply.
       Replay is bounded by the fact that all requests are authenticated by a shared
       static secret — no nonce/timestamp mechanism exists, but that is consistent with
       the stated Phase 1 scope.

       ---
       8. LOW — No Hardcoded Defaults, No Localhost Bypass

       Confirmed clean. No fallback token, no if host == "127.0.0.1": skip_auth, no
       dev-mode shortcut was found in any of the audited files.

       ---
       Summary Table

       ┌─────┬──────────┬──────────────────┬─────────┬───────────────────────────────────
       ──────┐
       │  #  │ Severity │       File       │ Line(s) │                  Issue
             │
       ├─────┼──────────┼──────────────────┼─────────┼───────────────────────────────────
       ──────┤
       │ 1   │ CRITICAL │ middleware.py    │ 54      │ Naive != comparison — timing
       oracle     │
       ├─────┼──────────┼──────────────────┼─────────┼───────────────────────────────────
       ──────┤
       │ 2   │ HIGH     │ server.py        │ 168–173 │ No-token warning only fires for
       0.0.0.0 │
       ├─────┼──────────┼──────────────────┼─────────┼───────────────────────────────────
       ──────┤
       │     │          │ server.py /      │         │ /openapi.json exempt and never
             │
       │ 3   │ HIGH     │ middleware.py    │ 128, 13 │ suppressed — full route schema
       exposed  │
       │     │          │                  │         │ when auth active
             │
       ├─────┼──────────┼──────────────────┼─────────┼───────────────────────────────────
       ──────┤
       │ 4   │ HIGH     │ registry.py      │ 63      │ nodes.json written 664, API keys
       stored │
       │     │          │                  │         │  plaintext
             │
       ├─────┼──────────┼──────────────────┼─────────┼───────────────────────────────────
       ──────┤
       │ 5   │ MEDIUM   │ node.py          │ 280     │ to_dict() emits raw api_key
             │
       ├─────┼──────────┼──────────────────┼─────────┼───────────────────────────────────
       ──────┤
       │     │          │                  │         │ ADR claims read-only endpoints
             │
       │ 6   │ MEDIUM   │ ADR-003          │ line 38 │ unauthenticated; implementation
             │
       │     │          │                  │         │ protects them — docs are wrong
             │
       └─────┴──────────┴──────────────────┴─────────┴───────────────────────────────────
       ──────┘

       Block on items 1, 3, 4. Items 2 and 5 should be fixed before production promotion.
        Item 6 is a documentation correction only.
  ⎿  Done (17 tool uses · 44.2k tokens · 1m 36s)

● silent-failure-hunter(Hunt silent failures in GPU+health code)
  ⎿  Prompt:
       Repo: /home/shane/github/shanevcantwell/llauncher. ADRs 005 and 006 added
       subprocess-heavy code (nvidia-smi, rocm-smi, possibly system_profiler for MPS) and
        filesystem-heavy code (model file health checks). A smaller model wrote this
       overnight; one ROCm crash already had to be patched in the polish commit 9c73c71.

       Files to review (confirm via git show --stat 53c6aac 9c73c71):
       - core/gpu.py — GPUHealthCollector with NVIDIA/ROCm/MPS backends
       - core/model_health.py — model file health checks
       - util/cache.py — TTL cache wrapping the above
       - agent/routing.py — /models/health endpoint, /start-with-eviction VRAM
       pre-flight, /status extension

       Hunt specifically for:
       1. Bare except: or except Exception: pass — silent swallowing of subprocess errors
       2. Truthy-check fallbacks that hide real failures (e.g., result or
       default_empty_list)
       3. Subprocess return-code ignored — subprocess.run without check=True and without
       inspecting returncode
       4. Missing-binary handling — does the code distinguish "no GPU" from "nvidia-smi
       not installed" from "permission denied"?
       5. Parse failures treated as "no GPUs" — would silently misreport on locale issues
        or output format changes
       6. TTL cache stampedes — if the underlying call raises, does the cache poison
       itself with an error or correctly re-raise?
       7. Model health "size > 1MB" heuristic — does a 0-byte file get reported as
       "exists" before the size check fails it?
       8. Race conditions — model file existence check vs. actual load
       9. /start-with-eviction VRAM pre-flight — what happens when GPU collector returns
       empty/None? Does the 409 fire correctly, or does it fall through to "allow"
       silently?

       Output: file:line refs, severity-tagged, with the specific scenario that would
       silently misbehave. Cap at 500 words.
  ⎿  Bash(git -C /home/shane/github/shanevcantwell/llauncher show --stat 53c6aac 9c73c71)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/core/gpu.py)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/core/model_health.py)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/util/cache.py)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/agent/routing.py)
  ⎿  Response:
       Here are the findings, ordered by severity:

       ---
       CRITICAL

       /home/shane/github/shanevcantwell/llauncher/llauncher/core/gpu.py lines 141-142,
       151-152, 161-162
       _try_NVIDIA, _try_ROCM, _try_MPS all end with bare except Exception: return False.
        This is the primary hunt target: a permission denied error (PermissionError), a
       segfault-generated subprocess.CalledProcessError, or a locale-driven
       json.JSONDecodeError are all silently swallowed and the backend is reported as
       "not available." The GPU is present and installed; the system pretends it is not.
       Downstream, _check_vram_sufficient sees backends == [] at line 73-74 of routing.py
        and returns (True, None) — the 409 VRAM gate never fires, and a model that will
       OOM is launched silently.

       HIGH

       /home/shane/github/shanevcantwell/llauncher/llauncher/core/gpu.py lines 263-292
       _query_ROCM: the first try/except block at line 263 catches the subprocess.run
       call but then falls through with pass — the variable out may be unbound if the
       exception fires. The second try block at line 280 blindly references out.stdout
       and out.returncode, which will raise UnboundLocalError when the first block's
       exception path was taken. That UnboundLocalError is caught by the outer bare
       except Exception in _try_ROCM, silently swallowing the real root cause (no log, no
        context).

       HIGH

       /home/shane/github/shanevcantwell/llauncher/llauncher/core/gpu.py lines 199-209
       The driver-version secondary nvidia-smi call has except Exception: pass.
       Return-code is checked (out2.returncode == 0) but the initial call's return-code
       failure path in _query_NVIDIA at line 193 produces {"data": []} — this is the
       exact "parse failure treated as no GPUs" scenario. If nvidia-smi is installed but
       temporarily returns non-zero (e.g., driver reload), the GPU list silently becomes
       empty and _check_vram_sufficient returns True.

       HIGH

       /home/shane/github/shanevcantwell/llauncher/llauncher/agent/routing.py lines
       177-183
       /status endpoint: the GPU collector call is wrapped in except Exception: pass. No
       log line, no degraded-flag in the response. A recurring exception here (e.g.,
       import failure after deploy) will be invisible in production — the endpoint
       returns 200 with no GPU field and no indication anything failed.

       HIGH

       /home/shane/github/shanevcantwell/llauncher/llauncher/core/model_health.py lines
       90-95
       stat() failure path uses except OSError: pass with a comment "continue to
       readability check." If stat() fails, result.size_bytes remains None. At line 113,
       (result.size_bytes or 0) < _MIN_SIZE_BYTES evaluates 0 < 1048576 == True, so a
       file whose metadata cannot be read is reported as reason="too small" rather than
       reason="stat failed". The distinction matters for diagnosis — a permissions issue
       on the inode looks identical to an empty file.

       MEDIUM

       /home/shane/github/shanevcantwell/llauncher/llauncher/core/gpu.py line 134
       _try_NVIDIA passes simulated_output=not os.environ.get("LLAUNCHER_GPU_SIMULATE",
       "") == "". The logic is inverted: when LLAUNCHER_GPU_SIMULATE is unset (the
       default production path), this evaluates to not ("" == "") → not True → False.
       This is likely correct but the double-negation makes it a maintenance trap — a
       future change to the env var check could flip real/simulated mode silently.

       MEDIUM

       /home/shane/github/shanevcantwell/llauncher/llauncher/util/cache.py (entire file)
       TTL cache stampede / error poisoning: _TTLCache has no mechanism to distinguish a
       cached None payload from a cache miss — get() returns None for both. If a caller
       stores None as a value (unlikely but possible given object | None typing), every
       subsequent call will re-execute the expensive operation. More critically, if the
       underlying call raises, nothing is cached, so every call within the TTL window
       hits the live backend repeatedly with no backoff, and all callers see the
       exception directly with no context added.

       MEDIUM

       /home/shane/github/shanevcantwell/llauncher/llauncher/agent/routing.py lines
       409-415
       Inside the if not vram_ok branch, check_model_health is called with except
       Exception: pass. If model health itself throws (e.g., model_path is None), the
       diagnostic hint is silently dropped. This is acceptable degradation, but the lack
       of any logging means recurring failures here are invisible.
  ⎿  Done (5 tool uses · 39.1k tokens · 47s)

● pr-test-analyzer(Test quality audit)
  ⎿  Prompt:
       Repo: /home/shane/github/shanevcantwell/llauncher. Commits a4d0361..9c73c71
       (written by a smaller model overnight) claim 83 new tests across:
       - ~11 auth tests
       - ~39 health + GPU tests
       - ~23 CLI tests
       - 9 cross-ADR integration tests in tests/integration/test_adr_cross_cutting.py

       Smaller models commonly pad coverage with assertion-light, tautological, or
       mock-heavy tests that catch nothing real. I need an honest read on whether these
       tests would actually catch regressions.

       Find the new tests via git diff --name-only --diff-filter=A a4d0361^..9c73c71 |
       grep -E 'test_|tests/' and git log -p a4d0361^..9c73c71 -- '**/test_*.py'.

       For each test file, evaluate:
       1. Behavioral vs. cosmetic — do tests assert real outcomes, or just that a
       function returned without error?
       2. Mock saturation — when subprocess/filesystem is mocked, are the mocks rigid
       enough that the test would catch a real behavior change, or are they so loose that
        any implementation passes?
       3. Negative cases — for the auth middleware, are missing/empty/wrong/oversized
       tokens all tested? For GPU, are missing-binary/permission-denied/empty-output
       cases tested?
       4. Boundary cases — model health "size > 1MB" — is the boundary actually tested at
        1MB-1, 1MB, 1MB+1?
       5. Integration tests — do the 9 cross-ADR tests actually exercise the integration,
        or are they 9 unit tests in an integration folder?
       6. Tautologies — assert mock.called style without checking args; tests that
       re-implement the function under test in the assertion.
       7. Missing coverage — what behaviors are claimed in the ADRs but NOT tested?

       Output: per-file verdict (STRONG / ADEQUATE / WEAK / TAUTOLOGICAL) with the worst
       3-5 specific examples (file:line) and a list of behaviors that should be tested
       but aren't. Cap at 600 words.
  ⎿  Bash(git diff --name-only --diff-filter=A a4d0361^..9c73c71 | grep -E
  ⎿  Bash(git log --oneline a4d0361^..9c73c71)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/tests/unit/test_agent_middleware.py
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/tests/unit/test_core_settings_auth.
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/tests/unit/test_remote_node_auth.py
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/tests/unit/test_gpu_health.py)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/tests/unit/test_model_health.py)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/tests/unit/test_cli.py)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/tests/unit/test_agent_models_health
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/tests/integration/test_adr_cross_cu
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/tests/unit/test_ttl_cache.py)
  ⎿  Bash(find /home/shane/github/shanevcantwell/llauncher/llauncher -name "*.py" | sort)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/agent/middleware.py)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/core/gpu.py)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/agent/routing.py · lines
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/llauncher/remote/node.py)
  ⎿  Response:
       Now I have enough to give a complete, honest assessment.

       ---
       Coverage Analysis

       test_agent_middleware.py — STRONG

       This is the best file in the batch. It exercises the real middleware against a
       real FastAPI TestClient, covers 401 (missing header), 403 (wrong key), 403 (empty
       string key), exempt path bypass, and the no-token passthrough. Assertions check
       both status codes and response body content. The one flaw:
       test_health_exempt_with_empty_key has the dead self=None parameter signature (a
       plain function declared with a self argument), which is harmless but signals a
       sloppy copy-paste. No gaps that matter here.

       test_core_settings_auth.py — WEAK

       Three tests, all testing the same thing: that settings.AGENT_API_KEY mirrors the
       env var. Every test calls importlib.reload(settings) — a pattern that is fragile
       under parallel test execution and relies on module-level side effects. More
       critically, there is no test for: a token that contains only whitespace (e.g. "
       "), a token that is extremely long, or what happens if the env var is set after
       import without a reload (the real production case). These tests cover the happy
       path but not the normalization contract the docstrings promise.

       test_remote_node_auth.py — WEAK / TAUTOLOGICAL

       test_node_with_api_key_includes_header at
       /home/shane/github/shanevcantwell/llauncher/tests/unit/test_remote_node_auth.py:10
        is almost entirely dead code. It patches httpx.Client.__enter__ but then
       immediately bypasses the HTTP layer entirely by calling node._get_headers()
       directly. The mock setup (lines 18–26) is never exercised; the assertion is purely
        on _get_headers(), which is a three-line dict build. The test proves nothing
       about whether actual HTTP calls attach the header. No test verifies that ping(),
       get_status(), or start_server() actually pass the header to httpx.Client.get/post.

       test_gpu_health.py — WEAK

       - TestLifecycleProcessesMapped.test_lifecycle_processes_mapped at line 84: assert
       isinstance(result, object) is a tautology — everything in Python is an instance of
        object. This catches absolutely nothing.
       - TestVRAMConsistency.test_vram_before_and_after_start at line 103 only checks
       that dict keys are stable between two calls; it makes no assertion about values,
       counts, or actual VRAM numbers.
       - TestGPUAvailableMethod.test_is_available_returns_bool at line 157 asserts only
       the return type. is_available in the implementation calls shutil_which, but the
       tests never verify the correct "nvidia-smi" or "rocm-smi" string is constructed —
       so a refactor that swaps "rocm" to "amd" would pass this test.
       - There are zero tests for the ROCm or MPS backends. The _query_ROCM parser has a
       regex that would silently return no devices if the rocm-smi output format changed
       — untested.
       - _try_NVIDIA has a subtle logic inversion: simulated_output=not
       os.environ.get("LLAUNCHER_GPU_SIMULATE", "") == "" — this means simulated mode is
       active when the env var is NOT set. No test covers this production path.

       test_model_health.py — ADEQUATE

       The boundary condition is properly tested: the file is written as exactly 1024 *
       1024 + 1 bytes (one byte over) for the valid case, and 0 bytes for the invalid
       case. However, the exact 1 MB boundary (1,048,576 bytes) is never tested — it is
       unclear from the tests whether the threshold is > 1MB or >= 1MB.
       test_last_modified_populated_for_valid at line 132 has a weak assertion: assert
       isinstance(result.model_dump()["last_modified"], str) or hasattr(result,
       "last_modified") — the or hasattr branch can make this pass even if last_modified
       is None in the dict. Overall this file is the second-strongest in the batch.

       test_cli.py — ADEQUATE

       Good use of typer.testing.CliRunner. JSON output tests parse the actual JSON
       (json.loads). test_start_with_explicit_port at line 198 has a conditional
       assertion: if call_kwargs: assert … — if call_args is None (mock not called), the
       test passes silently. No tests cover: server start when the port is already in
       use, node add with an invalid port (negative, > 65535), or model list --json when
       JSON is malformed in the config file.

       test_agent_models_health_api.py — WEAK

       TestVRAMPreFlightEndpoint.test_vram_error_contains_required_and_available at lines
        199–206: the test is gated on if response.status_code == 409: — if the
       environment has no GPU, the if body never runs and the test passes vacuously. This
        is a no-op test on any CI machine without a GPU. The _patched_health_client
       helper at line 47 is created but never used; all tests construct their own
       clients, suggesting the helper was written then abandoned.

       test_adr_cross_cutting.py — WEAK (integration folder, but mostly unit tests)

       - TestCliAndAuthIntegration.test_cli_node_add_with_api_key at line 131: the final
       assertion is assert "secure-node" in result.output or result.exit_code == 0 — the
       or result.exit_code == 0 makes this always pass on any successful invocation
       regardless of output. This is a tautology.
       - TestVRAMPreFlightLogic.test_vram_heuristic_estimates_for_7b_model at line 162
       opens a hardcoded absolute path
       /home/node/github/llauncher/llauncher/agent/routing.py — this will fail (or pass
       accidentally) depending on the execution machine. It does not test the routing
       logic; it reads source code text and asserts the string "start_with_eviction"
       appears. This is a tautological source-inspection test.
       - None of the 9 "integration" tests actually boot the full stack (app + middleware
        + routing + GPU collector) together. They test individual components in isolation
        or use hand-built minimal FastAPI apps. The TestGPUHealthWithStatus class has one
        test that is identical to a unit test already in test_gpu_health.py.

       test_ttl_cache.py — STRONG

       Clean, well-isolated, covers expiry with real time.sleep, per-call TTL override,
       instance isolation, invalidate_all, and the zero-TTL edge case. This is the most
       professionally written file in the batch.

       ---
       Critical Gaps

       1. RemoteNode HTTP calls never verified with headers. No test calls ping(),
       get_status(), or start_server() through a mock HTTP server and asserts the
       X-Api-Key header is present on the wire.
       2. VRAM pre-flight is untestable on GPU-less CI. The only test for POST
       /start-with-eviction is conditionally skipped when no GPU is present. The
       _estimate_vram_mb function and _check_vram_sufficient in
       /home/shane/github/shanevcantwell/llauncher/llauncher/agent/routing.py have no
       unit tests at all.
       3. ROCm and MPS backends have zero coverage. The _query_ROCM regex parser and the
       _query_MPS function are completely untested. The MPS function has a dead loop (for
        line in out.stdout.splitlines(): match = ... but match is never used).
       4. test_adr_cross_cutting.py:162 — hardcoded /home/node/ path will fail in any
       environment other than the author's machine. This is a broken test masquerading as
        passing.
       5. _try_NVIDIA env-var logic inversion is untested. The expression
       simulated_output=not os.environ.get("LLAUNCHER_GPU_SIMULATE", "") == "" means
       simulation runs in production (when the var is absent). No test covers this code
       path with the env var set vs. unset.

       Behaviors Claimed by ADRs But Not Tested

       - Token size limit / oversized header rejection (ADR-003)
       - Whitespace-only token normalization (ADR-003)
       - _estimate_vram_mb VRAM heuristic for non-7B sizes (3B, 14B, 70B, no-match
       fallback) (ADR-006)
       - Partial GPU offload scaling (n_gpu_layers < 999) in _estimate_vram_mb
       - Exact 1 MB boundary for model health validity (ADR-005)
       - ROCm backend parsing (ADR-006)
       - ping() updating node.status and node.last_seen on success/failure (ADR-003)
       - CLI server start port-conflict error path
  ⎿  Done (16 tool uses · 56.0k tokens · 1m 47s)

● architect(ADR architectural soundness review)
  ⎿  Prompt:
       Repo: /home/shane/github/shanevcantwell/llauncher. A smaller model wrote 4 ADRs
       overnight at docs/adrs/003-agent-api-authentication.md,
       004-cli-subcommand-interface.md, 005-model-cache-health.md,
       006-gpu-resource-monitoring.md. Existing ADRs 001 and 002 in the same folder show
       the project's prior style.

       Read all 6 ADRs (003-006 plus the two earlier ones for style baseline) and answer
       for each new ADR:

       1. Is this an actual decision, or just a feature description? Real ADRs document a
        choice between alternatives with consequences. Cargo-cult ADRs just describe the
       implementation.
       2. Are alternatives considered? Or is the chosen approach presented as the only
       option?
       3. Are consequences honest? Does it acknowledge tradeoffs, future maintenance
       burden, security implications?
       4. Does it match the existing ADR style in 001 and 002? Or does it pattern-match a
        generic ADR template from training data?
       5. Scope creep / under-spec — does ADR-003 (auth) really only specify the
       middleware, or does it punt critical design questions like token rotation,
       multi-tenant keys, audit logging?
       6. Coherence across the 4 ADRs — do 005 and 006 overlap (model health vs GPU
       pre-flight)? Should they have been one ADR? Are interactions between them
       addressed?

       Be ruthless. The user has explicitly asked for honest critique. Output: per-ADR
       verdict (SOLID / ADEQUATE / SHALLOW / RUBBER-STAMP) with 2-3 sentence
       justification each, plus a short cross-cutting paragraph. Cap at 500 words.
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/docs/adrs/001-llama-server-only.md)
  ⎿  Error: File does not exist. Note: your current working directory is
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/docs/adrs/002-pyqt6-gui.md)
  ⎿  Error: File does not exist. Note: your current working directory is
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/docs/adrs/003-agent-api-authenticat
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/docs/adrs/004-cli-subcommand-interf
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/docs/adrs/005-model-cache-health.md
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/docs/adrs/006-gpu-resource-monitori
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/docs/adrs)
  ⎿  Error: EISDIR: illegal operation on a directory, read
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/docs/adrs/001-process-isolation.md)
  ⎿  Error: File does not exist. Note: your current working directory is
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/docs/adrs/002-fastapi-agent.md)
  ⎿  Error: File does not exist. Note: your current working directory is
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/docs/adrs/README.md)
  ⎿  Error: File does not exist. Note: your current working directory is
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/docs/adrs/001.md)
  ⎿  Error: File does not exist. Note: your current working directory is
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/docs/adrs/001-llama-cpp-only.md)
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/docs/adrs/002-streamlit-ui.md)
  ⎿  Error: File does not exist. Note: your current working directory is
  ⎿  Error: File does not exist. Note: your current working directory is
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/docs/adrs/0001-process-model.md)
  ⎿  Error: File does not exist. Note: your current working directory is
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/docs/adrs/001-architecture.md)
  ⎿  Error: File does not exist. Note: your current working directory is
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/docs/adrs/001-mcp-stdio-protocol.md
  ⎿  Read(/home/shane/github/shanevcantwell/llauncher/docs/adrs/002-agent-fastapi.md)
  ⎿  Error: File does not exist. Note: your current working directory is
  ⎿  Error: File does not exist. Note: your current working directory is
  ⎿  Response:
       I cannot enumerate the directory and the user's reference to "001 and 002"
       filenames doesn't match what I can probe. I'll proceed with the four new ADRs and
       judge them against the standard ADR template (Context/Decision/Consequences) the
       user references, while noting the limitation that I could not load the style
       baseline. Now to the critique.

       ---
       ADR-003 (Auth): SHALLOW
       This presents itself as a decision but only documents one approach (X-Api-Key
       header middleware) without considering real alternatives like mTLS, OAuth/JWT,
       Unix socket binding, SSH-tunnel-only access, or reverse proxy delegation — all
       viable for an internal agent on :8765. Critical design questions are explicitly
       punted to "Phase 2": key rotation, multi-tenant scoping, audit logging, session
       management, and the still-open question of whether 0.0.0.0 default binding should
       change. For an authentication ADR, deferring rotation and audit is deferring half
       the actual decision; the "Open Questions" section is doing load-bearing work that
       the Decision section should own.

       ADR-004 (CLI): ADEQUATE
       This is the most honest of the four. The decision is genuinely scoped (Typer
       subcommands, local-state vs remote split), the "double-discovery problem" is named
        explicitly as a negative consequence, and the open questions are tactical rather
       than existential. Still weak on alternatives — Click, argparse, plain python -m
       invocations, or just-shell-aliases get no consideration, and the "Typer is already
        a dependency via FastAPI" claim should be verified (FastAPI does not ship Typer
       transitively in all installs). Also no discussion of shell completion, exit codes,
        or machine-readable output (JSON mode), which are table stakes for "CI/CD
       pipelines" usage it claims to enable.

       ADR-005 (Model Cache Health): RUBBER-STAMP
       This reads as a feature spec dressed in ADR clothing. The "Decision" is "add a
       check and an endpoint" — there is no choice being made between alternatives (e.g.,
        lazy validation at load time vs eager at startup vs filesystem-watcher
       invalidation vs trusting the OS). The depth-of-validation question (existence vs
       GGUF header parse) is the actual architectural decision and it's relegated to Open
        Questions. The safe_to_load: file > 1MB heuristic is presented without
       justification and would silently pass a truncated 2MB file. Also conflates three
       distinct features: pre-flight check, health endpoint, and a register CLI command —
        these warrant separate ADRs or one cohesive proposal.

       ADR-006 (GPU Monitoring): SHALLOW with scope-overlap
       A real decision here would be "build vs adopt" (nvitop, gpustat, pynvml, NVML
       directly, Prometheus node_exporter) — none are evaluated. The Apple MPS approach
       via /dev/memfd is fabricated; that path doesn't exist on macOS and memfd is a
       Linux-only API. That's a fact-check failure that should disqualify this ADR until
       rewritten. Per-process VRAM attribution on shared GPUs is acknowledged as
       imprecise but no decision is made about how to surface that uncertainty to the
       operator (the 409 pre-flight check assumes precision it doesn't have).

       Cross-cutting: ADRs 005 and 006 substantially overlap — both add pre-flight gates
       to start_with_eviction, both extend /status, both add Streamlit widgets, and 005's
        response shape explicitly references 006's GPU data. They should be one ADR
       ("Pre-flight Validation Pipeline") with model-file and VRAM as two checks in a
       shared framework, or 005 should depend on 006 explicitly with a defined contract.
       None of the four ADRs reference each other despite obvious coupling: 003's auth
       middleware needs to gate 005's and 006's new endpoints, and 004's CLI will consume
        all three. The set reads as four parallel feature tickets retrofitted into ADR
       format by a model pattern-matching the template, not as coherent architectural
       reasoning. Recommend: reject 005 and 006 as drafts, merge into one pre-flight ADR;
        rewrite 003 to actually decide rotation/audit/binding; accept 004 with revisions.

       Files reviewed:
       - /home/shane/github/shanevcantwell/llauncher/docs/adrs/003-agent-api-authenticati
       on.md
       - /home/shane/github/shanevcantwell/llauncher/docs/adrs/004-cli-subcommand-interfa
       ce.md
       - /home/shane/github/shanevcantwell/llauncher/docs/adrs/005-model-cache-health.md
       - /home/shane/github/shanevcantwell/llauncher/docs/adrs/006-gpu-resource-monitorin
       g.md

       Caveat: I was unable to locate ADRs 001 and 002 at guessed filenames in
       /home/shane/github/shanevcantwell/llauncher/docs/adrs/ — the style-baseline
       comparison in question 4 is therefore based on the conventional
       Context/Decision/Consequences template the new ADRs already follow structurally,
       not on your project's actual prior style. If you share the 001/002 filenames I can
        re-evaluate question 4 specifically.
  ⎿  Done (17 tool uses · 38.7k tokens · 1m 28s)

──────────────────────────────────────────────────────────────────────────────────────────────
  Showing detailed transcript · ctrl+o to toggle · ctrl+e to show all                 verbose 
                                                                                              


  ---
  My Read

  This is roughly what I'd expect from a 35B-A3B model running unsupervised against an
  aggressive plan: it wired things up, wrote tests that exercised the happy path, and produced
   a polished-looking summary. What it didn't do is the work that requires architectural
  skepticism — constant-time crypto, file permissions, distinguishing "no GPU" from "tool
  crashed," coherent ADR scoping, or honest test counts.

  The auth feature is currently weaker than no auth, because operators will trust it. The GPU
  pre-flight currently provides negative safety — it tells operators it's checking VRAM while
  silently allowing any launch when nvidia-smi hiccups.

  Recommended Next Move

  Two clean options — pick one and I'll execute:

  1. Tactical patch sprint — I draft a single remediation commit covering the 3 CRITICAL + ~7
  HIGH items above (timing-safe compare, file perms + to_dict() redaction, narrow GPU
  exception handling with proper logging, openapi_url=None, _query_MPS indent fix, _try_NVIDIA
   flag rewrite, cache lock, CLI param rename). ~2-3 hours of focused work, no scope creep.
  2. Revert + redo — roll back 8bae36a..9c73c71 and re-run the overnight pipeline against a
  tightened brief that explicitly demands hmac.compare_digest, no bare excepts, no plaintext
  credential files, real ADR alternatives sections, and ground-truth test counts. Slower but
  cleaner audit trail.

  I'd lean option 1 — the bones are mostly fine, the bugs are concentrated, and the ADRs
  (after fixing 006's fabrication) are salvageable as documentation of what was built.
  Reverting throws away the ~74 real tests and the ADR scaffolding for marginal gain. But your
   call.

