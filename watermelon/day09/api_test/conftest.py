# conftest.py
import pytest
import requests
from auth import get_token
from config import BASE_URL

@pytest.fixture(scope="session")
def token():
    """整个测试会话只登录一次"""
    return get_token()

@pytest.fixture(scope="session")
def headers(token):
    """所有接口共享的请求头"""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

@pytest.fixture(scope="session")
def base_url():
    return BASE_URL