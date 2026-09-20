from datetime import datetime

from public.util.lighting_schedule import default_lighting_schedule




class Experiment_setting_entity:
    def __init__(self):
        self.animals:[Animal] = []
        self.groups:[Group] = []
        self.animalGroupRecords:[AnimalGroupRecord] = []
        # 按笼号保存实验开始前的体重，键为 Group.id，值单位固定为 g。
        self.pre_experiment_weights: dict = {}
        self.lighting_schedule: dict = default_lighting_schedule()
        # P2 experiment-level selections.  They are kept on the setting
        # entity so the UI, template database and running services share the
        # same animal type and grouping plan.
        self.animal_species: str = "小鼠"
        self.grouping_plan: str = "对照组 / 高脂组 / 运动组"
        self.channel_modules: dict = {}
        self.channel_data_config: dict = {}
        self.channel_animal_info: dict = {}
        super().__init__()
    def is_emtpy(self) -> bool:

        return all(len == 0 for len in [len(self.animals),len(self.groups),len(self.animalGroupRecords)])

    def __repr__(self):
        return (f"Experiment_setting_entity(animals={self.animals}, groups={self.groups}, animalGroupRecords='{self.animalGroupRecords}')")
class Animal:
    """
    类描述：表示数据库中的动物记录
    """

    def __init__(self,
                 id: int=None,
                 id_write: int=None,
                 name: str=None,
                 sex: bool=None,
                 weight: float=None,
                 weight_unit: str=None,
                 note: str=None,
                 create_time: datetime=None,
                 update_time: datetime=None):
        """

        :param id: 序号
        :param id_write: ID
        :param name: 动物名称
        :param sex: 动物性别
        :param weight: 动物重量
        :param weight_unit: 单位
        :param note: 备注
        :param create_time: 创建时间
        :param update_time: 更新时间
        """
        self.id = id  # 序号
        self.id_write = id_write  # ID
        self.name = name  # 动物名称
        self.sex = sex  # 性别
        self.weight = weight  # 重量
        self.weight_unit = weight_unit  # 重量单位
        self.note = note  # 备注
        self.create_time = create_time  # 获取时间
        self.update_time = update_time  # 更新时间

    def __repr__(self):
        return (f"Animal(id={self.id}, id_write={self.id_write}, name='{self.name}', "
                f"sex={self.sex}, weight={self.weight}, weight_unit='{self.weight_unit}', "
                f"note='{self.note}', create_time='{self.create_time}', update_time='{self.update_time}')")


class Group:
    """
    类描述：表示数据库中的分组记录
    """

    def __init__(self,
                 id: int=None,
                 name: str=None,
                 create_time: datetime=None,
                 update_time: datetime=None,
                 is_selected: bool=False): # 新增勾选状态属性，默认未勾选
        """

        :param id: 序号
        :param name: 分组/通道名称
        :param create_time: 创建时间
        :param update_time: 更新时间
        :param is_selected: 是否被勾选
        """
        self.id = id  # 序号
        self.name = name  # 名称
        self.create_time = create_time  # 获取时间
        self.update_time = update_time  # 更新时间
        self.is_selected = is_selected  # 新增:勾选状态

    def __repr__(self):
        return (f"Record(id={self.id}, name='{self.name}', is_selected={self.is_selected},"
                f"create_time='{self.create_time}', update_time='{self.update_time}')")
class AnimalGroupRecord:
    """
    类描述：表示数据库中的动物和组的记录
    """

    def __init__(self,
                 id: int=None,
                 aid: int=None,
                 gid: int=None,
                 note: str=None,
                 create_time: datetime=None,
                 update_time: datetime=None):
        self.id = id                    # 序号
        self.aid = aid                  # 动物序号
        self.gid = gid                  # 组/通道序号，布尔值
        self.note = note                # 备注
        self.create_time = create_time   # 获取时间
        self.update_time = update_time   # 更新时间

    def __repr__(self):
        return (f"AnimalGroupRecord(id={self.id}, aid='{self.aid}', gid={self.gid}, "
                f"note='{self.note}', create_time='{self.create_time}', update_time='{self.update_time}')")
class AnimalGroupRecord_View:
    """
    类描述：表示数据库中的动物和组的记录 显示在界面上的类
    """

    def __init__(self,
                 id: int=None,
                 animal: Animal=None,
                 group: Group=None,
                 note: str=None,
                 create_time: datetime=None,
                 update_time: datetime=None):
        self.id = id                    # 序号
        self.animal = animal                  # 动物
        self.group = group                  # 组/通道
        self.note = note                # 备注
        self.create_time = create_time   # 获取时间
        self.update_time = update_time   # 更新时间

    def __repr__(self):
        return (f"AnimalGroupRecord(id={self.id}, animal='{self.animal}', group={self.group}, "
                f"note='{self.note}', create_time='{self.create_time}', update_time='{self.update_time}')")
