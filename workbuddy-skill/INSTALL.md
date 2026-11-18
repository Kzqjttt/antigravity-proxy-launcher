# Installing the WorkBuddy Skill

This directory is a self-contained [WorkBuddy](https://www.workbuddy.cn/) skill.
It is optional — the main repository works without WorkBuddy.

## Install (user-level, recommended)

Copy this whole `workbuddy-skill/` directory into your user skills folder and
rename it to the skill id:

```bash
# Windows (Git Bash / PowerShell)
cp -r workbuddy-skill "$USERPROFILE/.workbuddy/skills/antigravity-proxy-launcher"

# macOS / Linux
cp -r workbuddy-skill ~/.workbuddy/skills/antigravity-proxy-launcher
```

Resulting layout:

```
~/.workbuddy/skills/antigravity-proxy-launcher/
├── SKILL.md
└── scripts/
    └── agproxy.py
```

Restart the conversation (or reload skills) and the skill will be available.
Ask something like:

> "Antigravity 登录报 dial tcp oauth2.googleapis.com/token 超时，帮我修一下"

## Project-level install

Copy the same directory to
`<workspace>/.workbuddy/skills/antigravity-proxy-launcher/` to share it with a
team working in that workspace.

## Keeping the script in sync

`scripts/agproxy.py` here is a byte-for-byte copy of the repository root
`agproxy.py`. If you change one, copy it to the other:

```bash
cp agproxy.py workbuddy-skill/scripts/agproxy.py
```

## Notes

- No third-party Python packages are required.
- The skill deliberately keeps all network operations behind an explicit
  read-only `--check` first, and never modifies system routing, DNS, registry,
  or the system proxy setting.
- It will not claim success unless the post-launch verification confirms
  `language_server` traffic is going through the proxy.
