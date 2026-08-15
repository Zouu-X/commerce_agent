# Milestone 1：不依赖 LLM 的电商业务沙盒

完成日期：2026-07-12

## 1. 里程碑目标

Milestone 1 的目标是在接入 Agent 之前，先建立一套确定、可测试、具备数据隔离的电商业务能力。

这一阶段刻意不使用 LLM。商品价格、订单归属、物流状态、退款金额上限和退货期限都属于业务事实或确定性规则，不应该由模型猜测。Agent 在下一阶段只负责理解用户意图和选择工具；工具背后的数据访问与规则判断由本阶段的服务层负责。

完成后的系统已经可以通过 API 独立处理：

- 商品搜索、分类、价格和库存过滤；
- 商品及 SKU 详情查询；
- 当前顾客订单列表和订单详情；
- 物流轨迹查询和异常识别；
- 售后申请状态查询；
- 取消订单和申请退款的资格判断；
- tenant、store、customer 三层上下文隔离。

## 2. 数据库和依赖选择

后端新增了：

- SQLAlchemy 2：异步 ORM 和类型化查询；
- asyncpg：PostgreSQL 异步驱动；
- Alembic：数据库迁移；
- Pydantic Settings：环境变量配置；
- aiosqlite：测试中的快速内存数据库。

生产和 Docker 环境继续使用 PostgreSQL。服务单元测试使用 SQLite 内存数据库，以获得快速反馈；最终验收再使用真实 PostgreSQL 执行迁移、seed 和 API 请求，避免 SQLite 与 PostgreSQL 的差异被忽略。

## 3. 数据模型

本阶段实现了 10 张电商域表：

```text
tenants
  ├── stores
  ├── customers
  ├── products
  │     └── product_variants
  └── orders
        ├── order_items ──> product_variants
        ├── shipments
        │     └── shipment_events
        └── after_sales
```

知识库、会话、消息、待审批动作和审计日志没有提前加入。这些实体会在对应里程碑实现，避免现在建立没有真实行为支撑的空模型。

### 3.1 Tenant 和店铺

`tenants` 是最高层业务隔离边界，`stores` 必须属于一个 tenant。店铺保存营业时间和时区，为后续客服承诺、超时判断和政策生效时间提供依据。

### 3.2 顾客

`customers` 直接携带 `tenant_id`。邮箱在同一 tenant 内唯一，但不同 tenant 可以拥有相同邮箱，避免全局唯一约束错误地把不同商户的数据绑定在一起。

### 3.3 商品和 SKU

商品表保存 tenant、store、名称、描述、分类和状态；价格、库存、颜色等可销售属性放在 `product_variants`。

将商品和 SKU 分开是因为同一商品可能有多个规格，每个规格的价格和库存不同。Agent 以后不能只回答“商品有货”，而需要基于具体 SKU 返回真实库存。

数据库对价格和库存增加非负检查约束，防止无效数据绕过应用层直接写入。

### 3.4 订单和订单项

订单直接保存：

- `tenant_id`；
- `store_id`；
- `customer_id`；
- 订单号、订单状态、支付状态、实付金额和创建时间。

订单号只要求在 tenant 内唯一，而不是全局唯一。所有服务查询都同时匹配 tenant、store 和 customer，不能只依赖用户提供的订单号。

订单项保存下单时的 `unit_price`，而不是每次读取商品当前价格。商品后续调价时，历史订单的实付事实不会改变。

### 3.5 物流

`shipments` 保存承运商、运单号、当前状态和最后更新时间；`shipment_events` 保存完整时间线。

两张表分开后，查询当前状态不需要扫描所有节点，但仍然可以向用户展示可解释的物流轨迹。

### 3.6 售后

`after_sales` 保存 tenant、订单、顾客、类型、原因、状态和申请金额。售后记录同时保存 tenant/customer，是为了让权限过滤直接且明确；它也会通过订单关系检查 store 归属。

## 4. 数据库约束和索引

迁移中加入了：

- 主键和外键；
- 级联删除或限制删除策略；
- tenant 内唯一约束；
- SKU、运单号等唯一约束；
- 金额、库存、数量的 Check Constraint；
- 商品分类查询索引；
- tenant/store/customer/created_at 订单复合索引；
- 物流事件时间线索引。

这些规则不只写在 Python 中。数据库约束可以防止未来脚本、后台任务或其他服务绕过 API 后写入非法状态。

## 5. Alembic 迁移

初始迁移版本为：

```text
20260712_0001
```

API 容器启动时按顺序执行：

```text
alembic upgrade head
python -m app.commerce.seed --if-empty
uvicorn app.main:app ...
```

这样新环境第一次执行 `make up` 时，不需要手工创建表或导入数据。

自动迁移适合当前单实例求职 Demo。真实多实例生产环境通常会把迁移拆成独立部署任务，避免多个副本同时运行迁移；这是当前阶段有意识的简化。

最终使用 `alembic check` 对真实 PostgreSQL 做了模型漂移检查，结果是：

```text
No new upgrade operations detected.
```

说明 SQLAlchemy metadata 和已提交迁移保持一致。

## 6. 确定性模拟数据

Seed 使用固定 UUID namespace、UUID v5 和固定时间基准：

```text
2026-07-12 08:00:00 UTC
```

同一个业务键每次都会生成同一个 UUID。例如 tenant、顾客、订单、物流和售后 ID 都不会因为重置而变化。

数据规模为：

| 实体 | 数量 |
|---|---:|
| tenant | 2 |
| 店铺 | 2 |
| 顾客 | 12 |
| 商品 | 24 |
| SKU | 48 |
| 订单 | 60 |
| 物流单 | 28 |
| 物流节点 | 56 |
| 售后申请 | 6 |

两个模拟 tenant 分别是“极光生活”和“海港数码”，各自有独立店铺、顾客、商品和订单。

### 6.1 内置边界场景

Seed 特意包含：

- 商品两个 SKU 均缺货；
- 同一商品存在不同颜色和价格的 SKU；
- 多位顾客拥有不同订单；
- 两个 tenant 数据完全隔离；
- 订单处于 paid、pending、shipped、delivered、cancelled 等状态；
- 物流超过 5 天没有更新；
- 物流派送失败；
- 售后申请正在审核；
- 申请退款金额可以超过实付金额；
- 订单可以超过当前简化的 7 天退货窗口。

### 6.2 Seed 的幂等行为

容器启动使用 `--if-empty`：已有数据时不会污染当前演示状态。

显式执行：

```bash
make seed
make reset-demo
```

会按照外键依赖的逆序清空电商表，再写入完全一致的数据。真实 PostgreSQL 上重复 reset 后订单数仍然是 60，证明重置不会重复累积记录。

## 7. 可信业务上下文

所有服务方法都要求传入不可变的：

```python
@dataclass(frozen=True, slots=True)
class CommerceContext:
    tenant_id: UUID
    store_id: UUID
    customer_id: UUID
```

HTTP Demo 通过三个 Header 选择模拟身份：

```text
X-Tenant-Id
X-Store-Id
X-Customer-Id
```

Header 只是当前 Demo 的身份选择入口。关键安全边界在服务层：身份字段不会作为普通搜索参数传给业务查询，更不会在下一阶段暴露给 LLM 填写。Agent 工具只会生成订单号、关键词等业务参数；`CommerceContext` 由服务器根据已认证会话注入。

## 8. 为什么越权查询返回 404

订单详情查询同时添加：

```text
order_number = 用户请求的订单号
tenant_id    = 服务端上下文 tenant
store_id     = 服务端上下文 store
customer_id  = 服务端上下文 customer
```

如果顾客 A 查询顾客 B 的订单，数据库查询结果为空，API 返回统一的：

```json
{"detail":"order_not_found"}
```

这里选择 404 而不是 403，是为了不向攻击者确认“这个订单确实存在，只是不属于你”。商品、物流和售后也使用相同模式。

真实 PostgreSQL API 验证中，顾客 1 查询顾客 0 的 `AUR-202607-0001`，结果为 HTTP 404。

## 9. 业务服务

### 9.1 CatalogService

支持：

- 名称和描述模糊搜索；
- 分类过滤；
- 有货/无货过滤；
- SKU 最大价格过滤；
- 限制最大返回数量；
- 商品详情和全部 SKU 查询。

搜索条件始终叠加 tenant/store/status 条件。真实 API 测试中，`in_stock=false` 只返回预设的缺货商品。

### 9.2 OrderService

支持当前顾客订单列表和订单详情。详情一次加载订单项、SKU、商品名、物流是否存在以及关联售后 ID，避免在序列化阶段触发异步懒加载。

### 9.3 ShipmentService

返回当前物流状态和按时间倒序排列的节点，并确定性识别：

- `DELIVERY_FAILED`；
- `NO_UPDATE_5_DAYS`。

真实 API 对 `AUR-202607-0005` 的验证返回 `NO_UPDATE_5_DAYS`，最后更新时间为 2026-07-06。

### 9.4 AfterSaleService

按售后 ID 查询状态，但同时检查 tenant、customer 和订单 store。只知道售后 UUID 不能跨顾客读取申请原因或金额。

### 9.5 OrderPolicyService

提供只读资格判断，不执行真实取消或退款。

取消规则根据订单状态判断；退款规则检查：

- 是否已经支付；
- 当前订单状态是否可退款；
- 是否在简化的 7 天期限内；
- 申请金额是否超过订单实付金额。

规则返回稳定错误码，例如：

```text
ORDER_STATUS_DELIVERED
PAYMENT_NOT_COMPLETED
RETURN_WINDOW_EXPIRED
REFUND_AMOUNT_EXCEEDS_PAID_AMOUNT
```

这些错误码可以被后续 Agent 转换成自然语言，但模型不能改变判断结果。

当前退货窗口使用订单创建时间近似计算。真实电商系统通常根据签收时间和商品类别计算；在拥有更完整的签收与政策数据后应替换这个简化规则。

## 10. API

新增只读接口：

```text
GET /api/v1/demo/contexts
GET /api/v1/catalog/products
GET /api/v1/catalog/products/{product_id}
GET /api/v1/orders
GET /api/v1/orders/{order_number}
GET /api/v1/orders/{order_number}/shipment
GET /api/v1/orders/{order_number}/eligibility
GET /api/v1/after-sales/{after_sale_id}
```

系统接口新增：

```text
GET /api/v1/ready
```

`health` 只表示 API 进程能够响应；`ready` 会执行 `SELECT 1`，只有数据库可连接时才返回 ready。区分存活和就绪，可以避免流量发送到数据库尚不可用的 API 实例。

`/demo/contexts` 用于前端和招聘方查看可选择的模拟店铺、顾客及代表订单，不需要手工查询数据库 UUID。

## 11. API Schema 和错误边界

响应通过 Pydantic Schema 定义，不直接把 ORM 对象交给 FastAPI 自动序列化。

这样可以：

- 明确控制对外字段；
- 避免未来误暴露 email、内部外键等字段；
- 把 `attributes_json` 转换成更清晰的 `attributes`；
- 返回稳定的金额、物流异常和业务错误结构。

`ResourceNotFoundError` 由统一异常处理器转换成安全的 404 JSON，数据库异常和内部堆栈不会直接返回给用户。

## 12. 测试策略

当前后端共有 9 项测试。

### 12.1 快速服务测试

每个异步测试创建 SQLite 内存数据库，通过 SQLAlchemy metadata 建表并装载完整确定性 seed。

覆盖：

- Seed 数量和 UUID 稳定性；
- 商品只返回当前 tenant/store 数据；
- 缺货过滤；
- 跨 tenant 商品详情不可见；
- 跨顾客订单不可见；
- 跨 tenant 订单不可见；
- 5 天未更新物流异常；
- 派送失败异常；
- 超额退款拒绝；
- 过期退款拒绝；
- 售后跨顾客不可见。

### 12.2 API 测试

通过 HTTPX ASGITransport 调用真实 FastAPI 路由，并用 dependency override 注入测试数据库。测试明确确认订单拥有者得到 200，另一顾客得到 404。

### 12.3 PostgreSQL 集成验收

真实 Docker 环境验证了：

1. PostgreSQL healthy；
2. Alembic 从空版本升级到 `20260712_0001`；
3. API 自动 seed；
4. 数据库 tenant 数量为 2、订单数量为 60；
5. `alembic check` 没有迁移漂移；
6. `/ready` 返回 ready；
7. 缺货商品查询正确；
8. 跨顾客订单查询返回 404；
9. 物流超时识别正确；
10. 超额退款判断正确；
11. reset 后数据数量保持一致。

最终正式命令结果：

```text
Ruff: all checks passed
mypy: success, 19 source files
pytest: 9 passed
frontend ESLint: passed
frontend TypeScript/Vite build: passed
```

## 13. 关键设计取舍

### 13.1 先服务层，再 Agent 工具

Agent 工具不应该直接拼 SQL。下一阶段的工具会调用现有 Service，使普通 API、Agent 工具和自动评测复用同一套权限与规则。

### 13.2 返回 404 而不是先查订单再判断归属

如果先按订单号查到记录、再在 Python 判断 customer，会增加误泄露风险。当前查询直接把所有上下文条件放进 SQL，数据库只会返回调用者可见的对象。

### 13.3 固定 seed 而不是随机 Faker 数据

随机数据更像真实数据量，但不利于 Demo 和评测。固定订单号、UUID、金额和时间可以让招聘方复现问题，也让后续 Agent 评测拥有稳定标准答案。

### 13.4 单元测试使用 SQLite，验收使用 PostgreSQL

全部测试都依赖 Docker 会降低开发反馈速度；只使用 SQLite 又可能漏掉 Postgres 差异。两层组合兼顾速度和真实性。

### 13.5 当前用请求头模拟身份

本项目不是完整账号系统，当前 UI 需要选择模拟店铺和顾客，因此用 Header 表达已选上下文。它不是最终认证机制。真正的安全点是上下文在服务器边界构建，并且不会进入 LLM 可填写的工具参数。

## 14. 面试时如何讲这一阶段

可以用下面这段作为主线：

> 在接入 LLM 之前，我先实现了一个完全确定性的多租户电商沙盒。我用 SQLAlchemy 2 和 Alembic 建立 tenant、店铺、顾客、商品 SKU、订单、物流和售后模型，并在数据库层加入外键、金额库存约束和面向查询路径的索引。所有服务都接收服务器构造的不可变 CommerceContext，订单查询在 SQL 中同时匹配 tenant、store 和 customer，因此模型即使拿到其他人的订单号也查不到记录，并统一返回 404，避免泄露资源存在性。
>
> 为了让 Demo 和评测可复现，我没有使用随机 Faker 数据，而是用 UUID v5 和固定时间生成两套 tenant 数据，包括缺货、物流五天未更新、派送失败、跨顾客订单、超额退款和过期退款等场景。商品、订单、物流、售后和资格判断都封装在服务层，未来 Agent 工具只负责调用这些服务，不能自己决定金额和状态规则。
>
> 测试采用两层策略：SQLite 内存数据库快速覆盖服务和越权场景，再用 Docker 中的真实 PostgreSQL 验证 Alembic、seed、API 和 reset。最终迁移无漂移，跨顾客访问返回 404，9 项测试及全部类型和 lint 检查通过。

如果面试官继续追问，可以重点展开：

- 为什么可信身份不放进工具 Schema；
- 为什么跨顾客返回 404；
- 为什么订单项保存历史成交价；
- 为什么 seed 必须确定；
- SQLite 与 PostgreSQL 双层测试的取舍；
- 自动迁移为什么只适合当前单实例 Demo；
- 当前退货时间规则有哪些简化。

## 15. 当前边界

本阶段仍然没有：

- LLM Provider 和 tool-calling loop；
- 对话及消息持久化；
- 知识库和政策引用；
- 退款、发券、取消订单的真实写操作；
- 人工审批和幂等执行；
- 完整审计与 trace。

HTTP Header 是 Demo 身份选择器，不等同于生产认证。当前退款窗口按订单创建时间近似，后续应结合签收时间、商品类型和版本化店铺政策。

## 16. 下一步

Milestone 2 将把这些确定性服务包装成只读 Agent 工具，重点实现：

- OpenAI-compatible Provider 抽象；
- Tool Registry 和严格 Pydantic 参数 Schema；
- 服务端注入 CommerceContext；
- tool-calling loop、循环次数、超时和预算限制；
- 商品、订单、物流、售后只读工具；
- user、assistant、tool 消息持久化；
- Agent 无法跨顾客访问订单的端到端测试。

Agent 层不会重新实现电商规则，而是复用 Milestone 1 的 Service。这是从“可运行电商后端”走向“安全 Agent 业务闭环”的关键衔接。
