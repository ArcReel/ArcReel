"""落盘字节的容器识别：只读文件头，不读扩展名。

产物文件名是落盘那一层起的（成片的规范 ``.mp4`` 资源路径、测试连接的 ``artifact.mp4`` /
``artifact.png``），而真实容器由供应商那一侧的导出设置决定，两者对不上时按扩展名声明 MIME 会让
浏览器按一个错误的类型去解。故「这段字节是什么容器」由本模块一处回答：魔数只有这一份，落盘后的
容器核对与产物读接口的 MIME 都引用它，不会两处各存一张表、新增容器时只改一边。

与 :mod:`lib.backends.data_uri` 的分工：那边答的是「出站请求体里这张素材该声明什么 MIME」，
故带 GIF / HEIC 与按扩展名回落的兼容口径；本模块只认成片与分镜图这几种容器，认不出就说认不出。
"""

from __future__ import annotations

from collections.abc import Mapping

#: 判定容器要读的文件头字节数：够读到 ``RIFF....WEBP`` 的第二段魔数，也够读到 ``ftyp`` 的 major brand。
CONTAINER_HEAD_BYTES = 12

#: ISO BMFF 里属于静态图、不是成片的 major brand。
#:
#: 认的是这一边而不是「视频 brand 白名单」：视频侧的 brand 是开放集（``isom`` / ``iso2`` /
#: ``mp41`` / ``mp42`` / ``avc1`` / ``qt`` / ``M4V`` / ``dash`` …，各家导出器还在加），白名单
#: 会把合法成片判成认不出——那比漏认一张 HEIC 更糟。brand 与 MIME 的对应沿用
#: ``data_uri._image_mime_from_bytes`` 已有的那一份。
_ISO_BMFF_IMAGE_BRANDS: Mapping[bytes, str] = {
    b"heic": "image/heic",
    b"heix": "image/heic",
    b"hevc": "image/heic",
    b"hevx": "image/heic",
    b"mif1": "image/heif",
    b"msf1": "image/heif",
    b"avif": "image/avif",
    b"avis": "image/avif",
}


def sniff_container(head: bytes) -> str | None:
    """这段文件头对应的容器 MIME；认不出给 ``None``。

    ``ftyp``（落在偏移 4–8 字节）只说「这是 ISO BMFF 家族」，HEIC / AVIF 这类静态图同用这一层，
    故要按 major brand 把它们摘出来——否则一张 HEIC 装进 ``.mp4`` 的名字会被当成成片放行。其余
    brand 一律给 ``video/mp4``：``.mov`` 与 ``.m4v`` 的 brand 各家写法不一，而它们在播放侧与
    ``.mp4`` 同路，细分到白名单只会把合法成片判成认不出。
    """
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return _ISO_BMFF_IMAGE_BRANDS.get(head[8:12], "video/mp4")
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"
    return None
