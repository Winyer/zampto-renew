# Zampto Auto Renew

自动续期 Zampto Free-4 服务器（每小时一次）。

## GitHub Actions 使用

### 1. 配置 Secrets

在仓库 Settings → Secrets and variables → Actions 中添加：

| Secret | 值 | 说明 |
|--------|------|------|
| `ZAMPTO_EMAIL` | 你的登录邮箱 | 如 `x@end.tw` |
| `ZAMPTO_PASSWORD` | 你的登录密码 | |
| `SERVER_IDS` | 服务器 ID（逗号分隔） | 如 `10852` |

### 2. 手动触发

Actions 页面 → Zampto Auto Renew → Run workflow

### 3. 自动运行

默认每小时整点过 5 分钟自动运行（UTC 时间）。

## 本地运行

```bash
pip install playwright
playwright install chromium

# 单次执行
python zampto_auto_renew.py once

# 持续运行（每小时）
python zampto_auto_renew.py loop
```

### 本地使用 CentBrowser（Windows）

设置环境变量 `BROWSER_MODE=centbrowser`，或将 CentBrowser 放在默认路径：
`C:\Program Files\CentBrowser\Application\chrome.exe`

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `BROWSER_MODE` | 自动检测 | `playwright` 或 `centbrowser` |
| `CENTBROWSER_PATH` | `C:\Program Files\CentBrowser\Application\chrome.exe` | CentBrowser 路径 |
| `CDP_PORT` | `9222` | CDP 端口 |
| `ZAMPTO_EMAIL` | - | 登录邮箱 |
| `ZAMPTO_PASSWORD` | - | 登录密码 |
| `SERVER_IDS` | `10852` | 服务器 ID 列表（逗号分隔） |
