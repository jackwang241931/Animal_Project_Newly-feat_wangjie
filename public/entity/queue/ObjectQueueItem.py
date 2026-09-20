from datetime import datetime
from typing import Any


class ObjectQueueItem:
    def __init__(self,origin:str="",to:str="",title:str="",message:str="",data:Any=None,time:str=None):
        """

        :param origin: 消息源头进程
        :param to:  消息目的进程
        :param data: 消息数据
        :param time: 消息时间
        :param title: 消息标题
        :param message : 消息内容
        """
        self.origin:str=origin
        self.to:str=to
        self.title:str=title
        self.message:str=message
        self.data :Any=data
        self.time :str=time
    def __str__(self):
        return f"{{'origin':{self.origin},'to':{self.to},'title':{self.title},'message':{self.message},'data':{self.data},'time':{self.time}}}"
    def is_Empty(self) -> bool:
        return self.data is None and self.time is None and self.title is None  and self.origin == "" and self.to == "" and self.title == "" and self.message == ""

