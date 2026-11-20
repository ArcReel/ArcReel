---
status: accepted
---

# 商品原图用于保真验收，商品资产图提供可选的派生参考

商品外观必须与真品一致。原图保留可核对的包装、标志与材质，上传只做解码校验并保留原件。生成的商品资产图可能偏离真品，所以作为辅助参考，随其他资产图按 [ADR 0062](0062-artifact-lifecycle-and-currency.md) 登记和判定时效。

- 分镜图生视频路线在商品镜头优先注入商品参考，含已声明的资产图和原图。氛围镜头不注入商品。商品参考在分镜画面里体现，视频请求以分镜图为输入，避免同一路线再次注入商品参考。
- 参考生视频路线依 [ADR 0064](0064-video-unit-body-is-the-only-truth.md) 按作者的首次提及顺序处理商品。已声明资产图时使用资产图，否则使用商品原图。不可用的已声明参考按 [ADR 0073](0073-generation-entry-requires-registered-assets-with-sheets.md) 阻断。
- 商品拥有多张原图，当前全局资产库的单图条目不承载商品资产。商品资产图的版式见 [ADR 0066](0066-asset-sheet-layout-by-consumer.md)。
