import sys
import traceback


class AsyPromise:
    PENDING = 'pending'
    RESOLVED = 'resolved'
    REJECTED = 'rejected'

    def __init__(self, executor=None, reject_with=None, resolve_with=None, **kwargs):
        self._state = AsyPromise.PENDING
        self._value = None
        self._reason = None
        self._on_fulfilled = []
        self._on_rejected = []

        if reject_with is not None:
            self.reject(reject_with)
        elif resolve_with is not None:
            self.resolve(resolve_with)
        elif executor:
            try:
                executor(self.resolve, self.reject, **kwargs)
            except Exception as e:
                self.reject(e)

    @staticmethod
    def reject_immediately(reason):
        """创建一个直接 rejected 的 Promise"""
        return AsyPromise(reject_with=reason)

    @staticmethod
    def resolve_immediately(value):
        """创建一个直接 resolved 的 Promise"""
        return AsyPromise(resolve_with=value)

    @staticmethod
    def log_and_continue(error, logger=None, message_prefix="Error"):
        """
        记录错误（包含详细的traceback信息）并继续执行Promise链

        Args:
            error: 错误信息
            logger: 日志记录器，如果为None则使用print
            message_prefix: 错误消息前缀

        Returns:
            None: 让Promise链继续执行
        """
        # 获取详细的错误信息
        error_details = f"{message_prefix}: {error}"

        # 尝试多种方式获取错误的详细信息
        full_error_message = AsyPromise._format_error_message(error_details, error)

        if logger:
            logger.error(full_error_message)
        else:
            print(full_error_message)

        return None

    @staticmethod
    def log_and_reject(error, logger=None, message_prefix="Error"):
        """
        记录错误（包含详细的traceback信息）并重新抛出，中断Promise链

        Args:
            error: 错误信息
            logger: 日志记录器，如果为None则使用print
            message_prefix: 错误消息前缀

        Returns:
            AsyPromise: 一个rejected状态的Promise
        """
        # 获取详细的错误信息
        error_details = f"{message_prefix}: {error}"

        # 尝试多种方式获取错误的详细信息
        full_error_message = AsyPromise._format_error_message(error_details, error)

        if logger:
            logger.error(full_error_message)
        else:
            print(full_error_message)

        return AsyPromise.reject_immediately(error)

    @staticmethod
    def _format_error_message(error_details, error):
        """格式化错误信息，尝试获取尽可能详细的信息"""
        lines = [error_details]

        # 1. 获取错误的类型和值
        error_type = type(error).__name__
        error_value = str(error)
        lines.append(f"Error Type: {error_type}")
        lines.append(f"Error Value: {error_value}")

        # 2. 如果是Exception对象，尝试获取其traceback
        if isinstance(error, Exception):
            if hasattr(error, '__traceback__') and error.__traceback__:
                lines.append("Traceback:")
                tb_lines = traceback.format_tb(error.__traceback__)
                lines.extend(tb_lines)
                lines.append(f"{error_type}: {error_value}")
            else:
                lines.append("No traceback available")

        # 3. 尝试获取当前的调用栈（可能有用）
        current_stack = traceback.format_stack()[:-1]  # 排除当前函数
        if current_stack:
            lines.append("Current Stack:")
            lines.extend(current_stack[-5:])  # 只显示最后5层调用栈

        # 4. 如果error有特殊属性，也打印出来
        if hasattr(error, 'args') and error.args:
            lines.append(f"Error Args: {error.args}")

        # 5. 如果error有其他有用的属性
        if hasattr(error, '__dict__') and error.__dict__:
            lines.append(f"Error Attributes: {error.__dict__}")

        return "\n".join(lines)

    def then(self, on_fulfilled=None, on_rejected=None):
        def _wrap_on_fulfilled(value):
            if on_fulfilled:
                return on_fulfilled(value)
            return value

        def _wrap_on_rejected(reason):
            if on_rejected:
                return on_rejected(reason)
            # 确保 reason 是异常对象
            if isinstance(reason, BaseException):
                raise reason
            else:
                raise Exception(str(reason))

        if self._state == AsyPromise.RESOLVED:
            return AsyPromise(lambda resolve, reject: resolve(_wrap_on_fulfilled(self._value)))
        if self._state == AsyPromise.REJECTED:
            return AsyPromise(lambda resolve, reject: reject(_wrap_on_rejected(self._reason)))

        nxt = AsyPromise()
        self._on_fulfilled.append(lambda v: self._propagate(nxt, _wrap_on_fulfilled, v))
        self._on_rejected.append(lambda e: self._propagate(nxt, _wrap_on_rejected, e))
        return nxt

    def catch(self, on_rejected):
        return self.then(None, on_rejected)

    def _propagate(self, nxt, fn, arg):
        try:
            res = fn(arg)
            if isinstance(res, AsyPromise):
                if res._state == AsyPromise.PENDING:
                    res.then(nxt.resolve, nxt.reject)
                elif res._state == AsyPromise.RESOLVED:
                    nxt.resolve(res._value)
                elif res._state == AsyPromise.REJECTED:
                    nxt.reject(res._reason)
            else:
                nxt.resolve(res)
        except Exception as e:
            # 保存当前的traceback信息到异常对象中
            exc_type, exc_value, exc_traceback = sys.exc_info()
            if exc_traceback:
                e.__traceback__ = exc_traceback
            nxt.reject(e)
    def resolve(self, value=None):
        if self._state != AsyPromise.PENDING:
            return
        self._state = AsyPromise.RESOLVED
        self._value = value
        for cb in self._on_fulfilled:
            cb(value)

    def reject(self, reason=None):
        if self._state != AsyPromise.PENDING:
            return
        self._state = AsyPromise.REJECTED
        self._reason = reason
        for cb in self._on_rejected:
            cb(reason)


# 使用示例
def main1(result):
    print("成功:", result)
    # 在 then 中返回一个 rejected Promise
    return AsyPromise.reject_immediately("在then中被拒绝")


if __name__ == '__main__':
    # 方法1：直接创建 rejected Promise
    AsyPromise.reject_immediately("直接拒绝").catch(lambda e: print(f"捕获错误1: {e}"))

    # 方法2：使用构造函数参数
    AsyPromise(reject_with="构造函数拒绝").catch(lambda e: print(f"捕获错误2: {e}"))

    # 方法3：在 then 链中返回 rejected Promise
    AsyPromise.resolve_immediately("初始值") \
        .then(main1) \
        .catch(lambda e: print(f"捕获错误3: {e}"))