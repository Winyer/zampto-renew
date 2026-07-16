#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Zampto 自动续期脚本
====================
自动登录 Zampto 并续期 Free-4 服务器，每小时一次。

支持两种浏览器模式（通过环境变量 BROWSER_MODE 切换）：
  - centbrowser (默认): 通过 CDP 连接本地 CentBrowser，适合 Windows 本地运行
  - playwright: 使用 Playwright 自带 Chromium，适合 GitHub Actions / Linux / Docker

用法:
  python zampto_auto_renew.py              # 单次执行
  python zampto_auto_renew.py loop         # 持续运行（每小时）

环境变量:
  BROWSER_MODE        centbrowser | playwright (默认: 自动检测)
  CENTBROWSER_PATH    CentBrowser 可执行文件路径 (仅 centbrowser 模式)
  CDP_PORT            CDP 端口 (默认: 9222)
  ZAMPTO_EMAIL        登录邮箱 (默认从配置读取)
  ZAMPTO_PASSWORD     登录密码 (默认从配置读取)
  SERVER_IDS          逗号分隔的服务器 ID 列表

依赖: pip install playwright
"""

import os
import sys
import json
import time
import random
import logging
import traceback
import subprocess
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ===================== 配置 =====================

EMAIL = os.environ.get("ZAMPTO_EMAIL", "x@end.tw")
PASSWORD = os.environ.get("ZAMPTO_PASSWORD", "RS0WJV73..")
SERVER_IDS_STR = os.environ.get("SERVER_IDS", "10852")
KNOWN_SERVER_IDS = [s.strip() for s in SERVER_IDS_STR.split(",") if s.strip()]

BASE_URL = "https://dash.zampto.net"
LOGIN_URL = f"{BASE_URL}/auth/login"
FREE_TIER = "Free-4"

# 浏览器模式
BROWSER_MODE = os.environ.get("BROWSER_MODE", "")
if not BROWSER_MODE:
    # 自动检测：Windows 有 CentBrowser 则用 centbrowser，否则用 playwright
    BROWSER_MODE = "centbrowser" if sys.platform == "win32" and os.path.exists(
        os.environ.get("CENTBROWSER_PATH", r"C:\Program Files\CentBrowser\Application\chrome.exe")
    ) else "playwright"

CENTBROWSER_PATH = os.environ.get("CENTBROWSER_PATH", r"C:\Program Files\CentBrowser\Application\chrome.exe")
CDP_PORT = int(os.environ.get("CDP_PORT", "9222"))

# 路径
SCRIPT_DIR = Path(__file__).parent
LOG_DIR = SCRIPT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "zampto_renew.log"
STATUS_FILE = SCRIPT_DIR / "renew_status.json"
SCREENSHOT_DIR = SCRIPT_DIR / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)
USER_DATA_DIR = SCRIPT_DIR / "browser_data"

# ===================== 日志 =====================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ===================== 状态管理 =====================

def load_status():
    if STATUS_FILE.exists():
        try:
            with open(STATUS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_status(status):
    try:
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(status, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"保存状态失败: {e}")

def record_renewal(success, message=""):
    status = load_status()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    record = {"time": now, "success": success, "message": message}
    if "history" not in status:
        status["history"] = []
    status["history"].append(record)
    status["history"] = status["history"][-100:]
    status["last_run"] = now
    status["last_success"] = success
    save_status(status)

# ===================== 浏览器管理 =====================

def launch_centbrowser():
    """启动 CentBrowser + CDP 连接，返回 (pw, browser, context, page, proc)"""
    logger.info(f"[CentBrowser] 启动浏览器 (CDP 端口: {CDP_PORT})...")

    cmd = [
        CENTBROWSER_PATH,
        f"--remote-debugging-port={CDP_PORT}",
        "--no-first-run",
        "--disable-extensions",
        "--disable-component-update",
        "--no-default-browser-check",
        f"--user-data-dir={USER_DATA_DIR}",
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0,
    )

    for i in range(30):
        try:
            urllib.request.urlopen(f"http://localhost:{CDP_PORT}/json/version", timeout=2)
            logger.info(f"[CentBrowser] 已就绪 (PID: {proc.pid})")
            break
        except Exception:
            time.sleep(1)
    else:
        proc.kill()
        raise RuntimeError("CentBrowser 启动超时")

    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
    logger.info("[CentBrowser] CDP 连接成功!")

    contexts = browser.contexts
    context = contexts[0] if contexts else browser.new_context()
    page = context.new_page()
    page.wait_for_timeout(2000)

    return pw, browser, context, page, proc


def launch_playwright():
    """使用 Playwright 自带 Chromium，返回 (pw, browser, context, page, None)"""
    headless = os.environ.get("HEADLESS", "true").lower() != "false"
    logger.info(f"[Playwright] 启动 Chromium (headless={headless})...")

    pw = sync_playwright().start()
    use_proxy = os.environ.get("USE_PROXY", "").lower() == "true"
    # 优先使用系统 Chrome（指纹更真实），fallback 到 Playwright Chromium
    chrome_paths = [
        "/usr/bin/google-chrome-stable",
        "/usr/bin/google-chrome",
        "/opt/google/chrome/chrome",
    ]
    exe_path = None
    for p in chrome_paths:
        if os.path.exists(p):
            exe_path = p
            break

    launch_args = {
        "headless": headless,
        "args": [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--no-first-run",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
        ],
    }
    if exe_path:
        launch_args["executable_path"] = exe_path
        logger.info(f"[Playwright] 使用系统 Chrome: {exe_path}")
    if use_proxy:
        launch_args["proxy"] = {"server": "socks5://127.0.0.1:10808"}
        logger.info("[Playwright] 使用代理: socks5://127.0.0.1:10808")

    browser = pw.chromium.launch(**launch_args)
    context = browser.new_context(
        viewport={"width": 1280, "height": 720},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
        locale="en-US",
        timezone_id="America/New_York",
    )
    page = context.new_page()

    # 注入 JS 隐藏自动化特征
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [{name:'Chrome PDF Plugin'},{name:'Chrome PDF Viewer'},{name:'Native Client'}] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
        Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
        Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });
        window.chrome = { runtime: {} };
        window.Notification = window.Notification || {};
        const origQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (p) => (p.name === 'notifications' ? Promise.resolve({state: 'default'}) : origQuery(p));
        // Fake WebGL vendor
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(p) {
            if (p === 37445) return 'Intel Inc.';
            if (p === 37446) return 'Intel Iris Xe Graphics';
            return getParameter.call(this, p);
        };
        // Fake canvas
        const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function(type) {
            if (this.width > 16 && this.height > 16) {
                const ctx = this.getContext('2d');
                if (ctx) ctx.fillText = () => {};
            }
            return origToDataURL.call(this, type);
        };
    """)

    logger.info("[Playwright] Chromium 启动成功!")

    return pw, browser, context, page, None


def stop_browser(pw, browser, context, page, proc):
    """关闭浏览器"""
    try:
        page.close()
    except Exception:
        pass
    try:
        context.close()
    except Exception:
        pass
    try:
        browser.close()
    except Exception:
        pass
    try:
        pw.stop()
    except Exception:
        pass
    if proc:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        logger.info("[CentBrowser] 已停止")

# ===================== 工具函数 =====================

def take_screenshot(page, name):
    try:
        path = SCREENSHOT_DIR / f"{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        page.screenshot(path=str(path))
        logger.info(f"截图已保存: {path}")
        return str(path)
    except Exception as e:
        logger.error(f"截图失败: {e}")
        return None


def remove_adblocker_overlay(page):
    """移除 Ad Blocker 弹窗"""
    for _ in range(3):
        try:
            page.evaluate(
                """
                () => {
                    for (var i = 0; i < 3; i++) {
                        var overlay = document.getElementById('adblocker-overlay');
                        if (overlay) overlay.remove();
                        document.querySelectorAll('[role="alertdialog"]').forEach(el => el.remove());
                    }
                }
                """
            )
        except Exception:
            pass


def js_fill_input(page, css_selector, text):
    """使用 JS nativeInputValueSetter 填写表单输入框"""
    safe_text = text.replace("\\", "\\\\").replace("'", "\\'")
    page.evaluate(
        f"""
        (() => {{
            var el = document.querySelector('{css_selector}');
            if (!el) return false;
            try {{
                var nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, "value"
                ).set;
                if (nativeInputValueSetter) {{
                    nativeInputValueSetter.call(el, '{safe_text}');
                }} else {{
                    el.value = '{safe_text}';
                }}
            }} catch(e) {{
                el.value = '{safe_text}';
            }}
            el.dispatchEvent(new Event('input', {{ bubbles: true }}));
            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return true;
        }})()
        """
    )


def wait_for_turnstile(page, timeout=120):
    """等待 Cloudflare Turnstile 验证"""
    logger.info("检查 Turnstile 验证...")
    has_turnstile = page.evaluate(
        """
        () => {
            return document.querySelector('input[name="cf-turnstile-response"]') !== null ||
                   document.querySelector('iframe[src*="challenges.cloudflare.com"]') !== null ||
                   document.querySelector('iframe[src*="turnstile"]') !== null;
        }
        """
    )
    if not has_turnstile:
        logger.info("未检测到 Turnstile 验证")
        return True

    logger.info("检测到 Turnstile，等待自动通过...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            solved = page.evaluate(
                """
                () => {
                    var input = document.querySelector('input[name="cf-turnstile-response"]');
                    return input && input.value && input.value.length > 20;
                }
                """
            )
            if solved:
                logger.info(f"Turnstile 验证通过 ({time.time() - start:.1f}s)")
                return True
        except Exception:
            pass
        time.sleep(2)

    logger.warning("Turnstile 验证超时")
    return False

# ===================== 登录 =====================

def login(page):
    """登录 Zampto"""
    logger.info(f"正在打开登录页: {LOGIN_URL}")
    for attempt in range(5):
        try:
            page.goto(LOGIN_URL, wait_until="load", timeout=60000)
            break
        except Exception as e:
            logger.warning(f"导航登录页失败 (尝试 {attempt+1}/5): {e}")
            page.wait_for_timeout(5000)
    else:
        logger.error("导航登录页失败（已重试 5 次）")
        take_screenshot(page, "login_nav_failed")
        return False

    page.wait_for_timeout(5000)

    if "auth/login" not in page.url:
        logger.info("已经在登录后页面")
        return True

    remove_adblocker_overlay(page)
    page.wait_for_timeout(1000)

    # 检查 OTP 页面
    try:
        back_link = page.locator('text=Back to password login').first
        if back_link.is_visible(timeout=3000):
            logger.info("检测到 OTP 验证页面，点击返回密码登录...")
            back_link.click()
            page.wait_for_timeout(3000)
            remove_adblocker_overlay(page)
    except Exception:
        pass

    # 填写邮箱
    logger.info("正在填写邮箱...")
    try:
        page.wait_for_selector('input[type="email"]', timeout=15000)
    except PlaywrightTimeout:
        logger.error("邮箱输入框未出现")
        take_screenshot(page, "login_no_email_field")
        return False

    js_fill_input(page, 'input[type="email"]', EMAIL)
    page.wait_for_timeout(500 + random.randint(0, 500))

    # 填写密码
    logger.info("正在填写密码...")
    js_fill_input(page, 'input[type="password"]', PASSWORD)
    page.wait_for_timeout(500 + random.randint(0, 500))

    # 随机鼠标移动（模拟真实用户）
    try:
        page.mouse.move(random.randint(100, 400), random.randint(100, 400))
        page.wait_for_timeout(random.randint(300, 800))
        page.mouse.move(random.randint(500, 900), random.randint(200, 500))
        page.wait_for_timeout(random.randint(300, 800))
    except Exception:
        pass

    # 等待 Turnstile 自动通过
    wait_for_turnstile(page)
    page.wait_for_timeout(random.randint(1000, 3000))

    # 点击 Login（精确匹配）
    logger.info("正在点击 Login...")
    try:
        login_clicked = page.evaluate(
            """
            () => {
                var buttons = document.querySelectorAll('button');
                for (var i = 0; i < buttons.length; i++) {
                    var txt = (buttons[i].innerText || buttons[i].textContent || '').trim();
                    if (txt === 'Login') {
                        buttons[i].click();
                        return true;
                    }
                }
                return false;
            }
            """
        )
        if not login_clicked:
            logger.error("未找到 Login 按钮")
            take_screenshot(page, "login_no_button")
            return False
    except Exception as e:
        logger.error(f"点击 Login 失败: {e}")
        take_screenshot(page, "login_click_error")
        return False

    logger.info("等待登录跳转...")
    page.wait_for_timeout(15000)
    remove_adblocker_overlay(page)

    # 检查是否又到了 OTP
    try:
        back_link = page.locator('text=Back to password login').first
        if back_link.is_visible(timeout=3000):
            logger.info("登录后又到了 OTP 页面，重试...")
            back_link.click()
            page.wait_for_timeout(3000)
            remove_adblocker_overlay(page)
            js_fill_input(page, 'input[type="email"]', EMAIL)
            page.wait_for_timeout(300)
            js_fill_input(page, 'input[type="password"]', PASSWORD)
            page.wait_for_timeout(300)
            login_clicked2 = page.evaluate(
                """
                () => {
                    var buttons = document.querySelectorAll('button');
                    for (var i = 0; i < buttons.length; i++) {
                        var txt = (buttons[i].innerText || buttons[i].textContent || '').trim();
                        if (txt === 'Login') { buttons[i].click(); return true; }
                    }
                    return false;
                }
                """
            )
            if not login_clicked2:
                logger.error("第二次也未找到 Login 按钮")
                return False
            page.wait_for_timeout(10000)
            remove_adblocker_overlay(page)
    except Exception:
        pass

    if "auth/login" not in page.url:
        logger.info(f"登录成功! URL: {page.url}")
        return True

    logger.error(f"登录失败，URL: {page.url}")
    take_screenshot(page, "login_failed")
    return False

# ===================== Free Tier / GetStarted =====================

def handle_free_tier_selection(page):
    if "/freetier/resources" not in page.url:
        return True
    logger.info("检测到 Free Tier 选择页面，选择 Free-4...")
    remove_adblocker_overlay(page)
    page.wait_for_timeout(1000)
    try:
        free4_btn = page.locator('button:has-text("Free-4")').first
        if free4_btn.is_visible():
            free4_btn.click()
            page.wait_for_timeout(2000)
        select_btn = page.locator('button:has-text("Select")').first
        if select_btn.is_visible():
            select_btn.click()
            logger.info("已选择 Free-4")
            page.wait_for_timeout(5000)
        remove_adblocker_overlay(page)
        page.wait_for_timeout(2000)
        body_text = page.locator('body').inner_text()
        if "Application error" in body_text:
            logger.warning("页面错误，刷新中...")
            page.reload()
            page.wait_for_timeout(5000)
        return True
    except Exception as e:
        logger.error(f"Free Tier 选择失败: {e}")
        take_screenshot(page, "free_tier_error")
        return False


def handle_getstarted(page):
    if "/getstarted" not in page.url:
        return True
    logger.info("检测到 GetStarted 页面")
    try:
        btn = page.locator('button:has-text("Get Started")').first
        if btn.is_visible(timeout=3000):
            btn.click()
            page.wait_for_timeout(5000)
    except Exception:
        pass
    return True

# ===================== 导航与续期 =====================

def safe_goto(page, url, max_retries=3):
    """安全导航：自动检测页面崩溃并重试"""
    for attempt in range(max_retries):
        try:
            page.goto(url, wait_until="load", timeout=60000)
            page.wait_for_timeout(3000)
            body_text = page.locator("body").inner_text()
            if "Application error" in body_text:
                logger.warning(f"页面崩溃 (尝试 {attempt+1}/{max_retries})，刷新重试...")
                page.wait_for_timeout(2000)
                continue
            remove_adblocker_overlay(page)
            return True
        except Exception as e:
            logger.warning(f"导航失败 (尝试 {attempt+1}/{max_retries}): {e}")
            page.wait_for_timeout(2000)
    logger.error(f"导航 {url} 失败（已重试 {max_retries} 次）")
    return False


def get_server_links(page):
    logger.info("获取服务器链接...")
    links = []
    try:
        links = page.evaluate(
            """
            () => {
                var result = [];
                var allLinks = document.querySelectorAll('a[href*="server?id="], a[href*="/server/"]');
                for (var i = 0; i < allLinks.length; i++) {
                    result.push({
                        href: allLinks[i].href,
                        text: (allLinks[i].innerText || allLinks[i].textContent || '').trim()
                    });
                }
                return result;
            }
            """
        )
    except Exception as e:
        logger.error(f"获取服务器链接失败: {e}")
    logger.info(f"找到 {len(links)} 个服务器链接")
    for link in links:
        logger.info(f"  - {link.get('text', 'Unknown')}: {link.get('href', '')}")
    return links


def renew_server(page, server_url, server_name=""):
    logger.info(f"续期服务器: {server_name or server_url}")
    if not safe_goto(page, server_url):
        return False, "导航失败"
    page.wait_for_timeout(1000)

    take_screenshot(page, f"server_page_{server_name}")

    renew_result = page.evaluate(
        """
        () => {
            var els = Array.from(document.querySelectorAll('a, button, [role="button"]'));
            for (var el of els) {
                var txt = (el.innerText || el.textContent || '').trim();
                if (txt === 'Renew Server' || txt === 'Renew' || txt.includes('Renew Server')) {
                    el.scrollIntoView({block: 'center'});
                    el.click();
                    return 'clicked: ' + txt;
                }
            }
            var renewEls = document.querySelectorAll('a[onclick*="handleServerRenewal"], button[onclick*="handleServerRenewal"]');
            for (var r of renewEls) {
                r.scrollIntoView({block: 'center'});
                r.click();
                return 'clicked: handleServerRenewal';
            }
            return null;
        }
        """
    )

    if not renew_result:
        logger.warning(f"未找到续期按钮: {server_name}")
        take_screenshot(page, f"no_renew_btn_{server_name}")
        return False, "未找到续期按钮"

    logger.info(f"已点击续期按钮: {renew_result}")
    wait_for_turnstile(page)
    page.wait_for_timeout(5000)

    # 检查确认弹窗
    try:
        confirm_result = page.evaluate(
            """
            () => {
                var modals = document.querySelectorAll('.modal.show, [role="dialog"]');
                for (var m of modals) {
                    var btns = m.querySelectorAll('button');
                    for (var b of btns) {
                        var txt = (b.innerText || b.textContent || '').trim().toLowerCase();
                        if (txt.includes('renew') || txt.includes('confirm') || txt.includes('submit')) {
                            b.click();
                            return 'confirm clicked';
                        }
                    }
                }
                return null;
            }
            """
        )
        if confirm_result:
            logger.info(f"确认按钮: {confirm_result}")
            page.wait_for_timeout(5000)
    except Exception:
        pass

    page.wait_for_timeout(3000)
    try:
        result = page.evaluate(
            """
            () => {
                var alerts = document.querySelectorAll('.alert-success, .alert, [role="status"]');
                for (var a of alerts) {
                    var txt = (a.innerText || a.textContent || '').trim();
                    if (txt.includes('renewed') || txt.includes('success') || txt.includes('extended')) {
                        return {success: true, message: txt};
                    }
                }
                var errs = document.querySelectorAll('.alert-danger, .alert-error');
                for (var e of errs) {
                    var txt2 = (e.innerText || e.textContent || '').trim();
                    if (txt2) return {success: false, message: txt2};
                }
                var body = document.body.innerText || '';
                if (body.includes('renewed') || body.includes('successfully')) {
                    return {success: true, message: '续期成功'};
                }
                if (body.includes("can't renew") || body.includes('already renewed')) {
                    return {success: false, message: '续期不可用'};
                }
                return {success: null, message: '无法确定'};
            }
            """
        )
        if result:
            logger.info(f"续期结果: {result}")
            take_screenshot(page, f"renew_result_{server_name}")
            return result.get("success", False), result.get("message", "")
    except Exception as e:
        logger.error(f"读取结果失败: {e}")

    take_screenshot(page, f"renew_result_{server_name}")
    return True, "续期操作已执行（结果不确定）"

# ===================== 主逻辑 =====================

def do_renewal():
    """执行一次完整的续期流程"""
    pw = browser = context = page = proc = None
    try:
        # Step 0: 启动浏览器
        logger.info(f"浏览器模式: {BROWSER_MODE}")

        if BROWSER_MODE == "centbrowser":
            pw, browser, context, page, proc = launch_centbrowser()
        else:
            pw, browser, context, page, proc = launch_playwright()

        # Step 1: 登录
        if not login(page):
            record_renewal(False, "登录失败")
            return False

        # Step 2: 处理 Free Tier / GetStarted
        handle_free_tier_selection(page)
        handle_getstarted(page)

        # Step 3: 构建服务器 URL 列表
        server_links = []

        if KNOWN_SERVER_IDS:
            logger.info(f"使用已知服务器 ID: {KNOWN_SERVER_IDS}")
            for sid in KNOWN_SERVER_IDS:
                server_links.append({
                    "href": f"{BASE_URL}/server?id={sid}",
                    "text": f"Server-{sid}",
                })
        else:
            if safe_goto(page, f"{BASE_URL}/servers"):
                page.wait_for_timeout(3000)
                server_links = get_server_links(page)
            if not server_links:
                logger.info("尝试从 homepage 获取服务器...")
                safe_goto(page, f"{BASE_URL}/homepage")
                server_links = get_server_links(page)

        if not server_links:
            logger.info("没有找到需要续期的服务器")
            take_screenshot(page, "no_servers")
            record_renewal(True, "没有需要续期的服务器")
            return True

        # Step 4: 逐个续期
        all_success = True
        for i, server in enumerate(server_links):
            server_url = server.get("href", "")
            server_name = server.get("text", f"Server-{i+1}")
            if not server_url:
                continue

            logger.info(f"\n--- 续期 {i+1}/{len(server_links)}: {server_name} ---")
            success, message = renew_server(page, server_url, server_name)
            if not success and "续期不可用" not in message:
                all_success = False

            if i < len(server_links) - 1:
                wait = random.uniform(3, 8)
                logger.info(f"等待 {wait:.1f} 秒...")
                page.wait_for_timeout(int(wait * 1000))

        result_msg = f"成功续期 {len(server_links)} 个服务器" if all_success else "部分服务器续期失败"
        record_renewal(all_success, result_msg)
        logger.info(f"\n{'='*50}")
        logger.info(f"续期完成: {result_msg}")
        logger.info(f"{'='*50}")
        return all_success

    except Exception as e:
        logger.error(f"续期过程出错: {e}")
        logger.error(traceback.format_exc())
        try:
            take_screenshot(page, "error")
        except Exception:
            pass
        record_renewal(False, f"异常: {str(e)[:200]}")
        return False
    finally:
        stop_browser(pw, browser, context, page, proc)

# ===================== 持续运行 =====================

def run_loop():
    """持续运行模式：每小时执行一次"""
    logger.info("启动持续续期模式（每小时执行一次）")
    logger.info(f"账号: {EMAIL}")
    logger.info(f"Free Tier: {FREE_TIER}")

    while True:
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"\n{'#'*60}")
            logger.info(f"开始执行续期 - {now}")
            logger.info(f"{'#'*60}")

            do_renewal()

            wait_seconds = 3600 + random.randint(0, 300)
            next_run = datetime.now() + timedelta(seconds=wait_seconds)
            logger.info(f"下次执行: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info(f"等待 {wait_seconds} 秒...")

            for _ in range(wait_seconds // 60):
                time.sleep(60)
        except KeyboardInterrupt:
            logger.info("收到中断信号，退出")
            break
        except Exception as e:
            logger.error(f"循环出错: {e}")
            time.sleep(60)

# ===================== 入口 =====================

if __name__ == "__main__":
    logger.info(f"Zampto 自动续期脚本启动")
    logger.info(f"浏览器模式: {BROWSER_MODE}")

    mode = sys.argv[1] if len(sys.argv) > 1 else "once"

    if mode == "loop":
        run_loop()
    else:
        success = do_renewal()
        sys.exit(0 if success else 1)
