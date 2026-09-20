import json


class json_util():
    def __init__(self):
        pass

    @classmethod
    def read_json_to_dict_list(cls, filename):
        with open(filename, 'r', encoding='utf-8-sig') as file:
            data = json.load(file)  # 读取 JSON 数据并解析为 Python 对象
        return data

    @classmethod
    def store_json_from_dict_list(cls, filename, data):
        with open(filename, 'w', encoding='utf-8-sig') as file:
            json.dump(data, file, indent=4)  # 保存 JSON 数据并解析为 Python 对象
