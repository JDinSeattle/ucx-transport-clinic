**项目 B：UCX Transport Clinic——CPU 可运行的协议与生命周期回归**

问题：异步通信的正确性依赖缓冲区所有权、连接状态、请求完成及资源释放；一个 sanitizer 报告还需要与协议格式、兼容性和性能约束一起判断。

范围：UCX 的 TCP/shared-memory 传输或 UCP wireup 中选择一个问题。用两个进程起步，只使用实际启用的 CPU transport；不添加 RDMA 驱动、不修改全部 wire format。

结构：固定 UCX revision 的调试构建 → stock 示例或最小 UCP 程序 → 可控制的分包/连接/退出序列 → request 与 buffer 生命周期记录 → gtest/回归。CPU debug/sanitizer 构建用于诊断，release 构建用于性能，避免把 debug 开销当成传输缺陷。

阶段：先复现并约简；写出合法调用序列与内存所有权；核对标准和上游意图；做最小修复及相邻边界；验证成功与失败退出都不泄漏、悬挂或重复完成。若改 packed 数据访问，应保持序列化兼容，并评估 hot path 代价。

验收：反例在旧版本可触发；修复后对所选 transport 的请求结果、完整数据、资源释放与超时行为符合契约；sanitizer 报告不再出现；CPU 参考负载的延迟分布无无法解释退化。交付最小程序、根因、补丁、单元测试和资源轨迹。规划 40–90 小时，4–8 CPU 线程、8–16 GB 内存可作为小范围起点；同机 TCP 不代表真实网络吞吐。[UCX README 与测试入口](https://github.com/openucx/ucx)

