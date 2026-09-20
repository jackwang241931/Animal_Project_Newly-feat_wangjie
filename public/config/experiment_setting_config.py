from enum import Enum


class Setting_Table(Enum):
    Group = {
       "table_name":"group",
        "desc":"通道/组",
       'column': [
            ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
            ("name", "名称", " TEXT "),
            ("create_time", "获取时间", " TIMESTAMP "),
            ("update_time", "获取时间", " TIMESTAMP "),
            ("is_selected", "是否启用", " BOOLEAN DEFAULT FALSE ")
        ],
        "foreign_key": None
    }

    Animal = {
        "table_name": "animals",
        "desc": "动物信息",
        'column': [
            ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
            ("id_write", "ID", " INTEGER "),
            ("name", "动物名称", " TEXT "),
            ("sex", "性别", " BOOLEAN "),
            ("weight", "重量", " REAL "),
            ("weight_unit", "重量单位", " TEXT "),
            ("note", "备注", " TEXT "),
            ("create_time", "获取时间", " TIMESTAMP "),
            ("update_time", "获取时间", " TIMESTAMP ")

        ],
        "foreign_key":None
    }

    Group_Animal= {
        "table_name": "group_animal",
        "desc": "组/通道-动物信息 关系表",
        'column': [
            ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
            ("aid", "动物序号", " INTEGER "),
            ("gid", "组/通道序号", " INTEGER "),
            ("note", "备注", " TEXT "),
            ("create_time", "获取时间", " TIMESTAMP "),
            ("update_time", "获取时间", " TIMESTAMP ")

        ],
        "foreign_key":{
            "key":['aid','gid'],
            "reference_key":[f"{Group['table_name']}(id)",f"{Animal['table_name']}(id)"],

        }
    }

    Pre_Experiment_Weight = {
        "table_name": "pre_experiment_weight",
        "desc": "实验前笼级体重",
        "column": [
            ("group_id", "笼号/通道序号", " INTEGER PRIMARY KEY "),
            ("weight_g", "实验前体重(g)", " REAL ")
        ],
        "foreign_key": None
    }

    Lighting_Schedule = {
        "table_name": "lighting_schedule",
        "desc": "实验光照时间表",
        "column": [
            ("id", "序号", " INTEGER PRIMARY KEY "),
            ("config_json", "光照时间表配置", " TEXT NOT NULL ")
        ],
        "foreign_key": None
    }

    Experiment_Metadata = {
        "table_name": "experiment_metadata",
        "desc": "实验级配置",
        "column": [
            ("id", "序号", " INTEGER PRIMARY KEY "),
            ("config_json", "实验级配置 JSON", " TEXT NOT NULL "),
        ],
        "foreign_key": None,
    }

    Channel_Config = {
        "table_name": "channel_config",
        "desc": "逐笼测量模块、采集参数和小鼠信息",
        "column": [
            ("channel_id", "笼号", " INTEGER PRIMARY KEY "),
            ("config_json", "逐笼配置 JSON", " TEXT NOT NULL "),
        ],
        "foreign_key": None,
    }
