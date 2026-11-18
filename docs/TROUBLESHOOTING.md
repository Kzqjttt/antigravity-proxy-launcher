# Troubleshooting

Run the read-only diagnostic first:

```bash
python agproxy.py --check --verbose
```

`--verbose` prints every proxy candidate and why it was rejected.

---

## `ERROR: 显式代理不可用，已停止；如需自动回落请加 --fallback`

When `--proxy` (or `proxy` in `config.json`) is set, agproxy probes **that
proxy only**. If it fails, the run stops with exit code 1 instead of silently
falling back to environment variables, the system proxy, or the built-in port
list. This prevents "I thought it was using A but it actually used B".

* Add `--fallback` to restore the old behaviour (keep trying other candidates).
* When a fallback candidate wins, `proxy_source` in the JSON report is
  `"fallback"`, so the switch is explicit rather than hidden.
* An invalid explicit proxy string is rejected immediately
  (`ERROR: 显式代理地址无效`) instead of being silently discarded.
* Without `--proxy`, discovery behaviour is unchanged.

---

## `ERROR: no proxy could reach oauth2.googleapis.com`

The last error line tells you which stage failed:

| Last error | Meaning | Fix |
|------------|---------|-----|
| `ConnectionRefusedError` / nothing listening | No proxy client running | Start your proxy client |
| `proxy refused CONNECT: HTTP/1.1 502 ...` | Port is open but the proxy cannot reach Google | Check proxy node / subscription / rules; try `--proxy <other port>` |
| `TimeoutError: timed out` on CONNECT | Proxy accepts connections but the tunnel hangs | Node is dead or blocked; switch node |
| `ssl.SSLCertVerificationError` | Proxy intercepts TLS with an untrusted CA | Install the proxy's CA into the OS trust store |
| `[WinError 10054] connection reset` | Proxy or upstream dropped the tunnel | Try another node / client |

**Important:** a "connected" Wi-Fi and a green icon in your proxy client are
not proof. The pre-flight deliberately requires a Google response. Test your
proxy independently if in doubt:

```bash
python agproxy.py --check --proxy http://127.0.0.1:7890 --verbose
```

---

## `ERROR: Antigravity not found`

Auto-detection covers the common Windows install location
`%LOCALAPPDATA%\Programs\antigravity\Antigravity.exe`, plus a few guesses on
macOS/Linux. If yours differs:

```bash
python agproxy.py --app "C:\Path\To\Antigravity.exe" --check
```

On macOS you can point `--app` at the `.app` bundle directly.

> The macOS/Linux path lists are best-effort and unverified. If you confirm a
> correct path, please open an issue or PR — it helps everyone.

---

## `RESULT: WARNING - N direct :443 connection(s) detected`

Environment variables were not inherited. Usual causes:

1. Antigravity was launched from the Start Menu / Dock / original shortcut
   instead of through `agproxy`.
2. You used `--no-kill` while an old instance was still running.
3. A helper process re-spawned `language_server` with a scrubbed environment.

Fix: close Antigravity fully (check Task Manager / Activity Monitor), then run
`python agproxy.py` with default options.

---

## Login succeeds but then says "account not eligible" / "no access"

This is **not** a network problem and `agproxy` cannot fix it. Official
criteria are in the [Antigravity FAQ](https://antigravity.google/docs/faq):

- the country associated with your Google account (check the
  [Google Terms of Service page](https://policies.google.com/terms); there is an
  official form to request a change)
- you must be 18 or older (age verification may be required)
- a personal Google account is recommended; Workspace accounts have limited
  starter quotas
- availability is limited to the published list of countries and territories

Network fixes and account eligibility are separate layers. Solve the network
first (so you can actually complete OAuth), then handle the account.

---

## It used to work, now it times out again

- The proxy client was closed or its port changed → run `--check --verbose`
- Antigravity auto-updated and you relaunched from the old shortcut → launch
  through `agproxy` again
- The proxy node's region changed → some accounts require the proxy exit region
  to match the account's associated country

---

## `TLS certificate validation failed`

Your proxy is doing TLS interception with a private CA. Recommended fix:

- Export the CA from your proxy client and install it into the OS trust store
  (Windows: `certmgr.msc` → Trusted Root Certification Authorities;
  macOS: Keychain → System → Always Trust)

`--insecure` disables validation, but **it also means a malicious proxy could
read your OAuth tokens**. Only use it on a proxy you fully control, and never
on an untrusted network.

---

## SOCKS proxies

The built-in probe only supports HTTP proxies with `CONNECT`. If your client
exposes a SOCKS5 port, point `--proxy` at its **HTTP/mixed port** instead
(most clients expose both — e.g. 7890 HTTP + 7891 SOCKS on Clash). Go's
`net/http` also prefers `HTTP_PROXY`/`HTTPS_PROXY` over raw SOCKS.

---

## Corporate / authenticated proxies

`agproxy` forwards `Proxy-Authorization: Basic` from a
`http://user:pass@host:port` URL during the probe, and sets the same URL in the
child environment (Go understands it). Credentials are **redacted** in all
output and JSON reports. Note that `config.json` containing credentials is
git-ignored, but still store it carefully.

If your corporate proxy uses a PAC file rather than a fixed host:port, resolve
the effective proxy address yourself and pass it via `--proxy`.

---

## Getting a machine-readable result

```bash
python agproxy.py --check --json
python agproxy.py --json        # includes launch + verification
```

Useful fields: `probe.ok`, `probe.reached_google`, `launched`,
`verification.via_proxy`, `verification.direct_443`, `messages`.

Exit codes: `0` = success, `1` = pre-flight/app detection/launch failure,
`2` = bad arguments, `130` = interrupted.

---

## Still stuck?

Open an issue with:

- output of `python agproxy.py --check --verbose`
- OS and Antigravity version
- proxy client name and version (no subscription links, no credentials)
- the last ~15 lines of `language_server.log`

**Never** paste tokens, cookies, `SESSDATA`, subscription URLs, or account
details into an issue.
