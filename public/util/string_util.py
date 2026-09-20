class String_util:
    def __init__(self):
        pass
    @classmethod
    def array_to_binary_string(cls,arr):
        """
        将数组转换为二进制字符串表示
        字符串长度由数组中的最大数字决定

        参数:
        arr: 输入数组

        返回:
        二进制字符串，存在的数字位为'1'，不存在的为'0'
        """
        if not arr:  # 处理空数组
            return ''

        max_num = max(arr)  # 获取数组中的最大数字

        # 创建一个长度为max_num的字符串，初始全为'0'
        result = ['0'] * max_num

        # 将数组中存在的数字对应位置设为'1'
        for num in arr:
            if 1 <= num <= max_num:  # 确保数字在有效范围内
                result[num - 1] = '1'  # 数字1对应索引0，数字2对应索引1，以此类推

        return ''.join(result)
if __name__ == '__main__':
    # 测试示例
    arr1 = [1, 2, 3, 4, 5, 6, 7, 8, 9]
    arr2 = [1, 3, 4, 5, 7, 9]
    arr3 = [2, 4, 6]  # 额外测试用例，最大数字为6

    print(f"数组 {arr1} 转换结果: {String_util.array_to_binary_string(arr1)}")
    print(f"数组 {arr2} 转换结果: {String_util.array_to_binary_string(arr2)}")
    print(f"数组 {arr3} 转换结果: {String_util.array_to_binary_string(arr3)}")