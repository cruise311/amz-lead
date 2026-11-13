# amz-lead: Amazon Seller Lead Generation Pipeline

![CI](https://github.com/cruise311/amz-lead/actions/workflows/ci.yml/badge.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.10+-blue.svg)
![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)
![Types: mypy](https://img.shields.io/badge/typed-100%25-blue.svg)

> **从 BSR 榜单到可打电话的卖家名单，全链路自动化。**

`amz-lead` 是一个面向跨境电商服务商（货代、海外仓、营销、合规、金融）的**开源数据采集与清洗框架**。它解决一个核心问题：**如何以低成本、高合规、可持续的方式，持续获取「亚马逊美国站中国卖家」的真实联系方式与经营画像。**

> ⚠️ **开源版 vs 闭源版**
> - ✅ **开源版（MIT）**：采集层、还原层、评分层、流水线编排、接口定义
> - 🔒 **闭源版（商业授权）**：补号层实现（探迹登录、验证码自愈、OCR识别、Excel回填）、OCR模型、云服务版API
> - 开源版仅提供 `ILookupProvider` 抽象接口 + `StubLookupProvider` 测试存根
> - 商业版/云服务版联系：cloud@amz-lead.example.com

---

## ✨ 核心能力

| 阶段 | 模块 | 输入 | 输出 | 关键技术 |
|---|---|---|---|---|
| **1. 采集** | `collect` | 类目树配置 | 卖家 ID、店铺名、BSR、地址、类目、价格 | 分布式爬虫、断点续采、反爬对抗、多站点支持 |
| **2. 还原** | `resolve` | 拼音店铺名 + 英文地址 | 中文主体名、统一社会信用代码、城市、置信度分层 | 拼音切分、行业词剥离、城市前缀加权、企查查/天眼查验证 |
| **3. 补号** | `lookup` | 中文主体名 | 手机/固话/邮箱、四色状态、可直接外呼标记 | 探迹/天眼查批量解锁、验证码自愈、虚拟号 OCR、Excel 回填 |

---

## 🚀 快速开始

```bash
# 1. 安装
pip install -e ".[dev]"          # 开发模式
playwright install chromium      # 浏览器内核

# 2. 配置（复制模板并填入你的 Key）
cp configs/cats.yaml.example configs/cats.yaml
cp .env.example .env

# 3. 跑通 Demo（采集 Top 100 卖家 → 还原主体 → 导出 CSV）
amz-pipeline --category home-garden --limit 100 --output demo_leads.csv
```

> **注意**：补号阶段需要探迹/天眼查/企查查账号与额度。开源版仅提供**接口适配层**，不含账号与额度。

---

## 📁 项目结构

```
amz-lead/
├── src/
│   ├── collect/          # 采集层：BSR 爬虫、类目链式扩展、多实例编队
│   │   ├── bsr_full.py           # 核心爬虫（单类目全量采集）
│   │   ├── bsr_chain.py          # 类目树遍历与链式扩展
│   │   ├── bsr_resolve.py        # 卖家 ID → 店铺详情解析
│   │   ├── resolver_fleet.py     # 多实例并发编队
│   │   └── cli.py                # 采集命令行入口
│   ├── resolve/          # 还原层：拼音→中文、主体核验、评分分层
│   │   ├── pinyin_restore.py     # 核心算法：切分→剥离→加权→候选排序
│   │   ├── leads_score.py        # 多维评分：可外呼性、主体一致性、运费潜力
│   │   ├── street_map.py         # 注册地址聚类、扫楼路线生成
│   │   └── cli.py                # 还原命令行入口
│   ├── lookup/           # 补号层：接口定义 + 存根实现（开源）
│   │   ├── __init__.py           # ILookupProvider、CompanyInfo、PhoneResult、StubLookupProvider
│   │   ├── cli.py                # 补号命令行入口（支持 --provider stub/tungee）
│   │   └── _private/             # 🔒 闭源实现（不含在开源分发中）
│   │       ├── tungee_runner.py      # 探迹批量跑批主流程
│   │       ├── tungee_unlock_api.py  # 探迹解锁接口封装
│   │       ├── captcha_agent.py      # 验证码自愈（滑块/点选/图标）
│   │       ├── ocr_phone.py          # 虚拟渲染号段 OCR 识别
│   │       ├── backfill_excel.py     # 结果回填 Excel、四色标记
│   │       ├── build_batches.py      # 外呼工单分批、去重、优先级排序
│   │       ├── tyc_lookup.py         # 天眼查兜底查询
│   │       └── pinyin_resolve.py     # 拼音辅助还原
│   └── utils/            # 共享工具：配置、日志、限流、存储
├── configs/
│   ├── cats.yaml                 # 类目树配置（含 yield_weights）
│   ├── yield_weights.txt         # 运费权重计算参数
│   └── settings.yaml             # 全局配置（代理、并发、重试）
├── examples/
│   └── quickstart.ipynb          # 10 分钟跑通全链路 Demo
├── tests/
│   ├── test_pinyin_restore.py    # 还原算法单测
│   └── test_leads_score.py       # 评分逻辑单测
├── pyproject.toml
├── README.md
└── LICENSE
```

---

## ⚙️ 配置说明

### `configs/cats.yaml` — 类目树与采集策略
```yaml
categories:
  - id: home-garden
    name: "Home & Garden"
    max_pages: 20
    yield_weight: 1.0
  - id: sporting-goods
    name: "Sporting Goods"
    max_pages: 15
    yield_weight: 1.2
# ... 更多类目
```

### `configs/yield_weights.txt` — 运费潜力计算权重
```
# BSR_rank_weight: 0.4
# price_weight: 0.3
# review_count_weight: 0.2
# category_multiplier: 1.0
```

### `.env` — 敏感配置（不提交 Git）
```bash
# 探迹
TUNGEE_USERNAME=your_username
TUNGEE_PASSWORD=your_password

# 天眼查
TYC_API_KEY=your_key

# 企查查
QCC_API_KEY=your_key

# 代理（可选）
PROXY_URL=http://user:pass@host:port
```

---

## 📊 产出数据说明

| 文件 | 字段 | 说明 |
|---|---|---|
| `leads_raw.csv` | seller_id, store_name, bsr, category, address, price, ... | 采集原始数据 |
| `leads_resolved.csv` | + chinese_name, unified_code, city, confidence, match_type | 还原后数据 |
| `leads_enriched.csv` | + phone, phone_label, email, callable, tier | 补号后数据（四色：绿=可直接打、黄=需复核、红=不可打、灰=无号） |

---

## 🧪 测试

```bash
# 单测
pytest tests/ -v

# 还原算法回归测试（含 3425 条真实样本）
pytest tests/test_pinyin_restore.py::test_real_samples -v
```

---

## 🐳 云服务版（托管交付）

不想自建爬虫、不想买探迹额度、不想维护代理池？

> **amz-lead Cloud**：每天早 8 点自动推送最新增量名单到你的 CRM/飞书/钉钉/Webhook。
> - 按天计费，无额度焦虑
> - 内置合规审计、数据去重、增量更新
> - 支持私有化部署、SLA 保障

👉 [了解云服务版](https://amz-lead.example.com/cloud) （示例链接，替换为你的实际落地页）

---

## ⚖️ 合规声明

- **采集数据**：仅采集亚马逊前台公开页面数据（店铺名、BSR、地址、价格等），不采集买家隐私、不绕过登录态。
- **还原算法**：基于公开工商信息（企查查/天眼查/探迹开放检索）做模糊匹配，**不存储、不转售** 原始工商数据。
- **补号能力**：开源版仅提供**调用接口封装**，使用者需**自备合规账号与额度**，自行承担数据使用合规责任。
- **云服务版**：采用「数据不出库、只出评分/标签」架构，满足《个人信息保护法》《反电信网络诈骗法》合规要求。

详见 [COMPLIANCE.md](docs/COMPLIANCE.md)。

---

## 🤝 贡献指南

1. Fork & Clone
2. `pip install -e ".[dev]"`
3. 写测试 → 改代码 → `pytest` 通过
4. `black . && ruff check .`
5. 提交 PR

---

## 📄 许可证

MIT License — 详见 [LICENSE](LICENSE)。

> **商业化建议**：核心算法（`pinyin_restore.py`）采用 MIT，可直接集成到闭源产品。补号模块（`lookup/`）涉及第三方平台接口，**建议仅开源接口定义，实现细节闭源**，通过云服务版或私有化部署变现。

---

## 🙋 常见问题

**Q: 能不能直接给我一份现成的名单？**
A: 开源版不提供数据，**提供生产名单的代码与方法论**。云服务版按天交付增量名单。

**Q: 采集会不会被封 IP？**
A: 内置 `bsr_watchdog.sh` 守护进程 + 指数退避 + 代理池接口。建议自备优质住宅代理池。

**Q: 还原准确率多少？**
A: 在 3425 条真实样本上，**城市前缀加权后 Top-1 命中率 82%**，Top-3 覆盖率 94%。详见 `tests/test_pinyin_restore.py`。

**Q: 支持欧洲站/日本站吗？**
A: `bsr_full.py` 已内置 `checkpoint_de.json`、`checkpoint_fr_X.json` 等多站点断点，**仅需在配置中启用目标站点**。

---

## 📞 联系与支持

- Issues: [GitHub Issues](https://github.com/yourname/amz-lead/issues)
- 云服务咨询: cloud@amz-lead.example.com
- 微信交流群: 扫码入群（README 末尾二维码）

---

*让每一个跨境服务商，都能用得起高质量的卖家线索。*