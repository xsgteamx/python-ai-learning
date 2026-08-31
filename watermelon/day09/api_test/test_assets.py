# test_assets.py
import pytest
import requests

class TestAssets:
    """资产管理接口测试"""

    def test_get_assets(self, base_url, headers):
        """测试：查询资产列表"""
        resp = requests.get(f"{base_url}/api/assets/", headers=headers)
        assert resp.status_code == 200, f"预期200，实际{resp.status_code}"
        data = resp.json()
        assert isinstance(data, list), "返回数据应为列表"
        print(f"✅ 共 {len(data)} 条资产")

    def test_create_asset(self, base_url, headers):
        """测试：创建资产"""
        new_asset = {
            "hostname": "pytest-test-server",
            "ip": "10.0.0.88",
            "port": 22,
            "username": "root",
            "password": "test123",
            "env": "prod",
            "role": "web",
            "owner": "pytest",
            "team": "自动化测试"
        }
        resp = requests.post(f"{base_url}/api/assets/", json=new_asset, headers=headers)
        assert resp.status_code == 201, f"预期201，实际{resp.status_code}"
        asset = resp.json()
        assert "id" in asset, "创建成功但未返回ID"
        assert asset["hostname"] == new_asset["hostname"], "主机名不匹配"
        
        # 保存 asset_id 到 fixture 中，供后续用例使用
        pytest.asset_id = asset["id"]
        print(f"✅ 创建成功，ID: {asset['id']}")

    def test_get_asset_detail(self, base_url, headers):
        """测试：查询资产详情"""
        asset_id = getattr(pytest, "asset_id", None)
        if asset_id is None:
            pytest.skip("跳过：没有可用的资产ID")
        
        resp = requests.get(f"{base_url}/api/assets/{asset_id}", headers=headers)
        assert resp.status_code == 200, f"预期200，实际{resp.status_code}"
        asset = resp.json()
        assert asset["id"] == asset_id, f"ID不匹配：预期{asset_id}，实际{asset['id']}"
        print(f"✅ 查询成功，主机名: {asset['hostname']}")

    def test_update_asset(self, base_url, headers):
        """测试：更新资产"""
        asset_id = getattr(pytest, "asset_id", None)
        if asset_id is None:
            pytest.skip("跳过：没有可用的资产ID")
        
        update_data = {
            "hostname": "pytest-test-updated",
            "remark": "通过pytest自动化测试更新"
        }
        resp = requests.put(f"{base_url}/api/assets/{asset_id}", json=update_data, headers=headers)
        assert resp.status_code == 200, f"预期200，实际{resp.status_code}"
        asset = resp.json()
        assert asset["hostname"] == "pytest-test-updated", "主机名未更新成功"
        print(f"✅ 更新成功，新主机名: {asset['hostname']}")

    def test_delete_asset(self, base_url, headers):
        """测试：删除资产"""
        asset_id = getattr(pytest, "asset_id", None)
        if asset_id is None:
            pytest.skip("跳过：没有可用的资产ID")
        
        resp = requests.delete(f"{base_url}/api/assets/{asset_id}", headers=headers)
        assert resp.status_code == 204, f"预期204，实际{resp.status_code}"
        print("✅ 删除成功")

    def test_get_asset_after_delete(self, base_url, headers):
        """测试：删除后查询应返回404"""
        asset_id = getattr(pytest, "asset_id", None)
        if asset_id is None:
            pytest.skip("跳过：没有可用的资产ID")
        
        resp = requests.get(f"{base_url}/api/assets/{asset_id}", headers=headers)
        assert resp.status_code == 404, f"预期404，实际{resp.status_code}"
        print("✅ 删除后查询返回404，符合预期")