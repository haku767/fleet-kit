# Getting started: a fleet of your own

What haku's fleet does, in the order you would build it, with the `fleet` command where it
helps. It is written for the next person (the first was Erik, 2026-10), from ws-guard's German
guide of 2026-10-27, and it says plainly where the kit still stops. Status: 2026-10-29,
fleet PLANS 13 stage 4.

## What a fleet is

A set of Claude Code bots that work on your GitHub projects while you are away, and one file
that says who may do what. Two bots per project:

- the **plan bot** sorts issues, writes issues with acceptance criteria, sets the label
  `ready`, keeps roadmap and docs, reviews the dev bot's pull requests. You talk to it in an
  interactive session.
- the **dev bot** builds `ready` issues, one pull request per issue, in headless rounds
  (`claude -p` from a systemd timer every 20 minutes, a fresh context per round). It answers
  red CI and reviews. It never merges: you do.

Each bot is its own Unix account on a machine that is always on, with its own GitHub App as
its identity. Later a **guard** per machine (a bot that keeps the house rules, checks the
machine read-only every hour and tells you what is urgent) and a **page** that shows the fleet.

Three principles hold it together:

1. **GitHub is the state.** What is pending is in labels, pull requests and reviews, not in a
   conversation. Every round reads it afresh.
2. **Hand-over only through `ready`**, and only when you or the project's plan bot set it. The
   dev bot checks who set the label before it builds.
3. **The fence is GitHub, not the prompt.** A ruleset on the default branch (pull request and
   review required, no bypass for Apps) keeps a bot off `main` whatever the model tries.

## What the kit does today, and what it does not

| You need | Where it is |
|---|---|
| one file with every fact about the fleet | `fleet.toml`, this kit; `fleet check` validates it |
| the per-bot configuration the round scripts read | `fleet render`, `fleet apply conf` (`/etc/fleet/bots/<account>.conf`) |
| the account lists the install script reads | `fleet apply lists` (`/etc/fleet/lists.sh`) |
| labels and the ruleset in each repository | `fleet labels`, `fleet ruleset` (compare, `--apply` writes) |
| a new project or bot in the file, and the steps that remain by hand | `fleet add-project`, `fleet add-bot` |
| a daily check of GitHub against the file | `fleet doctor` (the machine half is coming) |
| the round scripts `bot-round`, `bot-review`, the token helper `fleet-token`, the install script for accounts, units and timers | **not in the kit yet.** They live in haku's private dotfiles and read what the kit renders. Moving them here is the open decision of stage 4; until then ask haku for them, or write them from the description below (an afternoon). |
| the page (collector and HTML) | haku's fleet repository (`dashboard/`), not in the kit yet |

So today the kit carries the register and the GitHub side; the machine side still needs the
scripts.

## Prerequisites

- A Linux machine that stays on (a workstation, a small server), with systemd, git, tmux,
  `gh`, Python 3.11 or newer, and Claude Code (`claude`).
- A Claude subscription with Claude Code, or an API key. A dev round on Sonnet costs cents
  to half a dollar, Opus three to five times that; a project on a 20-minute timer costs one
  or two dollars on a busy day and almost nothing on an empty one, because a round without
  work ends at once.
- A GitHub account with the project repository. Private repositories work; the bots get
  access through GitHub Apps, not through your account.

Install the kit:

    git clone https://github.com/haku767/fleet-kit
    cd fleet-kit && python3 -m venv ~/.local/share/fleet-kit && ~/.local/share/fleet-kit/bin/pip install .
    ln -s ~/.local/share/fleet-kit/bin/fleet ~/.local/bin/fleet

or run it in place with `PYTHONPATH=src python3 -m fleet_kit ...`. Standard library only.

## Step 1: the file

Copy [`examples/minimal.toml`](../examples/minimal.toml) to `fleet.toml` and make it yours:
the owner login, the machine (`role = "bots"`, `family = "arch"` for any non-NixOS Linux,
`home_root`, `scratch_root` for build output), a stack with the project's test command and
the commands the dev bot may run, the project with its repository and its two bots. Then

    fleet check fleet.toml

Facts only, never a key, a token or a login: `fleet check` refuses a file that looks like it
holds one. Keep the file in a repository of its own (haku's is `haku767/fleet`, together with
the plans) so that every change is a commit.

## Step 2: GitHub

For every bot a **GitHub App** (no second account: GitHub allows one machine account per
person, none registered by bots; an App is free, has its own name, rights per repository
and tokens that expire after an hour). `fleet add-project` prints these steps for a new
project; for the first one, by hand on GitHub, Settings > Developer settings > GitHub Apps:

1. New GitHub App, named `<owner>-<bot>` (the file's default; `app = ` in the bot's table
   when it differs), webhook off. Repository permissions: Contents read/write, Issues
   read/write, Pull requests read/write, Metadata read, Checks read, Actions read.
2. Generate a private key (the `.pem`), note the App ID.
3. Install the App on your account, restricted to the project's repository.

Then the repository:

    fleet labels myapp fleet.toml            # compares; add --apply to create and change
    fleet ruleset myapp fleet.toml --apply   # when the project has `reviews = 1`; admin rights

The labels are the fleet's convention: `ready` (hand-over), `needs-haku` (a bot stopped,
you decide; the name is still haku's in the scripts), `next`, `live` (a human is on it, bots
keep off), `size:S/M/L` (the plan bot estimates; S and no label build on Sonnet, M and L on
Opus), `bot:<name>` per bot. The ruleset `main-rule` is the fence: no deletion, no force
push, a pull request with one approving review.

CI, if the project has tests: a workflow on pull requests, so the reviewer reads the result
instead of building. Without CI the dev bot names in the pull request what it ran (`ci =
"no"` in the file puts that sentence into its prompt).

## Step 3: the machine

One Unix account per bot, home under `home_root/<account>` (mode 0750), no sudo, no ssh from
outside; in it the project's clone, `~/.config/fleet/app-id` (one line) and `app.pem` (mode
600), `~/.claude` with the login (`sudo -u <bot> claude`, sign in once), `work/` for the dev
bot's worktrees, `log/`. `loginctl enable-linger <bot>` so its timers run without a login.
Why not everything under your user: then every bot reads every key and can kill every other
bot; with own accounts the kernel keeps them apart and every log line, commit and file says
who it was.

The kit gives the lists and the per-bot configuration; the accounts themselves are still the
install script's (see above):

    sudo fleet apply lists fleet.toml   # /etc/fleet/lists.sh: bots, round_bots, review_bots, paused_bots
    sudo fleet apply conf fleet.toml    # /etc/fleet/bots/<account>.conf, one per bot

A `.conf` is POSIX sh the round scripts source: `kind`, `project`, `repo`, `app`, `trusted`
(who may set `ready`: you, the plan bot's App, `ready_also`), `test_cmd`, `allow`, the
environment. Root writes it, the scripts refuse a file anyone else could write, so the allow
list is a fence too. `fleet render` shows all of it without root.

## Step 4: the plan bot

A tmux session as the bot (`sudo -u myapp-plan tmux new -s plan claude`) in which you talk to
it. Its role is in the account's `~/CLAUDE.md`, short: sort issues and comments; turn wishes
into issues with acceptance criteria ("done when: ..." as a list); estimate `size:*`; set
`ready` when an issue is clear, `next` for order; keep roadmap and user docs through pull
requests; no code, no build, no deploy, nothing on the running system; decisions into files
or issues, never only into the conversation.

That is enough for week one: you talk to the plan bot, it writes the issues, you set `ready`,
then comes the dev bot.

## Step 5: the dev bot in rounds

`bot-round`, root-owned in `/usr/local/bin`, runs as the bot from a systemd timer every 20
minutes. A round, in this order:

1. **Clean up**: remove worktrees in `work/` whose pull request is merged or closed.
2. **Fix round**, when an open pull request has red checks or the last review asks for
   changes: `claude -p` in the worktree with the review and the CI log, "fix it and push".
   Once per head for checks, once per review, at most three per pull request; then
   `needs-haku`.
3. **Next issue**, when fewer than N worktrees are open (N=1 at first; haku runs 2): the
   `ready` issues without `live`, `next` first, else the lowest number. Before that, who set
   `ready`:

       gh api repos/$repo/issues/$n/timeline --paginate \
         --jq '[.[] | select(.event=="labeled" and .label.name=="ready")] | last | .actor.login'

   Expected: one of `trusted` from the `.conf`. Anyone else: do not build, comment.
4. **Build**: `git worktree add work/$n -b myapp-dev/issue-$n origin/main`, then `claude -p`
   with a fixed prompt (read the issue, meet the acceptance criteria, run `test_cmd`, commit
   with the `Agent:` trailer, push, `gh pr create` with "Closes #n"), the model by size label,
   `--allowedTools` from `allow`, `--max-turns`, `--max-budget-usd`, `timeout 2h` around it.
5. **Stop instead of repeat**: a round without a pull request counts as an attempt; after
   the second, a comment on the issue, `needs-haku`, the worktree stays for you.

The unit is a template (`bot-round@.service`, `User=%i`), the timer `OnUnitActiveSec=20min`:
`systemctl enable --now bot-round@myapp-dev.timer`. Every round appends a line to the status
log the page reads. Run the first rounds by hand (`sudo -u myapp-dev bot-round`), read the
log, adjust prompt and allow list in `fleet.toml`, `fleet apply conf`, then the timer.

## Step 6: the reviewer in rounds

The same pattern as `bot-review`, as the plan bot, timer every 20 minutes, offset: the oldest
open pull request of the dev bot that closes an issue, not a draft, checks finished, no
review of the plan bot on this head yet. A detached checkout, `claude -p` reads issue, diff,
earlier reviews and CI and writes `review.md` with a verdict line; **the script posts the
review** through the plan App's API, the model never writes to GitHub itself. APPROVE only
with green checks. At most three review rounds per pull request, then `needs-haku`.

You merge what is approved. That is all of your part, and that is why the setup pays.

## Every day

    fleet doctor all fleet.toml -v

One line per check: the repository reachable, labels and ruleset as the file says, each App
with its permissions and installed where it should be (your login sees that; a bot's does
not), every open `ready` set by someone trusted, the runner online. Exit 1 on a `fail`.
The machine half (accounts, timers, status files, inboxes) is ws-guard's next piece.

## Growing

A second project is `fleet add-project <name> --repo <owner>/<name> --stack <stack>`: it
appends the block, checks the file and prints the GitHub and machine steps for the new bots.
A bot alone is `fleet add-bot`. A second machine (haku's case: a laptop that sleeps, with a
guard and a courier that carries inbox mail to the workstation) is a `[machines.<name>]` table
with `courier = { to = ..., every = ..., ssh = ... }`; `fleet render` then gives `courier.sh` too.

## Order, from haku's experience

1. Week 1: labels, ruleset, CI in the project; the plan bot interactive; you set `ready`.
2. Week 2: the dev App and account, `bot-round` by hand, read the log, adjust prompt and
   allow list, only then the timer with N=1.
3. Week 3: `bot-review` as the plan App, by hand, then the timer. From here you only merge.
4. Then: house rules, the guard, the page; N to 2; a second project is an afternoon.

What tripped haku most often, so you do not repeat it: the fix round must hang on the
review's id, not on the head, or it stalls after its own push. GitHub fills `reviewDecision`
only when the ruleset requires a review; else read the reviews themselves. A bot without an
own account sees every key. And never build a stranger's `ready`; the timeline query above
is not optional.
