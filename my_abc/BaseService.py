from abc import ABC, abstractmethod



class BaseService(ABC):

    def __init__(self):
        pass
    @abstractmethod
    def start(self,resolve,reject):
        """启动服务"""
        pass

    @abstractmethod
    def stop(self):
        """停止服务"""
        pass

