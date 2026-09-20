import multiprocessing as mp
import copy

from typing import List, Any


class ObjectQueue:
    def __init__(self, maxsize=0):
        self._queue = mp.Queue(maxsize)
        self._data_store = mp.Manager().list()
        self._lock = mp.Lock()

    def put(self, obj, block=True, timeout=None):
        """添加对象到队列"""
        # 验证对象是否可序列化
        if not self._is_serializable(obj):
            raise ValueError(f"对象 {type(obj)} 不可序列化")

        with self._lock:
            # 深拷贝对象以避免引用问题
            obj_copy = copy.deepcopy(obj)
            self._queue.put(obj_copy, block, timeout)
            self._data_store.append(obj_copy)

    def get(self, block=True, timeout=None):
        """获取并移除对象"""
        with self._lock:
            obj = self._queue.get(block, timeout)
            if self._data_store:
                self._data_store.pop(0)
            return obj

    def peek_all(self) -> List[Any]:
        """读取队列的所有对象而不出队"""
        with self._lock:
            return [copy.deepcopy(obj) for obj in self._data_store]

    def peek_range(self, start=0, end=None) -> List[Any]:
        """读取队列指定范围的对象"""
        with self._lock:
            return [copy.deepcopy(obj) for obj in self._data_store[start:end]]

    def peek_last(self, n=1) -> List[Any]:
        """读取队列最后n个对象"""
        with self._lock:
            return [copy.deepcopy(obj) for obj in self._data_store[-n:]] if n <= len(self._data_store) else [
                copy.deepcopy(obj) for obj in self._data_store]

    def find_objects_by_attribute(self, attr_name, attr_value) -> List[Any]:
        """根据对象属性查找对象"""
        with self._lock:
            result = []
            for obj in self._data_store:
                if hasattr(obj, attr_name) and getattr(obj, attr_name) == attr_value:
                    result.append(copy.deepcopy(obj))
            return result

    def find_objects_by_condition(self, condition_func) -> List[Any]:
        """根据条件函数查找对象"""
        with self._lock:
            result = []
            for obj in self._data_store:
                try:
                    if condition_func(obj):
                        result.append(copy.deepcopy(obj))
                except:
                    continue
            return result

    def update_object_by_index(self, index, new_obj):
        """根据索引更新对象"""
        if not self._is_serializable(new_obj):
            raise ValueError(f"对象 {type(new_obj)} 不可序列化")

        with self._lock:
            if 0 <= index < len(self._data_store):
                self._data_store[index] = copy.deepcopy(new_obj)
                return True
            return False

    def size(self) -> int:
        """获取队列大小"""
        with self._lock:
            return len(self._data_store)

    def empty(self) -> bool:
        """检查队列是否为空"""
        return self.size() == 0

    def clear(self):
        """清空队列"""
        with self._lock:
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except:
                    break
            self._data_store.clear()

    def _is_serializable(self, obj) -> bool:
        """检查对象是否可序列化"""
        try:
            import pickle
            pickle.dumps(obj)
            return True
        except:
            return False