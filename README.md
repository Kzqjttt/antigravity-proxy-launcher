# agproxy

Launch **Google Antigravity** with a working HTTP proxy injected into its process
environment, so sign-in can actually reach Google's OAuth endpoints.

Zero dependencies. Python 3.8+ standard library only. Windows / macOS / Linux.

> **Platform status:** Windows verified; macOS / Linux implemented but not tested on real hardware — test reports welcome.

[简体中文说明](README.zh-CN.md)

---

## What problem does this solve?

On networks where direct access to Google is unreliable, Antigravity sign-in can
fail with an error like:

```
There was an unexpected issue setting up your account.

Post "https://oauth2.googleapis.com/token":
dial tcp 192.179.26.95:443: connectex: A connection attempt failed because
the connected party did not properly respond after a period of time, or
established connection failed because connected host has failed to respond.
```

The browser part of the OAuth flow succeeds (the callback comes back), but the
**final token exchange** is performed by Antigravity's Go-based
`language_server`, and Go's `net/http` reads proxy settings from
`HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` **environment variables** — it does
not read the Windows registry system-proxy setting or macOS network
preferences. So even when your system proxy is on, the token request goes out
directly, hits an unreachable address, and times out.

`agproxy` fixes exactly that, and nothing else:

1. Finds a proxy that **really works** (not just a listening port).
2. Locates your Antigravity installation.
3. Restarts Antigravity with proxy environment variables set for its process
   tree only.
4. Verifies that `language_server` traffic is going through the proxy.

### What it does NOT do

- ❌ Does **not** modify any Antigravity binary or config file
- ❌ Does **not** install a TUN/TAP driver, create a virtual adapter, or touch
  routing tables / DNS servers / the system proxy setting
- ❌ Does **not** provide a proxy, proxy nodes, subscriptions, or any network
  egress of its own
- ❌ Does **not** bypass account eligibility, change your Google account's
  associated region, verify your age, or defeat any server-side check
- ❌ Does **not** wrap the `agy` CLI, automate account switching, or expose your
  Antigravity credentials to third-party applications
- ❌ Does **not** bundle or redistribute any third-party patcher

It is functionally equivalent to typing
`HTTPS_PROXY=... /path/to/Antigravity` in a terminal. The only added value is
automatic discovery, a real reachability pre-flight, and post-launch
verification.

> **If your account is not eligible** (wrong associated country, under 18,
> Workspace account, etc.), this tool will not help. Fix that first via the
> official routes described in the [Antigravity FAQ](https://antigravity.google/docs/faq).

---

## Quick start

**Prerequisite:** a working HTTP proxy client already running on your machine
(Clash, Mihomo, sing-box, v2rayN, a corporate proxy, etc.). This project does
not include one.

### 1. Diagnose (read-only, changes nothing)

```bash
python agproxy.py --check
```

Expected output:

```
agproxy 1.0.0 | Windows-11-...
Local DNS oauth2.googleapis.com -> 142.250.157.95
Discovering a working proxy ...
Proxy OK   : http://127.0.0.1:7890
  CONNECT  : HTTP/1.1 200 Connection established
  TLS      : TLSv1.3
  POST/tok : HTTP/1.1 400 Bad Request  [reached Google]
App        : C:\Users\...\antigravity\Antigravity.exe

RESULT: PASS - proxy reachable, app found. Nothing was launched.
```

That `400 Bad Request` is **good news** — it is Google's own response to our
deliberately invalid probe request, proving traffic really reached Google
rather than being black-holed or intercepted.

### 2. Launch

```bash
python agproxy.py
```

On Windows you can simply double-click **`run-windows.bat`**. On macOS/Linux:

```bash
chmod +x run-unix.sh && ./run-unix.sh
```

Then complete sign-in in the Antigravity window.

> ⚠️ Launch Antigravity **through this tool**, not from the Start Menu / Dock /
> original shortcut. Environment variables are inherited only when a process is
> created, so a normal launch will not have the proxy set.

---

## How a proxy is chosen

Candidates are tried in this order, and each one is **actively probed**:

| # | Source |
|---|--------|
| 1 | `--proxy` / `config.json` |
| 2 | `AGPROXY_PROXY`, `HTTPS_PROXY`, `HTTP_PROXY`, `ALL_PROXY` environment variables |
| 3 | Windows registry system proxy (`HKCU\...\Internet Settings`) / macOS `scutil --proxy` |
| 4 | Common local ports (7890, 7897, 10809, 1080, 8888, 65532, …) |

A candidate is accepted only if all of these succeed:

1. TCP connection to the proxy
2. `CONNECT oauth2.googleapis.com:443` → `200 Connection established`
3. TLS handshake with **certificate validation** and correct SNI
4. `POST /token` returns a Google-shaped error response

This is deliberate: a port that is listening but returns `502`, black-holes the
tunnel, or MITMs with an untrusted certificate is *not* good enough, and using
it would just reproduce the original error.

> If your proxy performs TLS interception with a private CA, install that CA
> into your OS trust store. `--insecure` exists but is strongly discouraged —
> it disables certificate validation and would let a malicious proxy read your
> OAuth traffic.

---

## CLI reference

```
python agproxy.py [options]

  --proxy URL          Proxy URL or bare port, e.g. http://127.0.0.1:7890 / 7890
  --fallback           Allow falling back to other candidates if --proxy fails
  --yes                Confirm force-closing Antigravity (non-interactive / --json)
  --verify-only        Verify a running Antigravity only; no probe/kill/launch
  --app PATH           Full path to Antigravity executable or .app bundle
  --config PATH        config.json (auto-loaded from script dir if present)
  --check              Read-only diagnosis; launch nothing
  --no-kill            Do not close already-running Antigravity instances
  --skip-probe         Trust --proxy without probing (not recommended)
  --insecure           Skip TLS certificate validation (unsafe)
  --ports 1,2,3        Extra local ports to probe first
  --timeout SEC        Probe timeout (default 6)
  --wait SEC           Seconds to poll for verification data (default 20)
  --no-verify          Skip post-launch connection check
  --json               Emit one JSON object (for scripts / CI / agents)
  -v, --verbose        Show every candidate being probed
  --version
```

### Configuration file

Copy `config.example.json` to `config.json` next to the script:

```json
{
  "proxy": "http://127.0.0.1:7890",
  "app": null,
  "extra_no_proxy": ["10.0.0.0/8", "*.internal"],
  "common_ports": [7890],
  "insecure": false
}
```

Command-line flags override the config file. `config.json` is git-ignored.

---

## Verifying it worked

`agproxy` checks `language_server` connections after launch:

```
RESULT: PASS - traffic goes through the proxy, 0 direct :443 connections.
```

If you see `WARNING - N direct :443 connection(s) detected`, the environment
variables were not inherited — usually because Antigravity was launched by
something else, or an existing instance was not closed (use the default
behaviour, not `--no-kill`).

You can also inspect Antigravity's own log:

| OS | Log |
|----|-----|
| Windows | `%APPDATA%\Antigravity\logs\language_server.log` |
| macOS | `~/Library/Application Support/Antigravity/logs/language_server.log` |
| Linux | `~/.config/Antigravity/logs/language_server.log` |

A healthy run has **no** `dial tcp ... i/o timeout` lines after launch.

---

## Troubleshooting

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) and
[docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md).

Common ones:

| Symptom | Fix |
|---------|-----|
| `no proxy could reach ...` | Start your proxy client; or pass `--proxy` |
| `Antigravity not found` | Pass `--app "C:\...\Antigravity.exe"` |
| `TLS certificate validation failed` | Install the proxy's CA; avoid `--insecure` |
| Login works but says "not eligible" | Account/region issue, not network — see FAQ |
| Still times out | Make sure the proxy node can reach Google; try `--verbose` |

---

## ⚠️ Terms of Service notice (please read)

This project is **not affiliated with, endorsed by, or supported by Google**.

The [official Antigravity FAQ](https://antigravity.google/docs/faq) states:

> "Using third-party software, tools, or services to access Antigravity is a
> violation of our Terms of Service and severely degrades the experience for
> legitimate product users. Such actions can result in suspension or
> termination of your account."

and, regarding the CLI:

> "Wrapping agy inside third-party coding agents or applications, or
> automating account rotation, is not supported under consumer plans."

Those statements address using third-party coding agents with an Antigravity
login, wrapping `agy`, and automating account rotation. **`agproxy` does none
of those** — it only sets standard proxy environment variables, the same
mechanism used by `git`, `pip`, `curl`, and `npm`. It does not touch your
account, your credentials, or the Antigravity installation.

However, we cannot determine whether **your particular usage** complies with
the Terms of Service, your local laws, or your organisation's policies. In
particular, the FAQ says Antigravity is unavailable in countries outside its
published availability list and recommends correcting your account's
associated region through official channels. Using a proxy to work around a
regional restriction is your own decision and your own risk.

**Use a lawful network path, use an eligible account, and read the Terms of
Service yourself.** The authors accept no liability for account suspension,
data loss, or any other consequence.

---

## Platform support

| Platform | Status |
|----------|--------|
| Windows 11 + Antigravity 2.32.2 | ✅ End-to-end tested (auto-discovery, pre-flight, launch, verification) |
| macOS | ⚠️ Code path implemented, **not verified on real hardware** |
| Linux | ⚠️ Code path implemented, **not verified on real hardware** |

On macOS/Linux the executable path may differ from our guesses — use `--app`
if auto-detection fails. Contributions and test reports are welcome.

---

## Contributing

Issues and PRs welcome, especially:
- confirmed macOS / Linux app paths
- additional proxy clients and their default ports
- translations

Please do **not** submit PRs that add account-rotation, eligibility bypass,
credential handling, or redistribution of third-party patchers.

## License

[MIT](LICENSE)
