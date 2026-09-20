




class global_setting():
    _setting = {}

    @classmethod
    def set_setting(cls, key, value):
        cls._setting[key] = value
        pass

    @classmethod
    def get_setting(cls, key, default=None):
        if default != None:
            return cls._setting.get(key, default)
        else:
            return cls._setting.get(key)

    pass

