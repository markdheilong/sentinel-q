# Getting this into GitHub

Ten minutes, once. The commit history is the one artifact you cannot retrofit later, so
the goal is to start it clean and start it now.

## Before the first push — two minutes that matter

Run the disclosure guard. It is the gate between your working tree and a public
repository that, per the contest rules, **cannot be deleted or made private after the
deadline**.

```bash
cd sentinel-q
python -m pytest tests/test_disclosure.py -v
```

All eight must pass. If one fails it will name the file. It caught three leaks the first
time it ran, which is the whole argument for having it.

Then confirm nothing private is sitting in the tree:

```bash
ls calibration/          # expect ONLY example.calibration.json and README.md
git status --porcelain   # after init, below — nothing surprising should be staged
```

## Create the repository

On GitHub, create **`sentinel-q`** under `markdheilong`. Public. Do **not** let GitHub
add a README, .gitignore or licence — this repo already has all three and you would
just have to reconcile them.

Then locally:

```bash
cd sentinel-q

git init -b main
git add .gitignore                       # FIRST, so the ignore rules apply to everything after
git commit -m "Add gitignore: keep calibrations and inference data out of version control"

git add .
git status                               # read this properly before continuing
git commit -m "Initial commit: Sentinel-Q gateway core

Hierarchical AI gateway for commercial vehicle pre-trip brake inspection,
built on the Arduino UNO Q for the Invent the Future contest.

Sensor calibration is loaded at runtime and is not included; see
calibration/README.md."

git remote add origin https://github.com/markdheilong/sentinel-q.git
git push -u origin main
```

**Stage `.gitignore` in its own first commit.** If you `git add .` before the ignore
rules are in effect, a stray calibration or `.sqlite` gets committed — and removing a file
from history afterwards means a force-push and a rewritten history, which is exactly the
kind of mess a judge browsing your commits will notice.

## Working rhythm

Commit small and often. Twenty commits telling a story beat one dump on September 9th,
and the difference is visible in ten seconds.

```bash
python -m pytest -q          # green before every commit
git add -p                   # stage in hunks; it makes you read your own diff
git commit
git push
```

`git add -p` is worth the extra keystrokes on this project specifically — it is the last
place you would catch a private value pasted in during debugging.

## Commit message convention

One line, imperative mood, under ~70 characters, **saying why where the why isn't
obvious**. A body paragraph when the reasoning deserves one.

```
Key the ledger on wheel-end identity rather than walkaround position

Coupling a trailer renumbers the driver side: the tractor's driver steer is
position 6 bobtail and position 10 with a two-axle trailer. Trending by
position would compare different physical brakes across sweeps, which is a
false positive on a safety system.
```

Good:

```
Refuse untrusted classes rather than inferring around them
Load calibration at runtime instead of hardcoding measurements
Sample during the hold rather than retrying after failure
Reserve D4/D5 for FDCAN1; move the PTI button to D2
```

Avoid: `update`, `fix`, `wip`, `changes`, `asked Claude for help`.

The test for a good message: someone reading it in six months, with no memory of today,
understands what changed and why it was worth changing.

## Tags worth setting

```bash
git tag -a v0.1.0-gateway -m "Gateway core, 78 tests, no hardware required"
git tag -a v0.2.0-hardware -m "First successful sweep against a real sensor"
git tag -a v1.0.0-submission -m "Hackster submission, Invent the Future"
git push --tags
```

Tags give a judge — and later, anyone reading your profile — a way to see the shape of
the project without reading every commit.

## If something private gets committed

Stop. Do not just delete the file in a new commit; the old one stays in history and is
still fetchable.

1. If you have **not pushed**: `git reset --soft HEAD~1`, remove the file, re-commit.
2. If you **have pushed**: treat the value as disclosed. Rotate it if it can be rotated —
   regenerate the calibration file under a new version, change any UUID that can be
   changed. Rewriting public history with `git filter-repo` is possible but people may
   already have the old objects.

The five seconds `git add -p` costs is insurance against the hour this would cost.

## Branches

For a three-week solo project, working directly on `main` is fine and keeps the history
readable. Use a branch for anything genuinely speculative — the CAN spike is the obvious
candidate — so an experiment that does not work out does not clutter the story:

```bash
git switch -c spike/can-j1939
# ...
git switch main && git merge --no-ff spike/can-j1939
```

`--no-ff` keeps the spike visible as a unit of work rather than dissolving it into the
mainline.
