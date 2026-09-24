#数据项
from enum import Enum

from public.function.Modbus.Modbus_Type import Modbus_Slave_Ids


class Data_Column(Enum):
    Cage_inside_light = {
        "id": 1,
        "column_text": "笼内光强",
        "column_name": "cage_inside_light",
        "unit": "Lux",
        "desc": "笼内光照强度",
        "data_format": "origin_data",
        "note": "",
        "module":Modbus_Slave_Ids.ENM,
        "table":"sensor_config",
        "columns": "light_luminance"
    }

    Cage_inside_color_temp = {
        "id": 2,
        "column_text": "笼内光照色温",
        "column_name": "cage_inside_color_temp",
        "unit": "kcal/hr",
        "desc": "笼内光照色温",
        "data_format": "origin_data",
        "note": "",
        "module":Modbus_Slave_Ids.ENM,
        "table":"sensor_config",
        "columns": "light_color_temp"
    }

    Light_duration = {
        "id": 3,
        "column_text": "光照时间",
        "column_name": "light_duration",
        "unit": "Hr或min或s",
        "desc": "从光照打开（HH:MM:SS）到光照关闭（HH:MM:SS）的时间段",
        "data_format": "origin_data",
        "note": "",
        "module":Modbus_Slave_Ids.ENM
    }

    Cage_inside_temperature = {
        "id": 4,
        "column_text": "笼内温度",
        "column_name": "cage_inside_temperature",
        "unit": "℃",
        "desc": "笼内温度",
        "data_format": "origin_data",
        "note": "显示到0.001",
        "module":Modbus_Slave_Ids.ENM,
        "table":"monitor_data",
        "columns": "temperature_num"
    }

    Cage_inside_humidity = {
        "id": 5,
        "column_text": "笼内湿度",
        "column_name": "cage_inside_humidity",
        "unit": "RH%",
        "desc": "笼内湿度",
        "data_format": "origin_data",
        "note": "显示到0.001",
        "module":Modbus_Slave_Ids.ENM,
        "table":"monitor_data",
        "columns": "humidity_num"
    }

    Cumulative_food_amount = {
        "id": 6,
        "column_text": "累计进食量",
        "column_name": "cumulative_food_amount",
        "unit": "Kg或g",
        "desc": "从开始实验到当前累积进食质量",
        "data_format": "origin_data",
        "note": "显示到0.001",
        "module":Modbus_Slave_Ids.EM,
        "table":"monitor_data",
        "columns": "weight_num"
    }

    Segment_food_amount = {
        "id": 7,
        "column_text": "分时间段进食量",
        "column_name": "segment_food_amount",
        "unit": "Kg或g",
        "desc": "任意指定时间段的累计进食量",
        "data_format": "calculated_data",
        "note": "",
        "module":Modbus_Slave_Ids.EM
    }

    Single_feed_duration = {
        "id": 8,
        "column_text": "单段进食时间",
        "column_name": "single_feed_duration",
        "unit": "s",
        "desc": "从进食开始到进食结束的时间（质量传感器从一个稳定状态到另一个稳定状态的时间）",
        "data_format": "origin_data",
        "note": "",
        "module":Modbus_Slave_Ids.EM
    }

    Cumulative_feeding_time = {
        "id": 9,
        "column_text": "累计进食时间",
        "column_name": "cumulative_feeding_time",
        "unit": "Hr或Min或s",
        "desc": "进食时间的总累加",
        "data_format": "calculated_data",
        "note": "",
        "module":Modbus_Slave_Ids.EM
    }

    Cumulative_drinking_amount = {
        "id": 10,
        "column_text": "累计饮水量",
        "column_name": "cumulative_drinking_amount",
        "unit": "Kg或g",
        "desc": "从开始实验到当前累积进水量",
        "data_format": "origin_data",
        "note": "显示到0.001",
        "module":Modbus_Slave_Ids.DWM
    }

    Segment_drinking_amount = {
        "id": 11,
        "column_text": "分时间段饮水量",
        "column_name": "segment_drinking_amount",
        "unit": "Kg或g",
        "desc": "分段时间内的饮水质量",
        "data_format": "calculated_data",
        "note": "",
        "module":Modbus_Slave_Ids.DWM
    }

    Single_drink_duration = {
        "id": 12,
        "column_text": "单段饮水时间",
        "column_name": "single_drink_duration",
        "unit": "s",
        "desc": "从饮水开始到饮水结束的时间（质量传感器从一个稳定状态到另一个稳定状态的时间）",
        "data_format": "origin_data",
        "note": "",
        "module":Modbus_Slave_Ids.DWM
    }

    Cumulative_drinking_time = {
        "id": 13,
        "column_text": "累计饮水时间",
        "column_name": "cumulative_drinking_time",
        "unit": "Hr或Min或s",
        "desc": "饮水时间的总累加",
        "data_format": "calculated_data",
        "note": "",
        "module":Modbus_Slave_Ids.DWM
    }

    Body_weight = {
        "id": 14,
        "column_text": "体重",
        "column_name": "body_weight",
        "unit": "Kg或g",
        "desc": "动物体重",
        "data_format": "origin_data",
        "note": "显示到0.001",
        "module":Modbus_Slave_Ids.WM,
        "table":"monitor_data",
        "columns": "weight_num"
    }

    O2_in_concentration = {
        "id": 15,
        "column_text": "O2in浓度",
        "column_name": "o2in_concentration",
        "unit": "%或ppm",
        "desc": "笼内进气氧气浓度",
        "data_format": "origin_data",
        "note": "显示到0.0001",
        "module":Modbus_Slave_Ids.ZOS,
        "table":"monitor_data",
        "columns": "oxygen_num"
    }

    CO2_in_concentration = {
        "id": 16,
        "column_text": "CO2in浓度",
        "column_name": "co2in_concentration",
        "unit": "%或ppm",
        "desc": "笼内进气二氧化碳浓度",
        "data_format": "origin_data",
        "note": "显示到0.0001",
        "module":Modbus_Slave_Ids.UGC,
        "table":"monitor_data",
        "columns": "CO2_num"
    }

    O2_out_concentration = {
        "id": 17,
        "column_text": "O2out浓度",
        "column_name": "o2out_concentration",
        "unit": "%或ppm",
        "desc": "笼内出气氧气浓度",
        "data_format": "origin_data",
        "note": "显示到0.0001",
        "module":Modbus_Slave_Ids.ZOS,
        "table":"monitor_data",
        "columns": "oxygen_num"
    }

    Dry_basis_O2_out_concentration = {
        "id": 63,
        "column_text": "干基氧浓度",
        "column_name": "dry_basis_o2_out_concentration",
        "unit": "%",
        "desc": "通过补偿算法得到的干基氧浓度",
        "data_format": "origin_data",
        "note": "显示到0.0001",
        "module":Modbus_Slave_Ids.ZOS,
        "table":"monitor_data",
        "columns": "dry_basis_oxygen_num"
    }

    CO2_out_concentration = {
        "id": 18,
        "column_text": "CO2out浓度",
        "column_name": "co2out_concentration",
        "unit": "%或ppm",
        "desc": "笼内出气二氧化碳浓度",
        "data_format": "origin_data",
        "note": "显示到0.001",
        "module":Modbus_Slave_Ids.UGC,
        "table":"monitor_data",
        "columns": "CO2_num"
    }

    ZOS_temperature_2 = {
        "id": 61,
        "column_text": "ZOS温度2测量值(°C)",
        "column_name": "zos_temperature_2",
        "unit": "°C",
        "desc": "ZOS第二温度测量值",
        "data_format": "origin_data",
        "note": "显示到0.1",
        "module":Modbus_Slave_Ids.ZOS,
        "table":"monitor_data",
        "columns": "oxygen_temperature_2_num"
    }

    ZOS_humidity = {
        "id": 62,
        "column_text": "ZOS湿度测量值(%RH)",
        "column_name": "zos_humidity",
        "unit": "%RH",
        "desc": "ZOS湿度测量值",
        "data_format": "origin_data",
        "note": "显示到0.1",
        "module":Modbus_Slave_Ids.ZOS,
        "table":"monitor_data",
        "columns": "oxygen_humidity_num"
    }

    Delta_O2 = {
        "id": 19,
        "column_text": "ΔO2",
        "column_name": "delta_o2",
        "unit": "%或ppm",
        "desc": "进气与出气氧气浓度差（O2in - O2out）",
        "data_format": "calculated_data",
        "note": "",
        "module":Modbus_Slave_Ids.ZOS
    }

    Delta_CO2 = {
        "id": 20,
        "column_text": "ΔCO2",
        "column_name": "delta_co2",
        "unit": "%或ppm",
        "desc": "出气与进气二氧化碳浓度差（CO2out - CO2in）",
        "data_format": "calculated_data",
        "note": "",
        "module":Modbus_Slave_Ids.UGC
    }

    V_in_flow = {
        "id": 21,
        "column_text": "Vin 进气流速",
        "column_name": "vin_flow_rate",
        "unit": "ml/hr或L/min",
        "desc": "进气气流速率（数据来源暂不确定）",
        "data_format": "origin_data",
        "note": "数据来源暂不确定；显示到0.0001（若为原始数采集应根据传感器精度设定）",
        "module":Modbus_Slave_Ids.UFC,
        "table":"monitor_data",
        "columns": "barometer_num_1"
    }

    V_out_flow = {
        "id": 22,
        "column_text": "Vout 出气流速",
        "column_name": "vout_flow_rate",
        "unit": "ml/hr或L/min",
        "desc": "出气气流速率",
        "data_format": "origin_data",
        "note": "显示到0.0001",
        "module":Modbus_Slave_Ids.UFC,
        "table":"monitor_data",
        "columns": "barometer_num_2"
    }

    VO2 = {
        "id": 23,
        "column_text": "VO2耗氧量",
        "column_name": "vo2_consumption",
        "unit": "L/min",
        "desc": "单位时间内氧气消耗速率",
        "data_format": "calculated_data",
        "note": "",
        "module":Modbus_Slave_Ids.ZOS
    }

    VCO2 = {
        "id": 24,
        "column_text": "VCO2二氧化碳产率",
        "column_name": "vco2_production",
        "unit": "L/min",
        "desc": "单位时间内二氧化碳产生速率",
        "data_format": "calculated_data",
        "note": "",
        "module":Modbus_Slave_Ids.UGC
    }

    RER_RQ = {
        "id": 25,
        "column_text": "RER/呼吸熵",
        "column_name": "rer_rq",
        "unit": "/",
        "desc": "呼吸熵（RER/RQ），计算公式 RER = VCO2 / VO2",
        "data_format": "calculated_data",
        "note": "",
        "module":None
    }

    CV_heat_value = {
        "id": 26,
        "column_text": "CV 热值系数",
        "column_name": "cv_heat_value",
        "unit": "kcal/L或kj/L",
        "desc": "热值系数，可按经验公式计算（例如 CV = 3.815 + 1.232 * RER 或 CV = 3.9 + 1.1 * RER）",
        "data_format": "calculated_data",
        "note": "可选择不同经验公式，需记录所用公式",
        "module":None
    }

    EE_energy_expenditure = {
        "id": 27,
        "column_text": "EE（Energy Expenditure 能量消耗）",
        "column_name": "ee_energy_expenditure",
        "unit": "kcal/hr",
        "desc": "能量消耗率（EE）。VO2/VCO2 为 L/min 时，EE = 60 * (3.941 * VO2 + 1.106 * VCO2)（kcal/hr）",
        "data_format": "calculated_data",
        "note": "记录所用 CV/公式",
        "module":None
    }

    BMR = {
        "id": 28,
        "column_text": "BMR（Basal Metabolic Rate）",
        "column_name": "bmr",
        "unit": "/",
        "desc": "基础代谢率：小鼠在清醒、安静、恒温环境下维持基本生命活动的能量消耗。测量需满足安静、空腹、适宜环境温度等前提条件",
        "data_format": "calculated_data",
        "note": "测量前需满足实验前提（如空腹4-12小时、环境温度等）",
        "module":None
    }

    PA_physical_activity = {
        "id": 29,
        "column_text": "PA（Physical Activity）",
        "column_name": "physical_activity",
        "unit": "/",
        "desc": "活动产热或活动量（根据具体算法或传感器数据计算）",
        "data_format": "calculated_data",
        "note": "",
        "module":None
    }

    TEF_thermic_effect_of_food = {
        "id": 30,
        "column_text": "TEF（Thermic Effect of Food）",
        "column_name": "tef_thermic_effect_of_food",
        "unit": "/",
        "desc": "食物热效应（进食引起的额外能量消耗）",
        "data_format": "calculated_data",
        "note": "",
        "module":None
    }

    AT_adaptive_thermogenesis = {
        "id": 31,
        "column_text": "AT（Adaptive Thermogenesis）",
        "column_name": "at_adaptive_thermogenesis",
        "unit": "/",
        "desc": "体温调节耗能（适应性产热）",
        "data_format": "calculated_data",
        "note": "",
        "module":None
    }

    Core_body_temperature = {
        "id": 32,
        "column_text": "体温",
        "column_name": "core_body_temperature",
        "unit": "℃",
        "desc": "动物核心体温",
        "data_format": "origin_data",
        "note": "显示到0.001",
        "module":None
    }

    Surface_temperature = {
        "id": 33,
        "column_text": "体表温度",
        "column_name": "surface_temperature",
        "unit": "℃",
        "desc": "动物体表温度（通常来自热成像）",
        "data_format": "origin_data",
        "note": "热量图像",
        "module":None
    }

    Total_activity_distance = {
        "id": 34,
        "column_text": "总活动量",
        "column_name": "total_activity_distance",
        "unit": "m",
        "desc": "动物笼内活动总量（通常按总移动距离计算）",
        "data_format": "origin_data",
        "note": "",
        "module":None
    }

    Segment_continuous_activity_time = {
        "id": 35,
        "column_text": "分段连续活动时间",
        "column_name": "segment_continuous_activity_time",
        "unit": "Hr或Min或s",
        "desc": "分段连续活动的时间长度",
        "data_format": "calculated_data",
        "note": "",
        "module":None
    }

    Segment_activity_speed = {
        "id": 36,
        "column_text": "分段活动速率",
        "column_name": "segment_activity_speed",
        "unit": "m/min或m/s",
        "desc": "分段连续活动移动距离 / 该段连续活动时间",
        "data_format": "calculated_data",
        "note": "",
        "module":None
    }

    Cage_position = {
        "id": 37,
        "column_text": "笼内位置",
        "column_name": "cage_position",
        "unit": "",
        "desc": "笼内位置坐标 (x, y)",
        "data_format": "origin_data",
        "note": "",
        "module":None
    }

    Cage_trajectory = {
        "id": 38,
        "column_text": "笼内轨迹",
        "column_name": "cage_trajectory",
        "unit": "",
        "desc": "位置轨迹追踪（序列坐标）",
        "data_format": "origin_data",
        "note": "",
        "module":None
    }

    Position_heatmap = {
        "id": 39,
        "column_text": "笼内位置热图",
        "column_name": "position_heatmap",
        "unit": "",
        "desc": "动物在笼内位置的热度/占位分布图（通常为图像或矩阵）",
        "data_format": "calculated_data",
        "note": "",
        "module":None
    }

    Sleep_detection = {
        "id": 42,
        "column_text": "睡眠判定",
        "column_name": "sleep_detection",
        "unit": "Hr或Min或s",
        "desc": "睡眠判定：≧40s保持不动且闭眼 或 呼吸频率≦90次/min 等判定规则",
        "data_format": "calculated_data",
        "note": "判定阈值（如40s、呼吸频率等）应记录",
        "module":None
    }

    Sleep_episode_count = {
        "id": 43,
        "column_text": "睡眠回合数",
        "column_name": "sleep_episode_count",
        "unit": "/",
        "desc": "分段睡眠的数量（回合数）",
        "data_format": "calculated_data",
        "note": "",
        "module":None
    }

    Average_sleep_length = {
        "id": 44,
        "column_text": "平均睡眠长度",
        "column_name": "average_sleep_length",
        "unit": "Hr或Min或s",
        "desc": "总睡眠时间 / 睡眠回合数",
        "data_format": "calculated_data",
        "note": "",
        "module":None
    }

    Minimum_sleep_length = {
        "id": 45,
        "column_text": "最小睡眠长度",
        "column_name": "minimum_sleep_length",
        "unit": "Hr或Min或s",
        "desc": "判定为睡眠的最短一段连续睡眠时间",
        "data_format": "calculated_data",
        "note": "",
        "module":None
    }

    Maximum_sleep_length = {
        "id": 46,
        "column_text": "最大睡眠长度",
        "column_name": "maximum_sleep_length",
        "unit": "Hr或Min或s",
        "desc": "判定为睡眠的最长一段连续睡眠时间",
        "data_format": "calculated_data",
        "note": "",
        "module":None
    }

    Total_sleep_time = {
        "id": 47,
        "column_text": "总睡眠时间",
        "column_name": "total_sleep_time",
        "unit": "Hr或Min或s",
        "desc": "所有睡眠时间累加值",
        "data_format": "calculated_data",
        "note": "",
        "module":None
    }

    Respiratory_rate = {
        "id": 48,
        "column_text": "呼吸频率",
        "column_name": "respiratory_rate",
        "unit": "次/min",
        "desc": "腹部呼吸起伏数 / min",
        "data_format": "origin_data",
        "note": "",
        "module":None
    }

    Long_distance_walk = {
        "id": 40,
        "column_text": "长距离行走",
        "column_name": "long_distance_walk",
        "unit": "m",
        "desc": "长距离行走（默认≧1m，可自定义阈值）",
        "data_format": "calculated_data",
        "note": "阈值（如1m）可自定义",
        "module":None
    }

    Short_distance_walk = {
        "id": 41,
        "column_text": "短距离行走",
        "column_name": "short_distance_walk",
        "unit": "m",
        "desc": "短距离行走（默认≧0.2m，可自定义阈值）",
        "data_format": "calculated_data",
        "note": "阈值（如0.2m）可自定义",
        "module":None
    }

    Touch_feeder_with_eating = {
        "id": 49,
        "column_text": "触碰进食器进食",
        "column_name": "touch_feeder_with_eating",
        "unit": "",
        "desc": "小鼠处于进食器口位置，进食传感器不稳定后发生质量减少（判定为实际进食）",
        "data_format": "origin_data",
        "note": "",
        "module":Modbus_Slave_Ids.EM,
        "table":"monitor_data",
        "columns": "weight_num"
    }

    Touch_feeder_without_eating = {
        "id": 50,
        "column_text": "触碰进食器无进食",
        "column_name": "touch_feeder_without_eating",
        "unit": "",
        "desc": "小鼠处于进食器口位置，进食传感器不稳定但未发生质量减少（仅触碰无进食）",
        "data_format": "origin_data",
        "note": "",
        "module":Modbus_Slave_Ids.EM,
        "table":"monitor_data",
        "columns": "weight_num"
    }

    Touch_bottle_with_drinking = {
        "id": 51,
        "column_text": "触碰饮水瓶饮水",
        "column_name": "touch_bottle_with_drinking",
        "unit": "",
        "desc": "小鼠处于饮水瓶口位置，饮水传感器不稳定后发生质量减少（判定为实际饮水）",
        "data_format": "origin_data",
        "note": "",
        "module":Modbus_Slave_Ids.DWM,
        "table":"monitor_data",
        "columns": "weight_num"
    }

    Touch_bottle_without_drinking = {
        "id": 52,
        "column_text": "触碰饮水瓶未饮水",
        "column_name": "touch_bottle_without_drinking",
        "unit": "",
        "desc": "小鼠处于饮水瓶口位置，传感器不稳定但未发生质量减少（仅触碰未饮水）",
        "data_format": "origin_data",
        "note": "",
        "module":Modbus_Slave_Ids.DWM,
        "table":"monitor_data",
        "columns": "weight_num"
    }

    Asleep_in_weight_module = {
        "id": 53,
        "column_text": "在体重套件内入眠",
        "column_name": "asleep_in_weight_module",
        "unit": "",
        "desc": "小鼠处于体重套件内并处于睡眠状态",
        "data_format": "origin_data",
        "note": "",
        "module":Modbus_Slave_Ids.WM,
        "table":"monitor_data",
        "columns": "weight_num"
    }

    Active_in_weight_module = {
        "id": 54,
        "column_text": "在体重套件内活动",
        "column_name": "active_in_weight_module",
        "unit": "",
        "desc": "小鼠处于体重套件内且为活动状态（非睡眠）",
        "data_format": "origin_data",
        "note": "",
        "module":Modbus_Slave_Ids.WM,
        "table":"monitor_data",
        "columns": "weight_num"
    }

    Running_wheel_rotations = {
        "id": 55,
        "column_text": "跑轮运动转数",
        "column_name": "running_wheel_rotations",
        "unit": "N",
        "desc": "跑轮转数（累计）",
        "data_format": "origin_data",
        "note": "",
        "module":Modbus_Slave_Ids.ENM,
        "table":"monitor_data",
        "columns": "running_wheel_num"
    }

    Running_wheel_distance = {
        "id": 56,
        "column_text": "跑轮运动距离",
        "column_name": "running_wheel_distance",
        "unit": "m",
        "desc": "跑轮行程距离（由转数与周长计算）",
        "data_format": "calculated_data",
        "note": "",
        "module":Modbus_Slave_Ids.ENM,
        "table":"monitor_data",
        "columns": "running_wheel_num"
    }

    Running_wheel_duration = {
        "id": 57,
        "column_text": "跑轮持续时间",
        "column_name": "running_wheel_duration",
        "unit": "Min",
        "desc": "跑轮持续运动时间",
        "data_format": "calculated_data",
        "note": "",
        "module":Modbus_Slave_Ids.ENM,
        "table":"monitor_data",
        "columns": "running_wheel_num"
    }

    Running_wheel_speed = {
        "id": 58,
        "column_text": "跑轮运动速度",
        "column_name": "running_wheel_speed",
        "unit": "M/min或m/s",
        "desc": "跑轮上的运动速度",
        "data_format": "calculated_data",
        "note": "",
        "module":Modbus_Slave_Ids.ENM
    }

    Running_wheel_acceleration = {
        "id": 59,
        "column_text": "跑轮运动加速度",
        "column_name": "running_wheel_acceleration",
        "unit": "a=dV/dt",
        "desc": "跑轮运动加速度（速度对时间的导数）",
        "data_format": "calculated_data",
        "note": "",
        "module":Modbus_Slave_Ids.ENM
    }
    Feeding_frequency= {
        "id": 60,
        "column_text": "进食频率",
        "column_name": "Feeding_frequency",
        "unit": "",
        "desc": "实验周期内或指定时间长度内进食的次数",
        "data_format": "calculated_data",
        "note": "",
        "module":Modbus_Slave_Ids.EM
    }

class Data_column_list(Enum):
    Data_list =[
        Data_Column.Cage_inside_light,
        Data_Column.Cage_inside_color_temp,
        Data_Column.Light_duration,
        Data_Column.Cage_inside_temperature,
        Data_Column.Cage_inside_humidity,
        Data_Column.Cumulative_food_amount,
        Data_Column.Segment_food_amount,
        Data_Column.Single_feed_duration,
        Data_Column.Cumulative_feeding_time,
        Data_Column.Cumulative_drinking_amount,
        Data_Column.Segment_drinking_amount,
        Data_Column.Single_drink_duration,
        Data_Column.Cumulative_drinking_time,
        Data_Column.Body_weight,
        Data_Column.O2_in_concentration,
        Data_Column.CO2_in_concentration,
        Data_Column.O2_out_concentration,
        Data_Column.Dry_basis_O2_out_concentration,
        Data_Column.CO2_out_concentration,
        Data_Column.ZOS_temperature_2,
        Data_Column.ZOS_humidity,
        Data_Column.Delta_O2,
        Data_Column.Delta_CO2,
        Data_Column.V_in_flow,
        Data_Column.V_out_flow,
        Data_Column.VO2,
        Data_Column.VCO2,
        Data_Column.RER_RQ,
        Data_Column.CV_heat_value,
        Data_Column.EE_energy_expenditure,
        Data_Column.BMR,
        Data_Column.PA_physical_activity,
        Data_Column.TEF_thermic_effect_of_food,
        Data_Column.AT_adaptive_thermogenesis,
        Data_Column.Core_body_temperature,
        Data_Column.Surface_temperature,
        Data_Column.Total_activity_distance,
        Data_Column.Segment_continuous_activity_time,
        Data_Column.Segment_activity_speed,
        Data_Column.Cage_position,
        Data_Column.Cage_trajectory,
        Data_Column.Position_heatmap,
        Data_Column.Long_distance_walk,
        Data_Column.Short_distance_walk,
        Data_Column.Sleep_detection,
        Data_Column.Sleep_episode_count,
        Data_Column.Average_sleep_length,
        Data_Column.Minimum_sleep_length,
        Data_Column.Maximum_sleep_length,
        Data_Column.Total_sleep_time,
        Data_Column.Respiratory_rate,
        Data_Column.Touch_feeder_with_eating,
        Data_Column.Touch_feeder_without_eating,
        Data_Column.Touch_bottle_with_drinking,
        Data_Column.Touch_bottle_without_drinking,
        Data_Column.Asleep_in_weight_module,
        Data_Column.Active_in_weight_module,
        Data_Column.Running_wheel_rotations,
        Data_Column.Running_wheel_distance,
        Data_Column.Running_wheel_duration,
        Data_Column.Running_wheel_speed,
        Data_Column.Running_wheel_acceleration,
        Data_Column.Feeding_frequency
    ]
