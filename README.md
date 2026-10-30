# fleet-kit

One `fleet.toml`, and a `fleet` command around it: set up and watch a fleet of Claude Code
bots that work on your GitHub projects, two bots per project (a plan bot that writes issues
and reviews, a dev bot that builds `ready` issues in headless rounds), each in its own Unix
account on a machine that is always on.

This is the tooling behind haku's fleet (haku767/fleet is its register and plans), pulled
out so that it pays for that fleet first and for the next person second. Status, 2026-10-30
(fleet PLANS 13, stage 3 complete): the file, `check`, `render`, `apply` (`conf`, `lists`,
`accounts`, `inbox`, `rounds`), `labels`, `ruleset`, `add-project`, `add-bot` and `doctor`
(GitHub and machine) work and run haku's fleet; the round scripts `bot-round`, `bot-review`
and `fleet-token` are the kit's own and `fleet apply rounds` installs them. What stays outside
is a machine's own (ssh keys, sudoers, deploy wrappers) and the page (haku's fleet repository).
[`docs/getting-started.md`](docs/getting-started.md) is the guide for a fleet of your own.

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
| `fleet doctor [project\|all] [-v] [--machine M] [fleet.toml]` | works | one line per check, `ok`/`warn`/`fail`/`skip`, exit 1 on a fail: the repository reachable, labels and ruleset as the file says, each bot's App with the permissions it needs (checks and actions read only where `ci` is not `no`), installed on the repository and its `reads` (seen only by the owner's login), every open `ready` set by someone trusted, the runner online, the guards' Apps. Then the machine half for this host (or `--machine`), when it is a bots machine in the file: each bot's account (home 750, linger, `~/agents`), its scratch directory, its `/etc/fleet/bots/<account>.conf` (root's, current), `claude` and the App's key in its home (root sees them, a bot gets `skip`), its timer on (off for a paused project), its status file fresh and signed in, every inbox in place and not stale, `/etc/fleet/lists.sh`, the scripts and units, the agents directory, the guard's report, the house rules. Without `gh` the GitHub half is skipped, the machine half still runs |
| `fleet apply rounds [--machine M] [--start] [--dir ROOT] [--dry-run] [fleet.toml]` | works | the headless rounds on a bots machine (arch family; sudo): installs the kit's `bot-round`, `bot-review` and `fleet-token` to `/usr/local/bin` and the four template units to `/etc/systemd/system`, root-owned, makes `agents_dir/status` and each bot's `work/` or `review/` and `log/`, `daemon-reload`, enables the timers of the machine's bots and disables a paused project's; starts nothing unless `--start` (the first round by hand first). NixOS: nothing, its module takes the rendered files |
| `fleet apply accounts [--machine M] [--dir ROOT] [--dry-run] [fleet.toml]` | works | the Unix side of a bots machine (arch family; sudo): the `agents` group and the agents directory (2775) with `inbox/` and `status/`; for every bot of the machine and every guard whose courier delivers here an account (`useradd`, home 750 under `home_root`, in `agents`, linger on, `~/agents` → the agents directory), a dev bot's scratch directory under `scratch_root` and podman's `storage.conf` pointing there (never rewritten); the machine's `admin` (`[machines.<m>] admin = "<login>"`, optional) in `agents` and in every bot's group. ssh keys, sudoers and deploy wrappers stay with the machine's own script |
| `fleet apply inbox [--machine M] [--dir ROOT] [--dry-run] [fleet.toml]` | works | `agents_dir/inbox/<name>/` for every bot and guard of the fleet: owned by the recipient when it lives here, else by the courier guard that delivers for it; 1730 (the group drops, cannot list), `done/` (a courier's also `sent/`) 700; ACLs: the admin reads all, a default entry keeps the recipient's and the admin's rights on dropped notes; what is inside already is chowned to the recipient. A recipient without an account here is skipped |
| `status`, `page` | planned | fleet PLANS 13, stage 4 |

Python 3.11 or newer, standard library only. To try it without installing:

    PYTHONPATH=src python3 -m fleet_kit check examples/minimal.toml

or `pip install -e .` for the `fleet` command. Tests: `python3 -m unittest discover tests`.

## Layout

- `src/fleet_kit/model.py`: loads the file, the checks.
- `src/fleet_kit/edit.py`: `add-project` and `add-bot` (text insertion, the file keeps its comments; the GitHub steps).
- `src/fleet_kit/doctor.py`: the GitHub checks, one Finding each; a fake `gh` in the tests.
- `src/fleet_kit/machine.py`: the machine checks, the same Finding; a temporary root, a fake `systemctl` and fake owners in the tests, so they run without root.
- `src/fleet_kit/accounts.py`: `fleet apply accounts` and `fleet apply inbox`; every system command goes through one function, which the tests record.
- `src/fleet_kit/rounds.py`: `fleet apply rounds`; `src/fleet_kit/data/`: `bot-round`, `bot-review`, `fleet-token` and the four units the kit ships (MIT; from haku's dotfiles, where they ran since 2026-10-07). What is a fleet's own in them comes from the `.conf`: `owner`, `guard`, `agents_dir`, the `[defaults.round]`/`[defaults.review]` knobs; a `.conf` without those lines runs with the values haku's fleet had.
- `src/fleet_kit/labels.py`, `ruleset.py`: what a repository should have, the comparison, the writes; `github.py` runs `gh api`.
- `src/fleet_kit/render.py`: what the tool derives (its docstring lists the files).
- `src/fleet_kit/cli.py`: the command.
- `examples/minimal.toml`: the smallest fleet.
- `tests/`: unittest, no fixtures beyond the example.

Maintained by the two guards of haku's fleet through pull requests;
haku merges. MIT, see `LICENSE`.
