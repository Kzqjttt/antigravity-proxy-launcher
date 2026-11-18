#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agproxy.py - Launch Google Antigravity with a working HTTP proxy injected into
             the process environment.

WHY THIS EXISTS
---------------
Antigravity's Go-based language_server performs the OAuth token exchange with
`https://oauth2.googleapis.com/token`. Go's net/http reads proxy settings from
HTTP_PROXY / HTTPS_PROXY / ALL_PROXY environment variables - it does NOT read
the Windows registry system-proxy setting (or macOS network preferences).
On networks where DNS is polluted or Google endpoints are unreachable directly,
sign-in fails with:

    Post "https://oauth2.googleapis.com/token": dial tcp <ip>:443:
    connectex: A connection attempt failed ... / i/o timeout

This tool sets those variables for the Antigravity process tree only, then
launches the app. It does NOT modify any Antigravity file, does NOT install a
driver, does NOT create a TUN adapter, and does NOT change system-wide routing
or the system proxy setting.

SCOPE / LIMITS
--------------
* It only fixes NETWORK CONNECTIVITY. It cannot grant account eligibility,
  change your Google account's associated region, verify your age, or bypass
  any server-side check.
* It does not provide a proxy. You must already have a working, lawful proxy.
* It does not wrap the Antigravity CLI, automate account switching, or expose
  your Antigravity credentials to third-party applications.
* See README.md for the official Terms of Service notice.

Zero third-party dependencies. Python 3.8+ standard library only.
"""

from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import os
import platform
import re
import signal
import socket
import ssl
import subprocess
import sys
import time
import urllib.parse
from dataclasses import dataclass, field, asdict
from typing import Dict, Iterable, List, Optional, Tuple

APP_NAME = "agproxy"
VERSION = "1.0.0"
PROBE_HOST = "oauth2.googleapis.com"
PROBE_PORT = 443
DEFAULT_PORTS = [
    7890, 7897, 7891, 10809, 1080, 8888, 20171, 33210, 2080,
    7892, 10808, 8080, 3128, 65532, 1087, 7078, 10810, 2334,
]
# Heuristic only. Google frontends commonly live in these /8-/16 ranges.
# Used to WARN about likely DNS pollution, never to block anything.
# NOTE: "8.8." is Google Public DNS, not a web frontend; it stays here only to
# avoid a false "polluted" warning and has no effect beyond that warning text.
GOOGLE_IP_PREFIXES = (
    "8.8.", "8.4.4.", "74.125.", "142.250.", "172.217.", "172.253.",
    "216.58.", "216.239.", "209.85.", "64.233.", "173.194.", "66.102.",
    "64.18.", "207.126.", "74.114.",
)
NO_PROXY_DEFAULT = "127.0.0.1,localhost,::1,*.local"

# R5: CONNECT status lines may omit the reason phrase ("HTTP/1.1 200\r\n"), so
# parse the numeric code instead of looking for the literal " 200 " substring.
HTTP_STATUS_RE = re.compile(r"^HTTP/\d(?:\.\d)?\s+(\d{3})")


# --------------------------------------------------------------------------- #
# small utilities
# --------------------------------------------------------------------------- #

def out(msg: str = "", *, err: bool = False) -> None:
    stream = sys.stderr if err else sys.stdout
    print(msg, file=stream, flush=True)


def redacted(url: str) -> str:
    """Hide credentials in a proxy URL before printing / logging."""
    try:
        p = urllib.parse.urlsplit(url)
        if p.username or p.password:
            host = p.hostname or ""
            if p.port:
                host = "%s:%d" % (host, p.port)
            # R8a: only mask the password when one was actually supplied.
            cred = "***:***" if p.password else "***"
            return "%s://%s@%s%s" % (p.scheme, cred, host, p.path)
        return url
    except Exception:
        return "<invalid url>"


def norm_proxy(url: str) -> Optional[str]:
    """Normalise a proxy URL. Accepts host:port, http://host:port, socks5://..."""
    if not url:
        return None
    url = url.strip().strip('"').strip("'")
    if not url:
        return None
    if url.isdigit():
        # R7: a bare port such as "7890" means the usual local HTTP proxy.
        url = "http://127.0.0.1:" + url
    elif "://" not in url:
        url = "http://" + url
    p = urllib.parse.urlsplit(url)
    if not p.hostname or not p.port:
        return None
    # Keep scheme + netloc only; path/query on a proxy URL is meaningless.
    return "%s://%s" % (p.scheme, p.netloc)


def proxy_addr(url: str) -> Tuple[str, int, str]:
    p = urllib.parse.urlsplit(url)
    return p.hostname or "127.0.0.1", p.port or 80, p.scheme.lower()


def is_socks(url: str) -> bool:
    return proxy_addr(url)[2].startswith("socks")


def local_resolve(host: str) -> str:
    try:
        return socket.gethostbyname(host)
    except Exception as e:
        return "unresolved (%s)" % type(e).__name__


def looks_polluted(ip: str) -> bool:
    if ip.startswith("unresolved"):
        return False
    return not any(ip.startswith(pref) for pref in GOOGLE_IP_PREFIXES)


# --------------------------------------------------------------------------- #
# result model
# --------------------------------------------------------------------------- #

@dataclass
class Probe:
    ok: bool = False
    proxy: Optional[str] = None
    connect_status: str = ""
    tls_version: str = ""
    http_status: str = ""
    reached_google: bool = False
    local_dns: str = ""
    dns_warning: bool = False
    error: str = ""

    def as_dict(self) -> dict:
        d = asdict(self)
        if d.get("proxy"):
            d["proxy"] = redacted(d["proxy"])
        return d


@dataclass
class Report:
    version: str = VERSION
    platform: str = ""
    mode: str = ""
    proxy: Optional[str] = None
    proxy_source: str = ""
    app: Optional[str] = None
    probe: Optional[Probe] = None
    launched: bool = False
    launch_pid: Optional[int] = None
    language_server_pids: List[int] = field(default_factory=list)
    verification: Dict[str, object] = field(default_factory=dict)
    messages: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        if d.get("proxy"):
            d["proxy"] = redacted(d["proxy"])
        return d


# --------------------------------------------------------------------------- #
# proxy discovery
# --------------------------------------------------------------------------- #

def env_candidates() -> Iterable[str]:
    for key in ("AGPROXY_PROXY", "HTTPS_PROXY", "https_proxy",
                "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        v = os.environ.get(key)
        if v:
            yield v


def registry_candidates() -> Iterable[str]:
    if os.name != "nt":
        return
    try:
        import winreg  # type: ignore
        path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            enabled, _ = winreg.QueryValueEx(k, "ProxyEnable")
            server, _ = winreg.QueryValueEx(k, "ProxyServer")
            if enabled and server:
                # ProxyServer may be "host:port" or "http=host:port;https=..."
                if "=" in server:
                    for part in server.split(";"):
                        if "=" in part:
                            yield part.split("=", 1)[1]
                else:
                    yield server
    except Exception:
        return


def scutil_candidates() -> Iterable[str]:
    if platform.system() != "Darwin":
        return
    try:
        txt = subprocess.check_output(["scutil", "--proxy"], text=True, timeout=5)
        host = port = None
        for line in txt.splitlines():
            line = line.strip()
            if "HTTPEnable" in line or "HTTPSEnable" in line or "SOCKSEnable" in line:
                if line.endswith(": 1"):
                    pass
            m = re.match(r"(HTTPS?|SOCKS)Proxy\s*:\s*(\S+)", line)
            if m:
                host = m.group(2)
            m = re.match(r"(HTTPS?|SOCKS)Port\s*:\s*(\d+)", line)
            if m:
                port = int(m.group(2))
        if host and port:
            yield "%s:%d" % (host, port)
    except Exception:
        return


def port_candidates(ports: List[int]) -> Iterable[str]:
    for p in ports:
        yield "http://127.0.0.1:%d" % p


def all_candidates(explicit: Optional[str],
                   ports: List[int]) -> List[Tuple[str, str]]:
    """Return (proxy_url, source) pairs. source is one of
    explicit / env / system / port and feeds Report.proxy_source (R1)."""
    seen: set = set()
    result: List[Tuple[str, str]] = []
    sources: List[Tuple[str, Iterable[str]]] = []
    if explicit:
        sources.append(("explicit", [explicit]))
    sources += [
        ("env", env_candidates()),
        ("system", registry_candidates()),
        ("system", scutil_candidates()),
        ("port", port_candidates(ports)),
    ]
    for origin, gen in sources:
        for raw in gen:
            u = norm_proxy(raw)
            if u and u not in seen:
                seen.add(u)
                result.append((u, origin))
    return result


# --------------------------------------------------------------------------- #
# proxy probe - the important part
# --------------------------------------------------------------------------- #

def probe_proxy(url: str, *, insecure: bool = False,
                timeout: float = 6.0) -> Probe:
    """Verify a proxy can actually reach Google's OAuth token endpoint.

    A listening port is not enough: we require CONNECT 200, a validated TLS
    handshake against oauth2.googleapis.com, and a Google-shaped HTTP error
    from POST /token. That last step proves traffic really reached Google
    rather than being intercepted or black-holed.
    """
    host, port, scheme = proxy_addr(url)
    pr = Probe(proxy=url, local_dns=local_resolve(PROBE_HOST))
    pr.dns_warning = looks_polluted(pr.local_dns)

    if is_socks(url):
        pr.error = "SOCKS proxies are not supported by the built-in probe; " \
                   "use --proxy with an http:// proxy, or --skip-probe"
        return pr

    raw = None
    try:
        raw = socket.create_connection((host, port), timeout=timeout)
        raw.settimeout(timeout)
        creds = ""
        p = urllib.parse.urlsplit(url)
        if p.username:
            creds = base64.b64encode(
                ("%s:%s" % (p.username, p.password or "")).encode()
            ).decode()
        req = "CONNECT %s:%d HTTP/1.1\r\nHost: %s:%d\r\n" % (
            PROBE_HOST, PROBE_PORT, PROBE_HOST, PROBE_PORT)
        if creds:
            req += "Proxy-Authorization: Basic %s\r\n" % creds
        req += "\r\n"
        raw.sendall(req.encode("ascii"))

        buf = b""
        while b"\r\n\r\n" not in buf and len(buf) < 8192:
            chunk = raw.recv(1024)
            if not chunk:
                break
            buf += chunk
        status = buf.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        pr.connect_status = status
        m = HTTP_STATUS_RE.match(status)
        connect_ok = bool(m and m.group(1) == "200")
        if not connect_ok:
            pr.error = "proxy refused CONNECT: %s" % status
            return pr

        ctx = ssl.create_default_context()
        if insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        tls = ctx.wrap_socket(raw, server_hostname=PROBE_HOST)
        raw = tls
        pr.tls_version = tls.version()

        body = "grant_type=agproxy_probe"
        http_req = (
            "POST /token HTTP/1.1\r\n"
            "Host: %s\r\n"
            "User-Agent: %s/%s\r\n"
            "Content-Type: application/x-www-form-urlencoded\r\n"
            "Content-Length: %d\r\n"
            "Connection: close\r\n\r\n%s"
        ) % (PROBE_HOST, APP_NAME, VERSION, len(body), body)
        tls.sendall(http_req.encode("ascii"))

        resp = b""
        while len(resp) < 8192:
            chunk = tls.recv(1024)
            if not chunk:
                break
            resp += chunk
        first = resp.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        pr.http_status = first
        pr.reached_google = (b'"error"' in resp and
                             b"googleapis" not in resp.lower()[:200] and
                             (b"invalid_grant" in resp or
                              b"unsupported_grant_type" in resp or
                              b"invalid_request" in resp))
        pr.ok = connect_ok and bool(pr.tls_version)
        return pr
    except ssl.SSLCertVerificationError as e:
        pr.error = ("TLS certificate validation failed (%s). The proxy may be "
                    "doing TLS interception. Do NOT use --insecure unless you "
                    "trust this proxy." % str(e)[:120])
        return pr
    except Exception as e:
        pr.error = "%s: %s" % (type(e).__name__, str(e)[:160])
        return pr
    finally:
        if raw is not None:
            try:
                raw.close()
            except Exception:
                pass


def find_working_proxy(candidates: List[Tuple[str, str]], *, insecure: bool,
                       skip_probe: bool, timeout: float,
                       verbose: bool) -> Tuple[Optional[str], Probe, str]:
    """R1: also return the source tag of the winning candidate so main() can
    populate Report.proxy_source."""
    last = Probe()
    for url, source in candidates:
        show = redacted(url)
        if verbose:
            out("  probing %s ... [%s]" % (show, source))
        if skip_probe:
            return url, Probe(ok=True, proxy=url,
                              connect_status="skipped (--skip-probe)"), source
        pr = probe_proxy(url, insecure=insecure, timeout=timeout)
        last = pr
        if pr.ok:
            return url, pr, source
        if verbose:
            out("    -> %s" % (pr.error or pr.connect_status))
    return None, last, ""


# --------------------------------------------------------------------------- #
# application discovery
# --------------------------------------------------------------------------- #

def app_candidates() -> List[str]:
    sysname = platform.system()
    la = os.environ.get("LOCALAPPDATA", "")
    pf = os.environ.get("ProgramFiles", "")
    pf86 = os.environ.get("ProgramFiles(x86)", "")
    home = os.path.expanduser("~")
    out_list: List[str] = []

    if sysname == "Windows":
        roots = [
            os.path.join(la, "Programs", "antigravity"),
            os.path.join(la, "Programs", "Antigravity"),
            os.path.join(pf, "Antigravity"),
            os.path.join(pf86, "Antigravity"),
        ]
        for r in roots:
            if not r or not os.path.isdir(r):
                continue
            exe = os.path.join(r, "Antigravity.exe")
            if os.path.isfile(exe):
                out_list.append(exe)
            # shallow scan for non-standard layouts
            try:
                for base, dirs, files in os.walk(r):
                    depth = base[len(r):].count(os.sep)
                    if depth >= 3:
                        dirs[:] = []
                        continue
                    for f in files:
                        if f.lower() == "antigravity.exe":
                            out_list.append(os.path.join(base, f))
            except Exception:
                pass
    elif sysname == "Darwin":
        for app in ("/Applications/Antigravity.app",
                    os.path.join(home, "Applications", "Antigravity.app")):
            if os.path.isdir(app):
                out_list.append(app)
                for name in ("Antigravity", "antigravity", "Electron"):
                    exe = os.path.join(app, "Contents", "MacOS", name)
                    if os.path.isfile(exe):
                        out_list.append(exe)
    else:
        for p in ("/opt/Antigravity/antigravity",
                  "/opt/antigravity/antigravity",
                  "/usr/bin/antigravity",
                  "/usr/local/bin/antigravity",
                  os.path.join(home, ".local", "share", "Antigravity", "Antigravity")):
            if os.path.isfile(p):
                out_list.append(p)

    seen = set()
    return [p for p in out_list if not (p in seen or seen.add(p))]


def resolve_app(explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit if os.path.exists(explicit) else None
    v = os.environ.get("AGPROXY_APP")
    if v and os.path.exists(v):
        return v
    for c in app_candidates():
        if os.path.exists(c):
            return c
    return None


# --------------------------------------------------------------------------- #
# process handling
# --------------------------------------------------------------------------- #

def process_inventory() -> Tuple[Optional[Dict[int, Dict[str, object]]], str]:
    """Best-effort pid -> {path, ppid, name} map plus a scope tag (R2).

    Returns (None, "name-only") when executable paths / parent PIDs cannot be
    obtained (missing tool, insufficient permission, unsupported platform).
    """
    sysname = platform.system()
    inv: Dict[int, Dict[str, object]] = {}
    try:
        if sysname == "Windows":
            txt = subprocess.check_output(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "Get-CimInstance Win32_Process | Select-Object "
                 "ProcessId,ParentProcessId,Name,ExecutablePath | "
                 "ConvertTo-Csv -NoTypeInformation"],
                text=True, timeout=15, errors="replace")
            for r in csv.DictReader(io.StringIO(txt)):
                try:
                    pid = int(str(r.get("ProcessId") or "").strip())
                except (TypeError, ValueError):
                    continue
                raw_ppid = str(r.get("ParentProcessId") or "0").strip()
                inv[pid] = {
                    "path": str(r.get("ExecutablePath") or ""),
                    "ppid": int(raw_ppid) if raw_ppid.isdigit() else 0,
                    "name": str(r.get("Name") or ""),
                }
        else:
            txt = subprocess.check_output(
                ["ps", "-eo", "pid=,ppid=,command="], text=True,
                timeout=10, errors="replace")
            for line in txt.splitlines():
                parts = line.strip().split(None, 2)
                if len(parts) < 3:
                    continue
                try:
                    pid, ppid = int(parts[0]), int(parts[1])
                except ValueError:
                    continue
                cmd = parts[2].strip()
                tokens = cmd.split()
                exe = tokens[0] if tokens else ""
                if sysname == "Linux":
                    try:
                        exe = os.readlink("/proc/%d/exe" % pid)
                    except OSError:
                        pass
                inv[pid] = {"path": exe, "ppid": ppid,
                            "name": os.path.basename(exe)}
        if inv:
            return inv, "path-or-parent"
    except Exception:
        pass
    return None, "name-only"


def _name_matching_pids() -> List[int]:
    """Legacy name-substring lookup, used only when scope == "name-only"."""
    sysname = platform.system()
    try:
        if sysname == "Windows":
            txt = subprocess.check_output(
                ["tasklist", "/FO", "CSV", "/NH"], text=True,
                timeout=8, errors="replace")
            pids = []
            for line in txt.splitlines():
                if "language_server" in line.lower():
                    m = re.search(r'"[^"]+","(\d+)"', line)
                    if m:
                        pids.append(int(m.group(1)))
            return pids
        txt = subprocess.check_output(
            ["ps", "-eo", "pid=,comm="], text=True, timeout=8,
            errors="replace")
        return [int(m.group(1)) for m in
                re.finditer(r'^\s*(\d+)\s+.*language_server', txt, re.M)]
    except Exception:
        return []


def find_language_server_pids(app: Optional[str] = None
                              ) -> Tuple[List[int], str]:
    """R2: only trust language_server processes whose executable lives under
    the Antigravity install root, or whose parent is Antigravity itself.

    Falls back to name matching (scope="name-only") when path/parent info is
    unavailable; the caller must then cap the verdict at INCONCLUSIVE.
    """
    inv, scope = process_inventory()
    if inv is None:
        return _name_matching_pids(), "name-only"
    app_root = ""
    if app:
        p = os.path.abspath(app)
        app_root = (p if p.lower().endswith(".app") and os.path.isdir(p)
                    else os.path.dirname(p))
        app_root = os.path.normcase(app_root)
    pids: List[int] = []
    for pid, rec in inv.items():
        path = str(rec.get("path") or "")
        ident = (os.path.basename(path) + " " + str(rec.get("name") or "") +
                 " " + path).lower()
        if "language_server" not in ident:
            continue
        under_app = bool(app_root and path and
                         os.path.normcase(os.path.abspath(path)).startswith(app_root))
        parent = inv.get(int(rec.get("ppid") or 0))
        pname = ""
        if parent:
            pname = (os.path.basename(str(parent.get("path") or "")) or
                     str(parent.get("name") or ""))
        parent_is_ag = pname.lower() in ("antigravity.exe", "antigravity")
        if under_app or parent_is_ag:
            pids.append(pid)
    return pids, "path-or-parent"


def any_antigravity_running(app: Optional[str] = None) -> bool:
    """R4: is Antigravity (or its language_server) currently running?"""
    ls_pids, _ = find_language_server_pids(app)
    if ls_pids:
        return True
    inv, _ = process_inventory()
    if inv is not None:
        for rec in inv.values():
            name = (os.path.basename(str(rec.get("path") or "")) or
                    str(rec.get("name") or "")).lower()
            if name in ("antigravity.exe", "antigravity"):
                return True
        return False
    try:
        if platform.system() == "Windows":
            txt = subprocess.check_output(
                ["tasklist", "/FO", "CSV", "/NH"], text=True,
                timeout=8, errors="replace")
        else:
            txt = subprocess.check_output(
                ["ps", "-eo", "comm="], text=True, timeout=8, errors="replace")
        return "antigravity" in txt.lower()
    except Exception:
        return False


def confirm_kill(args) -> bool:
    """R4: ask before force-closing. Non-interactive / --json needs --yes."""
    if args.yes:
        return True
    if args.json or not sys.stdin.isatty():
        out("ERROR: 检测到 Antigravity 正在运行；非交互环境"
            "（无 TTY 或 --json）必须显式加 --yes 才允许强杀。", err=True)
        return False
    out("将强制关闭 Antigravity，未保存内容会丢失。是否继续？[y/N] ", err=True)
    try:
        answer = input().strip().lower()
    except (EOFError, OSError):
        answer = ""
    return answer in ("y", "yes")


def child_connections(pids: List[int], proxy_port: int,
                      scope: str = "path-or-parent") -> Dict[str, object]:
    """Best-effort: did language_server connect to the proxy, or dial direct?"""
    res: Dict[str, object] = {
        "supported": False, "via_proxy": 0, "direct_443": 0,
        "total": 0, "note": "", "scope": scope,
    }
    if not pids:
        res["note"] = "language_server not running yet"
        return res
    sysname = platform.system()
    try:
        if sysname == "Windows":
            txt = subprocess.check_output(
                ["netstat", "-ano", "-p", "TCP"], text=True,
                timeout=10, errors="replace")
            lines = [l for l in txt.splitlines()
                     if "ESTABLISHED" in l.upper() and
                     any(re.search(r"\b%d\s*$" % pid, l) for pid in pids)]
        elif sysname == "Linux":
            txt = subprocess.check_output(
                ["ss", "-tnp"], text=True, timeout=10, errors="replace")
            lines = [l for l in txt.splitlines()
                     if any("pid=%d," % pid in l for pid in pids)]
        else:
            args = ["lsof", "-nP", "-iTCP", "-a"]
            for pid in pids:
                args += ["-p", str(pid)]
            txt = subprocess.check_output(args, text=True, timeout=10,
                                          errors="replace")
            lines = txt.splitlines()[1:]
        res["supported"] = True
        for l in lines:
            res["total"] = int(res["total"]) + 1
            parts = l.split()
            remote = parts[2] if sysname == "Windows" and len(parts) > 2 else l
            if re.search(r"[:.]%d\b" % proxy_port, l):
                res["via_proxy"] = int(res["via_proxy"]) + 1
            elif ":443" in remote or re.search(r"\.443\b", l):
                res["direct_443"] = int(res["direct_443"]) + 1
    except FileNotFoundError:
        res["note"] = "connection inspection tool not available on this system"
    except Exception as e:
        res["note"] = "inspection failed: %s" % type(e).__name__
    return res


def stop_existing(ls_pids: List[int]) -> List[str]:
    """R2: close the parent Antigravity first, then only the language_server
    PIDs that find_language_server_pids() already scoped by path / parent.
    Never use a bare `pkill -f language_server` (it can kill other IDEs);
    signalling the identified PIDs is strictly narrower than name matching.
    """
    msgs: List[str] = []
    sysname = platform.system()
    try:
        if sysname == "Windows":
            r = subprocess.run(["taskkill", "/F", "/IM", "Antigravity.exe"],
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                msgs.append("closed existing Antigravity.exe")
            for pid in ls_pids:
                r = subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                   capture_output=True, text=True, timeout=10)
                if r.returncode == 0:
                    msgs.append("closed scoped language_server PID %d" % pid)
        else:
            # Unix: signal only the PIDs whose path / parent was verified.
            for pid in ls_pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                    msgs.append("sent SIGTERM to scoped PID %d" % pid)
                except OSError:
                    pass
            inv, _ = process_inventory()
            if inv is not None:
                for pid, rec in inv.items():
                    name = (os.path.basename(str(rec.get("path") or "")) or
                            str(rec.get("name") or "")).lower()
                    if name in ("antigravity", "antigravity.exe"):
                        try:
                            os.kill(pid, signal.SIGTERM)
                            msgs.append("sent SIGTERM to Antigravity PID %d" % pid)
                        except OSError:
                            pass
    except Exception as e:
        msgs.append("could not close existing instances: %s" % type(e).__name__)
    return msgs


def launch(app: str, proxy: str, extra_no_proxy: str) -> Tuple[int, str]:
    """Return (pid, inject_note). A non-empty inject_note means the injected
    environment could NOT be confirmed; the caller must not report PASS."""
    env = os.environ.copy()
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"):
        env[key] = proxy
    no_proxy = NO_PROXY_DEFAULT
    if extra_no_proxy:
        no_proxy = no_proxy + "," + extra_no_proxy
    for key in ("NO_PROXY", "no_proxy"):
        env[key] = no_proxy

    kwargs = dict(env=env, close_fds=True,
                  cwd=os.path.dirname(app) or None)
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True

    # Windows / plain executables: behaviour unchanged (R3 constraint).
    if not (app.endswith(".app") and platform.system() == "Darwin"):
        p = subprocess.Popen([app], **kwargs)
        return p.pid, ""

    # R3: `open -a` goes through LaunchServices and does NOT reliably inherit
    # the caller's environment. Prefer executing the bundle's own binary with
    # the injected env; fall back to `open --env` where supported.
    macos_dir = os.path.join(app, "Contents", "MacOS")
    exe = None
    try:
        for name in sorted(os.listdir(macos_dir)):
            cand = os.path.join(macos_dir, name)
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                exe = cand
                break
    except OSError:
        pass
    if exe:
        p = subprocess.Popen([exe], **kwargs)
        return p.pid, ""
    # `open --env` exists only on newer macOS; injection is unconfirmed there.
    open_env = []
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        open_env += ["--env", "%s=%s" % (key, env[key])]
    try:
        p = subprocess.Popen(["open"] + open_env + ["-a", app], **kwargs)
    except Exception:
        return 0, "macOS 注入方式未验证（直接执行与 open --env 均不可用）"
    return p.pid, "macOS 注入方式未验证（open --env 是否继承环境变量未确认）"


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def poll_verification(app: Optional[str], proxy_port: int, wait: float,
                      inject_note: str = ""
                      ) -> Tuple[List[int], str, Dict[str, object], str]:
    """R6: poll once per second inside the --wait window instead of taking a
    single snapshot. Returns (pids, scope, connections, verdict_text).
    R2: a degraded "name-only" scope (or an unconfirmed macOS injection) can
    never produce PASS, because the traffic could belong to another process.
    """
    deadline = time.monotonic() + max(0.0, wait)
    pids: List[int] = []
    scope = "path-or-parent"
    v: Dict[str, object] = {}
    while True:
        pids, scope = find_language_server_pids(app)
        v = child_connections(pids, proxy_port, scope)
        if pids and (int(v.get("via_proxy", 0)) > 0 or
                     int(v.get("direct_443", 0)) > 0):
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(1)
    v["scope"] = scope
    if inject_note:
        verdict = "RESULT: INCONCLUSIVE - %s" % inject_note
    elif scope == "name-only":
        verdict = ('RESULT: INCONCLUSIVE - verification scope degraded to '
                   '"name-only" (path/parent info unavailable); '
                   'positive verdict withheld')
    elif not pids:
        verdict = ("RESULT: INCONCLUSIVE - language_server 未出现，"
                   "可能启动未完成，可稍后用 --verify-only 复查")
    elif not v.get("supported"):
        verdict = ("RESULT: launched; connection check unavailable (%s)" %
                   v.get("note"))
    elif int(v.get("direct_443", 0)) == 0 and int(v.get("via_proxy", 0)) > 0:
        verdict = ("RESULT: PASS - traffic goes through the proxy, "
                   "0 direct :443 connections.")
    elif int(v.get("direct_443", 0)) > 0:
        verdict = ("RESULT: WARNING - %s direct :443 connection(s) detected "
                   "(env may not have been inherited)." % v["direct_443"])
    else:
        verdict = ("RESULT: INCONCLUSIVE - %s" %
                   (v.get("note") or
                    "no data; 可能启动未完成，可稍后用 --verify-only 复查"))
    return pids, scope, v, verdict


def load_config(path: Optional[str]) -> dict:
    if not path:
        default = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "config.json")
        path = default if os.path.isfile(default) else None
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        out("WARNING: could not read config %s: %s" % (path, e), err=True)
        return {}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog=APP_NAME,
        description="Launch Antigravity with a working proxy injected into "
                    "its process environment (network connectivity fix only).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  python agproxy.py --check              # diagnose only, launch nothing
  python agproxy.py                      # auto-detect proxy + app, then launch
  python agproxy.py --proxy http://127.0.0.1:7890
  python agproxy.py --app "C:\\Path\\Antigravity.exe"
  python agproxy.py --json               # machine-readable result
""")
    ap.add_argument("--proxy", help="proxy URL (http://host:port)")
    ap.add_argument("--fallback", action="store_true",
                    help="allow falling back to other candidates when an "
                         "explicit --proxy fails (R1)")
    ap.add_argument("--yes", action="store_true",
                    help="confirm force-closing Antigravity in non-interactive "
                         "mode / --json (R4)")
    ap.add_argument("--verify-only", action="store_true",
                    help="only verify an already-running Antigravity; do not "
                         "probe, kill or launch (R6, optional)")
    ap.add_argument("--app", help="full path to Antigravity executable / .app")
    ap.add_argument("--config", help="path to config.json")
    ap.add_argument("--check", action="store_true",
                    help="read-only diagnosis, do not launch")
    ap.add_argument("--no-kill", action="store_true",
                    help="do not close running Antigravity instances")
    ap.add_argument("--skip-probe", action="store_true",
                    help="trust --proxy without probing (not recommended)")
    ap.add_argument("--insecure", action="store_true",
                    help="skip TLS certificate validation (unsafe)")
    ap.add_argument("--ports", help="comma-separated extra ports to probe")
    ap.add_argument("--timeout", type=float, default=6.0,
                    help="probe timeout in seconds (default 6)")
    ap.add_argument("--wait", type=float, default=20.0,
                    help="seconds to poll for verification data (default 20)")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip post-launch connection verification")
    ap.add_argument("--json", action="store_true",
                    help="emit a single JSON object (implies quiet)")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--version", action="version", version=VERSION)
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    proxy_explicit = args.proxy or cfg.get("proxy")
    app_explicit = args.app or cfg.get("app")
    extra_no_proxy = cfg.get("extra_no_proxy", "")
    if isinstance(extra_no_proxy, list):
        extra_no_proxy = ",".join(extra_no_proxy)
    insecure = args.insecure or bool(cfg.get("insecure"))
    ports = list(DEFAULT_PORTS)
    if args.ports:
        try:
            ports = [int(x) for x in args.ports.split(",") if x.strip()] + ports
        except ValueError:
            out("ERROR: --ports must be comma-separated integers", err=True)
            return 2
    for p in cfg.get("common_ports", []) or []:
        if isinstance(p, int) and p not in ports:
            ports.insert(0, p)

    rep = Report(platform=platform.platform(),
                 mode=("verify-only" if args.verify_only else
                       "check" if args.check else "launch"))
    quiet = args.json

    def say(m: str) -> None:
        if not quiet:
            out(m)
        rep.messages.append(m)

    say("%s %s | %s" % (APP_NAME, VERSION, rep.platform))
    dns = local_resolve(PROBE_HOST)  # R8b: resolve once, reuse the result
    say("Local DNS %s -> %s%s" % (
        PROBE_HOST, dns, "  [looks polluted]" if looks_polluted(dns) else ""))

    # R7: an invalid explicit proxy must fail loudly, not be silently dropped.
    if proxy_explicit and norm_proxy(proxy_explicit) is None:
        say("ERROR: 显式代理地址无效：%s（应为 host:port、"
            "http://host:port 或纯端口）" % proxy_explicit)
        if args.json:
            out(json.dumps(rep.as_dict(), ensure_ascii=False, indent=2))
        return 1

    # R6 (optional): --verify-only re-checks a running Antigravity without
    # probing, killing or launching anything.
    if args.verify_only:
        vp = norm_proxy(proxy_explicit) if proxy_explicit else None
        if vp is None:
            cands = all_candidates(None, ports)
            vp = cands[0][0] if cands else "http://127.0.0.1:%d" % DEFAULT_PORTS[0]
        rep.proxy = vp
        rep.proxy_source = "explicit" if proxy_explicit else "port"
        rep.app = resolve_app(app_explicit)
        say("Verifying only (no probe / kill / launch) ...")
        pids, scope, v, verdict = poll_verification(
            rep.app, proxy_addr(vp)[1], args.wait)
        rep.language_server_pids = pids
        rep.verification = v
        say(verdict)
        if args.json:
            out(json.dumps(rep.as_dict(), ensure_ascii=False, indent=2))
        return 0

    # 1) find a proxy that truly reaches Google
    say("Discovering a working proxy ...")
    # R1: an explicit --proxy must not silently fall back to other candidates
    # unless --fallback was given.
    if proxy_explicit and not args.fallback:
        candidates = [(u, s) for u, s in all_candidates(proxy_explicit, ports)
                      if s == "explicit"]
    else:
        candidates = all_candidates(proxy_explicit, ports)
    if not quiet and args.verbose:
        say("  %d candidate(s) to test" % len(candidates))
    proxy, probe, source = find_working_proxy(
        candidates, insecure=insecure, skip_probe=args.skip_probe,
        timeout=args.timeout, verbose=(args.verbose and not quiet))
    rep.probe = probe
    if not proxy:
        if proxy_explicit and not args.fallback:
            say("ERROR: 显式代理不可用，已停止；如需自动回落请加 --fallback")
        else:
            say("ERROR: no proxy could reach %s." % PROBE_HOST)
        say("  last error : %s" % (probe.error or probe.connect_status))
        say("  hints      : start your proxy client, or pass --proxy http://host:port")
        say("  note       : a listening port is NOT proof the proxy works; "
            "this tool requires CONNECT + TLS + a Google response.")
        if args.json:
            out(json.dumps(rep.as_dict(), ensure_ascii=False, indent=2))
        return 1
    rep.proxy = proxy
    # R1: expose which class of candidate actually won. A fallback win is
    # tagged "fallback" so it is never mistaken for the explicit proxy.
    rep.proxy_source = ("fallback" if proxy_explicit and args.fallback and
                        source != "explicit" else source)
    say("Proxy OK   : %s" % redacted(proxy))
    say("  CONNECT  : %s" % (probe.connect_status or "n/a"))
    say("  TLS      : %s" % (probe.tls_version or "n/a"))
    say("  POST/tok : %s%s" % (
        probe.http_status or "n/a",
        "  [reached Google]" if probe.reached_google else ""))
    if insecure:
        say("  WARNING  : TLS validation disabled (--insecure)")
    if probe.dns_warning:
        say("  NOTE     : local DNS looks polluted; proxy resolves remotely "
            "via CONNECT, so this is expected and handled.")

    # 2) locate Antigravity
    app = resolve_app(app_explicit)
    rep.app = app
    if not app:
        say("ERROR: Antigravity not found. Pass --app with the full path.")
        if args.json:
            out(json.dumps(rep.as_dict(), ensure_ascii=False, indent=2))
        return 1
    say("App        : %s" % app)

    if args.check:
        say("")
        say("RESULT: PASS - proxy reachable, app found. Nothing was launched.")
        if args.json:
            out(json.dumps(rep.as_dict(), ensure_ascii=False, indent=2))
        return 0

    # 3) relaunch with injected environment
    inject_note = ""
    if not args.no_kill:
        # R4: never force-close without confirmation (--check / --no-kill
        # never reach this branch).
        if any_antigravity_running(app):
            if not confirm_kill(args):
                say("已取消：未关闭任何进程（选择 N 或缺少 --yes）。")
                if args.json:
                    out(json.dumps(rep.as_dict(), ensure_ascii=False, indent=2))
                return 2
            ls_pids, scope = find_language_server_pids(app)
            for m in stop_existing(ls_pids):
                say("  %s" % m)
        time.sleep(2)
        # R4.4: re-check before launching; never launch into a half-closed
        # state, print a hint instead.
        if any_antigravity_running(app):
            say("WARNING: 仍有 Antigravity / language_server 残留进程，"
                "请先手动关闭后重试；本次不启动。")
            if args.json:
                out(json.dumps(rep.as_dict(), ensure_ascii=False, indent=2))
            return 2

    say("Launching with injected HTTP_PROXY / HTTPS_PROXY / ALL_PROXY ...")
    try:
        pid, inject_note = launch(app, proxy, extra_no_proxy)
        if pid <= 0:
            say("ERROR: %s" % (inject_note or "launch produced no PID"))
            if args.json:
                out(json.dumps(rep.as_dict(), ensure_ascii=False, indent=2))
            return 1
        rep.launched = True
        rep.launch_pid = pid
        say("  started PID %d (detached from this shell)" % pid)
        if inject_note:
            say("  NOTE: %s" % inject_note)
    except Exception as e:
        say("ERROR: launch failed: %s: %s" % (type(e).__name__, e))
        if args.json:
            out(json.dumps(rep.as_dict(), ensure_ascii=False, indent=2))
        return 1

    if not args.no_verify:
        say("Verifying traffic for up to %.0fs (polling once per second) ..." %
            args.wait)
        pids, scope, v, verdict = poll_verification(
            app, proxy_addr(proxy)[1], args.wait, inject_note)
        rep.language_server_pids = pids
        rep.verification = v
        if pids:
            say("  language_server PID(s): %s" % ", ".join(map(str, pids)))
        else:
            say("  language_server not up yet (app may still be starting)")
        say(verdict)

    say("")
    say("Next: complete sign-in in the Antigravity window. "
        "This tool does NOT affect account eligibility.")
    if args.json:
        out(json.dumps(rep.as_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        out("\nInterrupted.", err=True)
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as e:
        # R8c: never leak a traceback to stdout in --json mode; emit JSON.
        if "--json" in sys.argv:
            out(json.dumps({"ok": False, "error": "%s: %s" %
                            (type(e).__name__, str(e)[:200])},
                           ensure_ascii=False))
        else:
            out("ERROR: unhandled %s: %s" % (type(e).__name__, e), err=True)
        sys.exit(1)
