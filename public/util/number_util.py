import re
from typing import Counter

from loguru import logger


class number_util():
    def __init__(self):
        pass

    @classmethod
    def set_int_to_4_bytes_list(cls, value):
        if isinstance(value, str):
            value = int(value, 16)
        elif isinstance(value, int):
            value = int(f"{value:02X}", 16)
        # 将10进制数值转换成 4个字节的16进制数，位数不够前面补0  例如十进制数值10转换成[00 00 00 0A]
        datalist = re.findall(r'.{1,2}',
                              format(value,
                                     '08X')) if value != '' or value != 0 else re.findall(
            r'.{1,2}',
            format(int('0', 16), '08X'))  # 每 1~2 字符一组
        return datalist
        pass
    @classmethod
    def extract_numbers_with_patterns(cls,string_list, pattern_type='all'):
        """
        根据不同模式提取数字

        Args:
            string_list: 字符串列表
            pattern_type: 提取模式
                - 'all': 所有数字
                - 'integers': 只提取整数
                - 'decimals': 只提取小数
                - 'hex': 提取十六进制数字
                - 'first': 每个字符串中的第一个数字
                - 'last': 每个字符串中的最后一个数字

        Returns:
            tuple: (最频繁的数字, 出现次数, 所有数字列表)
        """
        # logger.debug(string_list)
        all_numbers = []

        for string in string_list:
            string = str(string)

            if pattern_type == 'all':
                # 提取所有数字（包括整数和小数）
                numbers = re.findall(r'\d+\.?\d*', string)
            elif pattern_type == 'integers':
                # 只提取整数
                numbers = re.findall(r'\b\d+\b', string)
            elif pattern_type == 'decimals':
                # 只提取小数
                numbers = re.findall(r'\d+\.\d+', string)
            elif pattern_type == 'hex':
                # 提取十六进制数字（如 0x1f, 0X04等）
                numbers = re.findall(r'0[xX][0-9a-fA-F]+', string)
            elif pattern_type == 'first':
                # 每个字符串中的第一个数字
                match = re.search(r'\d+\.?\d*', string)
                numbers = [match.group()] if match else []
            elif pattern_type == 'last':
                # 每个字符串中的最后一个数字
                matches = re.findall(r'\d+\.?\d*', string)
                numbers = [matches[-1]] if matches else []
            else:
                numbers = re.findall(r'\d+\.?\d*', string)

            all_numbers.extend(numbers)

        if not all_numbers:
            return None, 0, []

        counter = Counter(all_numbers)
        most_common = counter.most_common(1)[0]

        return most_common[0], most_common[1], all_numbers