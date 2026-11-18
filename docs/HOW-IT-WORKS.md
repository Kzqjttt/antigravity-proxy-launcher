# How it works

## The failing request

Antigravity sign-in is a standard OAuth flow, but it is split across two
components:

```
┌─────────────────────┐   1. open browser / Google login    ┌──────────┐
│  Electron UI        │ ──────────────────────────────────> │ Browser  │
│  (Chromium stack)   │ <────────────────────────────────── │          │
└─────────────────────┘   2. localhost callback (?code=…)   └──────────┘
          │
          │ 3. hands the code to the Go core
          v
┌─────────────────────────────────────────────────────────────────┐
│ language_server (Go)                                            │
│   POST https://oauth2.googleapis.com/token                     │
│   -> net/http.ProxyFromEnvironment()                            │
│   -> reads HTTP_PROXY / HTTPS_PROXY / ALL_PROXY only            │
│   -> if unset: direct TCP dial                                 │
│   -> FAIL: dial tcp <ip>:443: i/o timeout                     │
└─────────────────────────────────────────────────────────────────┘
```

This matches the real log from a failing run:

```
[auth-login] attempt=1 step=callback_received ms_to_callback=18204
[client.go:96] Post "https://play.googleapis.com/log":
              dial tcp 172.217.116.4:443: i/o timeout
[error_parsing.go:87] Post "https://oauth2.googleapis.com/token":
              dial tcp 192.179.26.95:443: connectex: ... timeout
[auth-login] attempt=1 net_error=dial ... outcome=error
```

Two things are worth noting:

1. **`callback_received` succeeded.** The browser side is fine. Only step 3
   (token exchange) fails, and that is the Go core.
2. **Both the wrong IP and the correct IP fail.** `192.179.26.95` is not a
   Google address (DNS pollution / hijack), but `172.217.116.4` *is* a real
   Google frontend and it also times out. So editing `hosts` or switching to a
   public DNS resolver alone will not fix this — the direct path itself is
   blocked. A working proxy is required.

## Why the system proxy setting does not help

| Component | Reads |
|-----------|-------|
| Chromium / Electron UI | OS system proxy (Windows registry, macOS prefs) — works |
| Go `language_server` | `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY` env vars **only** |

Go's `httpproxy.FromEnvironment` is deliberately independent of OS-level proxy
configuration. On Windows it does not consult
`HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings`; on macOS
it does not call `scutil`. Many proxy clients set the system proxy but never
set process environment variables, which produces exactly this split behaviour:
the login page loads, the token exchange dies.

## Why environment variables must be set at launch

A process inherits its environment **once**, at creation time. Changing a
parent shell's variables afterwards (or setting the system proxy) cannot
retroactively change a running `language_server`. Hence `agproxy` closes any
existing Antigravity instance and starts a fresh one with the variables set.

## What the pre-flight proves

A TCP port being open says almost nothing. `agproxy` requires:

| Check | What it rules out |
|-------|-------------------|
| `CONNECT oauth2.googleapis.com:443` → 200 | Port open but proxy misconfigured / returns 502 |
| TLS handshake with certificate validation + SNI | Plaintext interception, untrusted MITM CA, wrong endpoint |
| `POST /token` → Google-shaped `{"error": ...}` | Tunnel established but black-holed / answered by a fake server |

Only then is the proxy used. TLS validation is on by default; `--insecure`
turns it off but is explicitly discouraged.

## What changes on your machine

| Item | Changed? |
|------|----------|
| Antigravity binaries / config files | No |
| Windows registry / macOS network preferences | No |
| Routing table, default gateway, DNS servers | No |
| TUN/TAP adapter, virtual NIC, drivers | No |
| System proxy on/off state | No |
| Environment variables of the launcher shell | No (only of the child process tree) |
| Running Antigravity processes | Yes — restarted with proxy vars, by design |

Everything is scoped to the Antigravity process tree and disappears when the
app exits. There is nothing to uninstall and nothing to roll back.

## Why no TUN

A global TUN adapter takes over the default route for the whole machine. On a
system with an existing VPN virtual adapter, a corporate proxy, or a
sandboxed egress channel, that can create routing conflicts or a feedback loop
that cuts off remote access entirely. Process-level environment injection
avoids all of that: it affects only one process tree.

## Verification

After launch, `agproxy` enumerates `language_server` and inspects its
established TCP connections (`netstat` on Windows, `ss` on Linux, `lsof` on
macOS):

- `via_proxy > 0` and `direct_443 == 0` → PASS
- `direct_443 > 0` → env vars were not inherited
- tool unavailable → reported as inconclusive, not silently claimed as success

Windows path is end-to-end tested. macOS/Linux inspection is best-effort.
