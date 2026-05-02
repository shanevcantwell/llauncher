# ADR-009: Symmetric Hub/Spoke Topology

**Status:** Accepted  
**Date:** 2026-05-02  

## Context

`PRODUCT_REQUIREMENTS.md` describes three endpoints (HTTP Agent, MCP server, Streamlit UI) and a `nodes.json` peer registry on every node, but never explicitly states the topology of multi-node deployments. The reverse-engineered text leaves ambiguous:

- Is there a designated head node, or is every node identical?
- Where does the master copy of model configurations live?
- What does `nodes.json` represent — a global topology, or each node's local view?

ADR-001 ("TypeScript Extension for Pi") observed in passing that "each llauncher agent is peer-to-peer — no central head service," but treated this as a contextual note rather than a first-class decision. The implementation appears to follow it, and the scoping conversation for the v2 spec confirmed: *"anything can be a hub and anything can be a spoke, depending on command line."*

This ADR promotes that observation to an explicit architectural decision and pins the consequences for config ownership, peer discovery, and dispatch.

## Decision

### Option Chosen: Fully Symmetric Nodes; Hub vs. Spoke is a Runtime Role

Every node runs the same software (HTTP Agent + MCP server + optional Streamlit UI + CLI). There is no head/worker asymmetry baked into the binary. A node's role at any moment is determined entirely by how a caller invokes it:

- A node is a **hub** when a caller on it (UI, CLI, MCP client) makes calls that target peers.
- A node is a **spoke** when a peer's HTTP Agent serves a request originating elsewhere.

A single node can be both simultaneously: serving spoke requests from peers while making hub calls of its own.

### Config Sovereignty: Strictly Node-Local

Each node owns its own `~/.llauncher/config.json`. Implications:

- There is no master config. No node has authority over another node's configs.
- CRUD on configs always **targets a specific node**. The tool-layer signature carries `target: NodeName | None = None`, where `None` means "this node."
- A `model_path` like `/models/mistral-7b.gguf` is meaningful only on the node where that file exists. CRUD against a remote node operates on the remote node's filesystem semantics, not the caller's.
- "Push my configs to all peers" is **not** a built-in operation. If the user wants config parity across nodes, they handle it externally (rsync, ansible, manual).

### Peer Registry: Per-Node, May Diverge

`~/.llauncher/nodes.json` is **each node's local peer list**, not a global topology. Two nodes can hold different `nodes.json` contents and that is by design.

- Adding a node to one node's registry does not propagate.
- Removing a node likewise stays local.
- A node referencing itself in its own `nodes.json` is permitted but not required (see Self-Loop below).

This matches how `~/.ssh/known_hosts` works: each host maintains its own view of the peers it has interacted with.

### Self-Loop Dispatch: Short-Circuit to Local Infra

When the tool layer's `target` resolves to "this node" (matches `LAUNCHER_AGENT_NODE_NAME`, defaulting to `socket.gethostname()`), the call routes **directly** to the local infrastructure layer. It does **not** make an HTTP round-trip through this node's own HTTP Agent.

Reasons:

- **Latency.** No reason to serialize → HTTP → deserialize for an in-process call.
- **Availability.** The local UI and CLI continue to work even when the HTTP Agent isn't running — useful for `llaunch` shell sessions where the agent daemon hasn't been started.

The trade-off — one extra dispatch path — is worth it. The short-circuit and the remote dispatch share the same tool-layer signature; only the transport differs.

### Identity Resolution

A node identifies itself via `LAUNCHER_AGENT_NODE_NAME` (env), defaulting to `socket.gethostname()`. The tool layer compares `target` against this name to decide local vs. remote dispatch. No global directory; no DNS-style lookup beyond what's in `nodes.json`.

## Consequences

**Positive:**

- Single binary, single mental model. Every node is identical software; the human or agent decides its current role.
- No single point of failure at the topology level. Any node going down affects only direct interactions with it; peers continue independently.
- Config ownership is unambiguous: the node where the file lives is sovereign.
- Plays well with ADR-008's stateless facade: each node's facade reaches its own local sources of truth or, via the remote-dispatch path, a peer's.

**Negative:**

- No global view "for free." Asking "what's running across all my nodes?" requires the asking node to walk `nodes.json` and aggregate. The Streamlit dashboard and any aggregation tool live at the **caller's** node, not in some central service.
- `nodes.json` divergence is the user's problem to manage. Two nodes with different peer lists is a feature, not a bug — but a confused user might not see it that way.
- No built-in config sync. A user who wants the same models registered on every node must copy `config.json` themselves, accepting that paths must already match across hosts.

**Open Questions:**

1. Should the Streamlit UI offer an explicit "import nodes.json from a peer" button, or is that user-side scripting? (Probably user-side; out of scope here.)
2. Self-loop comparison currently uses node name only. If a user ever runs two llauncher instances on the same hostname (different ports), this breaks. Edge case worth flagging but not solving in this ADR.
3. When the local HTTP Agent isn't running and a peer tries to call this node, the peer gets a connection error. Is there a "node is briefly unavailable" UX expected, or is connect-fails-loudly fine? (Connect-fails-loudly is fine; track remote-aggregation UX as a separate Issue.)

## Relationship to Other ADRs

- **Builds on ADR-008** (stateless facade): the facade pattern is what makes "anything can be a hub" cheap — a hub is just a facade instance whose `target` resolves to a remote node.
- **Promotes ADR-001's observation** ("peer-to-peer — no central head service") to an explicit topology decision.
- **Constrains ADR-010** (forthcoming, port ownership): port resolution is local to the target node; no cross-node port coordination.
