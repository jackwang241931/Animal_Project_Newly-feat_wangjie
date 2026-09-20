import configparser
import traceback

from loguru import logger

#logger = logger.bind(category="gui_logger")
class ini_parser():
    """
    ini文件读取器
    """

    def __init__(self, file_path: str = None):
        """
        实例化函数
        :param file_path:读取文件地址
        """
        # 创建 ConfigParser 对象  保留名称大小写
        self.config = configparser.ConfigParser(interpolation=None)
        self.file_path = file_path
        # config是否已经读取数据
        self.is_read = False

    def is_exist(self, section: str = None, dict: str = None, value: str = None):
        """
        判断节值或配置值是否存在
        :param section:ini文件的节值
        :param dict:ini文件该节值的键值对的键
        :param value:ini文件该节值的键值对的值
        :return:是否存在 True or False
        """
        if self.is_read:

            # 只判断节是否存在
            if section in self.config and dict == None and value == None:
                return True
            if self.config.has_option(dict, value):
                return True
        else:
            logger.error(f"ini配置器还未读取数据，无法判断是否存在[{section}]{dict}={value}")
            return False

    def read(self, filepath: str = None):
        """
        获取ini文件的内容
        :param filepath (str) 文件地址
        :return 返回ini文件内容{"section":{"key1":value1,"key2":value2,....}，...}
        """
        if filepath == None and self.file_path == None:
            # 未设置文件地址参数
            logger.error("未设置ini读取器的文件地址参数file_path！")
            return None
        if filepath != None:
            # 覆盖参数
            self.file_path = filepath
        # 读取 INI 文件
        # try:
        #     self.config.read(self.file_path, encoding='utf-8')
        # except Exception as e:
        #     logger.error(Exception)
        #     return None
        try:
            self.config.read(self.file_path, encoding='utf-8-sig')
        except Exception as e:
            logger.error(f"读取{self.file_path}配置文件失败！失败原因：{e} |  异常堆栈跟踪：{traceback.print_exc()}")
            return None
        self.is_read = True
        # 获取所有节的名称
        sections = self.config.sections()
        # 返回ini文件内容{"section":{"key1":value1,"key2":value2,....}，...}
        data_section = {}
        for section in sections:
            data_section[section] = {}
            for key, value in self.config.items(section):
                data_section[section][key] = value
        return data_section
        pass

    def read_sections(self, filepath: str = None):
        """
        获取ini文件的节
        :param filepath (str) 文件地址
        :return 返回ini文件的节
        """
        if filepath == None and self.file_path == None:
            # 未设置文件地址参数
            logger.error("未设置ini读取器的文件地址参数file_path！")
            return None
        if filepath != None:
            # 覆盖参数
            self.file_path = filepath
        # 读取 INI 文件
        try:
            self.config.read(self.file_path, encoding='utf-8-sig')
        except Exception as e:
            logger.error(f"读取{self.file_path}配置文件失败！失败原因：{e} |  异常堆栈跟踪：{traceback.print_exc()}")
            return None
        self.is_read = True
        # 获取所有节的名称
        sections = self.config.sections()  # 输出: [section,....]
        return sections
        pass

    def set_file_path(self, filepath: str = ""):
        """
        设置读取器的文件地址
        :param filepath (str) 需要读取的文件地址
        :return:无返回
        """
        self.file_path = filepath
