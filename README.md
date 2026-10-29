# fleet-kit

One `fleet.toml`, and a `fleet` command around it: set up and watch a fleet of Claude Code
bots that work on your GitHub projects, two bots per project (a plan bot that writes issues
and reviews, a dev bot that builds `ready` issues in headless rounds), each in its own Unix
account on a machine that is always on.

This is the tooling behind haku's fleet (haku767/fleet is its register and plans), pulled
out so that it pays for that fleet first and for the next person second. Status, 2026-10-29
(fleet PLANS 13, stage 4): the file, `check`, `render`, `apply conf`, `apply lists`, `labels`,
`ruleset`, `add-project`, `add-bot` and the GitHub half of `doctor` work and run haku's fleet;
the round scripts (`bot-round`, `bot-review`, `fleet-token`) and the install script for the
accounts still live in haku's dotfiles and read what this tool renders. So the kit carries
the register and the GitHub side of a fleet today, not yet the scripts a round runs on.
[`docs/getting-started.md`](docs/getting-started.md) is the guide for a fleet of your own,
with that line drawn plainly.

## The file

`fleet.toml` holds every fact about the fleet once: the owner, the machines, the stacks (test
command, allow list, environment), the projects with their two bots, the guards, the
people who may set `ready`. Never a secret: App keys, runner tokens and the Claude logins
stay on the machine. [`examples/minimal.toml`](examples/minimal.toml) is the smallest fleet,
one machine, one project, two bots; haku's own file, two machines and seven projects, is in
haku767/fleet.

## The command

| Command | State | What it does |
|---|---|---|
| `fleet check [fleet.toml]` | works | validates the file: every bot has a project and a stack, names and accounts unique, repositories well-formed, no secret in the file; exit 1 on an error |
| `fleet render [--out DIR] [--bot NAME] [fleet.toml]` | works | the derived files into `rendered/`: `bots/<account>.conf` per bot, named after the Unix account (what `bot-round` and `bot-review` source in place of their `case` blocks, the same variable names), `machines/<m>/lists.sh` (the install scripts' account lists), `machines/<m>/courier.sh`, `tables.json` (the page's tables), `rules-table.md` (the house rules' project table); `--bot` prints one `.conf`. An add-on stack (its `test` holds `{each}`) wraps the other stacks' commands and takes the project's `test_extra_<stack>` in place of `test_extra` when there is one |
| `fleet apply conf [--dir D] [--dry-run] [fleet.toml]` | works | writes the rendered `bots/<account>.conf` into `/etc/fleet/bots` (sudo; `--dir` for a trial), atomically, 0644, and removes a file there that the render no longer has and that carries the generated header; prints added, changed, removed |
| `fleet apply lists [--machine M] [--dir D] [--dry-run] [fleet.toml]` | works | writes this host's `machines/<host>/lists.sh` as `/etc/fleet/lists.sh` (a bots machine: the install scripts' account lists) and `courier.sh` as `/etc/fleet/courier.sh` (a machine with a courier), sudo, same rules as `apply conf`; the host is `uname -n` unless `--machine` says otherwise |
| `fleet labels <project>\|all [--apply] [-v] [fleet.toml]` | works | compares a project's repository with the fleet's labels (`[labels]` in the file, or the kit's defaults: `ready`, `needs-haku`, `next`, `live`, `size:S/M/L`, one `bot:<name>` per bot; `{owner}`, `{plan}`, `{dev}` in a description are filled in per project) and lists what to create or change; `--apply` writes it through `gh api` as whoever runs it (write access to the repository). Other labels are left alone, nothing is deleted |
| `fleet ruleset <project>\|all [--apply] [fleet.toml]` | works | a project with `reviews = N` wants the ruleset `main-rule` on its default branch: no deletion, no force push, a pull request with N approving reviews; the command shows the differences, `--apply` creates or updates it (admin rights: the owner, not a bot). Without `reviews` it only reports what the repository has |
| `fleet add-project <name> --repo O/R --stack S [--test CMD] [--ci ...] [--reviews N] [--plan NAME] [--dev NAME] [--no-plan] [--no-dev] [--machine M] [--paused] [fleet.toml]` | works | appends `[projects.<name>]` with its plan and dev bot (default `<name>-plan`, `<name>-dev`) before the guards, checks the result and writes only when it is clean; then prints what is still by hand: the Apps with their permissions and installation, labels, ruleset, the accounts and keys on the machine, the first round |
| `fleet add-bot <project> plan\|dev [--name N] [--account A] [--persona P] [--machine M] [fleet.toml]` | works | adds the one bot line to the project's block, same check and the same steps for that bot |
| `fleet doctor [project\|all] [-v] [fleet.toml]` | GitHub half works | one line per check, `ok`/`warn`/`fail`/`skip`, exit 1 on a fail: the repository reachable, labels and ruleset as the file says, each bot's App with the permissions it needs (checks and actions read only where `ci` is not `no`), installed on the repository and its `reads` (seen only by the owner's login), every open `ready` set by someone trusted, the runner online, the guards' Apps. The machine half (accounts, timers, status files, inboxes) follows from ws-guard |
| `fleet apply` (accounts, units), `status`, `page` | planned | fleet PLANS 13, stage 3 |

Python 3.11 or newer, standard library only. To try it without installing:

    PYTHONPATH=src python3 -m fleet_kit check examples/minimal.toml

or `pip install -e .` for the `fleet` command. Tests: `python3 -m unittest discover tests`.

## Layout

- `src/fleet_kit/model.py`: loads the file, the checks.
- `src/fleet_kit/edit.py`: `add-project` and `add-bot` (text insertion, the file keeps its comments; the GitHub steps).
- `src/fleet_kit/doctor.py`: the GitHub checks, one Finding each; a fake `gh` in the tests.
- `src/fleet_kit/labels.py`, `ruleset.py`: what a repository should have, the comparison, the writes; `github.py` runs `gh api`.
- `src/fleet_kit/render.py`: what the tool derives (its docstring lists the files).
- `src/fleet_kit/cli.py`: the command.
- `examples/minimal.toml`: the smallest fleet.
- `tests/`: unittest, no fixtures beyond the example.

Maintained by the two guards of haku's fleet (tx13-guard, ws-guard) through pull requests;
haku merges. MIT, see `LICENSE`.
