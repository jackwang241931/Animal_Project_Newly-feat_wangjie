import base64
import datetime
import importlib.util
import inspect
import os
import sqlite3
import sys
from dataclasses import is_dataclass, asdict
from pathlib import Path
from typing import Any


class class_util():
    def __init__(self):
        pass
    @classmethod
    def to_dict(cls,obj: Any, *, _seen=None, max_depth: int = 10) -> Any:
        """
        把常见对象递归转换为可 JSON 序列化（或易读）的 dict/list/primitive。
        支持：None, bool, int, float, str, datetime, bytes, dict, list/tuple/set,
               dataclass, namedtuple, sqlite3.Row, 普通对象（取 __dict__）。
        参数：
          - max_depth: 防止无限递归
        返回：
          - 如果是可映射的对象，返回 dict；如果是列表类返回 list；基本类型原样返回。
        注意：
          - 对于 bytes 使用 base64 编码字符串表示。
          - 对于自定义类会读取 obj.__dict__（如果存在），忽略以双下划线开头的私有属性。
          - 不能保证处理任意复杂循环引用；会在循环情况下返回 "<cycle>"。
        """
        if _seen is None:
            _seen = set()
        if max_depth <= 0:
            return repr(obj)

        # 基本类型直接返回
        if obj is None or isinstance(obj, (bool, int, float, str)):
            return obj

        # 避循环：用 id 识别对象
        oid = id(obj)
        if oid in _seen:
            return "<cycle>"
        _seen.add(oid)

        try:
            # datetime -> ISO
            if isinstance(obj, (datetime.datetime, datetime.date, datetime.time)):
                return obj.isoformat()

            # bytes -> base64 字符串
            if isinstance(obj, (bytes, bytearray)):
                return base64.b64encode(bytes(obj)).decode('ascii')

            # dict -> 递归处理 key/value（key 转为 str）
            if isinstance(obj, dict):
                return {str(k): cls.to_dict(v, _seen=_seen, max_depth=max_depth - 1) for k, v in obj.items()}

            # sqlite3.Row 支持 dict() 直接转换
            if isinstance(obj, sqlite3.Row):
                return {k: cls.to_dict(obj[k], _seen=_seen, max_depth=max_depth - 1) for k in obj.keys()}

            # dataclass -> asdict 然后递归
            if is_dataclass(obj):
                return cls.to_dict(asdict(obj), _seen=_seen, max_depth=max_depth - 1)

            # # namedtuple -> _asdict
            # if _is_namedtuple_instance(obj):
            #     return to_dict(obj._asdict(), _seen=_seen, max_depth=max_depth - 1)

            # list/tuple/set -> list
            if isinstance(obj, (list, tuple, set)):
                return [cls.to_dict(i, _seen=_seen, max_depth=max_depth - 1) for i in obj]

            # 普通对象：尽量使用 __dict__，否则使用 repr
            if hasattr(obj, "__dict__"):
                d = {}
                for k, v in vars(obj).items():
                    # 可根据需要跳过私有属性：如果 k.startswith('_'): continue
                    if k.startswith("__"):  # 跳过 Python 魔法属性
                        continue
                    d[k] = cls.to_dict(v, _seen=_seen, max_depth=max_depth - 1)
                return d

            # fallback: 尝试转换为 str
            return repr(obj)
        finally:
            _seen.discard(oid)
    @classmethod
    def get_public_attributes_with_notes(cls,obj):
        """
        备注类似于这个模板
        :param obj:类
        :return:
        """
        # 获取类的 __init__ 方法
        init_method = obj.__init__
        signature = inspect.signature(init_method)

        # 获取 __init__ 的文档字符串
        doc_string = init_method.__doc__

        # 提取参数备注
        param_notes = {}
        if doc_string:
            lines = doc_string.strip().splitlines()
            for line in lines:
                line:str
                if line.strip().startswith(":param"):
                    parts = line.split(":")
                    if len(parts) > 2:
                        flag_lines =  parts[1].split(" ")[1]
                        if len(flag_lines) > 1:
                            param_name = parts[1].split(" ")[1].strip()  # 获取参数名
                            param_note = parts[2].strip()  # 获取备注
                            param_notes[param_name] = param_note
                        else:
                            #  报错
                            raise ValueError

        # 获取类实例的公共属性及其值和备注
        public_attributes = {}
        for param in signature.parameters.keys():
            if not param.startswith('_'):
                value = getattr(obj, param)
                note = param_notes.get(param, "无备注")
                public_attributes[param] = {
                    'value': value,
                    'note': note
                }

        return public_attributes
    @classmethod
    def get_classes_from_directory(cls, directory_path="", mapping=""):
        """
        从指定目录的 Python 文件中提取所有类定义
        :param directory_path: Python 文件所在的目录路径
        :param mapping : 匹配字符
        :return: 字典 {文件名: [类名列表]}
        """
        classes_dict = {}
        all_classes = []
        # 获取目录下所有 Python 文件
        py_files = Path(directory_path).glob(f'*{mapping}*.py')

        for file_path in py_files:
            # 跳过 __init__.py 文件（可选）
            if file_path.name == '__init__.py':
                continue

            # 动态导入模块
            module_name = file_path.stem  # 文件名（不带后缀）作为模块名
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            module = importlib.util.module_from_spec(spec)

            try:
                # 执行模块代码
                spec.loader.exec_module(module)

                # 获取模块中的所有类
                module_classes = []
                for name, obj in inspect.getmembers(module):
                    # 筛选出类定义，且排除内部类和导入的类
                    if (inspect.isclass(obj) and
                            obj.__module__ == module_name and
                            not name.startswith('_') and mapping in name):
                        module_classes.append(name)
                        all_classes.append(name)
                classes_dict[file_path.name] = module_classes

            except Exception as e:
                # 导入失败的模块可加入错误信息
                error_msg = f"导入错误: {str(e)}"
                classes_dict[file_path.name] = [error_msg]

        return classes_dict, all_classes

    @classmethod
    def get_class_obj_from_modules_names(cls, path, mapping):
        """
                从指定目录的 Python 文件中提取所有类定义中得到类对象
                :param directory_path: Python 文件所在的目录路径
                :param mapping : 匹配字符
                :return: 字典 {文件名: [类名列表]}
         """
        classes_dict, all_classes = cls.get_classes_from_directory(path, mapping)
        classess_obj = []
        for dict in classes_dict:
            spec = importlib.util.spec_from_file_location(dict.rsplit('.', 1)[0], path + dict)
            module = importlib.util.module_from_spec(spec)
            sys.modules[dict.rsplit('.', 1)[0]] = module
            spec.loader.exec_module(module)
            class_single = getattr(module, [value for value in classes_dict[dict] if mapping in value][0])
            classess_obj.append(class_single)
        return classess_obj
        pass
