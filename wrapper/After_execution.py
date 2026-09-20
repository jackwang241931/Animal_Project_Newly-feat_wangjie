from functools import wraps


def after_execution(after_func):
    """装饰器：在函数执行后执行指定函数"""
    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            # 执行原函数
            result = func(self, *args, **kwargs)
            # 执行后续函数
            after_func(self, result)
            return result
        return wrapper
    return decorator