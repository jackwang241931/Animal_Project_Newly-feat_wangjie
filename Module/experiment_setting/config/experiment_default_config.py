"""
实验默认配置

尾项不要加逗号
16进制用双引号的字符串框起来
"""
import json

from public.function.Modbus.Modbus_Type import Modbus_Slave_Ids
from public.util.json_util import json_util


def get_default_config():
    ufc_config = f"""
    
        "{Modbus_Slave_Ids.UFC.value['name']}":{{
            "desc":"{Modbus_Slave_Ids.UFC.value['description']}",
            "address":{Modbus_Slave_Ids.UFC.value['address']},
            "config":[
                {{
                    "function_code":5,
                    "desc":"写单个开关量输出",
                    "value":[
                        {{
                            "desc":"气路电磁阀",
                            "refer_value":{{
                                "0":"闭合",
                                "1":"断开"
                            }}
                          
                            
                        }}
                    ]
                }}
            ]
        }},
    """
    ugc_config = f""
    zos_config = f""
    enm_config = f"""
        "{Modbus_Slave_Ids.ENM.value['name']}":{{
            "desc":"{Modbus_Slave_Ids.ENM.value['description']}",
            "address":{Modbus_Slave_Ids.ENM.value['address']},
            "config":[
                {{
                    "function_code":5,
                    "desc":"写单个开关量输出",
                    "value":[
                        {{
                            "desc":"跑轮刹车",
                            "refer_value":{{
                                "0":{{
                                    "desc":"启用",
                                    "value":["0x00","0x00","0xff","0x00"]
                                    }},
                                "1":{{
                                    "desc":"解除",
                                    "value":["0x00","0x00","0x00","0x00"]
                                    }}
                            }}
                        }},
                        {{
                            "desc":"光照",
                            "refer_value":{{
                                "0":{{
                                    "desc":"开",
                                    "value":["0x00","0x01","0xff","0x00"]
                                    }},
                                "1":{{
                                    "desc":"关",
                                    "value":["0x00","0x01","0x00","0x00"]
                                    }}
                            }}
                        }}
                    ]
                }},
                {{
                    "function_code":6,
                    "desc":"写单个保持寄存器",
                    "value":[
                        {{
                            "desc":"光照色温",
                            "refer_value":{{
                                "1":{{
                                    "desc":"1",
                                    "value":["0x00","0x01","0x00","0x01"]
                                    }},
                               "2":{{
                                    "desc":"2",
                                    "value":["0x00","0x01","0x00","0x02"]
                                    }},
                                "3":{{
                                    "desc":"3",
                                    "value":["0x00","0x01","0x00","0x03"]
                                    }},
                                "4":{{
                                    "desc":"4",
                                    "value":["0x00","0x01","0x00","0x04"]
                                    }},
                                "5":{{
                                    "desc":"5",
                                    "value":["0x00","0x01","0x00","0x05"]
                                    }},
                                "6":{{
                                    "desc":"6",
                                    "value":["0x00","0x01","0x00","0x06"]
                                    }},
                                "7":{{
                                    "desc":"7",
                                    "value":["0x00","0x01","0x00","0x07"]
                                    }},
                                "8":{{
                                    "desc":"8",
                                    "value":["0x00","0x01","0x00","0x08"]
                                    }},
                                "9":{{
                                    "desc":"9",
                                    "value":["0x00","0x01","0x00","0x09"]
                                    }}
                            }}
                        }},
                        {{
                            "desc":"光照亮度",
                            "refer_value":{{
                                "1":{{
                                    "desc":"1",
                                    "value":["0x00","0x02","0x00","0x01"]
                                    }},
                               "2":{{
                                    "desc":"2",
                                    "value":["0x00","0x02","0x00","0x02"]
                                    }},
                                "3":{{
                                    "desc":"3",
                                    "value":["0x00","0x02","0x00","0x03"]
                                    }},
                                "4":{{
                                    "desc":"4",
                                    "value":["0x00","0x02","0x00","0x04"]
                                    }},
                                "5":{{
                                    "desc":"5",
                                    "value":["0x00","0x02","0x00","0x05"]
                                    }},
                                "6":{{
                                    "desc":"6",
                                   "value":["0x00","0x02","0x00","0x06"]
                                    }},
                                "7":{{
                                    "desc":"7",
                                    "value":["0x00","0x02","0x00","0x07"]
                                    }},
                                "8":{{
                                    "desc":"8",
                                    "value":["0x00","0x02","0x00","0x08"]
                                    }},
                                "9":{{
                                    "desc":"9",
                                    "value":["0x00","0x02","0x00","0x09"]
                                    }}
                            }}
                        }}
                    ]
                }}
            ]
        }},
    """
    dwm_config = f"""
    
    """
    em_config = f"""
    
        "{Modbus_Slave_Ids.EM.value['name']}":{{
            "desc":"{Modbus_Slave_Ids.EM.value['description']}",
            "address":{Modbus_Slave_Ids.DWM.value['address']},
            "config":[
                {{
                    "function_code":5,
                    "desc":"写单个开关量输出",
                    "value":[
                        {{
                            "desc":"食槽",
                            "refer_value":{{
                                "0":{{
                                    "desc":"开",
                                    "value":["0x00","0x70","0x00","0x00"]
                                    }},
                                "1":{{
                                    "desc":"关",
                                    "value":["0x00","0x70","0xff","0x00"]
                                    }}
                            }}
                            
                        }}
                    ]
                }}
            ]
        }}
    """
    wm_config=f""
    return json.loads(f"{{ {ufc_config}{ugc_config}{zos_config}{enm_config}{dwm_config}{em_config}{wm_config} }}")
    # return f"{{ {ufc_config} }}"
if __name__ == "__main__":
    config = get_default_config()
    print(config)
    # 将 JSON 字符串解析为 Python 对象
    json_util.store_json_from_dict_list(filename="./test_experiment.json", data=config)
