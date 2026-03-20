from . import SettingsMixinsExtension


def getMetaData():
    return {}


def register(app):
    return {"extension": SettingsMixinsExtension.SettingsMixinsExtension()}
