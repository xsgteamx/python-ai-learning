# -*- coding: utf-8 -*-
"""
定时集群巡检 · 群推送

每天指定时间（默认 09:00 北京时间）执行一次集群巡检，并把报告推送到
企业微信群（群机器人 Webhook）。

与智能机器人（URL回调/长连接）是两套东西：
- 智能机器人 = 双向问答（有人在群里 @ 才应答）
- 群机器人 Webhook = 单向推送（这里用来定时发巡检报告到群）

配置（.env）：
  WECOM_GROUP_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
  INSPECT_ENABLED=1
  INSPECT_HOUR=9
  INSPECT_MINUTE=0

用法：
  python inspect_scheduler.py          # 常驻调度，每天定时推送
  python inspect_scheduler.py --once   # 立即执行一次并退出（测试用）
"""
import json
import logging
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import config
import k8s_api

logger = logging.getLogger("inspect_scheduler")
# 北京时间固定 UTC+8（无夏令时），避免依赖 tzdata
_TZ = timezone(timedelta(hours=8))

# 企微群机器人 markdown 消息上限 4096 字节，留余量
_MAX_SEND_BYTES = 3800


def build_inspect_report():
    """生成巡检报告并保存到 logs/ 目录。

    返回 (报告文本, 保存文件名或None)：
    - 报告文本：群消息版（精简，不含 Pod 明细）
    - 保存文件：完整版（含全部 Pod 明细），供下载链接查看
    """
    report = k8s_api.cluster_inspect_report()
    report_full = k8s_api.cluster_inspect_report(include_pod_list=True)
    fname = None
    try:
        ts = datetime.now(_TZ).strftime("%Y%m%d-%H%M%S")
        fname = f"cluster-inspect-{ts}.txt"
        path = os.path.join(k8s_api.LOG_DIR, fname)
        os.makedirs(k8s_api.LOG_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(report_full)
    except Exception as exc:
        logger.error("保存巡检报告失败: %s", exc)
        fname = None
    return report, fname


def _send_to_group(content):
    """POST 到企微群机器人 Webhook，返回 (ok, errmsg)。"""
    if not config.WECOM_GROUP_WEBHOOK:
        return False, "未配置 WECOM_GROUP_WEBHOOK"
    payload = {"msgtype": "markdown", "markdown": {"content": content}}
    req = urllib.request.Request(
        config.WECOM_GROUP_WEBHOOK,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return False, str(exc)
    if data.get("errcode") != 0:
        return False, f"{data.get('errcode')}: {data.get('errmsg')}"
    return True, "ok"


def send_inspect_report():
    """执行一次巡检并推送到群；返回是否发送成功。"""
    report, fname = build_inspect_report()
    logger.info("巡检完成（报告 %d 字符）", len(report))

    # 群推送附 AI 分析（可选；失败不影响主流程）
    try:
        if config.LLM_API_KEY and config.LLM_ENHANCE:
            import llm_router
            report = llm_router.enhance_reply("cluster_inspect", report)
    except Exception as exc:
        logger.warning("巡检 AI 分析失败，使用原始报告: %s", exc)

    if not config.WECOM_GROUP_WEBHOOK:
        logger.warning("未配置 WECOM_GROUP_WEBHOOK，跳过群推送（完整报告已保存到 logs/）")
        return False

    content = report
    size = len(content.encode("utf-8"))
    if size > _MAX_SEND_BYTES:
        # 截断到可发送范围（按字符粗略截），并提示完整报告位置
        cut = int(_MAX_SEND_BYTES / 2)
        content = content[:cut] + "\n…（报告过长已截断）"
    if config.LOG_DOWNLOAD_BASE_URL and fname:
        content += f"\n完整报告: {config.LOG_DOWNLOAD_BASE_URL}/wecom-k8s-bot-logs/{fname}"
    ok, err = _send_to_group(content)
    if ok:
        logger.info("巡检报告已推送到企微群（%d 字节）", len(content.encode("utf-8")))
    else:
        logger.error("巡检报告群推送失败: %s", err)
    return ok


def _next_run(now=None):
    """下一次计划推送时间（北京时间 INSPECT_HOUR:INSPECT_MINUTE）。"""
    now = now or datetime.now(_TZ)
    nxt = now.replace(
        hour=config.INSPECT_HOUR, minute=config.INSPECT_MINUTE, second=0, microsecond=0
    )
    if nxt <= now:
        nxt += timedelta(days=1)
    return nxt


def run_scheduler():
    """常驻调度：到点执行巡检推送，执行后防重复等待。"""
    logger.info(
        "定时巡检调度启动：每天 %02d:%02d（北京时间）推送到企微群",
        config.INSPECT_HOUR, config.INSPECT_MINUTE,
    )
    while True:
        nxt = _next_run()
        wait = (nxt - datetime.now(_TZ)).total_seconds()
        logger.info("下次巡检推送: %s（%.0f 秒后）", nxt.strftime("%Y-%m-%d %H:%M:%S"), wait)
        time.sleep(max(wait, 1))
        try:
            send_inspect_report()
        except Exception as exc:
            logger.error("定时巡检执行异常: %s", exc)
        time.sleep(60)  # 防止整点边界重复触发


def _setup_file_logging():
    """运行日志按天切割写入 /app/logs/bot.log（保留 7 天）；主服务内置调用时已由 app.py 配置，这里防重复。"""
    try:
        import logging.handlers
        log_dir = k8s_api.LOG_DIR
        os.makedirs(log_dir, exist_ok=True)
        for h in logging.getLogger().handlers:
            if isinstance(h, logging.handlers.TimedRotatingFileHandler) \
                    and getattr(h, "baseFilename", "").endswith("bot.log"):
                return
        fh = logging.handlers.TimedRotatingFileHandler(
            os.path.join(log_dir, "bot.log"),
            when="midnight", interval=1, backupCount=7, encoding="utf-8",
        )
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logging.getLogger().addHandler(fh)
    except Exception as exc:
        logger.warning("运行日志文件保存初始化失败（不影响运行）: %s", exc)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    _setup_file_logging()
    if not config.INSPECT_ENABLED:
        logger.info("INSPECT_ENABLED=0，定时巡检未启用")
        return 0
    if "--once" in sys.argv:
        ok = send_inspect_report()
        logger.info("一次性巡检推送结束（成功=%s）", ok)
        # 未配置 webhook 只是"生成报告、不推送"，不算失败
        return 0 if ok or not config.WECOM_GROUP_WEBHOOK else 1
    if not config.WECOM_GROUP_WEBHOOK:
        logger.warning("未配置 WECOM_GROUP_WEBHOOK，定时推送不会真正发到群里（只生成报告）")
    run_scheduler()
    return 0


if __name__ == "__main__":
    sys.exit(main())
