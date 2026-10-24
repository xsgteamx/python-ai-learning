# 企业微信 K8s 查询机器人 - 生产镜像
# 基础镜像使用 3.12-slim（稳定、体积小）
FROM python:3.12-slim

# 日志实时输出、不写 __pycache__、容器时区与业务时区一致（报表时间戳用本地时间）
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt gunicorn==23.0.0

COPY . .

EXPOSE 8080

# gunicorn：2 个 worker × 4 线程；超时 30s（消息处理在独立线程中异步进行）
CMD ["gunicorn", "-w", "2", "--threads", "4", "-b", "0.0.0.0:8080", "--timeout", "30", "app:app"]
