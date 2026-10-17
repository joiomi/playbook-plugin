# Mind Map — Format Reference

A mind map is a **read/write memory** for AI agents — a knowledge graph that speeds up bootstrapping and carries context across sessions. This is not a log. Nodes are living documents that evolve as understanding deepens.

## What belongs (and what doesn't)

The map answers **"how does this work, and where is it?"** — never **"what happened, and when?"**. It is loaded into context to orient an agent, so every line is resident cost: keep the signal, push the log elsewhere.

- **Belongs:** the current shape — subsystems, data flow, boundaries, key files/functions, and the *why* behind a design that isn't obvious from the code.
- **Does NOT belong:** commit hashes, dates, changelog entries, "added in v2.0" timelines, a "Development History" node. Git already holds the *when*; worklogs hold the narrative. A node that reads like a diff or a release note is log noise that bloats every session — cut it, or keep only the *decision* it taught ("chose JSON over SQLite for hand-editability"), never the timestamp. Bootstrap loads this map every session; the audit's `mindmap-node-freshness` check flags nodes whose cited code has moved on — so stale prose is a liability, not a keepsake.

## Node Format

**One fact per line.** A node is its title line, then one fact per line under it — so a change touches one short line, and a diff shows that line alone.

```
[N] **Title** - One sentence that defines the concept, with [link-nr] where it fits.
- A fact, with [link-nr] embedded naturally.
- Another fact: a file it owns, a number, a decision and its reason.
```

The rules every reader (`tasks recall`, the bootstrap, the audit, `tasks mindmap-sync`, the merge checks) relies on:
- A node starts at its title line: `[N]`, a space, then the bold `**Title**`.
- It runs to the next title line.
- Never begin a fact with `[`: a line that begins with `[5]` starts node 5.
- No `#` heading or code fence among the facts: readers disagree on where a node with a heading ends, and an unclosed fence stops `tasks mindmap-sync --fix`.
- A summary node (its full text in `MIND_MAP_OVERFLOW.md`) carries `↗` on its title line, right after the bold title.
- Any `↗` on a title line marks a summary, so a full node keeps `↗` off its title line.
- Keep `↗` out of a full node's facts too: `tasks mindmap-sync --fix` never replaces an overflow node's text with a node that holds `↗` on any line, and names it for a sync by hand.
- A node missing from `MIND_MAP_OVERFLOW.md` is still appended to it whole, marker or not.

How to write them:
- The facts are `- ` lines right under the title, one fact each, with no blank line among them.
- One blank line between two nodes.
- Dense but readable: 150-400 words per node, every line adds information.

Every `MIND_MAP.md` starts with this header:

```markdown
> **For AI Agents:**
> - This mind map is your primary knowledge index.
> - Read overview nodes [1-5] first, then follow links [N] to what you need; always reference node IDs.
> - When you change something, update the outdated nodes at once — the overview nodes first.
> - Add a node only for a genuinely new concept; keep the map compact (20-50 nodes typical).
> - The mind map wraps every task: consult it, rely on it, update it.
```

## Routing Nodes [1-5]

The first 5 nodes are **routing hubs** — they point to everything else and get updated frequently:

- [1] **Overview** — what this is, main entry point, links to all major concepts
- [2-5] **Major themes** — each covers a domain, links to relevant detail nodes

**Routing nodes change as the map grows.** When you add node [47] about authentication, go back and add `[47]` to the relevant routing node.

## Node Hierarchy

| Level | Nodes | Content |
|-------|-------|---------|
| Foundation | 1-5 | Overview, core theory, data flow, major architectural layers |
| Systems | 6-15 | Major subsystems, key features, components |
| Implementation | 16-20 | Tech stack, history, workflow, roadmap, principles |
| Deep dives | 21+ | Algorithms, optimization, error handling, performance |

**Scale:** Simple topics 15-30 nodes. Standard projects 30-60. Complex systems 60-150. Large codebases 150+ (routing nodes become critical).

## Link Principles

Links `[N]` flow naturally in the facts, not listed at the end:
- Good: "The storage layer [6] handles persistence"
- Bad: "The storage layer handles persistence [6][7][8][12]"

- Every node links to 2-3+ others; important nodes link to 5-10.
- Bidirectional: if [5] mentions [12], [12] should mention [5].
- Link every fact or two, not every clause.

**Density targets:**
- Overview nodes: 5-10 links
- System nodes: 3-7 links
- Implementation nodes: 2-5 links
- Specialized nodes: 2-4 links

## Writing Style

- Use specific names: "The PCAVisualizer class in pcaVisualizer.ts" not "the visualizer"
- Include numbers: "5-10 features", "23,000+ lines"
- **Cite the files the node owns.** A system/implementation node should name its actual path(s) — `src/store.py`, `tasks/mindmap.py`. This is not just precision: those citations are the node's *anchor*, so `tasks audit` can tell you when the node has drifted from the code (the `mindmap-node-freshness` check), and `recall` lands you on the right node. A node that owns code but cites no path can't be checked for staleness.
- Explain WHY decisions were made, not just WHAT exists
- **Add a keyword alias when the search terms differ from the title.** If people would look for a node by a word its title doesn't contain, put `<!-- keywords: login, credentials, sso -->` on it so `recall` finds it by meaning (see Retrieval).
- When information changes, update existing nodes — don't create duplicates
- **Well-formedness is checked mechanically** — `tasks audit` flags duplicate ids, a node with no `**Title**`, and any node nothing links to (dead memory). Every node earns its place by being reachable: give a new node at least one incoming link from the subsystem it belongs under.

## Examples

### Good — One Fact per Line, Natural Linking

```
[1] **Project Overview** - TaskFlow is a command-line task manager built in Python, driven from the keyboard.
- Task storage [6] is separate from the rendering layer [7], so a new output format never touches business logic.
- Users work through vim-style keybindings [12], chosen over menus for speed.
- The data model [3] is flat JSON, not nested categories: real task hierarchies rarely go deeper than two levels.
- Calendar sync [9] exists because most users needed deadlines in their calendar.

[6] **Storage Layer** - `StorageManager` in `storage.py` handles all persistence [1][3].
- An in-memory cache syncs to disk on every write — enough for typical volumes (under 10,000 items).
- JSON, not SQLite: portable and editable by hand.
- Backup rotation keeps the last 5 versions automatically.
```

### Bad — A Paragraph on One Line

```
[6] **Storage Layer** - The StorageManager class in storage.py handles all persistence operations [1][3]. It maintains an in-memory cache that syncs to disk on every write, which is acceptable for typical task volumes. The JSON file format was chosen over SQLite for portability.
```

### Bad — Links Clustered at End

```
[1] **Project Overview** - TaskFlow is a command-line task manager.
- Integration with calendars exists. [3][6][7][9][12]
```

### Bad — Over-Linked

```
[1] **Project Overview** - TaskFlow [2] is a command-line [4] task manager [3] built in Python [5].
- It emphasizes keyboard-driven [12] workflows [7].
```

## Node Templates

**System node:**
```
[N] **System Name** - Brief definition and purpose [parent-node].
- COMPONENT1 handles TASK1 [detail-node].
- COMPONENT2 handles TASK2 [detail-node].
- Implementation: path/to/files, using TECHNOLOGY [tech-node].
- Integrates with EXTERNAL_SYSTEM [integration-node] through API_METHOD.
- PARAM1: range, default; PARAM2: type, purpose [parameter-node].
```

**No history node.** Resist the urge to add a "Development History" node full of commit hashes and dates — that is a log, and it bloats the resident map for information git already holds. When the *evolution* of a subsystem carries a design lesson, fold that lesson (the decision and its reason, not the hash) into the owning subsystem node: "Storage moved from SQLite to flat JSON so tasks stay hand-editable" — not "commit a1b2c3d (2024-03) switched to JSON".

## Retrieval — `tasks recall`

`tasks recall` is the way in: it prints a node whole, spans both tiers and ranks by relevance:

```bash
tasks recall 5             # node 5 in FULL — from MIND_MAP.md AND MIND_MAP_OVERFLOW.md
tasks recall auth policy   # ranked relevance search (BM25 + plural stemming), best node first
```

Without the CLI, a node is the lines from its title line to the next one:

```bash
grep -n "^\[5\] " MIND_MAP.md        # where node 5 starts — read on to the next [M] line
grep -n "\[5\]" MIND_MAP.md          # every line that references node 5
```

**Keyword aliases (optional):** a node can declare synonyms its prose doesn't spell out, so a search finds it by *meaning*, not just wording. Put `<!-- keywords: login, credentials, sso -->` at the end of any of the node's lines; `tasks recall` weights those terms heavily. Use this for a node whose common search terms differ from its title (an "Identity" node people look for as "auth"/"login").

## Quality Checklist

- All major systems/features have nodes
- Every significant file/component is mentioned
- Design *decisions* and their reasons captured — but no commit hashes, dates, or a history node (git holds the *when*)
- Every node has 2+ links, important nodes 5+
- Links embedded naturally in text, bidirectional where appropriate
- Each node has a clear title, and its title line defines the concept
- Actual file/function names used, not generic descriptions
- One fact per line: the title line, then `- ` fact lines, no blank line inside a node
