# 9008 握手（Firehose 进入）—— 独立操作文档

> 适用：一加平板 Pro / SM8650，售后授权包 `SM8650_V1.0.21`
> 结论来源：2026-11-08 01:04 实测，全程 **0.9 秒** 完成

---

## 0. 一句话结论

**端口出现的第一个"读者"必须是 `QSaharaServer`。**
任何在它之前的读串口行为（嗅探、探活、试读）都会消耗掉 `SAHARA_HELLO`，让后续握手永久卡在 `SAHARA_WAIT_HELLO`。

---

## 1. 正确顺序（照抄即可）

```bash
cd /home/yourcarina/Projects/edl-gui
FW=SM8650_8Gen3
MELF=$FW/OPPO_SM8650_V1.0.21.melf
DIG=$FW/OPPO_SM8650_Digest.elf
SIG=$FW/OPPO_SM8650_Sign.bin

# 1) Sahara（第一个碰端口的进程）
./QSaharaServer -p /dev/ttyUSB0 -v 1 -s 13:$MELF

# 2) digest
./fh_loader --port=/dev/ttyUSB0 --memoryname=ufs \
    --signeddigests=$DIG --search_path=$FW --testvipimpact --noprompt

# 3) verify ping
mkdir -p /tmp/x && cd /tmp/x
echo '<?xml version="1.0"?><data><verify value="ping" EnableVip="1"/></data>' > verify.xml
/home/yourcarina/Projects/edl-gui/fh_loader --port=/dev/ttyUSB0 --memoryname=ufs \
    --sendxml=verify.xml --noprompt

# 4) Sign.bin
cd /home/yourcarina/Projects/edl-gui
./fh_loader --port=/dev/ttyUSB0 --memoryname=ufs \
    --signeddigests=$SIG --search_path=$FW --testvipimpact --noprompt

# 5) sha256init
cd /tmp/x
echo '<?xml version="1.0"?><data><sha256init Verbose="1"/></data>' > sha.xml
/home/yourcarina/Projects/edl-gui/fh_loader --port=/dev/ttyUSB0 --memoryname=ufs \
    --sendxml=sha.xml --noprompt
```

一键版：`bash scripts/handshake.sh`（等端口 → 依次执行上面五步，不做任何预读）

---

## 2. 实测记录（2026-11-08 01:04:23）

```
01:04:23.618  端口出现 → 立刻交给 Sahara
01:04:23.840  ✓ Sahara OK        Successfully uploaded all images
                                 Sahara protocol completed
01:04:24.471  digest  rc=0       TARGET: bin version: SM8650_V1.0.21
01:04:24.483  verify  rc=0       TARGET: Storage type set to value UFS
01:04:24.498  sign    rc=0       TARGET: verify passed.
01:04:24.509  sha     rc=0
              ALL_OK
```

紧接着验证 Firehose 可用（读 boot_a 头 8 扇区）：

```
INFO: EnableFlash found
INFO: Calling handler for read
INFO: entry:boot_a, w:1, r:1, e:1, c:0, s:0, start:0x1db86, num:0xc000
INFO: devprg_svip_check_permission ok
落盘 p.bin 32768 字节，头部 "ANDROID!"
```

---

## 2.5 实测遇到的三种结果（2026-11-08 01:33 起，同一会话连续尝试）

| 输出 | 含义 | 处置 |
| --- | --- | --- |
| `main:315 Could not connect to /dev/ttyUSB0` | 端口节点刚出现、还没就绪，**进程根本没碰设备** | 0.6 秒后重试（**可恢复**） |
| `sahara_rx_data:277 Unable to read packet header. Only read 7 bytes` | 包头被截断（应为 8 字节），设备侧传输中断，**HELLO 仍会继续广播** | 0.6 秒后重试（**可恢复**） |
| `SAHARA_WAIT_HELLO` 长时间无任何输出 | 设备不再广播 HELLO（被上一次读取消费掉了） | **不可恢复**，冷启动 |

实测重试记录：

```
第 1 次  Could not connect
第 2 次  Only read 7 bytes
第 3 次  ✓ Sahara protocol completed
         1525932 bytes transferred in 0.209710 seconds (6.9393MBps)
```

随后 `digest / verify / sign / sha256init` 全部 `rc=0`。

**结论：前两种错误必须自动重试（最多 5 次、间隔 0.6s）；第三种只能冷启动。**

---

## 3. 三条铁律

### 铁律一：握手前不要读串口

设备进 9008 后周期性发 `SAHARA_HELLO`。**任何一次 `read()` 都会把这个包取走**，设备随即认为主机已收到，转入"等待 HELLO_RESP/镜像"状态，不再重发。之后再启动 `QSaharaServer` 就会永远卡在：

```
EVENT: STATE <-- SAHARA_WAIT_HELLO
```

**这个状态无法用软件复位**（`--reset` / `-k` / `-i` 均无效），只能断电。

### 铁律二：握手过程中不要杀进程

`QSaharaServer` 跑到一半被 `SIGKILL`，设备同样会停在"等镜像"状态。表现和铁律一完全相同。

### 铁律三：端口路径会变

Sahara 完成后设备会**重新枚举 USB**，`/dev/ttyUSB0` 会消失再出现（设备号从 085 变 086）。所以：

- 每一步都按**路径**重新连接，不要缓存已打开的文件描述符
- 需要判断"端口是否可用"时，用 `open()` 试一下即可（打开不读不会吃包）

---

## 4. 状态识别

| 现象 | 含义 | 处理 |
| --- | --- | --- |
| `SAHARA_WAIT_HELLO` 长时间不动，端口 4 秒 0 字节 | 设备不在广播 HELLO（已被消费） | 冷启动 |
| `Received a different command: N while waiting for hello packet` | 设备已在 Firehose，不在 Sahara | 跳过 Sahara，直接走 digest |
| `Zero length packet … hardware connection went off` | 连接已断 | 冷却、换口/换线、冷启动 |
| `fh_loader` 说 `your target is speaking SAHARA protcol` | 设备在 Sahara，别用 fh_loader | 先跑 Sahara |
| `ERROR: DevprgRSAVerify verify signature failed` 后跟 `verify passed.` | 该会话已下发过签名块，属重复下发 | 正常，继续 |

---

## 5. 冷启动（唯一可靠的复位手段）

1. 长按电源 **15 秒**，确认完全关机
2. 等 3 秒
3. 进 9008
4. **不要读串口**，直接交给 `QSaharaServer`

---

## 6. 授权能力边界（售后包）

| 能力 | 状态 |
| --- | --- |
| programmer 上传 + 五步握手 | ✅ |
| 按分区 label 回读 / 写入 | ✅ |
| 裸读 GPT（LBA 0–33） | ⛔ `VIP img authentication failed (rsp_0=0x40000b)` |

分区信息只能来自 `rawprogram*.xml` 或手动偏移。
