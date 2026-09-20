import threading
import time


class ActionCompleteBarrier:
    """
    自定义Barrier类，确保action完全执行完毕后再唤醒所有等待的线程

    与标准threading.Barrier的区别：
    - 标准Barrier：最后一个线程到达时执行action，但所有线程同时被唤醒
    - 本类：确保action完全执行完毕后，其他线程才会从wait()返回

    使用场景：
    当需要在所有线程同步点执行某个关键操作（如初始化、清理等），
    并且必须确保该操作完成后其他线程才能继续执行时使用。

    Attributes:
        barrier (threading.Barrier): 内部使用的标准Barrier对象
        custom_action (callable): 用户自定义的action函数
        action_done (threading.Event): 用于标记action是否执行完成的事件
        parties (int): 参与同步的线程数量
    """

    def __init__(self, parties, action=None, timeout=None):
        """
        初始化ActionCompleteBarrier

        Args:
            parties (int): 参与同步的线程数量，必须大于0
            action (callable, optional): 当最后一个线程到达barrier时执行的函数
                                       该函数不应接受任何参数，默认为None
            timeout (float, optional): 默认的超时时间（秒），默认为None（无超时）

        Raises:
            ValueError: 当parties <= 0时抛出
            TypeError: 当action不是可调用对象时抛出
        """
        if parties <= 0:
            raise ValueError("parties must be > 0")

        if action is not None and not callable(action):
            raise TypeError("action must be callable")

        # 创建内部标准Barrier，不传入action（我们自己处理action的执行时机）
        self.barrier = threading.Barrier(parties, timeout=timeout)

        # 保存用户自定义的action函数
        self.custom_action = action

        # 创建事件对象，用于标记action是否执行完成
        # 当action执行完毕时，该事件会被设置（set()）
        self.action_done = threading.Event()

        # 保存线程数量，用于后续的属性访问
        self.parties = parties

    def wait(self, timeout=None):
        """
        等待所有线程到达barrier，并确保action完全执行后再返回

        执行流程：
        1. 调用内部barrier的wait()方法，等待所有线程到达
        2. 最后到达的线程（index=0）负责执行action
        3. 其他线程等待action执行完成的信号
        4. 所有线程在action完成后才从此方法返回

        Args:
            timeout (float, optional): 等待的超时时间（秒）
                                     如果为None，则使用初始化时设置的默认超时时间
                                     如果默认超时时间也为None，则无限等待

        Returns:
            int: 线程到达的顺序索引
                 0: 最后一个到达的线程（负责执行action）
                 1: 倒数第二个到达的线程
                 ...
                 parties-1: 第一个到达的线程

        Raises:
            threading.BrokenBarrierError: 当barrier被破坏时（如其他线程调用abort()）
            TimeoutError: 当等待超时时

        Note:
            返回值0的线程是最后到达的线程，它负责执行action
            其他线程会等待该线程完成action后才继续执行
        """
        try:
            # 调用内部barrier的wait方法，等待所有线程到达同步点
            # index表示当前线程到达的顺序：0是最后到达，parties-1是第一个到达
            index = self.barrier.wait(timeout)

            # 判断当前线程是否是最后一个到达的线程
            if index == 0 and self.custom_action:
                # 最后到达的线程负责执行action

                try:
                    # 执行用户自定义的action函数
                    self.custom_action()
                except Exception as e:
                    # 如果action执行出错，记录错误但仍然要设置完成标志
                    # 避免其他线程永远等待
                    print(f"[ActionCompleteBarrier] Action执行出错: {e}")
                finally:
                    # 无论action是否成功，都要通知其他线程可以继续
                    self.action_done.set()
            else:
                # 非最后到达的线程需要等待action执行完成


                # 等待action完成的信号
                # 如果在指定时间内action没有完成，wait()会返回False
                action_completed = self.action_done.wait(timeout)

                if not action_completed:
                    # 如果等待action完成超时，抛出超时错误
                    raise TimeoutError("等待action完成超时")

            return index

        except threading.BrokenBarrierError:
            # 如果barrier被破坏（如其他线程调用了abort()），直接重新抛出异常
            raise
        except Exception:
            # 如果发生其他异常，也直接重新抛出
            raise

    def reset(self):
        """
        重置barrier以便重复使用

        重置操作会：
        1. 重置内部的threading.Barrier对象
        2. 清除action完成标志，为下次使用做准备

        注意：
        - 如果有线程正在等待barrier，reset会导致这些线程收到BrokenBarrierError
        - 应该在确保没有线程在等待时调用此方法
        - 重置后可以重新使用这个barrier对象

        Raises:
            threading.BrokenBarrierError: 如果在有线程等待时调用reset
        """
        # 重置内部的标准barrier
        self.barrier.reset()

        # 清除action完成标志，为下次使用做准备
        self.action_done.clear()


    def abort(self):
        """
        中止barrier，使所有等待的线程收到BrokenBarrierError

        中止操作会：
        1. 中止内部的threading.Barrier，使等待的线程收到异常
        2. 设置action完成标志，避免线程在action_done.wait()处永远等待

        使用场景：
        - 在发生错误需要紧急停止所有等待线程时
        - 在程序关闭时清理资源

        注意：
        - 一旦调用abort，barrier就被永久破坏，不能再使用
        - 要重新使用需要创建新的barrier对象
        """
        # 中止内部barrier，这会使所有等待的线程收到BrokenBarrierError
        self.barrier.abort()

        # 设置action完成标志，避免线程在等待action完成时永远阻塞
        self.action_done.set()



    @property
    def broken(self):
        """
        检查barrier是否被破坏

        Returns:
            bool: True表示barrier已被破坏（通过abort()或异常），False表示正常

        当barrier被破坏时：
        - 任何试图调用wait()的线程都会立即收到BrokenBarrierError
        - 需要创建新的barrier对象才能继续使用
        """
        return self.barrier.broken

    @property
    def n_waiting(self):
        """
        获取当前正在等待barrier的线程数量

        Returns:
            int: 当前正在等待的线程数量（0到parties-1之间）

        用途：
        - 监控barrier的状态
        - 调试和诊断同步问题
        - 在某些情况下决定是否需要等待
        """
        return self.barrier.n_waiting
