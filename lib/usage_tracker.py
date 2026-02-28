"""
API 调用记录追踪器

管理 SQLite 数据库，记录图片/视频生成 API 的调用信息和费用。
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

from lib.cost_calculator import cost_calculator


class UsageTracker:
    """API 调用记录追踪器"""

    def __init__(self, db_path: Path):
        """
        初始化追踪器

        Args:
            db_path: SQLite 数据库文件路径
        """
        self.db_path = Path(db_path)
        self._init_db()

    @staticmethod
    def _date_start(value: datetime) -> datetime:
        """归一化到当天 00:00:00（忽略时分秒）"""
        return datetime(value.year, value.month, value.day)

    @classmethod
    def _date_end_exclusive(cls, value: datetime) -> datetime:
        """归一化到次日 00:00:00（用于 end_date 的开区间上界）"""
        return cls._date_start(value) + timedelta(days=1)

    @staticmethod
    def _iso_millis(value: datetime) -> str:
        """统一使用毫秒精度 ISO 字符串，便于排序/前端解析"""
        try:
            return value.isoformat(timespec="milliseconds")
        except TypeError:
            # 兼容极旧 Python 版本
            return value.isoformat()

    def _init_db(self) -> None:
        """初始化数据库表结构"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS api_calls (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,

                    -- 基础信息
                    project_name    TEXT NOT NULL,
                    call_type       TEXT NOT NULL,
                    model           TEXT NOT NULL,

                    -- 调用参数
                    prompt          TEXT,
                    resolution      TEXT,
                    duration_seconds INTEGER,
                    aspect_ratio    TEXT,
                    generate_audio  BOOLEAN DEFAULT 1,

                    -- 结果信息
                    status          TEXT NOT NULL DEFAULT 'pending',
                    error_message   TEXT,
                    output_path     TEXT,

                    -- 性能指标
                    started_at      DATETIME NOT NULL,
                    finished_at     DATETIME,
                    duration_ms     INTEGER,
                    retry_count     INTEGER DEFAULT 0,

                    -- 费用信息
                    cost_usd        REAL DEFAULT 0.0,

                    -- 索引友好
                    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 创建索引
            conn.execute("CREATE INDEX IF NOT EXISTS idx_project_name ON api_calls(project_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_call_type ON api_calls(call_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON api_calls(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON api_calls(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_started_at ON api_calls(started_at)")

            conn.commit()

    def start_call(
        self,
        project_name: str,
        call_type: str,
        model: str,
        prompt: str = None,
        resolution: str = None,
        duration_seconds: int = None,
        aspect_ratio: str = None,
        generate_audio: bool = True,
    ) -> int:
        """
        记录调用开始

        Args:
            project_name: 项目名称
            call_type: 调用类型 ('image' | 'video')
            model: 模型名称
            prompt: 调用 prompt（可截断存储）
            resolution: 分辨率
            duration_seconds: 视频时长（秒）
            aspect_ratio: 宽高比
            generate_audio: 是否生成音频

        Returns:
            call_id: 记录 ID，用于后续 finish_call()
        """
        started_at = self._iso_millis(datetime.now())

        # 截断 prompt 存储（最多 500 字符）
        prompt_truncated = prompt[:500] if prompt else None

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT INTO api_calls (
                    project_name, call_type, model,
                    prompt, resolution, duration_seconds, aspect_ratio, generate_audio,
                    status, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """, (
                project_name, call_type, model,
                prompt_truncated, resolution, duration_seconds, aspect_ratio, generate_audio,
                started_at
            ))
            conn.commit()
            return cursor.lastrowid

    def finish_call(
        self,
        call_id: int,
        status: str,
        output_path: str = None,
        error_message: str = None,
        retry_count: int = 0,
    ) -> None:
        """
        记录调用结束，计算费用

        Args:
            call_id: 记录 ID（来自 start_call()）
            status: 状态 ('success' | 'failed')
            output_path: 输出文件路径
            error_message: 失败时的错误信息
            retry_count: 重试次数
        """
        finished_at = self._iso_millis(datetime.now())

        with sqlite3.connect(self.db_path) as conn:
            # 获取调用信息
            row = conn.execute(
                "SELECT call_type, model, resolution, duration_seconds, generate_audio, started_at FROM api_calls WHERE id = ?",
                (call_id,)
            ).fetchone()

            if not row:
                return

            call_type, model, resolution, duration_seconds, generate_audio, started_at = row

            # 计算耗时
            try:
                start = datetime.fromisoformat(started_at)
                end = datetime.fromisoformat(finished_at)
                duration_ms = int((end - start).total_seconds() * 1000)
            except (ValueError, TypeError):
                duration_ms = 0

            # 计算费用（失败记录费用为 0）
            cost_usd = 0.0
            if status == 'success':
                if call_type == 'image':
                    cost_usd = cost_calculator.calculate_image_cost(resolution or "1K", model=model)
                elif call_type == 'video':
                    cost_usd = cost_calculator.calculate_video_cost(
                        duration_seconds=duration_seconds or 8,
                        resolution=resolution or "1080p",
                        generate_audio=bool(generate_audio),
                        model=model,
                    )

            # 截断错误信息
            error_truncated = error_message[:500] if error_message else None

            # 更新记录
            conn.execute("""
                UPDATE api_calls
                SET status = ?, finished_at = ?, duration_ms = ?,
                    retry_count = ?, cost_usd = ?, output_path = ?, error_message = ?
                WHERE id = ?
            """, (
                status, finished_at, duration_ms,
                retry_count, cost_usd, output_path, error_truncated,
                call_id
            ))
            conn.commit()

    def get_stats(
        self,
        project_name: str = None,
        start_date: datetime = None,
        end_date: datetime = None,
    ) -> Dict[str, Any]:
        """
        获取统计摘要

        Args:
            project_name: 项目名称（可选，不传则统计全局）
            start_date: 开始日期（可选）
            end_date: 结束日期（可选）

        Returns:
            统计信息字典：
            - total_cost: 总费用
            - image_count: 图片调用次数
            - video_count: 视频调用次数
            - failed_count: 失败次数
            - total_count: 总调用次数
        """
        conditions = []
        params = []

        if project_name:
            conditions.append("project_name = ?")
            params.append(project_name)
        if start_date:
            start = self._date_start(start_date)
            conditions.append("started_at >= ?")
            params.append(self._iso_millis(start))
        if end_date:
            end_exclusive = self._date_end_exclusive(end_date)
            conditions.append("started_at < ?")
            params.append(self._iso_millis(end_exclusive))

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

        with sqlite3.connect(self.db_path) as conn:
            # 总费用
            row = conn.execute(
                f"SELECT COALESCE(SUM(cost_usd), 0) FROM api_calls {where_clause}",
                params
            ).fetchone()
            total_cost = row[0] if row else 0

            # 图片调用次数
            image_params = params + ["image"]
            image_where = f"{where_clause} {'AND' if conditions else 'WHERE'} call_type = ?"
            row = conn.execute(
                f"SELECT COUNT(*) FROM api_calls {image_where}",
                image_params
            ).fetchone()
            image_count = row[0] if row else 0

            # 视频调用次数
            video_params = params + ["video"]
            video_where = f"{where_clause} {'AND' if conditions else 'WHERE'} call_type = ?"
            row = conn.execute(
                f"SELECT COUNT(*) FROM api_calls {video_where}",
                video_params
            ).fetchone()
            video_count = row[0] if row else 0

            # 失败次数
            failed_params = params + ["failed"]
            failed_where = f"{where_clause} {'AND' if conditions else 'WHERE'} status = ?"
            row = conn.execute(
                f"SELECT COUNT(*) FROM api_calls {failed_where}",
                failed_params
            ).fetchone()
            failed_count = row[0] if row else 0

            # 总调用次数
            row = conn.execute(
                f"SELECT COUNT(*) FROM api_calls {where_clause}",
                params
            ).fetchone()
            total_count = row[0] if row else 0

        return {
            "total_cost": round(total_cost, 4),
            "image_count": image_count,
            "video_count": video_count,
            "failed_count": failed_count,
            "total_count": total_count,
        }

    def get_calls(
        self,
        project_name: str = None,
        call_type: str = None,
        status: str = None,
        start_date: datetime = None,
        end_date: datetime = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """
        获取调用记录列表（分页）

        Args:
            project_name: 项目名称（可选）
            call_type: 调用类型 ('image' | 'video')
            status: 状态 ('success' | 'failed')
            start_date: 开始日期
            end_date: 结束日期
            page: 页码（从 1 开始）
            page_size: 每页记录数

        Returns:
            {
                "items": [...],
                "total": 总记录数,
                "page": 当前页,
                "page_size": 每页大小
            }
        """
        conditions = []
        params = []

        if project_name:
            conditions.append("project_name = ?")
            params.append(project_name)
        if call_type:
            conditions.append("call_type = ?")
            params.append(call_type)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if start_date:
            start = self._date_start(start_date)
            conditions.append("started_at >= ?")
            params.append(self._iso_millis(start))
        if end_date:
            end_exclusive = self._date_end_exclusive(end_date)
            conditions.append("started_at < ?")
            params.append(self._iso_millis(end_exclusive))

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # 获取总数
            row = conn.execute(
                f"SELECT COUNT(*) FROM api_calls {where_clause}",
                params
            ).fetchone()
            total = row[0] if row else 0

            # 获取分页数据
            offset = (page - 1) * page_size
            rows = conn.execute(
                f"""
                SELECT id, project_name, call_type, model, prompt, resolution,
                       duration_seconds, aspect_ratio, generate_audio, status,
                       error_message, output_path, started_at, finished_at,
                       duration_ms, retry_count, cost_usd, created_at
                FROM api_calls {where_clause}
                ORDER BY started_at DESC
                LIMIT ? OFFSET ?
                """,
                params + [page_size, offset]
            ).fetchall()

            items = [dict(row) for row in rows]

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def get_projects_list(self) -> List[str]:
        """
        获取有调用记录的项目列表（用于筛选下拉框）

        Returns:
            项目名称列表
        """
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT project_name FROM api_calls ORDER BY project_name"
            ).fetchall()
            return [row[0] for row in rows]
