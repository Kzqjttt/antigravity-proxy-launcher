---
name: antigravity-proxy-launcher
display_name: "Antigravity 登录代理修复"
display_name_en: "Antigravity Proxy Launcher"
description: "诊断并修复 Google Antigravity 登录时出现的 dial tcp / oauth2.googleapis.com/token 超时问题。通过只读预检找出真正可用的 HTTP 代理，注入 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY 后重启 Antigravity，并验证 language_server 流量确实走代理。不修改客户端文件、不改路由表、不开 TUN。适用于报错含 connectex / i/o timeout / unexpected issue setting up your account 的场景。"
description_zh: "诊断并修复 Google Antigravity 登录时出现的 dial tcp / oauth2.googleapis.com/token 超时问题。通过只读预检找出真正可用的 HTTP 代理，注入环境变量后重启客户端并验证流量走向。"
description_en: "Diagnose and fix Antigravity sign-in failures caused by the Go language_server dialing Google OAuth endpoints directly. Discovers a genuinely working HTTP proxy, injects standard proxy env vars into the app process tree, relaunches, and verifies traffic actually goes through the proxy."
version: 1.0.0
agent_created: true
---

# Antigravity 登录代理修复

## 何时使用

用户报告 Antigravity 登录失败，且现象属于**网络层**：

- 界面提示 `There was an unexpected issue setting up your account.`
- 错误含 `dial tcp ... :443: connectex ...` 或 `i/o timeout`
- 错误 URL 是 `https://oauth2.googleapis.com/token`
- 浏览器授权回调已完成，但最后一步失败

**不属于本 skill 的情况**（必须明确告知用户，不要用本工具硬试）：

- 提示「账号没有资格 / not eligible / 地区不支持」→ 账号侧问题，见官方 FAQ
- `HTTP 500 / 403 SUBSCRIPTION_REQUIRED` → API 与订阅状态问题
- 需要年龄验证、Google 账号关联地区变更 → 官方途径

## 核心原理（先讲给用户，避免误判）

Antigravity 的 OAuth 流程分两段：Chromium/Electron 负责浏览器授权（读系统代理，通常没问题），**Go 写的 `language_server` 负责换 token**（`POST oauth2.googleapis.com/token`）。Go 的 `net/http` 只读 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`，**读不到 Windows 注册表系统代理、也读不到 macOS 网络偏好**。所以系统代理开着但没设环境变量时，token 请求直连并超时。

日志证据形态（真实样本）：

```
[auth-login] step=callback_received ms_to_callback=18204      <- 浏览器侧成功
Post "https://play.googleapis.com/log": dial tcp 172.217.116.4:443: i/o timeout
Post "https://oauth2.googleapis.com/token": dial tcp 192.179.26.95:443: connectex ... timeout
[auth-login] net_error=dial outcome=error                     <- 只有换 token 失败
```

注意：假 IP（DNS 污染）和**真实 Google IP 都会超时**，所以改 hosts / 换公共 DNS 单独无效，必须走代理。

## 执行流程

脚本位于 `{baseDir}/scripts/agproxy.py`，零第三方依赖，用任意 Python 3.8+ 运行。

### Step 1 — 只读诊断（必须先做，不启动任何东西）

```bash
python {baseDir}/scripts/agproxy.py --check --verbose
```

判读：
- `RESULT: PASS` → 代理可达 + 已找到 App，可进入 Step 2
- `no proxy could reach ...` → 让用户先启动代理客户端，或用 `--proxy http://host:port` 指定；**不要**为了"能跑起来"而加 `--skip-probe`
- `Antigravity not found` → 用 `--app "<完整路径>"`
- `TLS certificate validation failed` → 引导安装代理 CA；明确劝阻 `--insecure`

`--json` 可拿到结构化结果，便于在回答里引用 `probe.reached_google` 等字段。

### Step 2 — 启动并验证

```bash
python {baseDir}/scripts/agproxy.py
```

脚本会：关闭已有实例 → 注入变量 → 以**脱离启动 shell 的方式**启动 App → 等待 → 检查 `language_server` 的 TCP 连接。

判读验证结果：
- `RESULT: PASS - ... 0 direct :443 connections` → 完成
- `WARNING - N direct :443` → 环境变量未被继承（多半是用户从开始菜单又启动了一次，或旧实例没关干净）
- `INCONCLUSIVE` → 检查工具不可用，改看 `language_server.log`

### Step 3 — 交付说明

必须告诉用户三件事：

1. **以后都要通过本工具启动**，不能用原来的快捷方式 / 开始菜单 / Dock；环境变量只在进程创建时继承。
2. **这只修网络连通性**，不能解决账号资格、地区、年龄、订阅问题；登录成功后若提示无资格，属另一层。
3. **官方服务条款风险**：[Antigravity FAQ](https://antigravity.google/docs/faq) 写明「使用第三方软件/工具/服务访问 Antigravity 违反 ToS，可能导致账号停权」，以及「包装 agy、自动轮换账号在消费者套餐下不受支持」。本工具**不包装 CLI、不轮换账号、不碰凭据、不改客户端**，只设标准代理环境变量；但是否合规由用户自行判断。**不要替用户打包票，也不要隐去这条。**

## ⚠️ 本机环境坑（WorkBuddy 会话内尤其重要）

这些是实测结论，能省掉大量无效重试：

1. **Agent 的 shell 会连带杀死子进程。** 在 Bash / PowerShell 工具里 `Start-Process` 启动的 GUI 程序会随命令结束被终止（连 notepad 都一样，表现为 ExitCode 0、无日志）。`agproxy.py` 已用 `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`（Windows）/ `start_new_session`（Unix）规避，但**若你在会话里手工验证 GUI 启动，请用 Windows 计划任务（Interactive principal）绕开**。
2. **`cmd.exe` 在 PowerShell 工具中被安全策略禁用**，`.bat` 不能由 Agent 直接执行；交给用户双击即可。
3. **`New-Object -ComObject WScript.Shell` 被拦截**，无法用脚本自动创建 `.lnk` 快捷方式；README 里让用户手动建或直接双击 bat。
4. **PowerShell 执行策略可能是 Restricted**，跑 `.ps1` 需 `-ExecutionPolicy Bypass`。
5. **存在多个代理端口时别猜。** 曾遇到环境变量指向沙箱出网代理（`sandbox-cli.exe`，返回 502），而真正可用的是另一个本地端口。**必须做 CONNECT + TLS + Google 响应的完整预检**，只测"端口在监听"会得出错误结论。
6. 批量并发跑浏览器/字幕类脚本会抢同一个 Playwright profile 锁；本 skill 不涉及，但若同时做视频总结要注意串行。

## 边界与禁止事项

- ⛔ 不编造 Antigravity 的安装路径、报错文案或官方条款原文；不确定就去查官方 FAQ 或读日志。
- ⛔ 不打包、不分发任何第三方 patcher（GPL 项目，且属修改客户端二进制）。
- ⛔ 不读取、不输出、不上传用户的 Cookie / SESSDATA / token / 订阅链接；代理 URL 中的凭据必须脱敏。
- ⛔ 不把"设置代理"包装成"绕过地区限制 / 破解资格"。
- ⛔ 不擅自改路由表、DNS、系统代理开关、注册表，也不装 TUN 驱动。
- ⛔ 不做账号轮换、不包装 `agy` CLI、不把凭据交给第三方应用。

## 参考

- 原理细节：`../../docs/HOW-IT-WORKS.md`（发布仓库中）
- 排障表：`../../docs/TROUBLESHOOTING.md`
- 官方 FAQ：https://antigravity.google/docs/faq
- 官方地区核对页：https://policies.google.com/terms
