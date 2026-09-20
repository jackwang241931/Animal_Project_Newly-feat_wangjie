import queue
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from public.config_class.global_setting import global_setting


@dataclass
class DataItem:
    """存储数据项"""
    id: str
    data: Any
    result_queue: Optional[queue.Queue] = None
    timestamp: float = None

@dataclass
class StorageResult:
    """存储结果"""
    item_id: str
    success: bool
    error: Optional[str] = None
    timestamp: float = None
def store_data_with_result(data, need_result=False, timeout=5):
    """
    存储数据并可选择性获取结果

    Args:
        data: 要存储的数据
        need_result: 是否需要存储结果
        timeout: 等待结果的超时时间（秒）

    Returns:
        StorageResult对象（如果need_result=True）或None
    """
    # 生成唯一ID
    item_id = str(uuid.uuid4())

    # 创建结果队列（如果需要的话）
    result_queue = None
    if need_result:
        result_queue = queue.Queue()

        # 注册结果队列
        result_queues_lock_q = global_setting.get_setting("result_queues_lock")
        result_queues_q = global_setting.get_setting("result_queues")
        with result_queues_lock_q:
            result_queues_q[item_id] = result_queue

    # 创建数据项
    data_item = DataItem(
        id=item_id,
        data=data,
        result_queue=result_queue,
        timestamp=time.time()
    )

    # 放入存储队列
    lock_g = global_setting.get_setting("store_Q_lock", threading.Lock())
    storeQ_g = global_setting.get_setting("store_Q", queue.Queue())

    with lock_g:
        storeQ_g.put(data_item)

    # 如果需要结果，等待并返回
    if need_result and result_queue:
        try:
            result = result_queue.get(timeout=timeout)
            return result
        except queue.Empty:
            # 超时清理
            with result_queues_lock_q:
                if item_id in result_queues_q:
                    del result_queues_q[item_id]
            return StorageResult(item_id, False, f"等待存储结果超时({timeout}秒)")

    return None
