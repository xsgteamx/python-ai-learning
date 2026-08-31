# auth.py
import hashlib
import requests
from config import BASE_URL, USERNAME, PASSWORD

def get_token():
    """登录并返回 Token"""
    password_digest = hashlib.sha256(PASSWORD.encode()).hexdigest()
    resp = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": USERNAME, "password": password_digest}
    )
    if resp.status_code == 200:
        return resp.json()["token"]
    else:
        raise Exception(f"登录失败: {resp.text}")