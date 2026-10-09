# 毛球加密货币桌面看板（阿杜）

Windows 桌面悬浮行情看板。启动后显示可拖动的绿色毛线球；悬停展开，单击固定，再单击收起。默认关注 BTC、ETH、AR、NEAR、LINK、ONDO，可继续添加其他币种。程序只读取公开行情，不连接交易账户，也不提供交易或价格预测。

## 下载与使用

在 [Releases](https://github.com/duyubomidjer/maoqiu-crypto-watch/releases) 下载 `MaoqiuCryptoWatch-v1.0.0-windows-x64.zip`，解压整个 ZIP 到可写目录，双击 `MaoqiuCryptoWatch.exe`。便携版已包含 Python 运行环境，无须另外安装 Python。不要只取出单个 EXE；它需要同目录下的 `_internal` 文件夹。

首次运行会在 EXE 同目录生成 `state/`，保存自选和设置。程序需要能访问 OKX 和 CoinGecko；断网时仍可打开，但价格不会更新，旧数据会提示过期。若需要 CoinGecko Demo Key，请在程序「设置」中自行填写。Key 使用当前 Windows 用户的 DPAPI 加密，换电脑后需重新填写。不要分享 `state/`。

此版本未签名；从互联网下载后，Windows 可能提示“未知发布者”或应用信誉不足。遇到明确的病毒检测，请先停止运行并核查，不要直接忽略。

## 行情口径

| 显示内容 | 来源 |
|---|---|
| 默认六币的最新价格 | OKX USDT 现货，实时推送；断线时尝试重连或轮询 |
| 未核验 OKX 对应关系的新增币价格 | CoinGecko 综合 USD 价格 |
| 24h/7d 涨跌、市值、排名、24h 全市场成交额 | CoinGecko 综合统计，默认每 15 分钟更新，可手动刷新 |

24h 全市场成交额是数据商覆盖交易所的该币综合成交额，并非单个交易所的量，也不保证覆盖全球每笔交易。OKX 价格和 CoinGecko 统计的计价单位不同，界面会标出来源。

涨跌幅的绝对值**严格大于 10%**时，对应 24h 或 7d 单元格每秒亮暗交替；上涨为绿、下跌为红。刚好 ±10%、缺失或过期数据不闪烁。

手动刷新有 30 秒冷却；CoinGecko 限流时至少退避 5 分钟。自动间隔可设为 5、10、15、30 或 60 分钟。免费 API 有额度限制，建议保留默认 15 分钟。

## 从源码运行

需要 Windows、Python 3.12。克隆仓库后在仓库目录执行：

```powershell
py -3.12 -m pip install -r requirements.txt
py -3.12 app.py
```

不联网的功能检查：

```powershell
py -3.12 -m unittest -q test_market test_reliability test_highlight
py -3.12 validate_orbit.py
```

源码目录运行和便携版运行使用各自目录下的 `state/`。从旧电脑迁移自选时，只需复制 `state/settings.json`；不要复制 `coingecko-key.dpapi`，在新电脑重新填写 Key。

## 自行构建便携版

`build-portable.ps1` 会在项目内创建隔离的 `.build-venv/`，安装构建依赖，生成 `dist/MaoqiuCryptoWatch/` 和可发布的 ZIP。仅在 Windows x64 上构建和运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\build-portable.ps1
```

构建环境和运行数据均由 `.gitignore` 排除。发布的 ZIP 只含程序和运行必需素材，不含任何用户配置、缓存或密钥。

代码及原创界面素材按 [MIT 许可](LICENSE)发布；币种标志的权利归各自所有者，见 [素材说明](assets/ASSETS.md)。
