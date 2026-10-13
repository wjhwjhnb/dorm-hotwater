# 宿舍热水 · 一键开

> 双击一下就能开宿舍热水。不掏手机、不开小程序、没有开屏广告。
> 洗完双击「关热水」，它会告诉你这次用了几分钟。

适用：**乐校通**系的水电控（微信小程序 `wxc83b34a65e93b730`）。

---

## 它解决什么问题

学校的热水要用微信小程序开，每次流程是：

```
拿出手机 → 打开微信 → 找到小程序 → 等开屏广告 → 点进设备列表
→ 选楼栋 → 选楼层 → 选房间 → 点开阀 → 等 3 秒
```

**每天两次，每次半分钟，中间还夹广告。**

这个工具把它变成：**桌面上双击一下。**

---

## 第一次用（大概 3 分钟）

1. 下载整个文件夹（`Code` → `Download ZIP`，解压）
2. 双击 **「一键配置」**
3. 它会问你三件事：
   - 手机号
   - 密码（就是登录小程序那个）
   - 宿舍号（比如 `402`；如果重名它会列出候选让你选）
4. 等它说「配置完成」

**中间你不用管**：没装 Python 它会自动下载安装（当前用户，不要管理员权限）、
依赖自动装、验证码自动识别、桌面图标自动创建。

### ⚠️ 密码请一次输对

服务器限制登录尝试次数（错几次会锁号）。
验证码是自动识别的，识别错了它会自己换一张重试，**这部分不用你操心**。

---

## 以后每天怎么用

| 双击这个 | 会发生什么 |
|---|---|
| **开热水** | 水来了（实测 0.3 秒） |
| **关热水** | 关掉，并告诉你这次用了几分钟 |

就这两个。

---

## 为什么不用蓝牙

一开始的实现是**蓝牙直连水控器**（扫描 → 连接 → 走 `cmd F0/08/02/06` 握手）。
能用，但有个前提：**手机连着这台设备时电脑就连不上**（蓝牙设备一次只能被一个主机连接），
而且每次要扫设备、握手，慢。

后来发现：`deviceVer` 第二段是 `07` 的设备是 **4G 机**
（小程序 `util/version.js` 里 `FourthGCanUse` 表含 `"07"`，`getCache` 里也有 `ali4G` + IMEI），
**开阀和关阀都是服务器下发指令**，根本不用蓝牙。

```
POST /bath/valve/open     {typeId, channelWay:4, machineId, deviceVer,
                           machineInfoJson:{lastPosMoney:"0000", lastPosSerial:"0000"}}
     -> Data.communicationVal    ← 关阀/查状态要回传这个值

POST /bath/valve/close    {.., machineInfoJson:{machineId, communicationVal}}

GET  /bath/valve/getStatus?typeId&machineId&modeValue&communicationVal
```

实测开阀 **0.29 秒**。旧蓝牙那套代码还在 `src/` 里（`lxt_ble.py` / `lxt_nb.py` /
`lxt_open.py`），如果你的设备是蓝牙机可以参考。

---

## 登录：只有手机号 + 密码 + 图片验证码，**没有短信**

这一点很容易搞错，我一开始也搞错了：

```python
# 真实登录就两步
GET  user/authentication/getCode?account=<手机号>&topservice=true
     -> 图片验证码（base64）

POST user/login/userLoginV2
     {"studentMobile":"..","loginPassword":"..","code":"<图片验证码>","topservice":true}
     -> 响应头 Set-Cookie: tokenInfo=<学号>#?<32位hex>
```

**`code` 字段名叫 code，其实是【图片验证码】，不是短信码。**

那 `moblieMessageV2` 是干什么的？它属于**忘记密码/重置密码**流程
（对应 `user/modify/loginPasswordByAuthenticationMessage`），跟登录无关。

已实测验证：用**假密码 + 真图片码**请求，服务器回
`{"Code":-45,"Message":"登录账号或密码不正确!,还有2次机会!"}`
—— 说明验证码先被接受了 ⇒ 登录不需要短信。

### 验证码怎么自动识别

用 [ppllocr](https://pypi.org/project/ppllocr/)（纯 ONNX 的验证码识别库，内置模型，CPU 推理 <150ms）。

实测这个 5 位数字验证码**识别率 5/5**。万一认错也没关系 —— 验证码可以无限重取，
代码里就是个重试循环，错了自动换一张。

**不上打码平台**：要钱、要联网、要把用户验证码发给第三方，而且这个验证码本地模型足够。

---

## 登录过期怎么办

会话实测能活 10 小时以上。`credentials.py` 用 **Windows DPAPI** 加密保存账号密码
（只有你这台电脑 + 这个 Windows 账户能解开，拷走也打不开），
`keepalive.py` 定期 ping，票据失效时**自动重新登录**（验证码还是自动识别），你不会察觉。

不想存密码：跑 `python src/credentials.py --clear`，或者配置时选 n。

---

## 文件夹里都是啥

```
一键配置.bat          ← 第一次用 / 换电脑时点这个
开热水.bat / 关热水.bat ← 平时只用这俩
SETUP.bat             ← 一键配置的真实逻辑（ASCII 文件名）
run.bat               ← 开/关热水共用的启动器
setup.py              ← 配置向导（中文界面）
cli.py                ← 命令行入口
src/
  lxt_login.py        登录（手机号+密码+图片码）
  captcha.py          验证码识别
  valve.py            开阀/关阀/状态
  discover.py         按宿舍号找设备
  credentials.py      凭据加密存储（DPAPI）
  keepalive.py        会话保活
  lxt_ble.py 等       旧的蓝牙实现（备用）
docs/
  协议说明.md          接口、签名、X-Ghost、踩过的坑
  常见问题.md
```

---

## 常见问题

**双击「开热水」说找不到 Python？**
会自己转去跑「一键配置」。或者 python.org 装一个 3.9+，勾上 "Add python.exe to PATH"。

**⚠️ 不要用微软商店版的 Python** —— 那个 `python.exe` 是个假壳子，装不了依赖。
启动脚本会自动跳过它。

**宿舍号输了没找到？**
输完整一点，比如 `15栋402`。重名房号（不同楼栋都是 402）会都列出来，看清楚地址再选。

**能开别人宿舍吗？**
不能。工具用的是**你自己的**账号，只能操作你账号权限内的设备。

**会多扣钱吗？**
不会，就是正常用水计费，跟你用小程序开一样。

**我们学校设备型号不一样能用吗？**
先跑「一键配置」看 `deviceVer`。第二段是 `07` 的话就是 4G 机，走服务器下发，能用。
蓝牙机也能用，但需要 `src/lxt_ble.py` 那套。

---

## 几句实话

- 开热水是**真的会出水、真的会扣钱**
- 只操作**你自己账号、你自己宿舍**的设备
- 别拿去搞别人宿舍

## 许可

MIT，见 LICENSE。
仅供自己宿舍、自己账号使用。
