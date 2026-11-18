# agproxy

给 **Google Antigravity** 启动时注入可用的 HTTP 代理环境变量，让登录流程能真正连上 Google 的 OAuth 端点。

零第三方依赖，仅用 Python 3.8+ 标准库，支持 Windows / macOS / Linux。

> **平台状态：** Windows 已实测；macOS / Linux 已实现但未在真机验证，欢迎提交测试反馈。

[English README](README.md)

---

## 解决什么问题

在无法稳定直连 Google 的网络里，Antigravity 登录可能报：

```
There was an unexpected issue setting up your account.

Post "https://oauth2.googleapis.com/token":
dial tcp 192.179.26.95:443: connectex: A connection attempt failed ...
```

浏览器授权那一步其实是成功的（回调已经回来了），失败的是**最后一步换取 token**。这一步由 Antigravity 里 Go 写的 `language_server` 完成，而 Go 的 `net/http` **只读 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` 环境变量**，读不到 Windows 注册表的系统代理设置、也读不到 macOS 网络偏好。所以哪怕你的系统代理是开着的，token 请求仍然直连，最后超时。

`agproxy` 只修这一件事：

1. 自动找出一个**真正可用**的代理（不只是"端口在监听"）
2. 定位 Antigravity 安装位置
3. 带着代理环境变量重启 Antigravity（只影响它的进程树）
4. 启动后验证 `language_server` 的流量确实走了代理

### 它不做什么

- ❌ 不修改任何 Antigravity 二进制或配置文件
- ❌ 不装 TUN/TAP 驱动、不建虚拟网卡、不改路由表 / DNS / 系统代理设置
- ❌ 不提供代理、节点、订阅，也不自带任何出网能力
- ❌ 不绕过账号资格、不改 Google 账号关联地区、不做年龄验证、不破解任何服务端校验
- ❌ 不包装 `agy` CLI、不自动切换账号、不把登录凭据交给第三方应用
- ❌ 不打包、不分发任何第三方 patcher

本质上它等价于你在终端里手动输入 `HTTPS_PROXY=... /path/to/Antigravity`。额外价值只是：自动发现、真实可达性预检、启动后验证。

> **如果账号本身没有资格**（关联国家不对、未满 18 岁、Workspace 账号等），这个工具帮不了你，请按[官方 FAQ](https://antigravity.google/docs/faq) 走账号侧的正当途径。

---

## 快速开始

**前提**：你本机已经有一个正常工作的 HTTP 代理客户端（Clash、Mihomo、sing-box、v2rayN、公司代理等）。本项目不含代理。

### 1. 只读诊断（不改任何东西）

```bash
python agproxy.py --check
```

正常输出：

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

那个 `400 Bad Request` 是**好消息**：它是 Google 对我们故意构造的非法探测请求返回的正常错误，证明流量真的到了 Google，而不是被黑洞吞掉或被劫持。

### 2. 启动

```bash
python agproxy.py
```

Windows 也可直接双击 **`run-windows.bat`**；macOS / Linux：

```bash
chmod +x run-unix.sh && ./run-unix.sh
```

然后在 Antigravity 窗口里完成登录。

> ⚠️ 必须通过本工具启动，**不要**用开始菜单 / Dock / 原来的快捷方式。环境变量只在进程创建时继承，普通启动不会带上代理。

---

## 代理是怎么选出来的

按下面顺序找候选，并逐个**主动探测**：

| 顺序 | 来源 |
|---|---|
| 1 | `--proxy` / `config.json` |
| 2 | `AGPROXY_PROXY`、`HTTPS_PROXY`、`HTTP_PROXY`、`ALL_PROXY` 环境变量 |
| 3 | Windows 注册表系统代理 / macOS `scutil --proxy` |
| 4 | 常见本地端口（7890、7897、10809、1080、8888、65532 …） |

只有全部通过才算可用：

1. 能 TCP 连上代理
2. `CONNECT oauth2.googleapis.com:443` 返回 `200 Connection established`
3. TLS 握手成功，**校验证书**且 SNI 正确
4. `POST /token` 返回 Google 形状的错误响应

这样设计是有原因的：端口在监听但返回 502、隧道被黑洞、或者用不受信任证书做 MITM，都不算可用，硬用只会复现原来的报错。

> 如果你的代理用私有 CA 做 TLS 解密，请把该 CA 装进系统信任库。`--insecure` 虽然存在但强烈不建议——它会关闭证书校验，等于允许恶意代理读取你的 OAuth 流量。

---

## 命令行参数

```
  --proxy URL          代理地址，如 http://127.0.0.1:7890；也支持纯端口 7890
  --fallback           显式代理失败时允许继续回落到其它候选
  --yes                非交互 / --json 模式下确认强制关闭 Antigravity
  --verify-only        只验证已在运行的 Antigravity，不探测/不关闭/不启动
  --app PATH           Antigravity 可执行文件 / .app 的完整路径
  --config PATH        配置文件（默认读脚本同目录的 config.json）
  --check              只读诊断，不启动
  --no-kill            不关闭已在运行的 Antigravity
  --skip-probe         跳过预检直接信任 --proxy（不推荐）
  --insecure           跳过 TLS 证书校验（不安全）
  --ports 1,2,3        优先探测的额外端口
  --timeout SEC        探测超时，默认 6 秒
  --wait SEC           启动后轮询验证多少秒，默认 20 秒
  --no-verify          跳过启动后的连接检查
  --json               输出单个 JSON（供脚本 / CI / Agent 使用）
  -v, --verbose        显示每个候选的探测过程
```

把 `config.example.json` 复制成 `config.json` 即可固化配置；命令行参数优先级更高，`config.json` 已被 git 忽略。

---

## 怎么确认生效了

脚本启动后会检查 `language_server` 的连接：

```
RESULT: PASS - traffic goes through the proxy, 0 direct :443 connections.
```

如果看到 `WARNING - N direct :443 connection(s) detected`，说明环境变量没被继承，通常是 App 被别的入口启动了，或者旧实例没关掉（别加 `--no-kill`）。

也可以直接看 Antigravity 自己的日志：

| 系统 | 路径 |
|---|---|
| Windows | `%APPDATA%\Antigravity\logs\language_server.log` |
| macOS | `~/Library/Application Support/Antigravity/logs/language_server.log` |
| Linux | `~/.config/Antigravity/logs/language_server.log` |

启动后不再出现 `dial tcp ... i/o timeout` 就是通的。

---

## 常见问题

详见 [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) 与 [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md)。

| 现象 | 处理 |
|---|---|
| `no proxy could reach ...` | 先启动代理客户端，或用 `--proxy` 显式指定 |
| `Antigravity not found` | 用 `--app "C:\...\Antigravity.exe"` 指定 |
| `TLS certificate validation failed` | 安装代理的 CA 证书；别用 `--insecure` |
| 登录进去了但提示"没有资格" | 账号/地区问题，不是网络问题，看官方 FAQ |
| 还是超时 | 确认代理节点能访问 Google；加 `--verbose` 看细节 |

---

## ⚠️ 服务条款提示（务必阅读）

本项目**与 Google 无任何关联，也未经其认可或支持**。

[Antigravity 官方 FAQ](https://antigravity.google/docs/faq) 明确写道：

> "Using third-party software, tools, or services to access Antigravity is a
> violation of our Terms of Service and severely degrades the experience for
> legitimate product users. Such actions can result in suspension or
> termination of your account."

以及关于 CLI：

> "Wrapping agy inside third-party coding agents or applications, or
> automating account rotation, is not supported under consumer plans."

上述条款针对的是「把 Antigravity 登录给第三方编码代理用」「包装 `agy`」「自动轮换账号」这类行为。**`agproxy` 三者都不做**——它只设置标准代理环境变量，与 `git` / `pip` / `curl` / `npm` 用的是同一套机制，不碰账号、不碰凭据、不碰 Antigravity 安装目录。

但我们无法判断**你的具体用法**是否符合服务条款、所在地法律或单位规定。特别是 FAQ 说明 Antigravity 仅在公布的国家和地区开放，并建议通过官方渠道更正账号关联地区。是否借代理绕开地区限制，是你自己的决定与风险。

**请使用合法的网络通道、使用有资格的账号，并自行阅读服务条款。** 作者不对账号停权、数据丢失或任何其他后果负责。

---

## 平台支持

| 平台 | 状态 |
|---|---|
| Windows 11 + Antigravity 2.32.2 | ✅ 已端到端实测（自动发现 / 预检 / 启动 / 验证） |
| macOS | ⚠️ 已实现分支，**未在真机验证** |
| Linux | ⚠️ 已实现分支，**未在真机验证** |

macOS / Linux 的可执行文件路径可能与我们的猜测不同，自动找不到时用 `--app` 指定。欢迎提交 issue / PR 补充实测结果。

## 贡献

特别欢迎：确认过的 macOS / Linux 安装路径、更多代理客户端默认端口、翻译。

**请勿**提交加入账号轮换、资格绕过、凭据处理或分发第三方 patcher 的 PR。

## 许可证

[MIT](LICENSE)
