# 自建 fh_loader（带 OPPO 扩展）

来源：https://github.com/LonelyFool/fh_loader —— Qualcomm firehose loader 的 Linux 可编译移植版
（C++，`fh_loader.cpp` 11395 行）。上游没有 23.06 版 Windows 工具里的 OPPO 私有开关，本目录是我们在其基础上打的补丁。

## 补丁内容（`patches/oppo-ext.patch`，76 行）

| 改动 | 目的 |
| --- | --- |
| `SLASH` 按平台分支（Windows `\` / Linux `/`） | 上游把分隔符写死成 `\`，Linux 上所有文件查找失败（`'/tmp/x/xml' not found`） |
| 新增 `--skip_configure` | 跳过 host 自带的 `<configure>`，保住 VIP/授权状态（对齐 23.06 版行为） |
| 新增 `--special_rw_mode=oplus_gptmain\|oplus_gptbackup` | OPPO 私有扩展，值写入 `<configure SpecialRwMode="…">` |
| 新增 `FH_MODETYPE=<0-5>` | 注入 `ModeType="N"`（19 版从不发，导致设备报 `Mode= Invalid value`） |
| 新增 `FH_ENABLEFLASH=1` | 注入 `EnableFlash="1"`（19 版从不发，导致 `EnableFlash not found`） |
| 新增 `FH_ENABLEVIP=1` | 注入 `EnableVip="1"` |
| `FH_SKIP_CONFIGURE=1` | 环境变量形式的 skip，便于脚本控制 |

## 构建

```bash
./scripts/build-fhloader.sh            # → build/fh_loader_oppo
```

## 状态（2026-11-07）

- ✅ 编译通过（GCC 16.2.1，`-fpermissive`），产物 251,976 B
- ✅ 选项解析与 configure 生成已验证：
  ```xml
  <configure MemoryName="ufs" ModeType="0" EnableFlash="1" EnableVip="1"
             SpecialRwMode="oplus_gptmain" ... />
  ```
- ✅ **离线等价性验证**（PTY 假设备，`/tmp/fh_emu.py`）：同一份 XML 下，自建版与原始 19.06 发出的
  `<read>` / `<program>` 数据块**逐字节一致**；唯一差异是首个 `<configure>` 的 `ZlpAwareHost=1`（作者命令要求的取值）
- ❌ **裸读 GPT 仍未打通**：设备侧回 `VIP img authentication failed (rsp_0=0x40000b)`，
  属于 VIP 物料缺失，不是本工具能解决的
- ⚠️ `EnableVip` 与 `SpecialRwMode` 同时出现在 configure 时，设备曾直接拒收
  （`The target rejected your <configure>`），说明这两个属性需要按设备接受的组合来用

## 说明

今晚所有**成功**的读/写测试使用的都是工作区里的原始二进制（19.06.10.18.44），
不是这个自建版。自建版仅用于尝试复刻 23.06 版的 `special_rw_mode` 能力。
