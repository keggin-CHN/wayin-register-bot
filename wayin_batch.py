#!/usr/bin/env python3
"""
WayinVideo 批量邀请注册器

功能：
  1. 注册 N 个邀请号（母号）
  2. 每个母号邀请 M 个子号
  3. 遇到域名不支持自动换域名重试
  4. 输出完整 JSON（含邀请计数）
  5. 可选网络代理

用法：
  python3 wayin_batch.py
"""

import json
import re
import time
import hashlib
import base64
import random
import string
import sys
from typing import Any, Dict, List, Optional, Tuple
from curl_cffi import requests as curl_requests

# ╔══════════════════════════════════════════════════════════════╗
# ║                        配 置 区                              ║
# ╚══════════════════════════════════════════════════════════════╝

# 注册多少个母号（邀请号）
NUM_INVITERS = 10

# 每个母号邀请多少人
INVITES_PER_INVITER = 10

# 验证码轮询超时（秒）
POLL_TIMEOUT = 120

# 验证码轮询间隔（秒）
POLL_INTERVAL = 5

# 注册间隔（秒），避免触发频率限制
# ⚠️ 每个子号必须用独立 session！共享 session 会导致邀请追踪丢失。
# 间隔不重要（0 秒也行），但建议留 1-2 秒避免极端情况。
REGISTER_DELAY = 2

# 域名不支持时的最大重试次数
DOMAIN_RETRY_MAX = 5

# ╔══════════════════════════════════════════════════════════════╗
# ║                    网络代理配置区                             ║
# ║  如需代理，取消注释并填写你的代理地址                          ║
# ╚══════════════════════════════════════════════════════════════╝

PROXY_CONFIG = {
    # "http": "http://127.0.0.1:7890",
    # "https": "http://127.0.0.1:7890",
    # "socks5": "socks5://127.0.0.1:1080",
}

# ╔══════════════════════════════════════════════════════════════╗
# ║                      常 量 区                                ║
# ╚══════════════════════════════════════════════════════════════╝

WAYIN_API = "https://wayinvideo-api.wayin.ai"
CHATGPTMAIL_BASE_URL = "https://mail.chatgpt.org.uk"


# ╔══════════════════════════════════════════════════════════════╗
# ║                   ChatGPTMail 邮箱客户端                     ║
# ╚══════════════════════════════════════════════════════════════╝

class ChatGPTMailClient:
    def __init__(self, proxy: Optional[Dict] = None):
        kwargs = {"impersonate": "chrome136"}
        if proxy:
            kwargs["proxies"] = proxy
        self.session = curl_requests.Session(**kwargs)

    def get_initial_token(self) -> str:
        r = self.session.get(CHATGPTMAIL_BASE_URL)
        r.raise_for_status()
        m = re.search(r"window\.__BROWSER_AUTH\s*=\s*({[^}]+})", r.text)
        if not m:
            raise RuntimeError("提取 chatgptmail token 失败")
        return json.loads(m.group(1))["token"]

    def generate_email(self) -> Tuple[str, str]:
        token = self.get_initial_token()
        r = self.session.post(
            f"{CHATGPTMAIL_BASE_URL}/api/generate-email",
            headers={"X-Inbox-Token": token, "Content-Type": "application/json"},
            json={},
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            raise RuntimeError(f"生成邮箱失败: {data}")
        return data["data"]["email"], data["auth"]["token"]

    def list_emails(self, email: str, inbox_token: str) -> List[Dict]:
        r = self.session.get(
            f"{CHATGPTMAIL_BASE_URL}/api/emails",
            params={"email": email},
            headers={"X-Inbox-Token": inbox_token},
        )
        r.raise_for_status()
        emails = r.json().get("data", {}).get("emails", [])
        return emails if isinstance(emails, list) else []

    def get_email_detail(self, email_id: str, inbox_token: str) -> Dict:
        r = self.session.get(
            f"{CHATGPTMAIL_BASE_URL}/api/email/{email_id}",
            headers={"X-Inbox-Token": inbox_token},
        )
        r.raise_for_status()
        return r.json()


# ╔══════════════════════════════════════════════════════════════╗
# ║                   WayinVideo API 客户端                      ║
# ╚══════════════════════════════════════════════════════════════╝

class WayinClient:
    def __init__(self, proxy: Optional[Dict] = None):
        kwargs = {"impersonate": "chrome136"}
        if proxy:
            kwargs["proxies"] = proxy
        self.session = curl_requests.Session(**kwargs)

    @staticmethod
    def compute_ticket(reason: str, email: str, timestamp_ms: int) -> str:
        raw = f"{reason}{email}{timestamp_ms}"
        md5_hex = hashlib.md5(raw.encode()).hexdigest()
        return base64.b64encode(md5_hex.encode()).decode()

    def send_verify_code(self, email: str) -> bool:
        reason = "SIGNUP"
        ts = int(time.time() * 1000)
        ticket = self.compute_ticket(reason, email, ts)
        body = json.dumps({
            "email": email,
            "reason": reason,
            "timestamp": ts,
            "ticket": ticket,
        })
        r = self.session.post(
            f"{WAYIN_API}/verify_code",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        r.raise_for_status()
        code = parse_xml_field(r.text, "code")
        return code == "0"

    def signup(self, username: str, email: str, password: str,
               verify_code: str, invitation_code: str = "") -> Dict:
        pw_md5 = hashlib.md5(password.encode()).hexdigest()
        payload = {
            "username": username,
            "email": email,
            "password": pw_md5,
            "verify_code": verify_code,
        }
        if invitation_code:
            payload["invitation_code"] = invitation_code
        r = self.session.post(f"{WAYIN_API}/signup", json=payload)
        r.raise_for_status()
        return parse_xml_full(r.text)

    def login(self, email: str, password: str) -> Dict:
        pw_md5 = hashlib.md5(password.encode()).hexdigest()
        auth = base64.b64encode(f"{email}:{pw_md5}".encode()).decode()
        r = self.session.post(
            f"{WAYIN_API}/login",
            json={"email": email, "password": pw_md5},
            headers={"Authorization": f"Basic {auth}"},
        )
        r.raise_for_status()
        return parse_xml_full(r.text)

    def get_user_info(self, token: str) -> Dict:
        r = self.session.get(
            f"{WAYIN_API}/api/user",
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        return parse_xml_full(r.text)


# ╔══════════════════════════════════════════════════════════════╗
# ║                       辅 助 函 数                            ║
# ╚══════════════════════════════════════════════════════════════╝

def parse_xml_field(xml: str, field: str) -> Optional[str]:
    m = re.search("<" + field + ">(.*?)</" + field + ">", xml)
    return m.group(1) if m else None


def parse_xml_full(xml: str) -> Dict[str, str]:
    result = {}
    for tag in ["code", "message", "user_id", "username", "email",
                "invitation_code", "cooling_time", "reason", "expires"]:
        val = parse_xml_field(xml, tag)
        if val:
            result[tag] = val
    token_m = re.search(r"<access_token>(.*?)</access_token>", xml)
    if token_m:
        result["access_token"] = token_m.group(1)
    refresh_m = re.search(r"<refresh_token>(.*?)</refresh_token>", xml)
    if refresh_m:
        result["refresh_token"] = refresh_m.group(1)
    # subscription
    plan_m = re.search(r"<current_plan>(.*?)</current_plan>", xml)
    if plan_m:
        result["current_plan"] = plan_m.group(1)
    reward_m = re.search(r"<reward_trial_plan>(.*?)</reward_trial_plan>", xml)
    if reward_m:
        result["reward_trial_plan"] = reward_m.group(1)
    credit_m = re.search(r"<feature>CREDIT</feature>.*?<limit>(\d+)</limit>", xml, re.S)
    if credit_m:
        result["credit_limit"] = credit_m.group(1)
    return result


def find_code_in_email(detail: Dict) -> Optional[str]:
    text = json.dumps(detail, ensure_ascii=False)
    for pattern in [r'verification code[:\s]*(\d{6})', r'code[:\s]*(\d{6})',
                    r'验证码[：:\s]*(\d{6})', r'\b(\d{6})\b']:
        m = re.search(pattern, text, re.I)
        if m:
            return m.group(1)
    return None


def poll_for_code(mail: ChatGPTMailClient, email: str, inbox_token: str,
                  timeout: int = POLL_TIMEOUT) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        emails = mail.list_emails(email, inbox_token)
        for e in emails:
            subj = str(e.get("subject", ""))
            if any(kw in subj.lower() for kw in ["wayinvideo", "verif", "verify", "code"]):
                eid = e.get("id")
                if eid:
                    detail = mail.get_email_detail(str(eid), inbox_token)
                    code = find_code_in_email(detail)
                    if code:
                        return code
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"超时 {timeout}s 未收到验证码")


def gen_username() -> str:
    return "wv" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def gen_password() -> str:
    upper = random.choice(string.ascii_uppercase)
    lower = random.choices(string.ascii_lowercase, k=4)
    digits = random.choices(string.digits, k=3)
    special = random.choice("!@#$%")
    rest = random.choices(string.ascii_letters + string.digits, k=8)
    chars = list(upper) + lower + digits + [special] + rest
    random.shuffle(chars)
    return "".join(chars)


# ╔══════════════════════════════════════════════════════════════╗
# ║                    核 心 注 册 流 程                          ║
# ╚══════════════════════════════════════════════════════════════╝

def register_one_account(
    wayin: WayinClient,
    mail: ChatGPTMailClient,
    invitation_code: str = "",
) -> Dict:
    """注册一个账号，支持域名不支持时自动换域名重试"""

    for attempt in range(DOMAIN_RETRY_MAX):
        # 1. 生成临时邮箱
        tmp_email, inbox_token = mail.generate_email()

        # 2. 发送验证码
        try:
            wayin.send_verify_code(tmp_email)
        except Exception as e:
            print(f"    [重试] 发送验证码失败: {e}")
            time.sleep(2)
            continue

        # 3. 等待验证码
        try:
            code = poll_for_code(mail, tmp_email, inbox_token)
        except TimeoutError:
            print(f"    [重试] 超时未收到验证码: {tmp_email}")
            continue

        # 4. 注册
        username = gen_username()
        password = gen_password()
        result = wayin.signup(username, tmp_email, password, code, invitation_code)

        if result.get("code") == "0":
            return {
                "email": tmp_email,
                "username": username,
                "password": password,
                "user_id": result.get("user_id", ""),
                "invitation_code": result.get("invitation_code", ""),
                "credit_limit": result.get("credit_limit", ""),
            }
        elif result.get("code") == "BE-042":
            # 域名不支持，换域名重试
            print(f"    [重试] 域名不支持 ({tmp_email.split('@')[1]})，换域名...")
            time.sleep(1)
            continue
        else:
            raise RuntimeError(f"注册失败: {result}")

    raise RuntimeError(f"重试 {DOMAIN_RETRY_MAX} 次均失败")


def process_one_inviter(index: int, wayin: WayinClient, mail: ChatGPTMailClient, proxy: Optional[Dict] = None) -> Dict:
    """注册一个母号 + 邀请 N 个子号"""

    print(f"\n{'='*60}")
    print(f"[母号 {index+1}/{NUM_INVITERS}] 开始注册...")

    # 1. 注册母号
    inviter = register_one_account(wayin, mail)
    print(f"  ✅ 母号注册成功: {inviter['email']} / {inviter['username']}")
    print(f"  邀请码: {inviter['invitation_code']}")

    # 2. 用母号登录获取 token
    login_data = wayin.login(inviter["email"], inviter["password"])
    inviter_token = login_data.get("access_token", "")
    inviter["has_reward_trial"] = bool(login_data.get("reward_trial_plan"))
    inviter["reward_trial_plan"] = login_data.get("reward_trial_plan", "")
    inviter["credit_limit"] = login_data.get("credit_limit", "")

    # 3. 邀请子号（每个子号用独立 session！）
    invited = []
    for j in range(INVITES_PER_INVITER):
        print(f"\n  [子号 {j+1}/{INVITES_PER_INVITER}] 注册中...")
        try:
            # 每个子号创建全新的客户端（独立 session）
            child_wayin = WayinClient(proxy=proxy)
            child_mail = ChatGPTMailClient(proxy=proxy)
            child = register_one_account(child_wayin, child_mail, inviter["invitation_code"])
            invited.append(child)
            print(f"    ✅ {child['email']} / {child['username']}")
        except Exception as e:
            print(f"    ❌ 失败: {e}")
            invited.append({"error": str(e)})
        if j < INVITES_PER_INVITER - 1:
            time.sleep(REGISTER_DELAY)

    # 4. 重新登录母号查看奖励
    try:
        login_data2 = wayin.login(inviter["email"], inviter["password"])
        inviter["credit_limit_after"] = login_data2.get("credit_limit", "")
        inviter["reward_trial_plan_after"] = login_data2.get("reward_trial_plan", "")
    except Exception:
        pass

    inviter["invited_count"] = sum(1 for c in invited if "error" not in c)
    inviter["invited_accounts"] = invited

    print(f"\n  📊 母号 {inviter['username']} 完成: 邀请 {inviter['invited_count']}/{INVITES_PER_INVITER} 人")
    return inviter


# ╔══════════════════════════════════════════════════════════════╗
# ║                        主 入 口                              ║
# ╚══════════════════════════════════════════════════════════════╝

def main():
    print(f"╔══════════════════════════════════════════════════╗")
    print(f"║   WayinVideo 批量邀请注册器                      ║")
    print(f"║   母号: {NUM_INVITERS}  |  每号邀请: {INVITES_PER_INVITER}  |  总计: {NUM_INVITERS * INVITES_PER_INVITER} 子号  ║")
    print(f"╚══════════════════════════════════════════════════╝")

    proxy = PROXY_CONFIG if PROXY_CONFIG else None
    if proxy:
        print(f"[代理] 已启用: {proxy}")

    wayin = WayinClient(proxy=proxy)
    mail = ChatGPTMailClient(proxy=proxy)
    results = []

    for i in range(NUM_INVITERS):
        try:
            inviter = process_one_inviter(i, wayin, mail, proxy)
            results.append(inviter)
        except Exception as e:
            print(f"\n  ❌ 母号 {i+1} 注册失败: {e}")
            results.append({"index": i + 1, "error": str(e)})
        if i < NUM_INVITERS - 1:
            time.sleep(REGISTER_DELAY * 2)

    # 构建输出 JSON
    total_invited = sum(r.get("invited_count", 0) for r in results if "error" not in r)
    output = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "config": {
            "num_inviters": NUM_INVITERS,
            "invites_per_inviter": INVITES_PER_INVITER,
        },
        "total_inviters": sum(1 for r in results if "error" not in r),
        "total_invited": total_invited,
        "inviters": [],
    }

    for r in results:
        if "error" in r:
            output["inviters"].append({"error": r["error"]})
            continue
        inviter_entry = {
            "email": r["email"],
            "username": r["username"],
            "password": r["password"],
            "user_id": r["user_id"],
            "invitation_code": r["invitation_code"],
            "invited_count": r.get("invited_count", 0),
            "credit_before": r.get("credit_limit", ""),
            "credit_after": r.get("credit_limit_after", ""),
            "reward_trial": r.get("reward_trial_plan_after", r.get("reward_trial_plan", "")),
            "invited_accounts": r.get("invited_accounts", []),
        }
        output["inviters"].append(inviter_entry)

    # 保存
    out_path = "/tmp/wayin_batch_result.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"✅ 全部完成!")
    print(f"   母号: {output['total_inviters']}")
    print(f"   子号: {output['total_invited']}")
    print(f"   JSON: {out_path}")


if __name__ == "__main__":
    main()
