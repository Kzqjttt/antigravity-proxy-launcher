# Publishing to GitHub

A short checklist for turning this directory into a public repository.

## 1. Pre-flight (do this before `git init`)

```bash
# Nothing machine-specific should be committed
grep -rInE 'SESSDATA|password@|Bearer |subscription|Cookie:' . || echo clean
```

Confirm these are **absent**:

- `config.json` (git-ignored; contains local paths / credentials)
- any `.log`, `_selftest*`, `__pycache__`
- absolute paths to your own machine
- cookies, tokens, subscription URLs, screenshots with account info

The tool itself redacts credentials in proxy URLs, but your **local config file**
is not redacted — keep it out of the repo.

## 2. Create the repository

Suggested name: `antigravity-proxy-launcher`
Suggested description:

> Launch Google Antigravity with a working HTTP proxy injected into its process
> environment. Fixes `oauth2.googleapis.com/token` dial timeouts. No TUN, no
> client patching, zero dependencies.

Suggested topics:
`antigravity` `proxy` `oauth` `networking` `python` `windows` `troubleshooting`

Pick **MIT** as the licence (a `LICENSE` file is already included). Do **not**
add a `SECURITY.md` promising support for account eligibility.

```bash
cd antigravity-proxy-launcher
git init
git add .
git commit -m "Initial release: agproxy v1.0.0"
git branch -M main
git remote add origin https://github.com/<you>/antigravity-proxy-launcher.git
git push -u origin main
```

## 3. Release the zip

```bash
git tag -a v1.0.0 -m "v1.0.0"
git push origin v1.0.0
```

Then attach `antigravity-proxy-launcher-v1.0.0.zip` to the GitHub Release.
Windows users will mostly download that zip rather than clone.

## 4. Repository settings worth enabling

- Issues: on (a bug report template is not included; the Troubleshooting doc
  lists what information to ask for)
- Discussions: optional
- Add a pinned issue: "macOS / Linux test reports wanted"

## 5. Content responsibilities

Please keep the Terms of Service notice in both READMEs intact. In particular:

- do not market the project as "bypass region lock" / "绕过地区限制"
- do not add account rotation, eligibility bypass, credential handling, or
  third-party patchers
- do not imply Google endorsement

The [Antigravity FAQ](https://antigravity.google/docs/faq) explicitly warns
about third-party tooling and account suspension. Readers deserve to see that
warning before they use the tool.

## 6. Attribution

State clearly that:

- this is an unofficial, unaffiliated community project
- it only sets standard proxy environment variables
- it does not provide a proxy or any network egress
