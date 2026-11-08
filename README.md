# EDL//BENCH

面向 Linux 的**签名 9008** 烧写台：`QSaharaServer` + `fh_loader` 的厂商签名流程包装成本地 Web 界面，
支持分区提取与写入。

## 目录

```
fh_loader              Qualcomm 19.06 Linux 构建（生产用）
QSaharaServer          Sahara 上传器
build/fh_loader_oppo   自编译增强版（--special_rw_mode / --skip_configure / ModeType 注入）
third_party/           自建版源码与补丁
server/  web/          WebUI（FastAPI + React）
scripts/               构建 / 启动 / udev 规则
SM8650_8Gen3/          授权物料：melf + digest + sign
data/                  运行时（runs/、session.json）
```

## 启动

```bash
sudo install -m644 scripts/udev-9008.rules /etc/udev/rules.d/51-qualcomm-9008.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
./scripts/start.sh            # http://127.0.0.1:8765
```

## 使用流程（9008 整包）

选固件目录为 **9008 整包**（如 `OPD2404_9008_14.1.0.222CN01`）时，分区表直接从包内 `IMAGES/gpt_main*.bin` 解析：

- **分区表以 `rawprogram{0..5}.xml` 为准**（包内自带，含 label / LUN / 起始 / 扇区 / 镜像名）
- `rawprogram{N}.xml` 缺省时回退解析 `gpt_main{N}.bin`（LUN = N，4K 扇区）
- 镜像按 `filename` 精确取用；`PrimaryGPT` 等条目直接对应 `gpt_main*.bin`
- 表里显示 `就绪 / 缺文件`，写入模式「全选」只勾有镜像的分区
- 提取按 label + LUN + start + sectors 直读；写入按分区把镜像分块下发

实测（OPD2404_9008_14.1.0.222CN01）：

| 项 | 数值 |
| --- | --- |
| rawprogram 条目 | 145 |
| 包内可用镜像 | 55 |
| 可写入数据量 | 858.8 MiB |
| 缺失镜像 | `super.img`（14 GiB）、`oplus`、`sec.dat` |
| 未被引用的动态分区镜像 | 17 个（`my_stock.img` 1.6 GiB、`odm.img` 1.5 GiB、`system_ext.img` 1.0 GiB…约 9 GiB） |

`boot_a` 解析为 LUN4 / start 121734 / 49152 扇区，与设备实测完全一致。

**关于包体积与 super.img**：包 11 GiB，rawprogram 只引用约 0.86 GiB，其余是动态分区镜像
（`system / system_ext / vendor / odm / my_*`，合计 8.8 GiB）。它们必须打进 `super` 才能刷。

`server/superbuild.py` 负责合成：

1. 读 `META/super_def.<nv>.json`（super 容量、group、各动态分区大小与镜像路径）
2. 稀疏镜像（magic `3aff26ed`）先用 `simg2img` 反稀疏
3. `lpmake --device-size … --metadata-size … --metadata-slots 2 --group name:size --partition name:readonly:size:group --image name=file` 组装
4. 产出 `IMAGES/super.img`（14.0 GiB，17 个逻辑分区），随后重新扫描包即变为可用

实测：合成后 145 条目 / 56 个可用镜像 / **写入量 14.8 GiB**，`super` 行显示 `super.img`。
`ocdt`、`secdata` 因包内无 `oplus` / `sec.dat`，标注为「可选缺失」（合计 156 KiB，不阻断写入）。

**写入细节**

- 分区按 `(LUN, 起始扇区)` 排序后顺序下发，避免跨 LUN 来回跳
- 写入扇区数取自镜像实际大小（向上取整到 4K），不写满整个分区
- 镜像大于分区 → 直接判错并跳过，不写入
- 写入模式下没有镜像的分区在表里变灰、不可勾选；「全选」只选有镜像的
- 底部实时显示 `选 N · X GiB`

**授权物料与 9008 包分开指定**

- 授权物料目录：`melf / digest / sign`，只用于 Sahara + VIP 授权链
- 9008 包目录：`IMAGES/gpt_main*.bin` + 镜像，只用于分区表与写入数据
- 两者互不影响：换包不会动授权文件，换授权文件也不会丢分区表

## 使用流程（rawprogram）

1. 设备进 9008，顶部显示 `9008 在线 · 待握手`
2. 选授权目录（自动匹配 `melf` / `digest` / `sign`，可单独覆盖）→ 点 **进入 FIREHOSE**
   执行 Sahara → digest → verify ping → Sign.bin → sha256init
3. 加载 `rawprogram*.xml` 得到分区表；没有表就打开「手填范围」填 `label / LUN / start / sectors`
4. 勾选分区 → 提取 / 写入（分块 8–64 MiB，可追加 fh_loader 参数）

握手成功后提取/写入**不会重复握手**；会话状态跟随端口，刷新页面不丢。

## 能力边界

授权物料是 **SM8650_V1.0.21（2023-11-07 构建）的售后授权包**：

| 能力 | 状态 |
| --- | --- |
| programmer 上传 / VIP 握手 | ✅ |
| 按分区提取（boot_a 192 MiB，sha256 与系统侧一致） | ✅ |
| 按分区写入（写回自检逐字节一致） | ✅ |
| 裸读 GPT（LBA 0–33） | ⛔ 售后授权无此能力 |

分区信息只能来自 `rawprogram*.xml` 或手动偏移。

## 构建自建 loader

```bash
./scripts/build-fhloader.sh          # → build/fh_loader_oppo
```

细节见：

- [docs/HANDSHAKE.md](docs/HANDSHAKE.md) —— **握手独立文档**（顺序、铁律、状态识别、冷启动）
- [docs/VERIFIED-FLOWS.md](docs/VERIFIED-FLOWS.md) —— 全部已验证流程
- `scripts/handshake.sh` —— 命令行握手脚本（端口出现即交给 Sahara，无任何预读）
