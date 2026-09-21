"""项目风格字段进入提示词前的统一归一化。

资产图、分镜图、宫格与参考生视频共用 :func:`normalize_style_value`：首尾空白去掉，非字符串（外部
编辑写坏的 ``project.json``、显式 null）按空处理，模版的真值条件因此不会输出只含空白的声明行。
产物依据按各自口径记录原始值，不经过这里。
"""


def normalize_style_value(value: object) -> str:
    """``style`` / ``style_description`` 渲染用的取值。"""
    return value.strip() if isinstance(value, str) else ""
