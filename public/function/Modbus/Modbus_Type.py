import itertools
from enum import Enum

from public.entity.send_message import Send_Message
from public.util.number_util import number_util
class Others_Tables(Enum):
    """
    其他数据项的数据库文件
    """
    # 轨迹相机存储值
    Detection_Results_Data = {
        "data": {
            'function_code': 0,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("image_name", "图像名称", " TEXT "),
                ("is_exist_depth_image", "深度图像是否存在", " INTEGER "),
                ("median_x", "中位数X", " REAL "),
                ("median_y", "中位数Y", " REAL "),
                ("median_z", "中位数Z", " REAL "),
                ("center_x", "中心X", " REAL "),
                ("center_y", "中心Y", " REAL "),
                ("center_z", "中心Z", " REAL "),
                ("time", "获取时间", " TIMESTAMP DEFAULT CURRENT_TIMESTAMP ")
            ],
        }
    }
    # # 深度相机存储值
    # Mouse_deep_position_Data = {
    #     "data": {
    #         'function_code': 0,
    #         'column': [
    #             ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
    #             ("recognize_time", "识别时间", " TIMESTAMP "),
    #             ("x", "x轴", "  REAL "),
    #             ("y", "y轴", "  REAL "),
    #             ("z", "z轴", "  REAL "),
    #             ("time", "获取时间", " TIMESTAMP ")
    #         ],
    #     }
    # }
    # 红外相机存储值
    Mouse_infrared_Data = {
        "data": {
            'function_code': 0,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("recognize_time", "识别时间", " TIMESTAMP "),
                ("tmp_hs_max", "最大值温度(摄氏度)", "  REAL "),
                ("time", "获取时间", " TIMESTAMP ")
            ],
        }
    }
    Zero_Carlibration_Data={
        "data": {
            'function_code': 0,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("oxygen_calibration_zero_value", "氧浓度0点校准值", " REAL "),
                ("zos_pressure_calibration_zero_value", "ZOS压力0点校准值", " REAL "),
                ("remarks", "备注", " TEXT "),
                ("time", "获取时间", " TIMESTAMP ")
            ],
        }
    }
    SPan_Carlibration_Data={
        "data": {
            'function_code': 0,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("oxygen_calibration_span_value", "氧浓传感器span数值", " REAL "),
                ("zos_pressure_calibration_span_value", "ZOS压力span数值", " REAL "),
                ("carbon_calibration_span_value", "二氧化碳浓传感器span数值", " REAL "),
                ("remarks", "备注", " TEXT "),
                ("time", "获取时间", " TIMESTAMP ")
            ],
        }
    }
    # 每一轮的数据表
    Epoch_Data={
        "data": {
            'function_code': 0,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("mouse_cage_number", "鼠笼号", " INTEGER "),
                ("oxygen_calibration_zero_value", "氧浓度0点校准值", " REAL "),
                ("zos_pressure_calibration_zero_value", "ZOS压力0点校准值", " REAL "),
                ("oxygen_calibration_span_value", "氧浓传感器span数值", " REAL "),
                ("zos_pressure_calibration_span_value", "ZOS压力span数值", " REAL "),
                ("carbon_calibration_span_value", "二氧化碳浓传感器span数值", " REAL "),
                ("mouse_cage_infrared_temp", "鼠笼红外温度(°C)", " REAL "),
                ("UFC_flow_num", "ufc_流量计测量值(sccm)", " INTEGER "),
                ("reference_flow_num", "ufc_参考气流量计测量值(sccm)", " INTEGER "),
                ("UGC_flow_num_1", "传感器状态", " TEXT "),
                ("UGC_air_pressure", "拟合后CO2", " REAL "),
                ("UGC_CO2_origin_num", "补偿前CO2", " REAL "),
                ("UGC_CO2_num", "气压补偿后CO2", " REAL "),
                ("reference_CO2_num", "参考气CO2(%)", " INTEGER "),
                ("CO2_output_num", "CO2生产量(%)", " REAL "),
                ("ZOS_oxygen_partial_pressure", "氧分压(hPa)", " REAL "),
                ("ZOS_temperature_num", "ZOS温度测量值(°C)", " REAL "),
                ("ZOS_gas_pressure", "气体压力(hPa)", " REAL "),
                ("ZOS_oxygen_num", "氧浓度(%)", " REAL "),
                ("ZOS_dry_basis_oxygen_num", "干基氧浓度(%)", " REAL "),
                ("ZOS_fault_code", "ZOS故障码", " INTEGER "),
                ("ZOS_oxygen_temperature_2_num", "ZOS温度2测量值(°C)", " REAL "),
                ("ZOS_oxygen_humidity_num", "ZOS湿度测量值(%RH)", " REAL "),
                ("reference_oxygen_num", "参考气氧浓度(%)", " INTEGER "),
                ("reference_dry_basis_oxygen_num", "参考气干基氧浓度(%)", " REAL "),
                ("oxygen_consumption_num", "耗氧量(%)", " REAL "),
                ("vo2_consumption", "VO2氧耗量(L/min)", " REAL "),
                ("vco2_production", "VCO2二氧化碳产生量(L/min)", " REAL "),
                ("rer_rq", "RER呼吸熵", " REAL "),
                ("ee_energy_expenditure", "EE能量消耗(kcal/hr)", " REAL "),
                ("ENM_temperature_num", "温度测量值(°C)", " REAL "),
                ("ENM_humidity_num", "湿度测量值(%RH)", " REAL "),
                ("ENM_noise_num", "噪声测量值(dB)", " REAL "),
                ("ENM_barometer_num", "大气压测量值(KPa)", " REAL "),
                ("ENM_running_wheel_num", "当前计量周期内跑轮圈数测量值", " REAL "),
                ("DWM_weight_num", "食物重量测量值(g)", " REAL "),
                ("EM_weight_num", "饮水重量测量值(g)", " REAL "),
                ("WM_weight_num", "称重重量测量值(g)", " TEXT "),
                ("epoch_start_time", "轮次开始时间", " TIMESTAMP "),
                ("epoch_end_time", "轮次结束时间", " TIMESTAMP "),
                ("remarks", "备注", " TEXT "),
                ("time", "获取时间", " TIMESTAMP ")
            ]
        }
    }
class Modbus_Slave_Tables(Enum):
    """
    数据库文件
    """
    # 02 04
    UFC_monitor_data={
            "monitor_data": {
                    'function_code':4,
                    'column': [
                            ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                            ("flow_num", "流量计测量值(sccm)", " INTEGER "),
                            ("reserve_high_num_1", "备用1高字节", " TEXT "),
                            ("reserve_low_num_1", "备用1低字节", " TEXT "),
                            ("reserve_high_num_2", "备用2高字节", " TEXT "),
                            ("reserve_low_num_2", "备用2低字节", " TEXT "),
                            ("remarks", "备注", " TEXT "),
                            ("time", "获取时间", " TIMESTAMP ")
                                ],
            }
    }
    UFC_out_port_state={
            "out_port_state": {
                'function_code': 1,
                'column': [
                    ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                    ("reference_gas", "参考气", " BOOLEAN "),
                    ("delivery_valve", "输出阀", " BOOLEAN "),
                    ("magnetic_valve_cage_1", "鼠笼1的电磁阀", " BOOLEAN "),
                    ("magnetic_valve_cage_2", "鼠笼2的电磁阀", " BOOLEAN "),
                    ("magnetic_valve_cage_3", "鼠笼3的电磁阀", " BOOLEAN "),
                    ("magnetic_valve_cage_4", "鼠笼4的电磁阀", " BOOLEAN "),
                    ("magnetic_valve_cage_5", "鼠笼5的电磁阀", " BOOLEAN "),
                    ("magnetic_valve_cage_6", "鼠笼6的电磁阀", " BOOLEAN "),
                    ("magnetic_valve_cage_7", "鼠笼7的电磁阀", " BOOLEAN "),
                    ("magnetic_valve_cage_8", "鼠笼8的电磁阀", " BOOLEAN "),
                    ("time", "获取时间", " TIMESTAMP ")
                ]
            }
    }
    UFC_sensor_status = {
            "sensor_status": {
                'function_code': 2,
                'column': [
                    ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                    ("flow", "流量", " BOOLEAN "),
                    ("diff_pressure", "差压", " BOOLEAN "),
                    ("barometer_1", "气压1", " BOOLEAN "),
                    ("barometer_2", "气压2", " BOOLEAN "),
                    ("reserve_1", "备用1", " BOOLEAN "),
                    ("reserve_2", "备用2", " BOOLEAN "),
                    ("time", "获取时间", " TIMESTAMP ")
                ]
            }
    }
    UFC_sensor_config = {
            "sensor_config": {
                'function_code': 3,
                'column': [
                    ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                    ("flow_config", "流量计配置流量", " TEXT "),
                    ("valve_opening_1", "调节阀1开度", " TEXT "),
                    ("valve_opening_2", "调节阀2开度", " TEXT "),
                    ("time", "获取时间", " TIMESTAMP ")
                ]
            }
    }
    UFC_module_information = {
            "module_information": {
                'function_code': 17,
                'column': [
                    ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                    ("manufacturer", "生产厂商", " TEXT "),
                    ("hardware_version", "硬件版本", " TEXT "),
                    ("software_version", "软件版本", " TEXT "),
                    ("factory_address", "出厂地址", " TEXT "),
                    ("current_address", "当前地址", " TEXT "),
                    ("reserve_1", "预留1", " TEXT "),
                    ("reserve_2", "预留2", " TEXT "),
                    ("reserve_3", "预留3", " TEXT "),
                    ("time", "获取时间", " TIMESTAMP ")
                ]
            }
        }
    #03 04
    UGC_monitor_data={
        "monitor_data": {
            'function_code': 4,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("flow_num_1", "传感器状态", " TEXT "),
                ('air_pressure','气压(KPa)'," REAL "),
                ("CO2_num", "CO2(%)", " REAL "),
                ("CO2_origin_num", "补偿前CO2(%)", " REAL "),
                ("CO2_output_num", "CO2生产量(%)", " REAL "),
                ("remarks", "备注", " TEXT "),
                ("time", "获取时间", " TIMESTAMP ")
            ]
        }
    }
    UGC_out_port_state={
        "out_port_state": {
            'function_code': 1,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("reserve_7", "预留7", " BOOLEAN "),
                ("reserve_6", "预留6", " BOOLEAN "),
                ("reserve_5", "预留5", " BOOLEAN "),
                ("reserve_4", "预留4", " BOOLEAN "),
                ("reserve_3", "预留3", " BOOLEAN "),
                ("reserve_2", "预留2", " BOOLEAN "),
                ("reserve_1", "预留1", " BOOLEAN "),
                ("control_valve_4", "调节阀4", " BOOLEAN "),
                ("control_valve_3", "调节阀3", " BOOLEAN "),
                ("control_valve_2", "调节阀2", " BOOLEAN "),
                ("control_valve_1", "调节阀1", " BOOLEAN "),
                ("valve_5_5", "五选一阀5", " BOOLEAN "),
                ("valve_5_4", "五选一阀4", " BOOLEAN "),
                ("valve_5_3", "五选一阀3", " BOOLEAN "),
                ("valve_5_2", "五选一阀2", " BOOLEAN "),
                ("valve_5_1", "五选一阀1", " BOOLEAN "),
                ("time", "获取时间", " TIMESTAMP ")
            ]
        }
    }
    UGC_sensor_status={
        "sensor_status": {
            'function_code': 2,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("reserve_7", "预留7", " BOOLEAN "),
                ("reserve_6", "预留6", " BOOLEAN "),
                ("reserve_5", "预留5", " BOOLEAN "),
                ("reserve_4", "预留4", " BOOLEAN "),
                ("reserve_3", "预留3", " BOOLEAN "),
                ("reserve_2", "预留2", " BOOLEAN "),
                ("reserve_1", "预留1", " BOOLEAN "),
                ("CO2", "CO2", " BOOLEAN "),
                ("air_pressure_2", "气压2", " BOOLEAN "),
                ("air_pressure_1", "气压1", " BOOLEAN "),
                ("humidity_2", "湿度2", " BOOLEAN "),
                ("humidity_1", "湿度1", " BOOLEAN "),
                ("temperature_2", "温度2", " BOOLEAN "),
                ("temperature_1", "温度1", " BOOLEAN "),
                ("flow_2", "流量2", " BOOLEAN "),
                ("flow_1", "流量1", " BOOLEAN "),
                ("time", "获取时间", " TIMESTAMP ")
            ]
        }
    }
    UGC_sensor_config={
        "sensor_config": {
            'function_code': 3,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("valve_opening_1", "调节阀1开度", " TEXT "),
                ("valve_opening_2", "调节阀2开度", " TEXT "),
                ("valve_opening_3", "调节阀3开度", " TEXT "),
                ("valve_opening_4", "调节阀4开度", " TEXT "),
                ("time", "获取时间", " TIMESTAMP ")
            ]
        }
    }
    UGC_module_information={
        "module_information": {
            'function_code': 17,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("manufacturer", "生产厂商", " TEXT "),
                ("hardware_version", "硬件版本", " TEXT "),
                ("software_version", "软件版本", " TEXT "),
                ("factory_address", "出厂地址", " TEXT "),
                ("current_address", "当前地址", " TEXT "),
                ("reserve_1", "预留1", " TEXT "),
                ("reserve_2", "预留2", " TEXT "),
                ("reserve_3", "预留3", " TEXT "),
                ("time", "获取时间", " TIMESTAMP ")
            ]
        }
    }
    #04 04
    ZOS_monitor_data={
        "monitor_data": {
            'function_code': 4,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("oxygen_partial_pressure", "氧分压(hPa)", " REAL "),
                ("zos_temperature_num", "ZOS温度测量值(°C)", " REAL "),
                ("gas_pressure", "气体压力(hPa)", " REAL "),
                ("oxygen_num", "氧浓度(%)", " REAL "),
                ("dry_basis_oxygen_num", "干基氧浓度(%)", " REAL "),
                ("fault_code", "ZOS故障码", " INTEGER "),
                ("oxygen_temperature_2_num", "ZOS温度2测量值(°C)", " REAL "),
                ("oxygen_humidity_num", "ZOS湿度测量值(%RH)", " REAL "),
                ("remarks", "备注", " TEXT "),
                ("time", "获取时间", " TIMESTAMP ")
            ]
        }
    }
    ZOS_out_port_state={
        "out_port_state": {
            'function_code': 1,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("control_oxygen_sensor_relay", "控制氧传感器通断电继电器", " BOOLEAN "),
                ("time", "获取时间", " TIMESTAMP ")
            ]
        }
    }
    ZOS_sensor_status={
        "sensor_status": {
            'function_code': 2,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("oxygen", "氧传感器状态", " BOOLEAN "),
                ("temperature", "温度传感器状态", " BOOLEAN "),
                ("time", "获取时间", " TIMESTAMP ")
            ]
        }
    }
    ZOS_sensor_config={
        "sensor_config": {
            'function_code': 3,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("oxygen_sensor_reserve", "氧传感器配置（预留）", " TEXT "),
                ("time", "获取时间", " TIMESTAMP ")
            ]
        }
    }
    ZOS_module_information={
        "module_information": {
            'function_code': 17,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("manufacturer", "生产厂商", " TEXT "),
                ("hardware_version", "硬件版本", " TEXT "),
                ("software_version", "软件版本", " TEXT "),
                ("factory_address", "出厂地址", " TEXT "),
                ("current_address", "当前地址", " TEXT "),
                ("reserve_1", "预留1", " TEXT "),
                ("reserve_2", "预留2", " TEXT "),
                ("reserve_3", "预留3", " TEXT "),
                ("time", "获取时间", " TIMESTAMP ")
            ]
        }
    }
    #11 04
    ENM_monitor_data={
        "monitor_data": {
            'function_code': 4,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("temperature_num", "温度测量值(°C)", " REAL "),
                ("humidity_num", "湿度测量值(%RH)", " REAL "),
                ("noise_num", "噪声测量值(dB)", " REAL "),
                ("barometer_num", "大气压测量值(KPa)", " REAL "),
                ("running_wheel_num", "当前计量周期内跑轮圈数测量值", " REAL "),
                ("remarks", "备注", " TEXT "),
                ("time", "获取时间", " TIMESTAMP ")
            ]
        }
    }
    ENM_out_port_state={
        "out_port_state": {
            'function_code': 1,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("running_wheel_brake", "跑轮刹车", " BOOLEAN "),
                ("light_control", "光照控制", " BOOLEAN "),
                ("time", "获取时间", " TIMESTAMP ")
            ]
        }
    }
    ENM_sensor_status={
        "sensor_status": {
            'function_code': 2,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("temperature", "温度传感器", " BOOLEAN "),
                ("barometer", "气压传感器", " BOOLEAN "),
                ("noise", "噪声传感器", " BOOLEAN "),
                ("time", "获取时间", " TIMESTAMP ")
            ]
        }
    }
    ENM_sensor_config={
        "sensor_config": {
            'function_code': 3,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("light_luminance", "光照亮度", " TEXT "),
                ("light_color_temp", "光照色温", " TEXT "),
                ("module_address", "模块地址", " TEXT "),
                ("time", "获取时间", " TIMESTAMP ")
            ]
        }
    }
    ENM_module_information={
        "module_information": {
            'function_code': 17,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("manufacturer", "生产厂商", " TEXT "),
                ("hardware_version", "硬件版本", " TEXT "),
                ("software_version", "软件版本", " TEXT "),
                ("factory_address", "出厂地址", " TEXT "),
                ("current_address", "当前地址", " TEXT "),
                ("reserve_1", "预留1", " TEXT "),
                ("reserve_2", "预留2", " TEXT "),
                ("reserve_3", "预留3", " TEXT "),
                ("time", "获取时间", " TIMESTAMP ")
            ]
        }
    }
    #12 04
    DWM_monitor_data={
        "monitor_data": {
            'function_code': 4,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("weight_num", "重量测量值(g)", " TEXT "),
                ("remarks", "备注", " TEXT "),
                ("time", "获取时间", " TIMESTAMP ")
            ]
        }
    }
    DWM_sensor_status={
        "sensor_status": {
            'function_code': 2,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("weight", "重量传感器状态", " BOOLEAN "),
                ("time", "获取时间", " TIMESTAMP ")
            ]
        }
    }
    DWM_module_information={
        "module_information": {
            'function_code': 17,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("manufacturer", "生产厂商", " TEXT "),
                ("hardware_version", "硬件版本", " TEXT "),
                ("software_version", "软件版本", " TEXT "),
                ("factory_address", "出厂地址", " TEXT "),
                ("current_address", "当前地址", " TEXT "),
                ("reserve_1", "预留1", " TEXT "),
                ("reserve_2", "预留2", " TEXT "),
                ("reserve_3", "预留3", " TEXT "),
                ("time", "获取时间", " TIMESTAMP ")
            ]
        }
    }
#13 04
    EM_monitor_data={
        "monitor_data": {
            'function_code': 4,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("weight_num", "重量测量值(g)", " REAL "),
                ("remarks", "备注", " TEXT "),
                ("time", "获取时间", " TIMESTAMP ")
            ]
        }
    }
    EM_out_port_state={
        "out_port_state": {
            'function_code': 1,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("food_switch", "食物开关", " BOOLEAN "),
                ("time", "获取时间", " TIMESTAMP ")
            ]
        }
    }
    EM_sensor_status={
        "sensor_status": {
            'function_code': 2,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("steering_engine", "舵机", " BOOLEAN "),
                ("weight", "重量传感器状态", " BOOLEAN "),
                ("time", "获取时间", " TIMESTAMP ")
            ]
        }
    }
    EM_module_information={
        "module_information": {
            'function_code': 17,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("manufacturer", "生产厂商", " TEXT "),
                ("hardware_version", "硬件版本", " TEXT "),
                ("software_version", "软件版本", " TEXT "),
                ("factory_address", "出厂地址", " TEXT "),
                ("current_address", "当前地址", " TEXT "),
                ("reserve_1", "预留1", " TEXT "),
                ("reserve_2", "预留2", " TEXT "),
                ("reserve_3", "预留3", " TEXT "),
                ("time", "获取时间", " TIMESTAMP ")
            ]
        }
    }
    #14 04
    WM_monitor_data={
        "monitor_data": {
            'function_code': 4,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("weight_num", "重量测量值(g)", " REAL "),
                ("remarks", "备注", " TEXT "),
                ("time", "获取时间", " TIMESTAMP ")
            ]
        }
    }
    WM_sensor_status={
        "sensor_status": {
            'function_code': 2,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("weight", "重量传感器状态", " BOOLEAN "),
                ("time", "获取时间", " TIMESTAMP ")
            ]
        }
    }
    WM_module_information={
        "module_information": {
            'function_code': 17,
            'column': [
                ("id", "序号", " INTEGER PRIMARY KEY AUTOINCREMENT "),
                ("manufacturer", "生产厂商", " TEXT "),
                ("hardware_version", "硬件版本", " TEXT "),
                ("software_version", "软件版本", " TEXT "),
                ("factory_address", "出厂地址", " TEXT "),
                ("current_address", "当前地址", " TEXT "),
                ("reserve_1", "预留1", " TEXT "),
                ("reserve_2", "预留2", " TEXT "),
                ("reserve_3", "预留3", " TEXT "),
                ("time", "获取时间", " TIMESTAMP ")
            ]
        }
    }

    UFC_all={
        **UFC_monitor_data,
        **UFC_out_port_state,
        **UFC_sensor_status,
        **UFC_sensor_config,
        **UFC_module_information
    }

    UGC_all = {
        **UGC_monitor_data,
        **UGC_out_port_state,
        **UGC_sensor_status,
        **UGC_sensor_config,
        **UGC_module_information
    }
    ZOS_all = {
        **ZOS_monitor_data,
        **ZOS_out_port_state,
        **ZOS_sensor_status,
        **ZOS_sensor_config,
        **ZOS_module_information
    }
    ENM_all={
        **ENM_monitor_data,
        **ENM_out_port_state,
        **ENM_sensor_status,
        **ENM_sensor_config,
        **ENM_module_information
    }
    DWM_all={
        **DWM_monitor_data,
        **DWM_sensor_status,
        **DWM_module_information
    }
    EM_all={
        **EM_monitor_data,
        **EM_sensor_status,
        **EM_out_port_state,
        **EM_module_information
    }
    WM_all={
        **WM_monitor_data,
        **WM_sensor_status,
        **WM_module_information
    }
class Others(Enum):
    """
    其他数据项
    """
    # 检测结果
    Detection_Results = {
        "name": "DetectionResults",
        "description": "目标检测结果",
        'address': 0x00,
        'int': int(0x00),
        'table': Others_Tables.Detection_Results_Data.value
    }
    # # 深度相机存储值
    # Mouse_deep_position = {
    #     "name": "MouseDeepPosition",
    #     "description": "老鼠的三维位置",
    #     'address': 0x00,
    #     'int': int(0x00),
    #     'table': Others_Tables.Mouse_deep_position_Data.value
    # }
    # 红外相机存储值
    Mouse_infrared = {
        "name": "MouseInfrared",
        "description": "老鼠的红外温度",
        'address': 0x00,
        'int': int(0x00),
        'table': Others_Tables.Mouse_infrared_Data.value
    }
    Zero_Carlibration={
        "name": "ZeroCalibration",
        "description": "零点标定",
        'address': 0x00,
        'int': int(0x00),
        'table': Others_Tables.Zero_Carlibration_Data.value
    }
    Span_Carlibration={
        "name": "SpanCalibration",
        "description": "SPan标定",
        'address': 0x00,
        'int': int(0x00),
        'table': Others_Tables.SPan_Carlibration_Data.value
    }
    Epoch={
        "name": "Epoch",
        "description": "每轮数据",
        'address': 0x00,
        'int': int(0x00),
        'table': Others_Tables.Epoch_Data.value
    }
class Modbus_Slave_Ids(Enum):
    """
    远程地址大全
    """

    UFC = {
        "name": "UFC",
        "description": "气流控制模块",
        'address': 0x02,
        'int': int(0x02),
        'table': Modbus_Slave_Tables.UFC_monitor_data.value
    }
    UGC = {
        "name": "UGC",
        "description": "二氧化碳含量模块",
        'address': 0x03,
        'int': int(0x03),
        'table':  Modbus_Slave_Tables.UGC_monitor_data.value
    }
    ZOS = {
        "name": "ZOS",
        "description": "氧气含量测量模块",
        'address': 0x04,
        'int': int(0x04),
        'table': Modbus_Slave_Tables.ZOS_monitor_data.value
    }
    ENM = {
        "name": "ENM",
        "description": "鼠笼环境监控模块",
        'address': 0x01,
        'int': int(0x01),
        'table': Modbus_Slave_Tables.ENM_monitor_data.value
        # 每个鼠笼都有该模块，
        # 地址还要加上当前鼠笼号*16
        # 例如鼠笼1的鼠笼环境监控模块地址就是0x11
    }
    DWM = {
        "name": "DWM",
        "description": "饮水监控模块",
        'address': 0x02,
        'int': int(0x02),
        'table': Modbus_Slave_Tables.DWM_monitor_data.value
        # 每个鼠笼都有该模块，
        # 地址还要加上当前鼠笼号*16
        # 例如鼠笼1的鼠笼环境监控模块地址就是0x11
    }
    EM = {
        "name": "EM",
        "description": "进食监控模块",
        'address': 0x03,
        'int': int(0x03),
        'table': Modbus_Slave_Tables.EM_monitor_data.value
        # 每个鼠笼都有该模块，
        # 地址还要加上当前鼠笼号*16
        # 例如鼠笼1的鼠笼环境监控模块地址就是0x11
    }

    WM = {
        "name": "WM",
        "description": "称重模块",
        'address': 0x04,
        'int': int(0x04),
        'table':Modbus_Slave_Tables.WM_monitor_data.value
        # 每个鼠笼都有该模块，
        # 地址还要加上当前鼠笼号*16
        # 例如鼠笼1的鼠笼环境监控模块地址就是0x11
    }

class Modbus_Slave_Send_Messages_Module_Info(Enum):
    # 所有读取模块id信息报文
    UFC = {
        'type': Modbus_Slave_Ids.UFC,
        'send_messages': [

            Send_Message(slave_address=Modbus_Slave_Ids.UFC.value['address'],
                         slave_desc=Modbus_Slave_Ids.UFC.value['description'], function_code=17,
                         function_desc="读取模块ID信息等", message={
                    'port': None,
                    'data': number_util.set_int_to_4_bytes_list(0),
                    'slave_id': format(int(Modbus_Slave_Ids.UFC.value['address']), '02X'),
                    'function_code': format(int(f"{11}", 16), '02X'),
                }),
        ]
    }
    UGC = {
        'type': Modbus_Slave_Ids.UGC,
        'send_messages': [

            Send_Message(slave_address=Modbus_Slave_Ids.UGC.value['address'],
                         slave_desc=Modbus_Slave_Ids.UGC.value['description'], function_code=17,
                         function_desc="读取模块ID信息等", message={
                    'port': None,
                    'data': number_util.set_int_to_4_bytes_list(0),
                    'slave_id': format(int(Modbus_Slave_Ids.UGC.value['address']), '02X'),
                    'function_code': format(int(f"{11}", 16), '02X'),
                }),
        ]
    }
    ZOS = {
        'type': Modbus_Slave_Ids.ZOS,
        'send_messages': [

            Send_Message(slave_address=Modbus_Slave_Ids.ZOS.value['address'],
                         slave_desc=Modbus_Slave_Ids.ZOS.value['description'], function_code=17,
                         function_desc="读取模块ID信息等", message={
                    'port': None,
                    'data': number_util.set_int_to_4_bytes_list(0),
                    'slave_id': format(int(Modbus_Slave_Ids.ZOS.value['address']), '02X'),
                    'function_code': format(int(f"{11}", 16), '02X'),
                }),
        ]
    }
    ENM = {
        'type': Modbus_Slave_Ids.ENM,
        'send_messages':
            [

                Send_Message(slave_address=Modbus_Slave_Ids.ENM.value['address'],
                             slave_desc=Modbus_Slave_Ids.ENM.value['description'], function_code=17,
                             function_desc="读取模块ID信息等", message={
                        'port': None,
                        'data': number_util.set_int_to_4_bytes_list(0),
                        'slave_id': format(
                            int(Modbus_Slave_Ids.ENM.value['address']) ,
                            '02X'),
                        'function_code': format(int(f"{11}", 16), '02X'),
                    }),
            ]

    }
    EM = {
        'type': Modbus_Slave_Ids.EM,
        'send_messages': [
                Send_Message(slave_address=Modbus_Slave_Ids.EM.value['address'],
                             slave_desc=Modbus_Slave_Ids.EM.value['description'], function_code=17,
                             function_desc="读取模块ID信息等", message={
                        'port': None,
                        'data': number_util.set_int_to_4_bytes_list('00540008'),
                        'slave_id': format(
                            int(Modbus_Slave_Ids.EM.value['address']) ,
                            '02X'),
                        'function_code': format(int(f"{11}", 16), '02X'),
                    }),
        ]
    }
    DWM = {
        'type': Modbus_Slave_Ids.DWM,
        'send_messages': [
                Send_Message(slave_address=Modbus_Slave_Ids.DWM.value['address'],
                             slave_desc=Modbus_Slave_Ids.DWM.value['description'], function_code=17,
                             function_desc="读取模块ID信息等", message={
                        'port': None,
                        'data': number_util.set_int_to_4_bytes_list('00540008'),
                        'slave_id': format(
                            int(Modbus_Slave_Ids.DWM.value['address']),
                            '02X'),
                        'function_code': format(int(f"{11}", 16), '02X'),
                    }),
            ]
    }
    WM = {
        'type': Modbus_Slave_Ids.WM,
        'send_messages': [
                Send_Message(slave_address=Modbus_Slave_Ids.WM.value['address'],
                             slave_desc=Modbus_Slave_Ids.WM.value['description'], function_code=17,
                             function_desc="读取模块ID信息等", message={
                        'port': None,
                        'data': number_util.set_int_to_4_bytes_list('00540008'),
                        'slave_id': format(
                            int(Modbus_Slave_Ids.WM.value['address']) ,
                            '02X'),
                        'function_code': format(int(f"{11}", 16), '02X'),
                    }),
        ]
    }




class Modbus_Slave_Send_Messages_Senior_State(Enum):
    # 所有读取传感器状态的报文信息
    UFC = {
        'type': Modbus_Slave_Ids.UFC,
        'send_messages': [

            Send_Message(slave_address=Modbus_Slave_Ids.UFC.value['address'],
                         slave_desc=Modbus_Slave_Ids.UFC.value['description'], function_code=2,
                         function_desc="读传感器状态信息", message={
                    'port': None,
                    'data': number_util.set_int_to_4_bytes_list(6),
                    'slave_id': format(int(Modbus_Slave_Ids.UFC.value['address']), '02X'),
                    'function_code': format(int(f"{2}", 16), '02X'),
                }),
        ]
    }
    UGC = {
        'type': Modbus_Slave_Ids.UGC,
        'send_messages': [

            Send_Message(slave_address=Modbus_Slave_Ids.UGC.value['address'],
                         slave_desc=Modbus_Slave_Ids.UGC.value['description'], function_code=2,
                         function_desc="读传感器状态信息", message={
                    'port': None,
                    'data': number_util.set_int_to_4_bytes_list(16),
                    'slave_id': format(int(Modbus_Slave_Ids.UGC.value['address']), '02X'),
                    'function_code': format(int(f"{2}", 16), '02X'),
                }),

        ]
    }
    ZOS = {
        'type': Modbus_Slave_Ids.ZOS,
        'send_messages': [
            Send_Message(
                slave_address=Modbus_Slave_Ids.ZOS.value['address'],
                slave_desc=Modbus_Slave_Ids.ZOS.value['description'],
                function_code=1,
                function_desc="读ZOS传感器状态",
                message={
                    'port': None,
                    'data': number_util.set_int_to_4_bytes_list('00080001'),  # 读参考气(08)状态
                    'slave_id': format(int(Modbus_Slave_Ids.ZOS.value['address']), '02X'),
                    'function_code': format(int(f"{1}", 16), '02X'),  # 功能码01
                }
            ),
        ]
    }
    ENM = {
        'type': Modbus_Slave_Ids.ENM,
        'send_messages': [
                Send_Message(slave_address=Modbus_Slave_Ids.ENM.value['address'],
                             slave_desc=Modbus_Slave_Ids.ENM.value['description'], function_code=2,
                             function_desc="读传感器状态信息", message={
                        'port': None,
                        'data': number_util.set_int_to_4_bytes_list(3),
                        'slave_id': format(
                            int(Modbus_Slave_Ids.ENM.value['address']) ,
                            '02X'),
                        'function_code': format(int(f"{2}", 16), '02X'),
                    }),
        ]
    }
    EM = {
        'type': Modbus_Slave_Ids.EM,
        'send_messages': [
                Send_Message(slave_address=Modbus_Slave_Ids.EM.value['address'],
                             slave_desc=Modbus_Slave_Ids.EM.value['description'], function_code=2,
                             function_desc="读传感器状态信息", message={
                        'port': None,
                        'data': number_util.set_int_to_4_bytes_list('00800002'),
                        'slave_id': format(
                            int(Modbus_Slave_Ids.EM.value['address']) ,
                            '02X'),
                        'function_code': format(int(f"{2}", 16), '02X'),
                    }),
        ]
    }
    DWM = {
        'type': Modbus_Slave_Ids.DWM,
        'send_messages': [
                Send_Message(slave_address=Modbus_Slave_Ids.DWM.value['address'],
                             slave_desc=Modbus_Slave_Ids.DWM.value['description'], function_code=2,
                             function_desc="读传感器状态信息", message={
                        'port': None,
                        'data': number_util.set_int_to_4_bytes_list('00800002'),
                        'slave_id': format(
                            int(Modbus_Slave_Ids.DWM.value['address']) ,
                            '02X'),
                        'function_code': format(int(f"{2}", 16), '02X'),
                    }),
        ]
    }
    WM = {
        'type': Modbus_Slave_Ids.WM,
        'send_messages': [
                Send_Message(slave_address=Modbus_Slave_Ids.WM.value['address'],
                             slave_desc=Modbus_Slave_Ids.WM.value['description'], function_code=2,
                             function_desc="读传感器状态信息", message={
                        'port': None,
                        'data': number_util.set_int_to_4_bytes_list('00800002'),
                        'slave_id': format(
                            int(Modbus_Slave_Ids.WM.value['address']) ,
                            '02X'),
                        'function_code': format(int(f"{2}", 16), '02X'),
                    }),


            ]
    }


class Modbus_Slave_Send_Messages_Senior_Config(Enum):
    # 所有读取传感器配置信息报文
    UFC = {
        'type': Modbus_Slave_Ids.UFC,
        'send_messages': [

            Send_Message(slave_address=Modbus_Slave_Ids.UFC.value['address'],
                         slave_desc=Modbus_Slave_Ids.UFC.value['description'], function_code=1,
                         function_desc="读输出端口状态信息", message={
                    'port': None,
                    'data': number_util.set_int_to_4_bytes_list(10),
                    'slave_id': format(int(Modbus_Slave_Ids.UFC.value['address']), '02X'),
                    'function_code': format(int(f"{1}", 16), '02X'),
                }),

            Send_Message(slave_address=Modbus_Slave_Ids.UFC.value['address'],
                         slave_desc=Modbus_Slave_Ids.UFC.value['description'], function_code=3,
                         function_desc="读配置寄存器", message={
                    'port': None,
                    'data': number_util.set_int_to_4_bytes_list(3),
                    'slave_id': format(int(Modbus_Slave_Ids.UFC.value['address']), '02X'),
                    'function_code': format(int(f"{3}", 16), '02X'),
                }),


        ]
    }
    UGC = {
        'type': Modbus_Slave_Ids.UGC,
        'send_messages': [
            Send_Message(slave_address=Modbus_Slave_Ids.UGC.value['address'],
                         slave_desc=Modbus_Slave_Ids.UGC.value['description'], function_code=1,
                         function_desc="读输出端口状态信息", message={
                    'port': None,
                    'data': number_util.set_int_to_4_bytes_list(16),
                    'slave_id': format(int(Modbus_Slave_Ids.UGC.value['address']), '02X'),
                    'function_code': format(int(f"{1}", 16), '02X'),
                }),

            Send_Message(slave_address=Modbus_Slave_Ids.UGC.value['address'],
                         slave_desc=Modbus_Slave_Ids.UGC.value['description'], function_code=3,
                         function_desc="读配置寄存器", message={
                    'port': None,
                    'data': number_util.set_int_to_4_bytes_list(8),
                    'slave_id': format(int(Modbus_Slave_Ids.UGC.value['address']), '02X'),
                    'function_code': format(int(f"{3}", 16), '02X'),
                }),
        ]
    }
    ZOS = {
        'type': Modbus_Slave_Ids.ZOS,
        'send_messages': [

            Send_Message(slave_address=Modbus_Slave_Ids.ZOS.value['address'],
                         slave_desc=Modbus_Slave_Ids.ZOS.value['description'], function_code=1,
                         function_desc="读输出端口状态信息", message={
                    'port': None,
                    'data': number_util.set_int_to_4_bytes_list(1),
                    'slave_id': format(int(Modbus_Slave_Ids.ZOS.value['address']), '02X'),
                    'function_code': format(int(f"{1}", 16), '02X'),
                }),

            Send_Message(slave_address=Modbus_Slave_Ids.ZOS.value['address'],
                         slave_desc=Modbus_Slave_Ids.ZOS.value['description'], function_code=3,
                         function_desc="读配置寄存器", message={
                    'port': None,
                    'data': number_util.set_int_to_4_bytes_list(1),
                    'slave_id': format(int(Modbus_Slave_Ids.ZOS.value['address']), '02X'),
                    'function_code': format(int(f"{3}", 16), '02X'),
                }),
        ]
    }
    ENM = {
        'type': Modbus_Slave_Ids.ENM,
        'send_messages': [

                Send_Message(slave_address=Modbus_Slave_Ids.ENM.value['address'],
                             slave_desc=Modbus_Slave_Ids.ENM.value['description'], function_code=1,
                             function_desc="读输出端口状态信息", message={
                        'port': None,
                        'data': number_util.set_int_to_4_bytes_list(2),
                        'slave_id': format(
                            int(Modbus_Slave_Ids.ENM.value['address']) ,
                            '02X'),
                        'function_code': format(int(f"{1}", 16), '02X'),
                    }),

                Send_Message(slave_address=Modbus_Slave_Ids.ENM.value['address'],
                             slave_desc=Modbus_Slave_Ids.ENM.value['description'], function_code=3,
                             function_desc="读配置寄存器", message={
                        'port': None,
                        'data': number_util.set_int_to_4_bytes_list(3),
                        'slave_id': format(
                            int(Modbus_Slave_Ids.ENM.value['address']) ,
                            '02X'),
                        'function_code': format(int(f"{3}", 16), '02X'),
                    }),

            ]
    }
    EM = {
        'type': Modbus_Slave_Ids.EM,
        'send_messages':  [

                Send_Message(slave_address=Modbus_Slave_Ids.EM.value['address'],
                             slave_desc=Modbus_Slave_Ids.EM.value['description'], function_code=1,
                             function_desc="读输出端口状态信息", message={
                        'port': None,
                        'data': number_util.set_int_to_4_bytes_list(1),
                        'slave_id': format(
                            int(Modbus_Slave_Ids.EM.value['address']) ,
                            '02X'),
                        'function_code': format(int(f"{1}", 16), '02X'),
                    }),

            ]
    }
    DWM = {
        'type': Modbus_Slave_Ids.DWM,
        'send_messages': [
        ]
    }
    WM = {
        'type': Modbus_Slave_Ids.WM,
        'send_messages': [

        ]
    }

class  Modbus_Slave_Send_Messages_Senior_Data(Enum):
    #所有读取传感器数值报文
    # 所有读取数值信息报文
    UFC = {
        'type': Modbus_Slave_Ids.UFC,
        'send_messages': [
            Send_Message(slave_address=Modbus_Slave_Ids.UFC.value['address'],
                         slave_desc=Modbus_Slave_Ids.UFC.value['description'], function_code=4,
                         function_desc="读传感器测量值", message={
                    'port': None,
                    'data': number_util.set_int_to_4_bytes_list(6),
                    'slave_id': format(int(Modbus_Slave_Ids.UFC.value['address']), '02X'),
                    'function_code': format(int(f"{4}", 16), '02X'),
                }),
        ]
    }
    UGC = {
        'type': Modbus_Slave_Ids.UGC,
        'send_messages': [
            Send_Message(slave_address=Modbus_Slave_Ids.UGC.value['address'],
                         slave_desc=Modbus_Slave_Ids.UGC.value['description'], function_code=4,
                         function_desc="读传感器测量值", message={
                    'port': None,
                    'data': number_util.set_int_to_4_bytes_list(8),
                    'slave_id': format(int(Modbus_Slave_Ids.UGC.value['address']), '02X'),
                    'function_code': format(int(f"{4}", 16), '02X'),
                }),
        ]
    }
    ZOS = {
        'type': Modbus_Slave_Ids.ZOS,
        'send_messages': [
            Send_Message(
                slave_address=Modbus_Slave_Ids.ZOS.value['address'],
                slave_desc=Modbus_Slave_Ids.ZOS.value['description'],
                function_code=1,
                function_desc="读ZOS传感器状态",
                message={
                    'port': None,
                    'data': ['00', '08', '00', '01'],  # 读参考气(08)状态
                    'slave_id': format(int(Modbus_Slave_Ids.ZOS.value['address']), '02X'),
                    'function_code': format(int(f"{1}", 16), '02X'),
                }
            ),
        ]
    }
    ENM = {
        'type': Modbus_Slave_Ids.ENM,
        'send_messages': [
                Send_Message(slave_address=Modbus_Slave_Ids.ENM.value['address'],
                             slave_desc=Modbus_Slave_Ids.ENM.value['description'], function_code=4,
                             function_desc="读传感器测量值", message={
                        'port': None,
                        'data': number_util.set_int_to_4_bytes_list(7),
                        'slave_id': format(
                            int(Modbus_Slave_Ids.ENM.value['address']),
                            '02X'),
                        'function_code': format(int(f"{4}", 16), '02X'),
                    }),
        ]
    }
    EM = {
        'type': Modbus_Slave_Ids.EM,
        'send_messages': [
                Send_Message(slave_address=Modbus_Slave_Ids.EM.value['address'],
                             slave_desc=Modbus_Slave_Ids.EM.value['description'], function_code=4,
                             function_desc="读传感器测量值", message={
                        'port': None,
                        'data': number_util.set_int_to_4_bytes_list('04010002'),
                        'slave_id': format(
                            int(Modbus_Slave_Ids.EM.value['address']),
                            '02X'),
                        'function_code': format(int(f"{4}", 16), '02X'),
                    }),
        ]
    }
    DWM = {
        'type': Modbus_Slave_Ids.DWM,
        'send_messages': [
                Send_Message(slave_address=Modbus_Slave_Ids.DWM.value['address'],
                             slave_desc=Modbus_Slave_Ids.DWM.value['description'], function_code=4,
                             function_desc="读传感器测量值", message={
                        'port': None,
                        'data': number_util.set_int_to_4_bytes_list('04010002'),
                        'slave_id': format(
                            int(Modbus_Slave_Ids.DWM.value['address']) ,
                            '02X'),
                        'function_code': format(int(f"{4}", 16), '02X'),
                    }),
        ]
    }
    WM = {
        'type': Modbus_Slave_Ids.WM,
        'send_messages': [
                Send_Message(slave_address=Modbus_Slave_Ids.WM.value['address'],
                             slave_desc=Modbus_Slave_Ids.WM.value['description'], function_code=4,
                             function_desc="读传感器测量值", message={
                        'port': None,
                        'data': number_util.set_int_to_4_bytes_list('04010002'),
                        'slave_id': format(
                            int(Modbus_Slave_Ids.WM.value['address']) ,
                            '02X'),
                        'function_code': format(int(f"{4}", 16), '02X'),
                        'module_name': 'WM',
                    }),
        ]
    }
    pass






class Modbus_Slave_Send_Messages_All(Enum):
    # 所有读取数值信息报文
    UFC = {
        'type': Modbus_Slave_Ids.UFC,
        'send_messages': [
            send_message
            for send_message in list(itertools.chain(Modbus_Slave_Send_Messages_Senior_Data.UFC.value['send_messages'],
                                                     Modbus_Slave_Send_Messages_Senior_State.UFC.value['send_messages'],
                                                     Modbus_Slave_Send_Messages_Senior_Config.UFC.value['send_messages'],
                                                     Modbus_Slave_Send_Messages_Module_Info.UFC.value['send_messages'],
                                                     )
                                     )
        ]
    }
    UGC = {
        'type': Modbus_Slave_Ids.UGC,
        'send_messages': [
            send_message
            for send_message in list(itertools.chain(Modbus_Slave_Send_Messages_Senior_Data.UGC.value['send_messages'],
                                                     Modbus_Slave_Send_Messages_Senior_State.UGC.value['send_messages'],
                                                     Modbus_Slave_Send_Messages_Senior_Config.UGC.value['send_messages'],
                                                     Modbus_Slave_Send_Messages_Module_Info.UGC.value['send_messages'],
                                                     )
                                     )
        ]
    }
    ZOS = {
        'type': Modbus_Slave_Ids.ZOS,
        'send_messages': [
            send_message
            for send_message in list(itertools.chain(Modbus_Slave_Send_Messages_Senior_Data.ZOS.value['send_messages'],
                                                     Modbus_Slave_Send_Messages_Senior_State.ZOS.value['send_messages'],
                                                     Modbus_Slave_Send_Messages_Senior_Config.ZOS.value['send_messages'],
                                                     Modbus_Slave_Send_Messages_Module_Info.ZOS.value['send_messages'],
                                                     )
                                     )
        ]
    }
    ENM = {
        'type': Modbus_Slave_Ids.ENM,
        'send_messages': [
                send_message
                for send_message in list(itertools.chain(Modbus_Slave_Send_Messages_Senior_Data.ENM.value['send_messages'],
                                                         Modbus_Slave_Send_Messages_Senior_State.ENM.value['send_messages'],
                                                         Modbus_Slave_Send_Messages_Senior_Config.ENM.value['send_messages'],
                                                         Modbus_Slave_Send_Messages_Module_Info.ENM.value['send_messages'],
                                                         )
                                         )
          ]
    }
    EM = {
        'type': Modbus_Slave_Ids.EM,
        'send_messages': [
                send_message
                for send_message in
                list(itertools.chain(Modbus_Slave_Send_Messages_Senior_Data.EM.value['send_messages'],
                                     Modbus_Slave_Send_Messages_Senior_State.EM.value['send_messages'],
                                     Modbus_Slave_Send_Messages_Senior_Config.EM.value['send_messages'],
                                     Modbus_Slave_Send_Messages_Module_Info.EM.value['send_messages'],
                                     )
                     )
        ]
    }
    DWM = {
        'type': Modbus_Slave_Ids.DWM,
        'send_messages': [
                send_message
                for send_message in
                list(itertools.chain(Modbus_Slave_Send_Messages_Senior_Data.DWM.value['send_messages'],
                                     Modbus_Slave_Send_Messages_Senior_State.DWM.value['send_messages'],
                                     Modbus_Slave_Send_Messages_Senior_Config.DWM.value['send_messages'],
                                     Modbus_Slave_Send_Messages_Module_Info.DWM.value['send_messages'],
                                     )
                     )
        ]
    }
    WM = {
        'type': Modbus_Slave_Ids.WM,
        'send_messages': [
                send_message
                for send_message in
                list(itertools.chain(Modbus_Slave_Send_Messages_Senior_Data.WM.value['send_messages'],
                                     Modbus_Slave_Send_Messages_Senior_State.WM.value['send_messages'],
                                     Modbus_Slave_Send_Messages_Senior_Config.WM.value['send_messages'],
                                     Modbus_Slave_Send_Messages_Module_Info.WM.value['send_messages'],
                                     )
                     )
        ]
    }


class Modbus_Slave_Type(Enum):
    #轨迹数据

    #相机数据
    Cameras = [
        Others.Detection_Results,Others.Mouse_infrared
    ]
    #每轮次数据
    Epochs = [
        Others.Epoch
    ]
    #标定数据
    Calibrations = [
        Others.Zero_Carlibration,Others.Span_Carlibration
    ]
    # 将上面的分为鼠笼内的和鼠笼外的传感器
    Not_Each_Mouse_Cage = [
        Modbus_Slave_Ids.UFC, Modbus_Slave_Ids.UGC, Modbus_Slave_Ids.ZOS
    ]
    Each_Mouse_Cage = [
        Modbus_Slave_Ids.ENM, Modbus_Slave_Ids.EM, Modbus_Slave_Ids.DWM, Modbus_Slave_Ids.WM
    ]

    Not_Each_Mouse_Cage_Message_All = [
        Modbus_Slave_Send_Messages_All.UFC, Modbus_Slave_Send_Messages_All.UGC, Modbus_Slave_Send_Messages_All.ZOS
    ]
    Each_Mouse_Cage_Message_All = [
        Modbus_Slave_Send_Messages_All.ENM, Modbus_Slave_Send_Messages_All.EM, Modbus_Slave_Send_Messages_All.DWM,
        Modbus_Slave_Send_Messages_All.WM
    ]
    Not_Each_Mouse_Cage_Message_Senior_Data = [
        Modbus_Slave_Send_Messages_Senior_Data.UFC, Modbus_Slave_Send_Messages_Senior_Data.UGC, Modbus_Slave_Send_Messages_Senior_Data.ZOS
    ]
    Each_Mouse_Cage_Message_Senior_Data= [
        Modbus_Slave_Send_Messages_Senior_Data.ENM, Modbus_Slave_Send_Messages_Senior_Data.EM, Modbus_Slave_Send_Messages_Senior_Data.DWM,
        Modbus_Slave_Send_Messages_Senior_Data.WM
    ]
    Not_Each_Mouse_Cage_Message_Senior_Config = [
        Modbus_Slave_Send_Messages_Senior_Config.UFC, Modbus_Slave_Send_Messages_Senior_Config.UGC, Modbus_Slave_Send_Messages_Senior_Config.ZOS
    ]
    Each_Mouse_Cage_Message_Senior_Config = [
        Modbus_Slave_Send_Messages_Senior_Config.ENM, Modbus_Slave_Send_Messages_Senior_Config.EM, Modbus_Slave_Send_Messages_Senior_Config.DWM,
        Modbus_Slave_Send_Messages_Senior_Config.WM
    ]
    Not_Each_Mouse_Cage_Message_Senior_State= [
        Modbus_Slave_Send_Messages_Senior_State.UFC, Modbus_Slave_Send_Messages_Senior_State.UGC, Modbus_Slave_Send_Messages_Senior_State.ZOS
    ]
    Each_Mouse_Cage_Message_Senior_State = [
        Modbus_Slave_Send_Messages_Senior_State.ENM, Modbus_Slave_Send_Messages_Senior_State.EM, Modbus_Slave_Send_Messages_Senior_State.DWM,
        Modbus_Slave_Send_Messages_Senior_State.WM
    ]
    Not_Each_Mouse_Cage_Message_Module_Info = [
        Modbus_Slave_Send_Messages_Module_Info.UFC, Modbus_Slave_Send_Messages_Module_Info.UGC, Modbus_Slave_Send_Messages_Module_Info.ZOS
    ]
    Each_Mouse_Cage_Message_Module_Info = [
        Modbus_Slave_Send_Messages_Module_Info.ENM, Modbus_Slave_Send_Messages_Module_Info.EM, Modbus_Slave_Send_Messages_Module_Info.DWM,
        Modbus_Slave_Send_Messages_Module_Info.WM
    ]
