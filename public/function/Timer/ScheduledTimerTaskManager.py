import json
import threading

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Any, Optional, Union
from datetime import datetime, timedelta

import schedule
from loguru import logger
import signal
import sys
from enum import Enum


class TaskStatus(Enum):
    """任务状态枚举"""
    WAITING = "waiting"  # 等待首次执行
    RUNNING = "running"  # 正在执行
    INTERVAL_MODE = "interval"  # 间隔模式运行中
    COMPLETED = "completed"  # 已完成
    STOPPED = "stopped"  # 已停止
    ERROR = "error"  # 出错


class StopCondition:
    """停止条件类"""

    def __init__(self,
                 max_executions: Optional[int] = None,
                 max_duration: Optional[int] = None,  # 秒
                 stop_time: Optional[str] = None,  # "HH:MM" 格式
                 custom_condition: Optional[Callable] = None):
        """
        初始化停止条件

        Args:
            max_executions: 最大执行次数
            max_duration: 最大运行时长（秒）
            stop_time: 停止时间 "HH:MM" 格式
            custom_condition: 自定义停止条件函数，返回True时停止
        """
        self.max_executions = max_executions
        self.max_duration = max_duration
        self.stop_time = stop_time
        self.custom_condition = custom_condition

    def should_stop(self, execution_count: int, start_time: datetime, task_result: Any = None) -> bool:
        """检查是否应该停止"""
        # 检查执行次数
        if self.max_executions and execution_count >= self.max_executions:
            return True

        # 检查运行时长
        if self.max_duration:
            running_time = (datetime.now() - start_time).total_seconds()
            if running_time >= self.max_duration:
                return True

        # 检查停止时间
        if self.stop_time:
            now = datetime.now()
            stop_hour, stop_minute = map(int, self.stop_time.split(':'))
            stop_datetime = now.replace(hour=stop_hour, minute=stop_minute, second=0, microsecond=0)

            # 如果停止时间已过，并且是今天，则停止
            if now >= stop_datetime:
                return True

        # 检查自定义条件
        if self.custom_condition:
            try:
                return self.custom_condition(execution_count, start_time, task_result)
            except Exception as e:
                logger.error("自定义停止条件检查失败", error=str(e))
                return False

        return False


class AdvancedScheduledTaskManager:
    """
    高级定时任务管理器
    支持定时到间隔的动态调度转换、普通任务、以及定时转间隔功能
    """

    def __init__(self, max_workers: int = 5, log_level: str = "INFO", log_file: str = "task_manager.log"):
        """初始化任务管理器"""
        self.max_workers = max_workers
        self.thread_pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="TaskWorker")
        self.scheduler_thread = None
        self.is_running = False
        self.shutdown_event = threading.Event()
        self.tasks = {}  # 存储任务信息
        self.task_counter = 0
        self.lock = threading.Lock()

        # 配置 loguru 日志
        self._setup_logging(log_level, log_file)

        # 注册信号处理器
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        logger.info("高级任务管理器初始化完成", max_workers=max_workers)

    def _setup_logging(self, level: str, log_file: str):
        """设置 loguru 日志配置"""
        # logger.remove()

        # 控制台输出
        logger.add(
            sys.stdout,
            level=level,
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
                   "<level>{level: <8}</level> | "
                   "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
                   "<level>{message}</level>",
            colorize=True
        )

        # 文件输出
        logger.add(
            log_file,
            level=level,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
            rotation="10 MB",
            retention="7 days",
            compression="zip",
            encoding="utf-8"
        )

        # 错误日志
        logger.add(
            "error.log",
            level="ERROR",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
            rotation="10 MB",
            retention="30 days",
            compression="zip",
            encoding="utf-8"
        )

    def _signal_handler(self, signum, frame):
        """信号处理器"""
        logger.warning("接收到信号 {signum}，开始优雅关闭...", signum=signum)
        self.shutdown()
        # sys.exit(0)

    def add_scheduled_to_interval_task(self,
                                       func: Callable,
                                       initial_time: str,
                                       interval_seconds: int,
                                       stop_condition: StopCondition,
                                       task_name: Optional[str] = None,
                                       **kwargs) -> str:
        """
        添加定时到间隔的动态调度任务

        Args:
            func: 要执行的函数
            initial_time: 首次执行时间 "HH:MM" 格式
            interval_seconds: 后续间隔执行的秒数
            stop_condition: 停止条件
            task_name: 任务名称
            **kwargs: 传递给任务函数的参数

        Returns:
            str: 任务ID
        """
        with self.lock:
            self.task_counter += 1
            task_id = f"dynamic_task_{self.task_counter}"

        if task_name is None:
            task_name = f"{func.__name__}_{task_id}"

        # 任务状态跟踪
        task_state = {
            'execution_count': 0,
            'start_time': None,
            'status': TaskStatus.WAITING,
            'last_result': None,
            'interval_job': None,
            'should_stop': False
        }

        task_logger = logger.bind(task_id=task_id, task_name=task_name)

        def dynamic_task_wrapper():
            """动态任务包装器"""
            nonlocal task_state

            start_time = time.time()
            thread_name = threading.current_thread().name

            try:
                task_state['status'] = TaskStatus.RUNNING
                task_state['execution_count'] += 1

                # 记录首次执行开始时间
                if task_state['start_time'] is None:
                    task_state['start_time'] = datetime.now()

                task_logger.info(
                    "执行动态任务",
                    execution_count=task_state['execution_count'],
                    thread=thread_name,
                    status=task_state['status'].value
                )

                # 执行任务
                future = self.thread_pool.submit(func, **kwargs)
                result = future.result()
                task_state['last_result'] = result

                execution_time = time.time() - start_time
                task_logger.success(
                    "动态任务执行完成",
                    execution_time=f"{execution_time:.2f}s",
                    execution_count=task_state['execution_count'],
                    result=str(result)[:100] if result else None
                )

                # 首次执行后，切换到间隔模式
                if task_state['execution_count'] == 1:
                    self._switch_to_interval_mode(task_id, task_state, interval_seconds, dynamic_task_wrapper)

                # 检查停止条件
                if stop_condition.should_stop(
                        task_state['execution_count'],
                        task_state['start_time'],
                        result
                ):
                    task_logger.info("满足停止条件，任务即将停止")
                    self._stop_dynamic_task(task_id, task_state, "条件满足")

                return result

            except Exception as e:
                execution_time = time.time() - start_time
                task_state['status'] = TaskStatus.ERROR
                task_logger.error(
                    "动态任务执行失败",
                    error=str(e),
                    execution_time=f"{execution_time:.2f}s",
                    execution_count=task_state['execution_count'],
                    exc_info=True
                )
                return None

        # 创建首次执行的定时任务
        try:
            initial_job = schedule.every().day.at(initial_time).do(dynamic_task_wrapper)
        except Exception as e:
            logger.error("创建定时任务失败", task_name=task_name, error=str(e))
            raise

        # 存储任务信息
        task_info = {
            'id': task_id,
            'name': task_name,
            'func': func,
            'initial_job': initial_job,
            'initial_time': initial_time,
            'interval_seconds': interval_seconds,
            'stop_condition': stop_condition,
            'task_state': task_state,
            'created_at': datetime.now(),
            'kwargs': kwargs,
            'type': 'dynamic_scheduled_to_interval'
        }

        with self.lock:
            self.tasks[task_id] = task_info

        logger.info(
            "动态调度任务已添加",
            task_id=task_id,
            task_name=task_name,
            initial_time=initial_time,
            interval_seconds=interval_seconds
        )
        return task_id

    def _switch_to_interval_mode(self, task_id: str, task_state: dict, interval_seconds: int, task_func: Callable):
        """切换到间隔模式"""
        with self.lock:
            if task_id not in self.tasks:
                return

            task_info = self.tasks[task_id]

            # 取消原始的定时任务
            if task_info.get('initial_job'):
                schedule.cancel_job(task_info['initial_job'])
                task_info['initial_job'] = None

            # 创建间隔任务
            interval_job = schedule.every(interval_seconds).seconds.do(task_func)
            task_info['interval_job'] = interval_job
            task_state['status'] = TaskStatus.INTERVAL_MODE

        logger.info(
            "任务已切换到间隔模式",
            task_id=task_id,
            interval_seconds=interval_seconds,
            task_name=task_info['name']
        )

    def _stop_dynamic_task(self, task_id: str, task_state: dict, reason: str):
        """停止动态任务"""
        with self.lock:
            if task_id not in self.tasks:
                return

            task_info = self.tasks[task_id]

            # 取消所有相关的调度任务
            if task_info.get('initial_job'):
                schedule.cancel_job(task_info['initial_job'])
                task_info['initial_job'] = None

            if task_info.get('interval_job'):
                schedule.cancel_job(task_info['interval_job'])
                task_info['interval_job'] = None

            task_state['status'] = TaskStatus.COMPLETED
            task_state['should_stop'] = True

        logger.info(
            "动态任务已停止",
            task_id=task_id,
            task_name=task_info['name'],
            reason=reason,
            execution_count=task_state['execution_count']
        )

    def add_task(self,
                 func: Callable,
                 schedule_type: str,
                 interval: Union[int, str],
                 unit: str = 'seconds',
                 task_name: Optional[str] = None,
                 convert_to_interval: bool = False,
                 interval_after_first: int = 0,
                 interval_unit_after_first: str = 'seconds',
                 run_duration: int = 0,
                 run_duration_unit: str = 'seconds',
                 **kwargs) -> str:
        """
        添加定时任务，支持定时任务完成后自动转为间隔调度

        Args:
            func: 要执行的函数
            schedule_type: 调度类型 ('interval', 'at')
            interval: 时间间隔或时间点
            unit: 时间单位 ('seconds', 'minutes', 'hours', 'days')
            task_name: 任务名称
            convert_to_interval: 是否在首次执行后转为间隔调度
            interval_after_first: 转为间隔调度后的时间间隔
            interval_unit_after_first: 转为间隔调度后的时间单位
            run_duration: 间隔调度持续时间，到期后任务会被移除
            run_duration_unit: 间隔调度持续时间的单位
            **kwargs: 传递给任务函数的参数

        Returns:
            str: 任务ID
        """
        with self.lock:
            self.task_counter += 1
            task_id = f"task_{self.task_counter}"

        if task_name is None:
            task_name = f"{func.__name__}_{task_id}"

        task_logger = logger.bind(task_id=task_id, task_name=task_name)

        # 任务执行状态跟踪
        task_execution_state = {
            'execution_count': 0,
            'first_executed': False,
            'converted_to_interval': False
        }

        def wrapped_task():
            start_time = time.time()
            thread_name = threading.current_thread().name

            try:
                task_execution_state['execution_count'] += 1

                task_logger.info(
                    "开始执行任务",
                    thread=thread_name,
                    kwargs=kwargs,
                    execution_count=task_execution_state['execution_count']
                )

                future = self.thread_pool.submit(func, **kwargs)
                result = future.result()

                execution_time = time.time() - start_time
                task_logger.success(
                    "任务执行完成",
                    execution_time=f"{execution_time:.2f}s",
                    thread=thread_name,
                    result=str(result)[:100] if result else None,
                    execution_count=task_execution_state['execution_count']
                )

                # 如果需要转换为间隔调度，且是首次执行
                if (convert_to_interval and
                        not task_execution_state['first_executed'] and
                        not task_execution_state['converted_to_interval']):
                    task_execution_state['first_executed'] = True
                    self._convert_to_interval(
                        task_id,
                        interval_after_first,
                        interval_unit_after_first,
                        run_duration,
                        run_duration_unit,
                        wrapped_task
                    )
                    task_execution_state['converted_to_interval'] = True

                return result

            except Exception as e:
                execution_time = time.time() - start_time
                task_logger.error(
                    "任务执行失败",
                    error=str(e),
                    execution_time=f"{execution_time:.2f}s",
                    thread=thread_name,
                    execution_count=task_execution_state['execution_count'],
                    exc_info=True
                )
                return None

        # 设置调度
        try:
            if schedule_type == 'interval':
                job = self._schedule_interval(wrapped_task, interval, unit)
            elif schedule_type == 'at':
                job = self._schedule_at(wrapped_task, interval)
            else:
                raise ValueError(f"不支持的调度类型: {schedule_type}")
        except Exception as e:
            logger.error("添加任务失败", task_name=task_name, error=str(e))
            raise

        # 存储任务信息
        task_info = {
            'id': task_id,
            'name': task_name,
            'func': func,
            'job': job,
            'schedule_type': schedule_type,
            'interval': interval,
            'unit': unit,
            'created_at': datetime.now(),
            'kwargs': kwargs,
            'type': 'normal',
            'convert_to_interval': convert_to_interval,
            'interval_after_first': interval_after_first,
            'interval_unit_after_first': interval_unit_after_first,
            'run_duration': run_duration,
            'run_duration_unit': run_duration_unit,
            'execution_state': task_execution_state,
            'end_time': None
        }

        with self.lock:
            self.tasks[task_id] = task_info

        logger.info(
            "普通任务已添加",
            task_id=task_id,
            task_name=task_name,
            schedule_type=schedule_type,
            interval=interval,
            unit=unit,
            convert_to_interval=convert_to_interval
        )
        return task_id

    def _convert_to_interval(self,
                             task_id: str,
                             interval: int,
                             unit: str,
                             run_duration: int,
                             run_duration_unit: str,
                             wrapped_func: Callable):
        """
        将任务转换为间隔调度

        Args:
            task_id: 任务ID
            interval: 时间间隔
            unit: 时间单位
            run_duration: 间隔调度持续时间
            run_duration_unit: 间隔调度持续时间的单位
            wrapped_func: 包装后的任务函数
        """
        with self.lock:
            if task_id in self.tasks:
                task_info = self.tasks[task_id]

                # 取消原始任务
                if task_info.get('job'):
                    schedule.cancel_job(task_info['job'])

                # 重新调度为间隔任务
                new_job = self._schedule_interval(wrapped_func, interval, unit)
                task_info['job'] = new_job
                task_info['schedule_type'] = 'interval'
                task_info['interval'] = interval
                task_info['unit'] = unit

                # 添加任务持续时间
                if run_duration > 0:
                    duration_mapping = {
                        'seconds': timedelta(seconds=run_duration),
                        'minutes': timedelta(minutes=run_duration),
                        'hours': timedelta(hours=run_duration),
                        'days': timedelta(days=run_duration)
                    }

                    if run_duration_unit in duration_mapping:
                        end_time = datetime.now() + duration_mapping[run_duration_unit]
                        task_info['end_time'] = end_time

                        logger.info(
                            "任务已转换为间隔调度，将在指定时间后自动移除",
                            task_id=task_id,
                            task_name=task_info['name'],
                            interval=interval,
                            unit=unit,
                            end_time=end_time.strftime("%Y-%m-%d %H:%M:%S")
                        )
                    else:
                        logger.warning("不支持的时间单位", unit=run_duration_unit)
                        logger.info(
                            "任务已转换为间隔调度，将无限期运行",
                            task_id=task_id,
                            task_name=task_info['name'],
                            interval=interval,
                            unit=unit
                        )
                else:
                    logger.info(
                        "任务已转换为间隔调度，将无限期运行",
                        task_id=task_id,
                        task_name=task_info['name'],
                        interval=interval,
                        unit=unit
                    )
            else:
                logger.warning("尝试转换不存在的任务", task_id=task_id)

    def _schedule_interval(self, func: Callable, interval: int, unit: str):
        """设置间隔调度"""
        unit_mapping = {
            'seconds': schedule.every(interval).seconds,
            'minutes': schedule.every(interval).minutes,
            'hours': schedule.every(interval).hours,
            'days': schedule.every(interval).days
        }

        if unit not in unit_mapping:
            raise ValueError(f"不支持的时间单位: {unit}")

        return unit_mapping[unit].do(func)

    def _schedule_at(self, func: Callable, time_str: str):
        """设置定时调度"""
        return schedule.every().day.at(time_str).do(func)

    def remove_task(self, task_id: str) -> bool:
        """移除任务"""
        with self.lock:
            if task_id in self.tasks:
                task_info = self.tasks[task_id]

                # 取消不同类型的任务
                if task_info['type'] == 'dynamic_scheduled_to_interval':
                    if task_info.get('initial_job'):
                        schedule.cancel_job(task_info['initial_job'])
                    if task_info.get('interval_job'):
                        schedule.cancel_job(task_info['interval_job'])
                else:
                    if task_info.get('job'):
                        schedule.cancel_job(task_info['job'])

                del self.tasks[task_id]

                logger.info(
                    "任务已移除",
                    task_id=task_id,
                    task_name=task_info['name'],
                    task_type=task_info['type']
                )
                return True
            else:
                logger.warning("尝试移除不存在的任务", task_id=task_id)
                return False

    def get_task_statistics(self) -> Dict[str, Any]:
        """获取任务统计信息"""
        with self.lock:
            stats = {
                'total_tasks': len(self.tasks),
                'tasks_by_type': {},
                'tasks_by_status': {},
                'tasks_by_unit': {},
                'oldest_task': None,
                'newest_task': None,
                'total_executions': 0
            }

            if self.tasks:
                # 统计任务类型和状态
                for task in self.tasks.values():
                    task_type = task['type']
                    unit = task.get('unit', 'unknown')

                    stats['tasks_by_type'][task_type] = stats['tasks_by_type'].get(task_type, 0) + 1
                    stats['tasks_by_unit'][unit] = stats['tasks_by_unit'].get(unit, 0) + 1

                    # 统计执行次数
                    if task_type == 'dynamic_scheduled_to_interval':
                        execution_count = task['task_state']['execution_count']
                        status = task['task_state']['status'].value
                    else:
                        execution_count = task.get('execution_state', {}).get('execution_count', 0)
                        status = 'running' if execution_count > 0 else 'waiting'

                    stats['total_executions'] += execution_count
                    stats['tasks_by_status'][status] = stats['tasks_by_status'].get(status, 0) + 1

                # 查找最老和最新的任务
                sorted_tasks = sorted(self.tasks.values(), key=lambda x: x['created_at'])
                stats['oldest_task'] = {
                    'id': sorted_tasks[0]['id'],
                    'name': sorted_tasks[0]['name'],
                    'created_at': sorted_tasks[0]['created_at'].isoformat()
                }
                stats['newest_task'] = {
                    'id': sorted_tasks[-1]['id'],
                    'name': sorted_tasks[-1]['name'],
                    'created_at': sorted_tasks[-1]['created_at'].isoformat()}

            return stats
    def get_task_info(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务信息"""
        with self.lock:
            task_info = self.tasks.get(task_id, None)
            if task_info:
                # 创建副本并处理特殊对象
                info_copy = task_info.copy()

                # 处理动态任务的状态信息
                if task_info['type'] == 'dynamic_scheduled_to_interval':
                    state = task_info['task_state']
                    info_copy['execution_count'] = state['execution_count']
                    info_copy['status'] = state['status'].value
                    info_copy['start_time'] = state['start_time']
                    info_copy['last_result'] = state['last_result']
                elif task_info['type'] == 'normal':
                    execution_state = task_info.get('execution_state', {})
                    info_copy['execution_count'] = execution_state.get('execution_count', 0)
                    info_copy['first_executed'] = execution_state.get('first_executed', False)
                    info_copy['converted_to_interval'] = execution_state.get('converted_to_interval', False)

                return info_copy
            return None

    def list_tasks(self) -> Dict[str, Dict[str, Any]]:
        """列出所有任务"""
        with self.lock:
            result = {}
            for k, v in self.tasks.items():
                info_copy = v.copy()

                # 处理动态任务的状态信息
                if v['type'] == 'dynamic_scheduled_to_interval':
                    state = v['task_state']
                    info_copy['execution_count'] = state['execution_count']
                    info_copy['status'] = state['status'].value
                    info_copy['start_time'] = state['start_time']
                    info_copy['last_result'] = state['last_result']
                elif v['type'] == 'normal':
                    execution_state = v.get('execution_state', {})
                    info_copy['execution_count'] = execution_state.get('execution_count', 0)
                    info_copy['first_executed'] = execution_state.get('first_executed', False)
                    info_copy['converted_to_interval'] = execution_state.get('converted_to_interval', False)

                result[k] = info_copy

            return result

    def start(self):
        """启动任务管理器"""
        if self.is_running:
            logger.warning("任务管理器已经在运行")
            return

        self.is_running = True
        self.shutdown_event.clear()

        self.scheduler_thread = threading.Thread(
            target=self._run_scheduler,
            daemon=True,
            name="SchedulerThread"
        )
        self.scheduler_thread.start()

        logger.success("高级任务管理器已启动", thread_count=self.max_workers)

    def _run_scheduler(self):
        """调度器主循环"""
        logger.info("调度器线程已启动", thread_name=threading.current_thread().name)

        while not self.shutdown_event.is_set():
            try:
                schedule.run_pending()
                time.sleep(1)
            except Exception as e:
                logger.error("调度器运行出错", error=str(e), exc_info=True)
                time.sleep(5)

        logger.info("调度器线程已停止")

    def shutdown(self, timeout: float = 30):
        """优雅关闭任务管理器"""
        if not self.is_running:
            logger.warning("任务管理器未运行")
            return

        logger.info("开始关闭高级任务管理器...", timeout=timeout)

        self.shutdown_event.set()
        self.is_running = False

        if self.scheduler_thread and self.scheduler_thread.is_alive():
            logger.debug("等待调度器线程结束...")
            self.scheduler_thread.join(timeout=5)

        logger.debug("关闭线程池...")
        self.thread_pool.shutdown(wait=True)

        schedule.clear()
        task_count = len(self.tasks)
        with self.lock:
            self.tasks.clear()

        logger.success("高级任务管理器已关闭", cleared_tasks=task_count)

    def get_status(self) -> Dict[str, Any]:
        """获取管理器状态"""
        with self.lock:
            dynamic_tasks = sum(1 for task in self.tasks.values() if task['type'] == 'dynamic_scheduled_to_interval')
            normal_tasks = len(self.tasks) - dynamic_tasks

            status = {
                'is_running': self.is_running,
                'total_tasks': len(self.tasks),
                'dynamic_tasks': dynamic_tasks,
                'normal_tasks': normal_tasks,
                'max_workers': self.max_workers,
                'scheduler_thread_alive': self.scheduler_thread.is_alive() if self.scheduler_thread else False,
                'pending_jobs': len(schedule.jobs),
                'shutdown_requested': self.shutdown_event.is_set()
            }

        return status


# 使用示例
def sample_task(name: str, count: int = 1):
    """示例任务函数"""
    logger.info(f"执行示例任务{name}|{count}")
    time.sleep(2)
    return f"任务 {name} 完成，计数: {count}"


def data_processing_task():
    """数据处理任务示例"""
    logger.info("开始数据处理")
    # 模拟数据处理
    time.sleep(3)
    return {"processed_records": 100, "status": "success"}
def error_task():
    """会出错的任务,用于测试错误处理"""
    logger.info("执行会出错的任务")
    raise ValueError("这是一个测试错误")

def custom_stop_condition(execution_count: int, start_time: datetime, result: Any) -> bool:
    """自定义停止条件示例"""
    # 如果结果包含错误或处理的记录数少于50，则停止
    if isinstance(result, dict):
        if result.get("status") == "error" or result.get("processed_records", 0) < 50:
            return True
    return False


if __name__ == "__main__":
    # 创建高级任务管理器
    manager = AdvancedScheduledTaskManager(max_workers=3, log_level="DEBUG")

    try:
        # 启动管理器
        manager.start()

        # 添加普通任务
        normal_task_id = manager.add_task(
            func=sample_task,
            schedule_type='interval',
            interval=30,
            unit='seconds',
            task_name='普通周期任务',
            name='测试',
            count=5,
            convert_to_interval=True,
            interval_after_first=10,
            interval_unit_after_first='seconds',
            run_duration=300,
            run_duration_unit='seconds'
        )

        # 添加动态调度任务：12:00开始，然后每10秒执行一次，最多执行5次
        stop_condition = StopCondition(
            max_executions=5,
            max_duration=300,  # 5分钟
            # stop_time="18:00",
            # custom_condition=custom_stop_condition
        )

        # 注意：这里使用当前时间后2分钟作为示例,实际使用时设置为具体时间如"12:00"
        from datetime import datetime, timedelta

        start_time_dynamic = (datetime.now() + timedelta(minutes=2)).strftime("%H:%M")

        dynamic_task_id = manager.add_scheduled_to_interval_task(
            func=data_processing_task,
            initial_time=start_time_dynamic,
            interval_seconds=10,
            stop_condition=stop_condition,
            task_name='动态数据处理任务'
        )

        # 添加一个会出错的任务来测试错误处理
        error_task_id = manager.add_task(
            func=error_task,
            schedule_type='interval',
            interval=45,
            unit='seconds',
            task_name='错误测试任务'
        )

        logger.info("任务管理器运行中，按 Ctrl+C 退出...")
        logger.info(f"动态任务将在 {start_time_dynamic} 开始执行")

        # 主线程监控
        while True:
            status = manager.get_status()
            stats = manager.get_task_statistics()
            tasks = manager.list_tasks()

            # 使用 JSON 格式输出
            logger.info(f"系统状态: {json.dumps(status, indent=2, ensure_ascii=False, default=str)}")
            logger.info(f"任务统计: {json.dumps(stats, indent=2, ensure_ascii=False, default=str)}")

            # 显示各种任务状态
            for task_id, task_info in tasks.items():
                if task_info['type'] == 'dynamic_scheduled_to_interval':
                    dynamic_info = {
                        'task_id': task_id,
                        'name': task_info['name'],
                        'status': task_info.get('status', 'unknown'),
                        'execution_count': task_info.get('execution_count', 0),
                        'start_time': task_info.get('start_time')
                    }
                    logger.info(f"动态任务状态: {json.dumps(dynamic_info, indent=2, ensure_ascii=False, default=str)}")
                elif task_info['type'] == 'normal' and task_info.get('convert_to_interval'):
                    normal_info = {
                        'task_id': task_id,
                        'name': task_info['name'],
                        'execution_count': task_info.get('execution_count', 0),
                        'converted_to_interval': task_info.get('converted_to_interval', False),
                        'end_time': task_info.get('end_time')
                    }
                    logger.info(
                        f"定时转间隔任务状态: {json.dumps(normal_info, indent=2, ensure_ascii=False, default=str)}")

                time.sleep(20)

            time.sleep(20)
    except Exception as e:
        logger.error("程序运行出错", error=str(e), exc_info=True)
    finally:
        manager.shutdown()
        logger.success("程序已退出")