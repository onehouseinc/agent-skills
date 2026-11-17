# agent-skills

Onehouse skills for coding agents (Claude Code, Cursor, Codex and others), in the
[Agent Skills](https://skills.sh) format: one directory per skill under `skills/`,
each with a `SKILL.md` plus any references and scripts it needs.

## Install

Install every skill into the current project:

```bash
npx skills add onehouseinc/agent-skills
```

Install one skill, for one agent, globally:

```bash
npx skills add onehouseinc/agent-skills --skill onehouse-autosetup -a claude-code -g
```

List what the repo offers without installing:

```bash
npx skills add onehouseinc/agent-skills --list
```

## Skills

| Skill | What it does |
| --- | --- |
| [`onehouse-autosetup`](skills/onehouse-autosetup/SKILL.md) | Set up, resume, check or tear down a Onehouse data plane on your own Kubernetes cluster with S3-compatible object storage (the Self-Managed Kubernetes cloud). Keeps a plan-and-checklist file at `~/onehouse-autosetup/<project id>/setup-plan.md`. Drives `oh` 0.7.0+, `kubectl` and `helm`; never handles your storage secret key. |

## Adding a skill

1. Create `skills/<name>/SKILL.md` with `name` and `description` frontmatter
   (`npx skills init <name>` scaffolds one).
2. Keep references in `skills/<name>/references/` and scripts in `skills/<name>/scripts/`;
   refer to them with paths relative to the skill directory.
3. Add a row to the table above.

`onehouse-autosetup` also ships with the Onehouse CLI: `oh install agent-skill` installs the
same skill.

## License

[Apache License 2.0](LICENSE).

## Tests

Script tests live in `tests/`, outside the skill folders so installs don't copy them:

```bash
pip install pytest pyyaml && pytest tests
```
