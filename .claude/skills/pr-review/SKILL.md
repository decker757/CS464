---
name: pr-review
description: Review a CS464 pull request or branch against its GitHub issue's acceptance criteria and Definition of Done, the repo's code conventions (backend/CLAUDE.md, frontend/CLAUDE.md), and the "tests earn their place" rule — flagging decorative tests for removal. Use on "/pr-review", "review PR 110", "review my branch", "is this PR ready", or before requesting review on your own work.
argument-hint: "[PR number | branch name]  (default: current branch vs dev)"
---

# PR review for CS464

You are reviewing a teammate's work. The goal is a short, plain-language report
they can act on: does this do what the ticket asked, is it written the way we
agreed, and does every test earn its place. The team is not deeply technical —
write every finding so someone who did not write the code can understand it
without opening five files.

**Read only. Do not edit files, push, or check out the author's branch.**
Findings go back as comments for the author to fix.

## 1. Get the diff

- **PR number given:**
  ```bash
  gh pr view <N> --json number,title,body,author,headRefName,baseRefName,closingIssuesReferences,files
  gh pr diff <N>
  gh pr checks <N>
  ```
  To read a whole changed file (not just the hunk), fetch the branch and use
  `git show origin/<headRefName>:<path>`. Do not `git checkout` it.
- **Branch name given:** `git fetch origin`, then diff `origin/<branch>` if it
  has been pushed, or the local `<branch>` if it has not.
- **Nothing given:** review the current branch: `git fetch origin dev` then
  `git diff origin/dev...HEAD`, plus uncommitted changes from `git diff HEAD`.

**Stacked work:** if the PR body says `Stacked on #N`, or the branch was built
on another feature branch, diff against that branch rather than `dev`, so you
review only this part. Then check the part stands alone: it does one thing and
passes its tests without the parts above it.

Skip generated and vendored files (`package-lock.json`, `.venv/`, `node_modules/`).

## 2. Find the ticket

Look, in order, for: `closingIssuesReferences`; `Closes #N` / `Refs #N` in the
PR body; the number at the start of the branch name (`22-buy-shares` → #22).

```bash
gh issue view <N>
gh api repos/{owner}/{repo}/issues/<N>/sub_issues --jq '.[] | "#\(.number) \(.title) [\(.state)]"'
```

Also read any issue the body names under "Depends on", "Blocked by" or
"Required by", and the parent if this is an `[FE]`/`[BE]` sub-issue.

Check the PR follows the root `CLAUDE.md` → Branches rule: a title of the form
`[<tag>] <what it does> (#N)` and `Closes #N` or `Refs #N` as the body's first
line, with `Closes` only if merging finishes the issue. A missing link is a
**Must fix**; a title in the wrong shape is a **Nit**.

If there is no issue at all, say so at the top of the report and review
conventions and tests only — you cannot judge "done" without the ticket. Do
not invent acceptance criteria from the code.

## 3. Load the rules for what changed

- The root `CLAUDE.md` is already loaded. Its section **Code style, tests and
  review** holds the test rule you will apply in step 6.
- Read `backend/CLAUDE.md` if any path is under `backend/`, and
  `frontend/CLAUDE.md` if any is under `frontend/`.
- Read any ADR or `DECISIONS.md` entry the diff or the issue cites, and the
  ADRs for the area touched (the root `CLAUDE.md` names which ADR covers what).
- If `~/.claude/bug-journal/` exists, read the Prevention lines for this project.

## 4. Acceptance criteria

Make one row per criterion in the issue, copied word for word:

| # | Acceptance criterion | Done where | Proved by test | Verdict |
|---|---|---|---|---|

- **Done where:** `file:line` of the code that makes it true.
- **Proved by test:** the test name that would fail if it stopped being true.
  A criterion with code but no test is ⚠️, not ✅.
- **Verdict:** ✅ met · ⚠️ partly (say what is missing) · ❌ not met ·
  ➖ belongs to a sub-issue (name it).

Then list anything in the diff that **no criterion asks for**. That is not
automatically wrong, but the author should say why it is in this PR.

## 5. Definition of Done

One line per item in the issue's Definition of Done. Answer each from evidence:
CI from `gh pr checks`, "tests passing" from CI or a local run, "reviewed" is
what you are doing now. Never mark an item done because the PR description
says so.

## 6. Tests

For every test **added or changed** in the diff, decide one of:

- **Keep** — it traces to an acceptance criterion, a DoD item, an invariant in
  `CLAUDE.md` or an ADR, or a real bug. You do not need to list these one by one.
- **Remove** — it is decorative under the root `CLAUDE.md` rule. Apply the
  check there: mentally undo the line of the feature it is about; if the test
  stays green, it goes. Name the test and give the one-sentence reason.
- **Merge** — it repeats another test with different data on the same branch;
  say which test to fold it into, or suggest `parametrize` / `it.each`.

Do not mark a guard test decorative just because it looks trivial — the root
`CLAUDE.md` names the ones that are deliberate.

Then list **missing** tests: a criterion with no test, no failure case
asserting the exact error code, and for anything that moves money, no rollback
or concurrency test (`backend/CLAUDE.md` → Tests).

## 7. Conventions

Check the diff against the side-specific `CLAUDE.md`. Most useful first:

1. **Reuse.** For each new function, search the repo for one that already does
   the job (`grep` the name's verb and noun, and the core expression it
   computes). A near-duplicate of existing code is a **Must fix**; name the
   existing function and where it lives. Also look for logic repeated *within*
   the diff.
2. **Names** that do not say what the function achieves, or that break the
   verb table in `backend/CLAUDE.md`.
3. **Layout:** helpers below or scattered between the public functions that
   use them; frontend helpers defined inside a component when they use no
   props or state.
4. **Comments** that restate the code, narrate history, or run past a few
   lines of reasoning that belongs in `DECISIONS.md`.
5. Frontend: hex colours, hover handlers editing `style`, URL strings or axios
   error parsing in a page.
6. **Clever code** (root `CLAUDE.md` → Readable beats clever): nested
   comprehensions, chained ternaries, one-liners doing several things,
   metaprogramming or type tricks. Quote the line and write the plain version
   beside it. A **Should fix**, unless nobody on the team could follow it.
7. **PR shape** (root `CLAUDE.md` → Branches): commits not split by layer, a
   refactor mixed with a behaviour change, or a refactor PR that edits a test
   assertion. If the PR does more than one thing, suggest how to split it into
   a stack.

Only flag code the diff adds or changes. If you notice a problem in untouched
code, put it under **Follow-ups**, not in the findings.

## 8. Correctness

Read the changed logic for real bugs, using the root `CLAUDE.md`'s "Things
that will waste your time" as the checklist for this repo: reads that decide a
write without `with_for_update()`, gating trading on `status == OPEN`, a stored
balance, an audit entry committed separately from its action, a publish before
commit, a new column without a `sql/migrations/` file, a credential in any file.

Every correctness finding needs a concrete scenario: "if two requests do X at
the same time, Y happens." If you cannot write the scenario, do not report it.

## 9. Report

Use this shape. Plain sentences, no jargon you do not explain, no praise
padding. Every finding gives `file:line`, what is wrong, and what to do.

```markdown
## Review: #<PR> <title>  (ticket #<N>)

**Verdict:** Ready · Ready after the must-fixes · Not ready
<one or two sentences on why>

### Acceptance criteria
<table from step 4>
<anything no criterion asks for>

### Definition of Done
- [x] / [ ] each item, with the evidence

### Must fix
Bugs, unmet criteria, duplicated logic, missing required tests.

### Should fix
Convention breaks that make the code harder to read or change.

### Tests to remove or merge
- `test_name` (`file:line`) — remove: <one-sentence reason>

### Missing tests
- <criterion or case> — <what the test should assert>

### Nits
At most five. Skip the section if there are none.

### Follow-ups (outside this PR)
Problems noticed in untouched code. Optional.
```

Leave out any section with nothing in it, except the verdict and the
acceptance criteria.

## 10. Posting

Show the report in the terminal first. Post it to GitHub only if the user
asks, and confirm before you do — it is public on the PR:

```bash
gh pr review <N> --comment --body-file <report.md>
```

Use `--comment`, never `--approve` or `--request-changes`; the reviewer
decides that. Never push a fix to the author's branch; the author makes the
changes.
