# 已验证流程（真机记录）

设备：一加平板 Pro（SM8650，UFS，SN 8C63FA39）
工具：`QSaharaServer` + `fh_loader` 19.06.10.18.44
物料：`SM8650_8Gen3/`（melf + digest + sign）

握手细节见 [HANDSHAKE.md](HANDSHAKE.md)，这里只记录**跑通过的操作**。

---

## 1. 握手（五步，实测 0.9 秒）

```bash
./QSaharaServer -p /dev/ttyUSB0 -v 1 -s 13:SM8650_8Gen3/OPPO_SM8650_V1.0.21.melf
./fh_loader --port=/dev/ttyUSB0 --memoryname=ufs --signeddigests=SM8650_8Gen3/OPPO_SM8650_Digest.elf --search_path=SM8650_8Gen3 --testvipimpact --noprompt
# verify ping / Sign.bin / sha256init 见 HANDSHAKE.md 第 1 节
```

实测输出：

```
1525932 bytes transferred in 0.209710 seconds (6.9393MBps)
Sahara protocol completed
digest / verify / sign / sha256init 全部 rc=0
```

---

## 2. 按分区回读

```bash
<configure MemoryName="ufs" ModeType="0" EnableFlash="1" />
<read SECTOR_SIZE_IN_BYTES="4096" filename="p.bin"
      physical_partition_number="4" start_sector="121734"
      num_partition_sectors="8192" label="boot_a"/>
```

实测（boot_a，LUN4，192 MiB）：

```
INFO: entry:boot_a, start:0x1db86, num:0xc000
INFO: devprg_svip_check_permission ok
落盘 201,326,592 字节
sha256 a8a578ad06ab9a100c81e2c882af03164f0bfcc853f0c5f62062d7fe7b812a96
与 Android 侧 /dev/block/by-name/boot_a 一致
头 "ANDROID!"，尾部 AVBf
```

要点：

- 必须**直接下发 `<read>`**，用 `--convertprogram2read` 会 `not allowed on external network`
- `ModeType`（数字）+ `EnableFlash="1"` 必须带
- LUN 必须正确（boot_a = 4），LUN 错会静默读到别处

---

## 3. 按分区写入

同 `<read>` 换成 `<program>`，分块 32 MiB。实测把 boot_a 原样写回再回读，逐字节一致。

---

## 4. 复位

```xml
<power value="reset"/>
```

下发后 USB 断开，约 12 秒回到系统。

---

## 5. 能力边界

| 能力 | 状态 |
| --- | --- |
| programmer 上传 / 五步握手 | ✅ |
| 分区内回读 / 写入 | ✅ |
| 裸读 GPT（LBA 0–33） | ⛔ `VIP img authentication failed (rsp_0=0x40000b)` |

分区偏移只能来自 `rawprogram*.xml` 或手动填写。
