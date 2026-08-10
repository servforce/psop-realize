### 10分钟周期性更新的初版流程
**先设定 只有 状态更新（现行->废止 即将实施->现行）和新增标准**

sync_national_updates.py 启动
  |
  |-- 获取 PostgreSQL advisory lock
  |     拿不到 -> 退出
  |
  |-- 创建 standard_sync_jobs
  |
  |-- 新增扫描 scan_new_until_known
  |     |
  |     |-- 从官网发布日期倒序第 1 页开始
  |     |-- 每页检查是否有新增
  |     |-- 新增 -> 下载 PDF -> 上传 MinIO -> 写 standards -> 立即材料化索引
  |     |-- 连续 2 页没有新增 -> 停止
  |     |-- 超过 max_pages_safety -> 停止
  |
  |-- upcoming 到期核验
  |     |
  |     |-- 查本地 upcoming 且 effective_date <= today
  |     |-- 访问详情页
  |     |-- 状态变了 -> 更新 standards 状态
  |
  |-- active 轮转核验
  |     |
  |     |-- 查本地最久没核验的 active N 条
  |     |-- 访问详情页
  |     |-- 如果废止 -> 更新 standards 状态
  |
  |-- 更新 standard_sync_jobs summary
  |
  |-- 释放 advisory lock