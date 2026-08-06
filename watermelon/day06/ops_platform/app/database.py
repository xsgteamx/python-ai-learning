import logging
import time
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from app.config import settings

logger = logging.getLogger("app.database")

# 创建数据库引擎
# - SQLite：禁用 check_same_thread（FastAPI 多线程访问所需）
# - MySQL/PostgreSQL：
#   * pool_pre_ping：每次取连接前先发 ping，避免使用已被服务端关闭的连接
#   * pool_recycle：主动回收连接（必须小于 MySQL 的 wait_timeout 和中间网络设备的空闲超时）
#   * connect_args：连接超时和读取超时，避免长时间卡住
_is_sqlite = "sqlite" in (settings.database_url or "")

if _is_sqlite:
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
    )
else:
    engine = create_engine(
        settings.database_url,
        # 连接池配置
        pool_size=5,            # 持久连接数
        max_overflow=10,        # 突发时额外连接数
        pool_timeout=30,        # 从池中获取连接的等待超时（秒）
        pool_pre_ping=True,     # 取连接前 ping 一次，剔除失效连接
        pool_recycle=300,       # 5 分钟主动回收（必须 < MySQL wait_timeout 和 LB 空闲超时）
        # PyMySQL 连接级参数
        connect_args={
            "connect_timeout": 10,   # 建立 TCP 连接的超时
            "read_timeout": 30,      # 读取查询结果超时
            "write_timeout": 30,     # 发送查询超时
        },
    )


@event.listens_for(engine, "connect")
def _on_db_connect(dbapi_conn, conn_record):
    """新连接建立时记录日志（便于排查连接问题）"""
    logger.debug("数据库新连接已建立: %s", id(dbapi_conn))


# 会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 基类
SQLAlchemyBase = declarative_base()
Base = SQLAlchemyBase


def get_db():
    """获取数据库会话（依赖注入用）

    带自动重试：遇到连接失效类错误时自动重建会话并重试一次，
    屏蔽偶发的 'Lost connection to MySQL server during query' 问题。
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def execute_with_retry(operation, max_retries: int = 2, retry_delay: float = 0.5):
    """执行数据库操作，遇到连接失效时自动重试

    适用于非请求上下文的场景（如后台任务）。FastAPI 路由内通过 get_db()
    依赖注入的会话，建议在该会话的 query 出错时手动调用此函数包装。

    Args:
        operation: 接收一个 db session 参数的可调用对象
        max_retries: 最大重试次数
        retry_delay: 重试间隔（秒）
    """
    last_err = None
    for attempt in range(max_retries + 1):
        db = SessionLocal()
        try:
            result = operation(db)
            db.commit()
            return result
        except OperationalError as e:
            db.rollback()
            last_err = e
            # 仅对连接类错误重试（错误码 2006/2013 表示连接丢失）
            err_code = getattr(e.orig, "args", [None])[0] if e.orig else None
            if err_code in (2006, 2013, 2059) and attempt < max_retries:
                logger.warning("数据库连接失效(code=%s)，第%d次重试...", err_code, attempt + 1)
                time.sleep(retry_delay)
                continue
            raise
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    raise last_err