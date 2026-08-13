from selenium import webdriver
from selenium.webdriver.edge.service import Service
from selenium.webdriver.edge.options import Options
import time

driver_path = r"D:\edgedriver_win64\msedgedriver.exe"

#配置Edge选项来隐藏自动化特征
options = Options()
options.add_argument("--disable-blink-features=AutomationControlled")
options.add_experimental_option("excludeSwitches", ["enable-automation"])
options.add_experimental_option("useAutomationExtension", False)

# 创建driver时传入options
driver = webdriver.Edge(service=Service(driver_path), options=options)

try:
    # 执行JS代码，删除webdriver痕迹
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    driver.maximize_window()
    driver.get("https://www.baidu.com")
    time.sleep(2)
    
    # 执行搜索
    driver.execute_script("""
        document.getElementById('kw').value = 'Selenium';
        document.getElementById('su').click();
    """)
    
    print("✅ 搜索完成")
    time.sleep(3)
    
except Exception as e:
    print(f"❌ 发生错误: {e}")
    
finally:
    driver.quit()